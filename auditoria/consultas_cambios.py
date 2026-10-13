"""Consultas de la bitácora para el tablero de cambios de horas.

Separadas del tablero a propósito: aquí vive el acceso a datos y la regla que
decide si un cambio costó horas extra, y en plantilla_tablero.html vive la
interfaz. Quien quiera revisar la cifra lee este archivo y no tiene que leer
JavaScript.

El esquema de la bitácora está en migracion/auditoria_schema.sql y el porqué del
método en auditoria/README.md.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa

# Variación por debajo de la cual el cambio es redondeo al reescribir la fila y
# no una decisión de supervisión. Mismo umbral que analisis_mensual.py.
UMBRAL_RUIDO_H = 0.02

# Lo que decide supervisión. Fuera quedan 'colaborador' (marcación propia) y
# 'sistema' (barrido de turnos olvidados).
ORIGENES_SUPERVISION = ("admin", "fuera_de_la_app")

# Jornada base: lo que excede se paga como hora extra. Ver core/config.py.
HORAS_BASE_TURNO = 9.0


# Un turno puede recibir varias intervenciones. Se mide de punta a punta: el
# estado antes de la primera y el estado después de la última. Sumar los deltas
# fila a fila da un número inflado cuando el supervisor se equivoca y se corrige
# enseguida; el caso está documentado en auditoria/README.md.
SQL_TURNOS_INTERVENIDOS = """
with marcado as (
    select *,
           row_number() over (partition by turno_id order by momento,      id)      as rn_asc,
           row_number() over (partition by turno_id order by momento desc, id desc) as rn_desc
    from turnos_auditoria
    where origen in :origenes
),
neto as (
    select turno_id,
           min(nombre)      as nombre,
           min(fecha_turno) as fecha_turno,
           count(*)         as intervenciones,
           -- nullif descarta el autor vacío de un cambio hecho fuera de la
           -- aplicación: sin él, un turno tocado por la base y por una persona
           -- sale etiquetado como ', mpillapa'.
           string_agg(distinct nullif(usuario, ''), ', ') as usuarios,
           bool_or((accion = 'DELETE' or coalesce((despues ->> 'anulado')::boolean, false)))          as borrado,
           min(momento)                        as primer_cambio,
           max(momento)                        as ultimo_cambio,
           max(antes ->> 'area')               as area,
           max(horas_antes)   filter (where rn_asc  = 1) as h_antes,
           max(horas_despues) filter (where rn_desc = 1) as h_despues,
           max((antes   ->> 'horas_extra')::numeric) filter (where rn_asc  = 1) as e_antes,
           max((despues ->> 'horas_extra')::numeric) filter (where rn_desc = 1) as e_despues,
           max(antes   ->> 'ts_entrada')    filter (where rn_asc  = 1) as entrada_antes,
           max(despues ->> 'ts_entrada')    filter (where rn_desc = 1) as entrada_despues,
           max(antes   ->> 'ts_salida')     filter (where rn_asc  = 1) as salida_antes,
           max(despues ->> 'ts_salida')     filter (where rn_desc = 1) as salida_despues,
           max(antes   ->> 'estado')        filter (where rn_asc  = 1) as estado_antes,
           max(despues ->> 'estado')        filter (where rn_desc = 1) as estado_despues,
           max(despues ->> 'observaciones') filter (where rn_desc = 1) as obs_despues,
           -- 'archivado' del estado previo: true significa que el mes ya estaba
           -- cerrado cuando se corrigió el turno.
           bool_or((antes ->> 'archivado')::boolean) as mes_ya_cerrado
    from marcado
    group by turno_id
)
select n.*,
       -- Lo que hoy existe para esa persona y esa fecha. Un turno borrado y
       -- rehecho tiene id nuevo, de modo que la comparación va por nombre y
       -- fecha y no por turno_id.
       (select count(*) from turnos t
         where not coalesce((to_jsonb(t) ->> 'anulado')::boolean, false) and lower(t.nombre) = lower(n.nombre) and t.fecha_turno = n.fecha_turno) as turnos_hoy,
       (select coalesce(sum(t.horas_trabajadas), 0) from turnos t
         where not coalesce((to_jsonb(t) ->> 'anulado')::boolean, false) and lower(t.nombre) = lower(n.nombre) and t.fecha_turno = n.fecha_turno) as horas_hoy,
       (select coalesce(sum(t.horas_extra), 0) from turnos t
         where not coalesce((to_jsonb(t) ->> 'anulado')::boolean, false) and lower(t.nombre) = lower(n.nombre) and t.fecha_turno = n.fecha_turno) as extra_hoy
