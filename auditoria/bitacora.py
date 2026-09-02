"""Lectura de la bitácora de cambios de turnos (tabla `turnos_auditoria`).

Es la vista de análisis de lo que el trigger va registrando: quién cambió qué,
cuándo, y de qué valor a qué valor. A diferencia de comparar_con_sheets.py, que
reconstruía el pasado desde el historial de Drive, esto lee una fuente propia y
completa, y no depende de Google.

El esquema y el porqué de cada campo están en migracion/auditoria_schema.sql.

Uso
---
    .venv\\Scripts\\python.exe auditoria\\bitacora.py
    .venv\\Scripts\\python.exe auditoria\\bitacora.py --desde 2026-09-01
    .venv\\Scripts\\python.exe auditoria\\bitacora.py --usuario gproanio
    .venv\\Scripts\\python.exe auditoria\\bitacora.py --usuario gproanio --csv

Sin argumentos resume todo lo registrado. Con --csv deja el detalle en
auditoria/salida/bitacora<sufijo>.csv, carpeta ignorada por git porque lleva
datos de personas. No imprime la cadena de conexión ni la contraseña.

Si la tabla todavía no existe, lo dice y sale: hay que ejecutar
migracion/auditoria_schema.sql en el SQL Editor de Supabase.
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


# --- Acceso a datos ---------------------------------------------------------

def motor():
    cfg = tomllib.load(open(RUTA_SECRETS, "rb"))
    return sa.create_engine(
        str(cfg["connections"]["supabase"]["url"]).strip(),
        connect_args={"sslmode": "require", "connect_timeout": ESPERA_CONEXION_SEG},
    )


def tabla_existe(conn) -> bool:
    return bool(conn.execute(sa.text(
        "select to_regclass('public.turnos_auditoria') is not null"
    )).scalar())


# --- Consultas --------------------------------------------------------------
# {FILTRO} se sustituye en tiempo de ejecución (ver main). No se usa format
# porque algunas consultas llevan llaves propias.

# En un DELETE el delta es la pérdida completa del turno, no un recorte
# comparable con el de un UPDATE, así que las dos cosas se cuentan aparte. El
# detalle está en migracion/auditoria_schema.sql.
_RECORTE = "delta_horas < 0 and accion = 'UPDATE'"
_ALZA = "delta_horas > 0 and accion = 'UPDATE'"

SQL_RESUMEN = f"""
select origen,
       count(*)                                  as cambios,
       count(distinct turno_id)                  as turnos,
       count(*) filter (where accion = 'DELETE')  as borrados,
       count(*) filter (where {_RECORTE})         as recortes,
       round(sum(delta_horas) filter (where {_RECORTE})::numeric, 2) as horas_recortadas,
       round(sum(delta_horas) filter (where {_ALZA})::numeric, 2)    as horas_sumadas,
       round(sum(horas_antes) filter (where accion = 'DELETE')::numeric, 2) as horas_borradas,
       min(momento) as primero,
       max(momento) as ultimo
from turnos_auditoria
where true {{FILTRO}}
group by 1
order by cambios desc
"""

SQL_POR_USUARIO = f"""
select usuario, origen,
       count(*) as cambios,
       count(distinct turno_id) as turnos,
       count(distinct nombre) as personas,
       count(*) filter (where accion = 'DELETE') as borrados,
       count(*) filter (where {_RECORTE}) as recortes,
       round(sum(delta_horas) filter (where {_RECORTE})::numeric, 2) as horas_recortadas,
       round(sum(delta_horas) filter (where {_ALZA})::numeric, 2)    as horas_sumadas,
       round(sum(horas_antes) filter (where accion = 'DELETE')::numeric, 2) as horas_borradas
from turnos_auditoria
where true {{FILTRO}}
group by 1, 2
order by coalesce(sum(delta_horas) filter (where {_RECORTE}), 0)
"""

# Solo lo que de verdad movió horas. Es la pregunta que originó la bitácora.
SQL_CAMBIOS_DE_HORAS = """
select momento::date as fecha,
       usuario, origen, accion,
       nombre, fecha_turno,
       horas_antes, horas_despues, delta_horas,
       despues ->> 'observaciones' as observacion_resultante
