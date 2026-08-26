"""Conexión a PostgreSQL (Supabase).

La URL de conexión vive en secrets bajo [connections.supabase]:

    [connections.supabase]
    url = "postgresql://postgres.xxxx:CLAVE@aws-0-us-east-1.pooler.supabase.com:5432/postgres"

Usa el "Session pooler" de Supabase, puerto 5432 (compatible con IPv4,
necesario para Streamlit Cloud). El engine se crea una sola vez por proceso.

No cambiar al pooler de transacción (puerto 6543): desde la red de la empresa
ese puerto no responde, ni siquiera lo bastante para dar un error. Comprobado
el 2026-08-26 con diagnostico/probar_conexion.py, que prueba ambos.

Los tiempos límite, los keepalives y el reintento de lecturas existen por los
cuelgues indefinidos del 2026-08-25. La causa y el arreglo están en
diagnostico/README.md; leerlo antes de tocar estos valores.
"""

import functools
import time
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.exc import DBAPIError

_engine = None

# Ninguna espera de red puede ser infinita: sin límite no se ve un error, se ve
# una aplicación colgada, que es mucho más difícil de diagnosticar.
ESPERA_CONEXION_SEG = 10      # establecer la conexión
ESPERA_CONSULTA_MS = 30_000   # ejecutar una consulta (statement_timeout)
ESPERA_POOL_SEG = 15          # obtener una conexión libre del pool

# Tiene que ser menor que lo que tarda el pooler (y cualquier NAT intermedio) en
# descartar una conexión inactiva. Estaba en 1800 s, muy por encima del umbral.
RECICLAR_CONEXION_SEG = 240

# Keepalives de TCP: si el pooler descarta una conexión sin cerrarla limpiamente,
# el socket queda medio abierto y el sistema operativo lo cree vivo. Con esto lo
# da por muerto en ~60 s y lanza el error que pool_pre_ping necesita.
_KEEPALIVES = {
    "keepalives": 1,
    "keepalives_idle": 30,     # empezar a sondear tras 30 s de inactividad
    "keepalives_interval": 10, # reintentar cada 10 s
    "keepalives_count": 3,     # darla por muerta tras 3 fallos
    # Cubre que la conexión muera a mitad de una consulta, caso que los
    # keepalives por sí solos no atrapan. libpq lo ignora donde no se soporta.
    "tcp_user_timeout": 15_000,
}

# Reintentos de lectura. Solo lecturas: repetir una escritura podría duplicar
# un turno, y eso es peor que mostrar un error.
INTENTOS_LECTURA = 3
PAUSA_ENTRE_INTENTOS_SEG = 1.0


def reintento_de_lectura(func):
    """Reconecta y repite la lectura, en vez de mostrarle un error al usuario por
    algo que se resuelve solo volviendo a conectar.

    `dispose()` descarta todo el pool, no solo la conexión que falló: si una
    murió porque el pooler cortó por inactividad, es probable que sus compañeras
    estén igual y reintentar con otra muerta solo gasta un intento.
    """
    @functools.wraps(func)
    def envoltura(*args, **kwargs):
        for intento in range(1, INTENTOS_LECTURA + 1):
            try:
                return func(*args, **kwargs)
            except DBAPIError as e:
                agotado = intento == INTENTOS_LECTURA
                # Distingue "se cayó la conexión" de "la consulta está mal
                # escrita": reintentar lo segundo solo retrasa el error.
                if agotado or not e.connection_invalidated:
                    raise
                get_engine().dispose()
                time.sleep(PAUSA_ENTRE_INTENTOS_SEG)
    return envoltura


def _leer_url_db() -> str:
    # 1) st.secrets: funciona en Streamlit Cloud y corriendo local con streamlit.
    try:
        import streamlit as st
        return str(st.secrets["connections"]["supabase"]["url"]).strip()
    except Exception:
        pass

    # 2) Fallback para scripts sueltos (ej. migración) ejecutados desde otra
    #    carpeta: leer el secrets.toml del proyecto directamente.
    import tomllib
    ruta = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"
    with open(ruta, "rb") as f:
        cfg = tomllib.load(f)
    return str(cfg["connections"]["supabase"]["url"]).strip()


def get_engine():
    """Engine de SQLAlchemy, cacheado a nivel de proceso.

    pool_pre_ping por sí solo no basta: sobre una conexión medio abierta el
    propio ping se queda esperando sin límite. De ahí los keepalives."""
    global _engine
    if _engine is None:
        url = _leer_url_db()
        if not url:
            raise RuntimeError(
                "Falta la URL de la base de datos en secrets "
                "([connections.supabase] url = ...)."
            )
        connect_args = dict(_KEEPALIVES)
        connect_args["connect_timeout"] = ESPERA_CONEXION_SEG
        if "sslmode" not in url:
            connect_args["sslmode"] = "require"
        _engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            pool_timeout=ESPERA_POOL_SEG,
            pool_recycle=RECICLAR_CONEXION_SEG,
            connect_args=connect_args,
        )

        # Con un SET sobre la sesión abierta y no con el parámetro `options` de
        # la cadena de conexión, que no todas las versiones de PgBouncer admiten.
        #
        # Va en "checkout" (cada préstamo del pool) y no en "connect" (solo al
        # abrir el socket): con "connect" la primera consulta salía con 30 s y la
        # siguiente volvía a 2 min, porque el pooler descarta el estado de sesión
        # al reciclar la conexión. Cuesta un viaje extra por préstamo.
        @event.listens_for(_engine, "checkout")
        def _limitar_duracion_consultas(dbapi_conn, _record, _proxy):
            with dbapi_conn.cursor() as cur:
                cur.execute(f"SET statement_timeout = {ESPERA_CONSULTA_MS}")

    return _engine