from neto n
order by n.fecha_turno, n.nombre
"""

# Secuencia completa de un turno, para poder abrir un caso y ver exactamente qué
# escribió cada quién y en qué orden. Incluye la marcación del colaborador,
# porque es el valor original contra el que se compara.
SQL_SECUENCIA = """
select turno_id, momento, usuario, origen, accion,
       horas_antes, horas_despues,
       antes   ->> 'ts_entrada' as entrada_antes,
       despues ->> 'ts_entrada' as entrada_despues,
       antes   ->> 'ts_salida'  as salida_antes,
       despues ->> 'ts_salida'  as salida_despues,
       antes   ->> 'estado'     as estado_antes,
       despues ->> 'estado'     as estado_despues
from turnos_auditoria
where turno_id in (
    select distinct turno_id from turnos_auditoria where origen in :origenes
)
order by turno_id, momento
"""

# Denominador: lo que cada persona tiene registrado hoy. Sin esto no se puede
# distinguir a quien trabajó muchos turnos de quien tuvo muchos turnos tocados.
SQL_CONTEXTO_PERSONA = """
select nombre,
       max(area)                               as area,
       to_char(fecha_turno, 'YYYY-MM')         as mes,
       count(*)                                as turnos,
       round(sum(horas_trabajadas), 2)         as horas,
       round(sum(horas_extra), 2)              as extra,
       count(*) filter (where horas_extra > 0) as turnos_con_extra
from turnos
where not coalesce((to_jsonb(turnos) ->> 'anulado')::boolean, false)
  and fecha_turno >= :desde
group by 1, 3
"""

SQL_COBERTURA = """
select min(momento)::date as primer_registro,
       max(momento)::date as ultimo_registro,
       count(*)           as filas
from turnos_auditoria
"""


def clasificar(fila: dict) -> dict:
    """Decide qué tipo de intervención fue y cuánta hora extra costó.

    La hora extra es lo único que se deja de pagar: la jornada base se paga
    igual, de modo que un turno que baja de 12.23 h a 9.23 h no cuesta 3 h sino
    las 3 h extra que tenía.

    Los casos, y por qué cada uno cuesta lo que cuesta:

    borrado_sin_turno   el turno se borró y esa fecha quedó sin ningún registro.
                        Se pierde toda la hora extra que tenía.
    borrado_con_menos   se borró y se rehízo con menos horas. Cuesta la
                        diferencia contra lo que hoy existe.
    borrado_rehecho     se borró y se rehízo sin perder horas. No cuesta.
    borrado_sin_horas   se borró un turno abierto o en revisión, sin horas
                        calculadas todavía. No cuesta.
    recorte             se corrigió el turno a la baja. Cuesta la hora extra
                        que desapareció.
    cierre_de_turno     el turno no tenía horas y el administrador le puso una
                        salida. Es completar una marcación, no quitar horas.
    alza                se corrigió al alza un turno que ya tenía horas.
    sin_cambio_horas    se tocó el turno sin mover las horas, por ejemplo solo
                        la observación.
    """
    h_antes = _num(fila["h_antes"])
    h_despues = _num(fila["h_despues"])
    e_antes = _num(fila["e_antes"])
    e_despues = _num(fila["e_despues"])
    horas_hoy = _num(fila["horas_hoy"])
    extra_hoy = _num(fila["extra_hoy"])

    if fila["borrado"]:
        if fila["h_antes"] is None:
            return _resultado("borrado_sin_horas", 0.0, 0.0)
        if fila["turnos_hoy"] == 0:
            return _resultado("borrado_sin_turno", -h_antes, -e_antes)
        if horas_hoy < h_antes:
            return _resultado("borrado_con_menos", horas_hoy - h_antes, extra_hoy - e_antes)
        return _resultado("borrado_rehecho", horas_hoy - h_antes, extra_hoy - e_antes)

    delta_h = h_despues - h_antes
    delta_e = e_despues - e_antes
    if delta_h <= -UMBRAL_RUIDO_H:
        return _resultado("recorte", delta_h, delta_e)
    if delta_h >= UMBRAL_RUIDO_H:
        tipo = "cierre_de_turno" if fila["h_antes"] is None else "alza"
        return _resultado(tipo, delta_h, delta_e)
    return _resultado("sin_cambio_horas", 0.0, 0.0)


def _resultado(tipo: str, delta_h: float, delta_e: float) -> dict:
    """Empaqueta la clasificación. extra_perdida es positiva cuando se perdió."""
    return {
        "tipo": tipo,
        "delta_horas": round(delta_h, 2),
        "delta_extra": round(delta_e, 2),
        "extra_perdida": round(-delta_e, 2) if delta_e < 0 else 0.0,
    }


def _num(valor) -> float:
    return 0.0 if valor is None else float(valor)


# El panel guarda los timestamps corregidos sin segundos, de modo que una
# corrección que no tocó la hora deja igualmente `09:13:29` -> `09:13:00`. Por
# debajo de este umbral el movimiento es ese truncamiento y no una decisión.
UMBRAL_MARCA_MOVIDA_MIN = 2.0


ZONA_ECUADOR = ZoneInfo("America/Guayaquil")


def a_hora_local(valor):
    """Un instante como datetime sin zona en hora de Ecuador, o None.

    La bitácora guarda los horarios dentro de un jsonb. Hasta V102 son texto
    sin zona ('2026-09-21T07:56:14'), que ya es hora de Ecuador; desde V102
    llevan desplazamiento ('2026-10-20T08:00:00-05:00', o '+00:00' si se
    escribió desde una sesión en UTC). Restar uno de cada tipo falla, y leer el
    segundo sin convertirlo daría la hora corrida. Esto los iguala."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        d = valor
    else:
        try:
            d = datetime.fromisoformat(str(valor))
        except ValueError:
            return None
    if d.tzinfo is not None:
        d = d.astimezone(ZONA_ECUADOR).replace(tzinfo=None)
    return d


