"""Auditoría de solo lectura: correcciones de turnos ya cerrados, por administrador.

Qué responde
------------
Cuántos turnos cerrados corrigió cada admin, a quién, cuándo, y cuánto se puede
afirmar sobre las horas modificadas.

De dónde sale la traza
----------------------
No hay tabla de auditoría. La única huella la escribe views/super_admin.py en la
columna `turnos.observaciones` con este formato:

    [Corrección YYYY-MM-DD por <usuario>]: <motivo> | [Orig]: <observación previa>

Limitación importante: se guarda quién, cuándo y por qué, pero NO el valor
anterior de las horas ni de los timestamps. El delta real de horas no es
recuperable desde la base de datos.

Cota inferior de horas recortadas
---------------------------------
Se puede acotar por abajo en un subconjunto de casos, usando una regla del
propio sistema (core/config.py UMBRAL_HORAS_EXTRA = 9.5 y core/marcado.py:208):
la app solo le pide justificación al colaborador cuando horas_trabajadas > 9.5.
Entonces, si la observación original preservada como `[Orig]` empieza con
"Horas extra justificadas", ese turno tenía más de 9.5 h antes de la
corrección. Si hoy tiene 9.5 h o menos, la corrección recortó horas, y el
recorte fue de al menos (9.5 - horas_actuales).

Es una cota inferior, no el delta. Para los turnos que siguen sobre 9.5 h
después de la corrección no hay forma de saber si las horas cambiaron.

Otro ojo sobre `actualizado_en`: no sirve como fecha de corrección, porque
core/data.archivar_historico también lo actualiza al cerrar el mes. La fecha
fiable es la del tag en observaciones.

Uso
---
    .venv\\Scripts\\python.exe auditoria\\correcciones_por_admin.py
    .venv\\Scripts\\python.exe auditoria\\correcciones_por_admin.py gproanio
    .venv\\Scripts\\python.exe auditoria\\correcciones_por_admin.py gproanio --csv
    .venv\\Scripts\\python.exe auditoria\\correcciones_por_admin.py gproanio --solo-marcados --csv

Con --solo-marcados quedan solo los turnos cuya entrada y salida marcó el propio
colaborador, y se excluyen los que un administrador registró o cerró a mano. El
reparto completo sale siempre en la tabla "Cómo se cerró cada turno corregido",
de modo que se ve qué queda fuera y por qué.

Con --csv deja el detalle en auditoria/salida/correcciones_<usuario>.csv, que
está ignorado por git. No imprime la cadena de conexión ni la contraseña.
"""

import csv
import sys
import tomllib
from pathlib import Path

import sqlalchemy as sa

RAIZ = Path(__file__).resolve().parent.parent
RUTA_SECRETS = RAIZ / ".streamlit" / "secrets.toml"
DIR_SALIDA = Path(__file__).resolve().parent / "salida"

ESPERA_CONEXION_SEG = 15

# Regla replicada de core/config.py. Si allá cambia, hay que cambiarla aquí.
UMBRAL_HORAS_EXTRA = 9.5


# --- Acceso a datos ---------------------------------------------------------

def leer_url() -> str:
    cfg = tomllib.load(open(RUTA_SECRETS, "rb"))
    return str(cfg["connections"]["supabase"]["url"]).strip()


def motor():
    return sa.create_engine(
        leer_url(),
        connect_args={"sslmode": "require", "connect_timeout": ESPERA_CONEXION_SEG},
    )


# --- Consultas --------------------------------------------------------------
# El patrón ilike tolera el acento de "Corrección", que viaja distinto según de
# dónde salió la fila (Sheet migrado o app).

# Se desenrollan TODOS los tags de la fila, no solo el más externo. En las
# filas históricas una segunda corrección envolvía el tag de la primera, de
# modo que `substring` atribuía al segundo admin una corrección que también
# hizo el primero. Hay 13 filas así. El formato está descrito en core/traza.py.
SQL_POR_ADMIN = r"""
select trim(t.captura[2]) as admin,
       count(*)                as correcciones,
       count(distinct u.id)    as turnos,
       count(distinct u.nombre) as personas,
       round(sum(coalesce(u.horas_extra,0))::numeric, 2) as h_extra_hoy,
       min(u.fecha_turno) as primer_turno,
       max(u.fecha_turno) as ultimo_turno
from turnos u
cross join lateral regexp_matches(
        u.observaciones,
        '\[Correcci\w*n\s+(\d{4}-\d{2}-\d{2})\s+por\s+([^\]]+)\]',
        'gi') as t(captura)
group by 1
order by correcciones desc
"""

