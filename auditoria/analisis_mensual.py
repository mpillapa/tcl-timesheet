"""Cuánto recortó supervisión sobre los turnos de un mes, medido en la bitácora.

Responde a la pregunta de nómina: de las horas que los colaboradores marcaron en
un mes, cuántas se quitaron después y cuántas de esas eran hora extra, que es lo
único que se deja de pagar. La jornada base de 9 h se paga igual, de modo que un
recorte de 12.23 h a 9.23 h no cuesta 3 h sino las 3 h extra que tenía el turno.
Ver core/config.py:HORAS_BASE_TURNO.

Diferencias con bitacora.py, que es la lectura general de `turnos_auditoria`:

- Acota a un mes por `fecha_turno`, no por la fecha del cambio. Lo que importa
  para la nómina es a qué periodo pertenece el turno, no cuándo se tocó. El
  corte por fecha del cambio se muestra aparte para que se vea la diferencia.
- Mide el neto por turno, no la suma de filas. Un turno corregido dos veces deja
  dos filas en la bitácora; sumarlas daría el mismo resultado por ser deltas
  encadenados, pero no permite ver cuántos turnos distintos se tocaron ni el
  antes real contra el después real.
- Separa la intervención de supervisión de la marcación del colaborador. El
  trigger audita las dos, y la marcación de salida es un delta positivo enorme
  (de turno abierto sin horas a turno cerrado) que no es un cambio de criterio.
  Ver auditoria/README.md.
- Calcula el delta de `horas_extra` leyéndolo del jsonb, campo que bitacora.py
  no desglosa.

Qué cuenta como intervención de supervisión
-------------------------------------------
Los cambios con origen 'admin' y 'fuera_de_la_app'. Quedan fuera 'colaborador'
(marcación propia) y 'sistema' (barrido de turnos olvidados), que no son
decisiones de supervisión. Un cambio 'fuera_de_la_app' es una edición hecha
directamente en la base, sin autor; se incluye porque afecta las horas igual, y
además se lista aparte por ser la señal que más conviene revisar.

Ruido de reescritura
--------------------
Las variaciones de 0.01 h son redondeo, no decisiones. Se excluyen del total con
UMBRAL_RUIDO_H y se cuentan aparte.

Uso
---
    .venv\\Scripts\\python.exe auditoria\\analisis_mensual.py
    .venv\\Scripts\\python.exe auditoria\\analisis_mensual.py --mes 2026-09
    .venv\\Scripts\\python.exe auditoria\\analisis_mensual.py --mes 2026-09 --csv

Sin --mes toma el mes anterior al actual. Con --csv deja el detalle por turno en
auditoria/salida/, carpeta ignorada por git porque lleva datos de personas.
No imprime la cadena de conexión ni la contraseña.

Limitación que hay que tener presente al leer el resultado: la bitácora arranca
el 2026-09-02. Un turno de septiembre corregido antes de esa fecha no aparece.
Para septiembre de 2026 el hueco es de un día y afecta solo a turnos de los días
1 y 2.
"""

import csv
import sys
from datetime import date
from pathlib import Path

import sqlalchemy as sa

from bitacora import motor, tabla_existe, DIR_SALIDA

# Variaciones por debajo de esto son redondeo al reescribir la fila, no una
# decisión de supervisión. Medido en la auditoría del 2026-09-02: 41 casos de
# exactamente 0.01 h. Ver auditoria/README.md.
UMBRAL_RUIDO_H = 0.02


# --- Acotar el mes ----------------------------------------------------------

def rango_del_mes(mes: str) -> tuple[date, date, str]:
    """De '2026-09' a (2026-09-01, 2026-10-01, '2026-09').

    El límite superior es exclusivo para no depender de cuántos días tiene el
    mes ni de si la columna es date o timestamp.
    """
    anio, num = (int(p) for p in mes.split("-"))
    desde = date(anio, num, 1)
    hasta = date(anio + 1, 1, 1) if num == 12 else date(anio, num + 1, 1)
    return desde, hasta, mes


def mes_anterior(hoy: date) -> str:
    return f"{hoy.year - 1}-12" if hoy.month == 1 else f"{hoy.year}-{hoy.month - 1:02d}"