def texto_hora_local(valor):
    """Igual que a_hora_local, como texto ISO para el tablero."""
    d = a_hora_local(valor)
    return d.isoformat(timespec="seconds") if d else None


def minutos_movidos(antes, despues):
    """Cuántos minutos se movió una marca de entrada o de salida.

    Devuelve None cuando falta alguno de los dos valores, que es el caso de un
    turno que se cerró o se borró y no tiene con qué comparar.
    """
    a, d = a_hora_local(antes), a_hora_local(despues)
    if a is None or d is None:
        return None
    return round(abs((d - a).total_seconds()) / 60, 1)


def tiene_motivo(obs_despues) -> bool:
    """Si el administrador escribió un motivo al corregir.

    El panel escribe el tag como `[Corrección <fecha> por <usuario>]: <motivo>`.
    Cuando no se escribe motivo, el tag queda cerrado directamente por la barra
    que separa la observación previa: `[Corrección ...] | [Orig]: ...`. La
    diferencia es el `:` justo después del corchete. Ver views/super_admin.py.
    """
    if not obs_despues or "[Corrección" not in obs_despues:
        return False
    if "]" not in obs_despues:
        return False
    tras_tag = obs_despues.split("]", 1)[1].lstrip()
    return tras_tag.startswith(":") and bool(tras_tag.lstrip(": ").strip())


def leer_todo(conn, desde: str) -> dict:
    """Trae los cuatro conjuntos que necesita el tablero."""
    origenes = {"origenes": list(ORIGENES_SUPERVISION)}
    expandible = sa.bindparam("origenes", expanding=True)

    turnos = [dict(r) for r in conn.execute(
        sa.text(SQL_TURNOS_INTERVENIDOS).bindparams(expandible), origenes).mappings()]

    secuencia = [dict(r) for r in conn.execute(
        sa.text(SQL_SECUENCIA).bindparams(expandible), origenes).mappings()]

    contexto = [dict(r) for r in conn.execute(
        sa.text(SQL_CONTEXTO_PERSONA), {"desde": desde}).mappings()]

    cobertura = dict(conn.execute(sa.text(SQL_COBERTURA)).mappings().one())

    return {"turnos": turnos, "secuencia": secuencia,
            "contexto": contexto, "cobertura": cobertura}