# Las filas con más de un tag, que son las que hay que leer a mano porque la
# atribución simple falla en ellas.
SQL_ANIDADAS = r"""
select id, nombre, fecha_turno, horas_trabajadas,
       (select count(*) from regexp_matches(
            observaciones,
            '\[Correcci\w*n\s+\d{4}-\d{2}-\d{2}\s+por\s+[^\]]+\]', 'gi')) as correcciones,
       observaciones
from turnos
where (select count(*) from regexp_matches(
           observaciones,
           '\[Correcci\w*n\s+\d{4}-\d{2}-\d{2}\s+por\s+[^\]]+\]', 'gi')) > 1
order by fecha_turno
"""

TAG = "observaciones ilike '%[Correcci%por ' || :usuario || ']%'"
ORIG_JUST = "observaciones ilike '%[Orig]: Horas extra justificadas%'"

# Cómo se cerró el turno, leído del segmento [Orig] que la corrección preserva.
# La app escribe "Horas extra justificadas: ..." cuando el propio colaborador
# marca una salida que pasa del umbral, y "Registro manual: ..." cuando un
# administrador registra la entrada o cierra el turno. Una observación previa
# vacía también es marcación del colaborador, por debajo del umbral, porque un
# cierre manual siempre deja su prefijo.
CERRADO_POR_COLABORADOR = (
    "ts_salida is not null and ("
    "  observaciones ilike '%[Orig]: Horas extra justificadas%'"
    "  or observaciones not ilike '%[Orig]:%')"
)

SQL_CLASIFICACION = f"""
select case
         when not ({ORIG_JUST})                       then 'sin justificacion original (sin cota)'
         when horas_trabajadas > {UMBRAL_HORAS_EXTRA} then 'original >9.5 y hoy >9.5 (delta desconocido)'
         else 'original >9.5 y hoy <=9.5 (recorte demostrado)'
       end as caso,
       count(*) as turnos,
       round(sum(coalesce(horas_trabajadas,0))::numeric, 2) as h_hoy
from turnos
where {TAG} {{FILTRO}}
group by 1
order by turnos desc
"""

SQL_COTA = f"""
select count(*)                                                        as turnos_recortados,
       count(distinct nombre)                                          as personas,
       round(sum({UMBRAL_HORAS_EXTRA} - horas_trabajadas)::numeric, 2) as h_recortadas_minimo,
       round(max({UMBRAL_HORAS_EXTRA} - horas_trabajadas)::numeric, 2) as recorte_mayor
from turnos
where {TAG} {{FILTRO}} and {ORIG_JUST} and horas_trabajadas <= {UMBRAL_HORAS_EXTRA}
"""

SQL_POR_FUNCIONARIO = f"""
select nombre, area,
       count(*) as turnos_corregidos,
       round(sum(coalesce(horas_trabajadas,0))::numeric, 2) as h_trabajadas_hoy,
       round(sum(coalesce(horas_extra,0))::numeric, 2)      as h_extra_hoy,
       count(*) filter (where {ORIG_JUST}
                          and horas_trabajadas <= {UMBRAL_HORAS_EXTRA}) as turnos_recortados,
       round(coalesce(sum({UMBRAL_HORAS_EXTRA} - horas_trabajadas)
                      filter (where {ORIG_JUST}
                                and horas_trabajadas <= {UMBRAL_HORAS_EXTRA}), 0)::numeric, 2)
                                                            as h_recortadas_minimo
from turnos
where {TAG} {{FILTRO}}
group by 1, 2
order by turnos_corregidos desc
"""

SQL_POR_MES = f"""
select substring(observaciones from '\\[Correcci[^0-9]*(\\d{{4}}-\\d{{2}})') as mes_correccion,
       count(*) as turnos,
       round(sum(coalesce(horas_trabajadas,0))::numeric, 2) as h_trabajadas_hoy
from turnos
where {TAG} {{FILTRO}}
group by 1
order by 1
"""

SQL_MOTIVOS = f"""
select coalesce(nullif(trim(substring(observaciones from '\\]: ([^|]{{0,90}})')), ''),
                '(sin motivo escrito)') as motivo,
       count(*) as turnos
from turnos
where {TAG} {{FILTRO}}
group by 1
order by turnos desc
limit 15
"""

