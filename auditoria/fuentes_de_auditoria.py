"""Inventario de solo lectura: qué fuentes de "antes y después" existen en el
servidor de Postgres.

Existe para no volver a responder por conjetura la pregunta de si los logs de
Supabase guardan el valor previo de un turno corregido. La conclusión del corte
del 2026-09-02 está escrita en auditoria/README.md; este script la reproduce.

Lo que revisa, y por qué cada cosa importa:

    log_statement                 en 'ddl' no se registra ningún UPDATE de datos
    log_min_duration_statement    en -1 tampoco se captura por consulta lenta
    extensiones instaladas        pgaudit y supa_audit son las que auditarían DML
    triggers en turnos            un trigger propio podría estar llevando bitácora
    esquemas                      supa_audit crearía un esquema 'audit'
    pg_stat_statements            guarda la consulta normalizada, sin valores
    wal_level y archive_mode      base de PITR, que no se consulta por SQL
    tablas del esquema public     por si quedó un respaldo olvidado

Uso:
    .venv\\Scripts\\python.exe auditoria\\fuentes_de_auditoria.py

No imprime la cadena de conexión ni la contraseña.
"""

import tomllib
from pathlib import Path

import sqlalchemy as sa

RAIZ = Path(__file__).resolve().parent.parent
RUTA_SECRETS = RAIZ / ".streamlit" / "secrets.toml"
ESPERA_CONEXION_SEG = 15

CONSULTAS = [
    ("Versión del servidor",
     "select version()"),

    ("Parámetros de log",
     "select name, setting, unit from pg_settings "
     "where name in ('log_statement','log_min_duration_statement','logging_collector',"
     "'log_parameter_max_length','wal_level','archive_mode') order by name"),

    ("Extensiones instaladas",
     "select extname, extversion from pg_extension order by 1"),

    ("Extensiones de auditoría, instaladas o solo disponibles",
     "select name, default_version, installed_version from pg_available_extensions "
     "where name ilike '%audit%' or name ilike '%temporal%' or name ilike '%history%' "
     "order by 1"),

    ("Triggers propios en turnos",
     "select tgname, tgenabled from pg_trigger "
     "where tgrelid = 'turnos'::regclass and not tgisinternal"),

    ("Esquemas del proyecto",
     "select nspname from pg_namespace where nspname not like 'pg_%' order by 1"),

    ("Tablas en public",
     "select tablename from pg_tables where schemaname = 'public' order by 1"),

    ("Desde cuándo acumula pg_stat_statements",
     "select stats_reset from pg_stat_statements_info"),

    ("Formas de UPDATE sobre turnos vistas por pg_stat_statements",
     "select calls, left(query, 95) as query from pg_stat_statements "
     "where query ilike '%update turnos%' order by calls desc limit 8"),

    ("Escrituras acumuladas en turnos",
     "select n_tup_ins, n_tup_upd, n_tup_del from pg_stat_user_tables "
     "where relname = 'turnos'"),

    ("Slots de replicación (WAL retenido y consultable)",
     "select slot_name, plugin, active from pg_replication_slots"),
]


def leer_url() -> str:
    cfg = tomllib.load(open(RUTA_SECRETS, "rb"))
    return str(cfg["connections"]["supabase"]["url"]).strip()


def mostrar(conn, titulo: str, sql: str) -> None:
    print(f"\n== {titulo} ==")
    try:
        res = conn.execute(sa.text(sql))
        cols = list(res.keys())
        filas = [[("" if v is None else str(v)[:95]) for v in r] for r in res]
    except Exception as e:
        print(f"  no disponible: {type(e).__name__}: {str(e)[:140]}")
        return
    if not filas:
        print("  (ninguno)")
        return
    ancho = [max([len(cols[i])] + [len(f[i]) for f in filas]) for i in range(len(cols))]
    print("  " + "  ".join(c.ljust(ancho[i]) for i, c in enumerate(cols)))
    print("  " + "  ".join("-" * a for a in ancho))
    for f in filas:
        print("  " + "  ".join(v.ljust(ancho[i]) for i, v in enumerate(f)))


def main() -> None:
    eng = sa.create_engine(
        leer_url(),
        connect_args={"sslmode": "require", "connect_timeout": ESPERA_CONEXION_SEG},
    )
    try:
        with eng.connect() as conn:
            for titulo, sql in CONSULTAS:
                mostrar(conn, titulo, sql)
        print("\nLectura del resultado: si log_statement no es 'all' o 'mod' y no hay "
              "\npgaudit, supa_audit ni triggers sobre turnos, entonces el valor anterior "
              "\nde un turno corregido no está en el servidor y no se puede reconstruir.")
    finally:
        eng.dispose()


if __name__ == "__main__":
    main()