# --- Consultas --------------------------------------------------------------
# Todas filtran por fecha_turno salvo SQL_POR_FECHA_DE_CAMBIO, que es el
# contraste deliberado.

_SUPERVISION = "origen in ('admin', 'fuera_de_la_app')"

# Estado del turno antes de la primera intervención de supervisión y después de
# la última. Las dos filas se eligen con row_number y se recogen con un filter,
# que es la forma de quedarse con un valor concreto dentro de un group by.
#
# En un turno borrado, la última fila es el DELETE y `despues` es null: el turno
# deja de existir y la pérdida es el total de sus horas, no un recorte. Por eso
# las dos cosas se separan en todo lo que viene después.
CTE_NETO = f"""
with marcado as (
    select *,
           row_number() over (partition by turno_id order by momento,      id)      as rn_asc,
           row_number() over (partition by turno_id order by momento desc, id desc) as rn_desc
    from turnos_auditoria
    where fecha_turno >= :desde and fecha_turno < :hasta
      and {_SUPERVISION}
),
neto as (
    select turno_id,
           min(nombre)      as nombre,
           min(fecha_turno) as fecha_turno,
           count(*)         as intervenciones,
           string_agg(distinct usuario, ', ')      as usuarios,
           bool_or((accion = 'DELETE' or coalesce((despues ->> 'anulado')::boolean, false)))              as borrado,
           max(momento)                            as ultimo_cambio,
           max(horas_antes)   filter (where rn_asc  = 1) as h_antes,
           max(horas_despues) filter (where rn_desc = 1) as h_despues,
           max((antes   ->> 'horas_extra')::numeric) filter (where rn_asc  = 1) as e_antes,
           max((despues ->> 'horas_extra')::numeric) filter (where rn_desc = 1) as e_despues,
           max(antes   ->> 'ts_entrada')    filter (where rn_asc  = 1) as entrada_antes,
           max(despues ->> 'ts_entrada')    filter (where rn_desc = 1) as entrada_despues,
           max(antes   ->> 'ts_salida')     filter (where rn_asc  = 1) as salida_antes,
           max(despues ->> 'ts_salida')     filter (where rn_desc = 1) as salida_despues,
           max(despues ->> 'observaciones') filter (where rn_desc = 1) as observacion
    from marcado
    group by turno_id
),
medido as (
    select *,
           round(coalesce(h_despues, 0) - coalesce(h_antes, 0), 2) as delta_horas,
           round(coalesce(e_despues, 0) - coalesce(e_antes, 0), 2) as delta_extra
    from neto
)
"""

SQL_PANORAMA = """
select origen, accion,
       count(*)                 as cambios,
       count(distinct turno_id) as turnos,
       count(distinct nombre)   as personas,
       round(sum(delta_horas) filter (where accion = 'UPDATE' and delta_horas <= -:ruido), 2) as h_recortadas,
       round(sum(delta_horas) filter (where accion = 'UPDATE' and delta_horas >=  :ruido), 2) as h_sumadas,
       round(sum(horas_antes)  filter (where (accion = 'DELETE' or coalesce((despues ->> 'anulado')::boolean, false))), 2)                           as h_en_borrados
from turnos_auditoria
where fecha_turno >= :desde and fecha_turno < :hasta
group by 1, 2
order by 1, 2
"""

# El mismo mes visto por fecha del cambio en vez de por fecha del turno, para
# que se vea cuánto de lo registrado en septiembre corresponde a turnos de otro
# mes y al revés.
SQL_POR_FECHA_DE_CAMBIO = f"""
select count(*)                                               as cambios,
       count(*) filter (where fecha_turno >= :desde
                          and fecha_turno <  :hasta)          as de_turnos_del_mes,
       count(*) filter (where fecha_turno <  :desde)          as de_turnos_anteriores,
       count(*) filter (where fecha_turno >= :hasta)          as de_turnos_posteriores
from turnos_auditoria
where momento >= :desde and momento < :hasta and {_SUPERVISION}
"""