SQL_ORIGEN = f"""
select case
         when {ORIG_JUST}                                        then 'marcación del colaborador, con justificación'
         when observaciones ilike '%[Orig]: Registro manual%'    then 'cierre o alta hecha por un administrador'
         when observaciones not ilike '%[Orig]:%'                then 'marcación del colaborador, sin justificación'
         else 'otro texto previo'
       end as origen_del_cierre,
       count(*) as turnos,
       count(*) filter (where ts_salida is null) as sin_salida
from turnos
where {TAG}
group by 1
order by turnos desc
"""

SQL_DETALLE = f"""
select id, nombre, area, fecha_turno, ts_entrada, ts_salida,
       horas_trabajadas, horas_efectivas, horas_extra, estado, evento, archivado,
       ({ORIG_JUST} and horas_trabajadas <= {UMBRAL_HORAS_EXTRA}) as recorte_demostrado,
       observaciones
from turnos
where {TAG} {{FILTRO}}
order by fecha_turno, ts_entrada
"""


# --- Presentación -----------------------------------------------------------

def tabla(conn, titulo: str, sql: str, params: dict = None) -> None:
    print(f"\n== {titulo} ==")
    res = conn.execute(sa.text(sql), params or {})
    cols = list(res.keys())
    filas = [[("" if v is None else str(v)) for v in r] for r in res]
    if not filas:
        print("  (sin resultados)")
        return
    ancho = [max([len(cols[i])] + [len(f[i]) for f in filas]) for i in range(len(cols))]
    print("  " + "  ".join(c.ljust(ancho[i]) for i, c in enumerate(cols)))
    print("  " + "  ".join("-" * a for a in ancho))
    for f in filas:
        print("  " + "  ".join(v.ljust(ancho[i]) for i, v in enumerate(f)))


def volcar_csv(conn, usuario: str, filtro: str, sufijo: str) -> Path:
    DIR_SALIDA.mkdir(exist_ok=True)
    destino = DIR_SALIDA / f"correcciones_{usuario}{sufijo}.csv"
    res = conn.execute(sa.text(SQL_DETALLE.replace("{FILTRO}", filtro)), {"usuario": usuario})
    with open(destino, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(res.keys())
        w.writerows(res)
    return destino


def main() -> None:
    args = sys.argv[1:]
    quiere_csv = "--csv" in args
    solo_marcados = "--solo-marcados" in args
    usuario = next((a for a in args if not a.startswith("--")), None)

    filtro = f"and ({CERRADO_POR_COLABORADOR})" if solo_marcados else ""
    sufijo = "_solo_marcados" if solo_marcados else ""

    def q(sql: str) -> str:
        """Inyecta el filtro opcional en la plantilla. Se usa replace y no
        format porque estas consultas llevan llaves propias en las expresiones
        regulares de Postgres."""
        return sql.replace("{FILTRO}", filtro)

    eng = motor()
    try:
        with eng.connect() as conn:
            tabla(conn, "Correcciones de turnos cerrados, por administrador", SQL_POR_ADMIN)
            tabla(conn, "Turnos con más de una corrección (atribución no fiable)",
                  SQL_ANIDADAS)
            if not usuario:
                print("\nPasa un usuario para ver su detalle. Ej: "
                      "auditoria\\correcciones_por_admin.py gproanio")
                return

            p = {"usuario": usuario}
            print(f"\n\n### Detalle de {usuario} ###")
            tabla(conn, "Cómo se cerró cada turno corregido", SQL_ORIGEN, p)
            if solo_marcados:
                print("\n  Filtro activo: solo turnos con entrada y salida marcadas por el\n"
                      "  propio colaborador. Quedan fuera los que un administrador registró\n"
                      "  o cerró a mano.")
            tabla(conn, "Qué se puede afirmar sobre las horas", q(SQL_CLASIFICACION), p)
            tabla(conn, f"Cota inferior de horas recortadas (umbral {UMBRAL_HORAS_EXTRA} h)",
                  q(SQL_COTA), p)
            tabla(conn, "Por funcionario", q(SQL_POR_FUNCIONARIO), p)
            tabla(conn, "Por mes de la corrección", q(SQL_POR_MES), p)
            tabla(conn, "Motivos más repetidos", q(SQL_MOTIVOS), p)
            if quiere_csv:
                destino = volcar_csv(conn, usuario, filtro, sufijo)
                print(f"\nDetalle turno por turno en: {destino}")
    finally:
        eng.dispose()


if __name__ == "__main__":
    main()
