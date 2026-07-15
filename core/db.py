"""Conexión a PostgreSQL (Supabase).

La URL de conexión vive en secrets bajo [connections.supabase]:

    [connections.supabase]
    url = "postgresql://postgres.xxxx:CLAVE@aws-0-us-east-1.pooler.supabase.com:5432/postgres"

Usa el "Session pooler" de Supabase (compatible con IPv4, necesario para
Streamlit Cloud). El engine se crea una sola vez por proceso y se reutiliza.
"""

from pathlib import Path

from sqlalchemy import create_engine

_engine = None


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

    pool_pre_ping revalida conexiones antes de usarlas: el pooler de Supabase
    corta conexiones inactivas y sin esto aparecerían errores intermitentes
    'server closed the connection unexpectedly' tras ratos sin actividad."""
    global _engine
    if _engine is None:
        url = _leer_url_db()
        if not url:
            raise RuntimeError(
                "Falta la URL de la base de datos en secrets "
                "([connections.supabase] url = ...)."
            )
        connect_args = {}
        if "sslmode" not in url:
            connect_args["sslmode"] = "require"
        _engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            pool_recycle=1800,
            connect_args=connect_args,
        )
    return _engine