SQL_TOTALES = CTE_NETO + """
select
    count(*)                                              as turnos_intervenidos,
    count(distinct nombre)                                as personas,
    count(*) filter (where borrado)                       as turnos_borrados,
    count(*) filter (where not borrado and delta_horas <= -:ruido) as recortes,
    count(*) filter (where not borrado and delta_horas >=  :ruido) as alzas,
    count(*) filter (where not borrado and abs(delta_horas) < :ruido) as sin_mover_horas,
    round(sum(delta_horas) filter (where not borrado and delta_horas <= -:ruido), 2) as horas_recortadas,
    round(sum(delta_extra) filter (where not borrado and delta_horas <= -:ruido), 2) as extra_recortada,
    round(sum(delta_horas) filter (where not borrado and delta_horas >=  :ruido), 2) as horas_sumadas,
    round(sum(h_antes)     filter (where borrado), 2)      as horas_borradas,
    round(sum(e_antes)     filter (where borrado), 2)      as extra_borrada
from medido
"""

SQL_POR_ADMIN = CTE_NETO + """
select usuarios as usuario,
       count(*)                                           as turnos,
       count(distinct nombre)                             as personas,
       count(*) filter (where borrado)                    as borrados,
       count(*) filter (where not borrado and delta_horas <= -:ruido) as recortes,
       round(sum(delta_horas) filter (where not borrado and delta_horas <= -:ruido), 2) as horas_recortadas,
       round(sum(delta_extra) filter (where not borrado and delta_horas <= -:ruido), 2) as extra_recortada,
       round(sum(h_antes)     filter (where borrado), 2)   as horas_borradas,
       round(sum(e_antes)     filter (where borrado), 2)   as extra_borrada,
       round(sum(delta_horas) filter (where not borrado and delta_horas >= :ruido), 2)  as horas_sumadas
from medido
group by 1
order by coalesce(sum(delta_horas) filter (where not borrado and delta_horas <= -:ruido), 0)
         + coalesce(-sum(h_antes) filter (where borrado), 0)
"""

SQL_POR_COLABORADOR = CTE_NETO + """
select nombre,
       count(*) filter (where not borrado and delta_horas <= -:ruido) as recortes,
       round(sum(delta_horas) filter (where not borrado and delta_horas <= -:ruido), 2) as horas_recortadas,
       round(sum(delta_extra) filter (where not borrado and delta_horas <= -:ruido), 2) as extra_recortada,
       count(*) filter (where borrado)                    as borrados,
       round(sum(h_antes) filter (where borrado), 2)      as horas_borradas,
       round(sum(e_antes) filter (where borrado), 2)      as extra_borrada,
       string_agg(distinct usuarios, ', ')                as corregido_por
from medido
where (not borrado and delta_horas <= -:ruido) or borrado
group by 1
order by coalesce(sum(delta_horas) filter (where not borrado and delta_horas <= -:ruido), 0)
         + coalesce(-sum(h_antes) filter (where borrado), 0)
"""

SQL_RECORTES = CTE_NETO + """
select nombre, fecha_turno, usuarios as corregido_por,
       ultimo_cambio::date as corregido_el, intervenciones,
       h_antes, h_despues, delta_horas,
       e_antes, e_despues, delta_extra,
       left(coalesce(observacion, ''), 55) as observacion
from medido
where not borrado and delta_horas <= -:ruido
order by delta_horas
"""

