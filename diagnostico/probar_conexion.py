"""Prueba la conexion a Supabase por los dos puertos del pooler, con un limite
de 10 segundos, para que un fallo se convierta en un mensaje legible en vez de
una espera indefinida como la de la aplicacion.

  5432  pooler de sesion      (el que usa hoy la aplicacion)
  6543  pooler de transaccion (al que migro HERRAMIENTAS_DOCUMENTALES_WEB
                               el 2026-08-24 por un fallo del de sesion)

Uso:
    .venv/Scripts/python.exe diagnostico/probar_conexion.py

No imprime la contrasena en ningun caso.
"""

import re
import time
import tomllib
from pathlib import Path

import sqlalchemy as sa

LIMITE_SEG = 10
RUTA_SECRETS = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"


def leer_url() -> str:
    cfg = tomllib.load(open(RUTA_SECRETS, "rb"))
    return str(cfg["connections"]["supabase"]["url"]).strip()


def con_puerto(url: str, puerto: int) -> str:
    """Devuelve la misma URL apuntando a otro puerto."""
    return re.sub(r"(@[^/]+):\d+/", rf"\1:{puerto}/", url)


def ocultar(url: str) -> str:
    """URL sin la contrasena, apta para imprimir."""
    return re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", url)


def probar(url: str, etiqueta: str) -> None:
    print(f"\n--- {etiqueta} ---")
    print(f"  {ocultar(url)}")
    motor = sa.create_engine(
        url, connect_args={"sslmode": "require", "connect_timeout": LIMITE_SEG}
    )
    inicio = time.monotonic()
    try:
        with motor.connect() as conn:
            conn.execute(sa.text("select 1"))
            filas = conn.execute(
                sa.text("select count(*) from turnos where archivado = false")
            ).scalar()
        print(f"  OK en {time.monotonic() - inicio:.1f}s - turnos activos: {filas}")
    except Exception as e:
        print(f"  FALLO tras {time.monotonic() - inicio:.1f}s")
        print(f"  {type(e).__name__}: {str(e)[:400]}")
    finally:
        motor.dispose()


def main() -> None:
    url = leer_url()
    puerto_actual = re.search(r"@[^/]+:(\d+)/", url)
    print(f"Puerto configurado hoy en secrets.toml: {puerto_actual.group(1)}")
    print(f"Limite de espera por intento: {LIMITE_SEG}s")
    probar(con_puerto(url, 5432), "5432  pooler de SESION")
    probar(con_puerto(url, 6543), "6543  pooler de TRANSACCION")
    print(
        "\nLectura del resultado:\n"
        "  6543 OK y 5432 falla  -> es el fallo conocido del pooler de sesion.\n"
        "                           La solucion es cambiar el puerto a 6543.\n"
        "  los dos fallan        -> el problema no es el modo de conexion.\n"
        "                           Mira el mensaje: credenciales, red o proyecto pausado.\n"
        "  los dos OK            -> la conexion no es la causa del cuelgue.\n"
        "                           Avisa y seguimos investigando por otro lado."
    )


if __name__ == "__main__":
    main()
