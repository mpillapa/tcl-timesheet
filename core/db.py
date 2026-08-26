"""Conexión a PostgreSQL (Supabase).

La URL de conexión vive en secrets bajo [connections.supabase]:

    [connections.supabase]
    url = "postgresql://postgres.xxxx:CLAVE@aws-0-us-east-1.pooler.supabase.com:5432/postgres"

Usa el "Session pooler" de Supabase, puerto 5432 (compatible con IPv4,
necesario para Streamlit Cloud). El engine se crea una sola vez por proceso y
se reutiliza.

No cambiar al pooler de transacción (puerto 6543): el 2026-08-26 se comprobó
que desde la red de la empresa ese puerto no responde, ni siquiera lo bastante
para dar un error. El 5432 conecta en menos de dos segundos. Ver
diagnostico/probar_conexion.py, que prueba ambos.

Sobre los cuelgues de "Running leer_registros()" del 2026-08-25:
el engine no tenía ningún tiempo límite, ni de conexión, ni de consulta, ni
keepalives de TCP. Cuando el pooler descartaba una conexión inactiva sin
cerrarla limpiamente, el socket quedaba medio abierto y la siguiente lectura
esperaba indefinidamente. Como `leer_registros` está bajo `st.cache_data`, que
serializa las llamadas con un lock por función, el hilo colgado retenía ese
lock y todas las demás sesiones quedaban esperando en el mismo punto. De ahí
que se arreglara reiniciando (se tira el pool entero) y que volviera a pasar al
rato. Los límites definidos abajo cierran ese agujero.
"""

import functools
import time
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.exc import DBAPIError

_engine = None

# Tiempos límite. Ninguna espera de red puede ser infinita: una espera sin
# límite no se ve como un error, se ve como una aplicación colgada, y eso es
# mucho más difícil de diagnosticar que un mensaje de fallo.
ESPERA_CONEXION_SEG = 10      # establecer la conexión
ESPERA_CONSULTA_MS = 30_000   # ejecutar una consulta (statement_timeout)
ESPERA_POOL_SEG = 15          # obtener una conexión libre del pool

# Cada cuánto se recicla una conexión del pool. Debe ser MENOR que el tiempo
# que el pooler de Supabase (y cualquier NAT intermedio) tarda en descartar una
# conexión inactiva. Estaba en 1800 s (30 min), muy por encima de ese umbral:
# las conexiones llevaban rato muertas antes de que el pool las renovara.
RECICLAR_CONEXION_SEG = 240

# Keepalives de TCP. Son la pieza que faltaba. Cuando el pooler descarta una
# conexión inactiva sin cerrarla limpiamente, el socket del cliente queda
# "medio abierto": el sistema operativo cree que sigue viva y cualquier lectura
# sobre ella espera para siempre. Con keepalives el sistema sondea la conexión
# y, si no hay respuesta, la da por muerta en ~60 s y lanza un error, que es
# justo lo que pool_pre_ping necesita para descartarla y abrir otra.
_KEEPALIVES = {
    "keepalives": 1,
    "keepalives_idle": 30,     # empezar a sondear tras 30 s de inactividad
    "keepalives_interval": 10, # reintentar cada 10 s
    "keepalives_count": 3,     # darla por muerta tras 3 fallos
    # Corta cualquier envío que lleve 15 s sin confirmarse, aunque la conexión
    # pareciera viva al empezar. Cubre el caso de que la conexión muera en
    # mitad de una consulta, que los keepalives por si solos no atrapan.
    # libpq lo ignora en las plataformas que no lo soportan.
    "tcp_user_timeout": 15_000,
}

# Reintentos de LECTURA. Solo lecturas: repetir una escritura podria duplicar un
# turno, y eso es peor que mostrar un error.
INTENTOS_LECTURA = 3
PAUSA_ENTRE_INTENTOS_SEG = 1.0


def reintento_de_lectura(func):
    """Repite una lectura cuando la conexión se cayó, en vez de fallar.

    Por qué existe: hasta el 2026-08-25, cuando una conexión del pool quedaba
    muerta la aplicación se colgaba y había que reiniciarla a mano. Los tiempos
    límite definidos arriba convierten ese cuelgue en un error, que ya es mejor,
    pero el usuario seguiría viendo un error por algo que se resuelve solo
    volviendo a conectar. Esto hace justo eso, y automáticamente.

    `dispose()` descarta TODO el pool, no solo la conexión que falló. Es lo
    mismo que conseguía reiniciar la aplicación: si una conexión murió porque el
    pooler cortó por inactividad, es probable que sus compañeras estén igual, y
    reintentar con otra conexión muerta solo gasta un intento.

    Se aplica únicamente a lecturas. Reintentar una escritura podría insertar el
    mismo turno dos veces.
    """
    @functools.wraps(func)
    def envoltura(*args, **kwargs):
        for intento in range(1, INTENTOS_LECTURA + 1):
            try:
                return func(*args, **kwargs)
            except DBAPIError as e:
                agotado = intento == INTENTOS_LECTURA
                # connection_invalidated distingue "se cayó la conexión" de
                # "la consulta está mal escrita". Reintentar lo segundo no
                # arregla nada y solo retrasa el error.
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
    """Devuelve (y cachea a nivel de proceso) el engine de SQLAlchemy.

    pool_pre_ping revalida cada conexión antes de entregarla. Por sí solo no
    basta: si la conexión está medio abierta, el propio ping se queda esperando
    sin límite. Por eso hace falta además ESPERA_CONEXION_SEG y los keepalives
    definidos arriba, que convierten una conexión muerta en un error."""
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

        # statement_timeout se aplica con un SET sobre la sesión ya abierta, no
        # con el parámetro `options` de la cadena de conexión: ese parámetro no
        # lo admiten todas las versiones de PgBouncer y, cuando no lo admite, la
        # conexión falla entera.
        #
        # El SET va en "checkout" (cada vez que el pool presta la conexión) y no
        # en "connect" (solo al abrir el socket). Comprobado el 2026-08-26: con
        # "connect", la primera consulta salía con 30 s pero la siguiente volvía
        # a 2 min, porque el pooler descarta el estado de sesión al reciclar la
        # conexión del lado del servidor. Cuesta un viaje extra a la base por
        # préstamo, que a este volumen es irrelevante.
        @event.listens_for(_engine, "checkout")
        def _limitar_duracion_consultas(dbapi_conn, _record, _proxy):
            with dbapi_conn.cursor() as cur:
                cur.execute(f"SET statement_timeout = {ESPERA_CONSULTA_MS}")

    return _engine