# Un borrado no siempre es una pérdida: puede ser la corrección de un turno
# duplicado o mal marcado que después se volvió a crear. Por eso se cuenta lo
# que hoy existe en `turnos` para esa persona y esa fecha, que es lo que al
# final se paga. Se compara por nombre y fecha porque el turno nuevo tiene otro
# id; la clave natural de la app lleva el timestamp de entrada, que el rehacer
# cambia. Ver migracion/schema.sql.
SQL_BORRADOS = """
select a.momento::date            as borrado_el,
       a.usuario, a.origen, a.nombre, a.fecha_turno,
       a.horas_antes                               as horas_que_tenia,
       (a.antes ->> 'horas_extra')::numeric        as extra_que_tenia,
       a.antes ->> 'ts_entrada'                    as entrada,
       a.antes ->> 'ts_salida'                     as salida,
       a.antes ->> 'estado'                        as estado,
       (select count(*) from turnos t
         where not coalesce((to_jsonb(t) ->> 'anulado')::boolean, false) and lower(t.nombre) = lower(a.nombre)
           and t.fecha_turno = a.fecha_turno)      as turnos_hoy,
       (select round(coalesce(sum(t.horas_trabajadas), 0), 2) from turnos t
         where not coalesce((to_jsonb(t) ->> 'anulado')::boolean, false) and lower(t.nombre) = lower(a.nombre)
           and t.fecha_turno = a.fecha_turno)      as horas_hoy,
       left(coalesce(a.antes ->> 'observaciones', ''), 45) as observaciones
from turnos_auditoria a
where (a.accion = 'DELETE' or coalesce((a.despues ->> 'anulado')::boolean, false))
  and a.fecha_turno >= :desde and a.fecha_turno < :hasta
order by a.horas_antes desc nulls last
"""

# Un borrado solo cuesta horas si nadie rehizo el turno. Lo normal es lo
# contrario: supervisión borra un turno en revisión o mal marcado y lo vuelve a
# crear corregido, con otro id. Por eso se compara el estado del primer borrado
# contra lo que hoy existe en `turnos` para esa persona y esa fecha, que es lo
# que se paga.
#
# Se agrupa por persona y fecha, no por turno_id: el turno rehecho tiene id
# nuevo. Se toma el borrado más antiguo porque es el estado previo a que
# supervisión interviniera; si hubo dos borrados de la misma fecha, el segundo
# ya parte de un valor tocado.
CTE_BORRADOS = """
with primer_borrado as (
    select distinct on (lower(nombre), fecha_turno)
           nombre, fecha_turno, momento, usuario,
           horas_antes                            as horas_antes,
           (antes ->> 'horas_extra')::numeric     as extra_antes,
           antes ->> 'estado'                     as estado_al_borrar
    from turnos_auditoria
    where (accion = 'DELETE' or coalesce((despues ->> 'anulado')::boolean, false))
      and fecha_turno >= :desde and fecha_turno < :hasta
    order by lower(nombre), fecha_turno, momento
),
contrastado as (
    select b.*,
           (select count(*) from turnos t
             where not coalesce((to_jsonb(t) ->> 'anulado')::boolean, false) and lower(t.nombre) = lower(b.nombre)
               and t.fecha_turno = b.fecha_turno)                       as turnos_hoy,
           (select coalesce(sum(t.horas_trabajadas), 0) from turnos t
             where not coalesce((to_jsonb(t) ->> 'anulado')::boolean, false) and lower(t.nombre) = lower(b.nombre)
               and t.fecha_turno = b.fecha_turno)                       as horas_hoy,
           (select coalesce(sum(t.horas_extra), 0) from turnos t
             where not coalesce((to_jsonb(t) ->> 'anulado')::boolean, false) and lower(t.nombre) = lower(b.nombre)
               and t.fecha_turno = b.fecha_turno)                       as extra_hoy
    from primer_borrado b
)
"""

SQL_BALANCE_BORRADOS = CTE_BORRADOS + """
select case
         when horas_antes is null              then 'borrado sin horas calculadas'
         when turnos_hoy = 0                   then 'sin turno hoy: la fecha quedo vacia'
         when horas_hoy >= horas_antes         then 'rehecho sin perder horas'
         else                                       'rehecho con menos horas'
       end                                                        as situacion,
       count(*)                                                   as casos,
       round(sum(horas_antes), 2)                                 as horas_al_borrar,
       round(sum(horas_hoy), 2)                                   as horas_hoy,
       round(sum(coalesce(horas_hoy, 0) - coalesce(horas_antes, 0)), 2) as delta_horas,
       round(sum(coalesce(extra_hoy, 0) - coalesce(extra_antes, 0)), 2) as delta_extra
from contrastado
group by 1
order by 5
"""