from turnos_auditoria
where delta_horas <> 0 {FILTRO}
order by delta_horas
limit 200
"""

SQL_BORRADOS = """
select momento, usuario, origen, nombre, fecha_turno,
       horas_antes,
       antes ->> 'ts_entrada'    as entrada,
       antes ->> 'ts_salida'     as salida,
       antes ->> 'observaciones' as observaciones
from turnos_auditoria
where accion = 'DELETE' {FILTRO}
order by momento desc
"""

# Cambios sin autor conocido: alguien editó la base por fuera de la app.
SQL_FUERA_DE_LA_APP = """
select momento, accion, nombre, fecha_turno, horas_antes, horas_despues, delta_horas
from turnos_auditoria
where origen = 'fuera_de_la_app' {FILTRO}
order by momento desc
limit 50
"""

SQL_DETALLE = """
select id, momento, usuario, origen, accion, turno_id, nombre, fecha_turno,
       horas_antes, horas_despues, delta_horas,
       antes  ->> 'ts_entrada'    as entrada_antes,
       despues->> 'ts_entrada'    as entrada_despues,
       antes  ->> 'ts_salida'     as salida_antes,
       despues->> 'ts_salida'     as salida_despues,
       antes  ->> 'estado'        as estado_antes,
       despues->> 'estado'        as estado_despues,
       antes  ->> 'observaciones' as observaciones_antes,
       despues->> 'observaciones' as observaciones_despues
from turnos_auditoria
where true {FILTRO}
order by momento
"""


# --- Presentación -----------------------------------------------------------

def tabla(conn, titulo: str, sql: str, params: dict) -> None:
    print(f"\n== {titulo} ==")
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


def volcar_csv(conn, sql: str, params: dict, sufijo: str) -> Path:
    DIR_SALIDA.mkdir(exist_ok=True)
    destino = DIR_SALIDA / f"bitacora{sufijo}.csv"
    res = conn.execute(sa.text(sql), params)
    with open(destino, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(res.keys())
        w.writerows(res)
    return destino


def main() -> None:
    args = sys.argv[1:]

    def opcion(nombre):
        return args[args.index(nombre) + 1] if nombre in args else None

    usuario = opcion("--usuario")
    desde = opcion("--desde")
    quiere_csv = "--csv" in args

    condiciones, params, sufijo = [], {}, ""
    if usuario:
        condiciones.append("and usuario = :usuario")
        params["usuario"] = usuario
        sufijo += f"_{usuario}"
    if desde:
        condiciones.append("and momento >= :desde")
        params["desde"] = desde
        sufijo += f"_desde_{desde}"
    filtro = " ".join(condiciones)

    def q(sql: str) -> str:
        return sql.replace("{FILTRO}", filtro)

    eng = motor()
    try:
        with eng.connect() as conn:
            if not tabla_existe(conn):
                print("La tabla turnos_auditoria no existe todavía.\n"
                      "Ejecuta migracion/auditoria_schema.sql en el SQL Editor de Supabase.")
                return
            if usuario or desde:
                print(f"Filtro: usuario={usuario or 'todos'}  desde={desde or 'el inicio'}")
            tabla(conn, "Resumen por origen del cambio", q(SQL_RESUMEN), params)
            tabla(conn, "Por usuario", q(SQL_POR_USUARIO), params)
            tabla(conn, "Cambios que movieron horas", q(SQL_CAMBIOS_DE_HORAS), params)
            tabla(conn, "Turnos borrados", q(SQL_BORRADOS), params)
            tabla(conn, "Cambios hechos por fuera de la aplicación",
                  q(SQL_FUERA_DE_LA_APP), params)
            if quiere_csv:
                destino = volcar_csv(conn, q(SQL_DETALLE), params, sufijo)
                print(f"\nDetalle completo en: {destino}")
    finally:
        eng.dispose()


if __name__ == "__main__":
    main()