SQL_BALANCE_BORRADOS_DETALLE = CTE_BORRADOS + """
select nombre, fecha_turno, usuario, momento::date as borrado_el,
       estado_al_borrar, horas_antes, horas_hoy,
       round(coalesce(horas_hoy, 0) - coalesce(horas_antes, 0), 2) as delta_horas,
       extra_antes, extra_hoy,
       round(coalesce(extra_hoy, 0) - coalesce(extra_antes, 0), 2) as delta_extra,
       turnos_hoy
from contrastado
where horas_antes is not null
  and coalesce(horas_hoy, 0) < coalesce(horas_antes, 0)
order by (coalesce(horas_hoy, 0) - coalesce(horas_antes, 0))
"""

# Cruce de control: si una misma persona y fecha aparece a la vez entre los
# recortes y entre los borrados, las dos cifras se solapan y no se pueden sumar.
SQL_SOLAPE = CTE_NETO + """
, recortados as (
    select nombre, fecha_turno from medido
    where not borrado and delta_horas <= -:ruido
),
eliminados as (
    select distinct nombre, fecha_turno from turnos_auditoria
    where (accion = 'DELETE' or coalesce((despues ->> 'anulado')::boolean, false))
      and fecha_turno >= :desde and fecha_turno < :hasta
)
select r.nombre, r.fecha_turno
from recortados r join eliminados e
  on lower(r.nombre) = lower(e.nombre) and r.fecha_turno = e.fecha_turno
"""

# Las alzas no son un regalo de horas. Casi todas son el cierre de un turno que
# el colaborador dejó abierto: el turno no tenía horas y el administrador le
# puso una salida. Se separan de las alzas sobre un turno que ya tenía horas,
# que sí son un cambio de criterio.
SQL_ALZAS = CTE_NETO + """
select case when h_antes is null
            then 'cierre de turno sin horas previas'
            else 'alza sobre un turno que ya tenia horas' end as tipo,
       count(*)                   as turnos,
       round(sum(delta_horas), 2) as horas,
       round(sum(delta_extra), 2) as extra
from medido
where not borrado and delta_horas >= :ruido
group by 1
order by 3 desc
"""

SQL_FUERA_DE_LA_APP = """
select momento, accion, nombre, fecha_turno,
       horas_antes, horas_despues, delta_horas,
       (antes   ->> 'horas_extra')::numeric as extra_antes,
       (despues ->> 'horas_extra')::numeric as extra_despues
from turnos_auditoria
where origen = 'fuera_de_la_app'
  and fecha_turno >= :desde and fecha_turno < :hasta
order by momento desc
"""

# Contexto para dimensionar el recorte: cuántas horas extra quedaron registradas
# en el mes después de todas las correcciones.
SQL_CONTEXTO = """
select count(*)                                      as turnos,
       count(distinct nombre)                        as personas,
       round(sum(horas_trabajadas), 2)               as horas_trabajadas,
       round(sum(horas_extra), 2)                    as horas_extra,
       count(*) filter (where horas_extra > 0)       as turnos_con_extra
from turnos
where not coalesce((to_jsonb(turnos) ->> 'anulado')::boolean, false)
  and fecha_turno >= :desde and fecha_turno < :hasta
"""

# Los turnos que empatan justo por encima de la jornada base son la huella del
# patrón descrito en auditoria/README.md: el recorte deja la hora extra en casi
# cero en vez de ajustarla a un valor cualquiera.
SQL_PATRON = CTE_NETO + """
select case
         when h_despues is null                      then 'turno borrado'
         when h_despues <  9.0                       then 'bajo la jornada base'
         when h_despues <= 9.5                       then 'entre 9.0 y 9.5 (sin justificacion exigible)'
         when h_despues <= 10.0                      then 'entre 9.5 y 10.0'
         else                                             'sobre 10.0'
       end                                           as horas_resultantes,
       count(*)                                      as turnos,
       round(sum(delta_horas), 2)                    as horas_recortadas,
       round(sum(delta_extra), 2)                    as extra_recortada
from medido
where borrado or delta_horas <= -:ruido
group by 1
order by 2 desc
"""


# --- Presentación -----------------------------------------------------------

def imprimir(conn, titulo: str, sql: str, params: dict, nota: str = "") -> None:
    print(f"\n== {titulo} ==")
    if nota:
        print(f"   {nota}")
    res = conn.execute(sa.text(sql), params)
    cols = list(res.keys())
    filas = [[("" if v is None else str(v)[:70]) for v in r] for r in res]
    if not filas:
        print("  (sin registros)")
        return
    ancho = [max([len(cols[i])] + [len(f[i]) for f in filas]) for i in range(len(cols))]
    print("  " + "  ".join(c.ljust(ancho[i]) for i, c in enumerate(cols)))
    print("  " + "  ".join("-" * a for a in ancho))
    for f in filas:
        print("  " + "  ".join(v.ljust(ancho[i]) for i, v in enumerate(f)))


def volcar_csv(conn, sql: str, params: dict, nombre: str) -> Path:
    DIR_SALIDA.mkdir(exist_ok=True)
    destino = DIR_SALIDA / nombre
    res = conn.execute(sa.text(sql), params)
    with open(destino, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(res.keys())
        w.writerows(res)
    return destino


def main() -> None:
    args = sys.argv[1:]
    mes = args[args.index("--mes") + 1] if "--mes" in args else mes_anterior(date.today())
    quiere_csv = "--csv" in args

    desde, hasta, etiqueta = rango_del_mes(mes)
    params = {"desde": desde, "hasta": hasta, "ruido": UMBRAL_RUIDO_H}

    eng = motor()
    try:
        with eng.connect() as conn:
            if not tabla_existe(conn):
                print("La tabla turnos_auditoria no existe todavía.\n"
                      "Aplica las migraciones con migracion/aplicar_migraciones.py.")
                return

            print(f"Analisis de cambios sobre los turnos de {etiqueta} "
                  f"({desde} a {hasta}, limite superior excluido).")
            print(f"Ruido descartado: variaciones menores a {UMBRAL_RUIDO_H} h.")

            imprimir(conn, "Contexto: lo que hoy tiene registrado el mes",
                     SQL_CONTEXTO, params,
                     "Valores ya corregidos. Es el denominador del recorte.")

            imprimir(conn, "Panorama de la bitacora, por origen y accion",
                     SQL_PANORAMA, params,
                     "Suma de filas, no neto por turno. Incluye la marcacion del colaborador.")

            imprimir(conn, "Totales de supervision, neto por turno",
                     SQL_TOTALES, params,
                     "Solo origen admin y fuera_de_la_app. extra_recortada es lo que se deja de pagar.")

            imprimir(conn, "Por administrador", SQL_POR_ADMIN, params)
            imprimir(conn, "Por colaborador afectado", SQL_POR_COLABORADOR, params)
            imprimir(conn, "Detalle de los recortes", SQL_RECORTES, params)

            imprimir(conn, "Que paso con las alzas", SQL_ALZAS, params,
                     "Un turno cerrado por el admin pasa de no tener horas a tenerlas.")

            imprimir(conn, "Balance de los borrados", SQL_BALANCE_BORRADOS, params,
                     "Contra lo que hoy existe en turnos para esa persona y esa fecha.")

            imprimir(conn, "Borrados que si costaron horas",
                     SQL_BALANCE_BORRADOS_DETALLE, params)

            imprimir(conn, "Control de solape entre recortes y borrados",
                     SQL_SOLAPE, params,
                     "Si sale vacio, las dos cifras se pueden sumar sin contar dos veces.")

            imprimir(conn, "Turnos borrados, detalle completo", SQL_BORRADOS, params,
                     "turnos_hoy y horas_hoy dicen si la persona conserva un turno esa fecha.")

            imprimir(conn, "Cambios hechos por fuera de la aplicacion",
                     SQL_FUERA_DE_LA_APP, params,
                     "Editados directo en la base, sin autor registrado.")

            imprimir(conn, "Donde quedaron las horas tras el recorte",
                     SQL_PATRON, params,
                     "La jornada base son 9 h; sobre 9.5 h la app exige justificacion.")

            if quiere_csv:
                d1 = volcar_csv(conn, SQL_RECORTES, params, f"recortes_{etiqueta}.csv")
                d2 = volcar_csv(conn, SQL_BORRADOS, params, f"borrados_{etiqueta}.csv")
                print(f"\nDetalle en: {d1}\n           {d2}")
    finally:
        eng.dispose()


if __name__ == "__main__":
    main()
