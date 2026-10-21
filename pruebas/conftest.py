"""Arnés de pruebas: un PostgreSQL 17 local y desechable, nunca producción.

Levanta un clúster en una carpeta temporal, le aplica las migraciones con el
mismo script que se usa en producción y entrega a cada prueba una base nueva,
copiada de una plantilla. Al terminar apaga el clúster y borra la carpeta.

Parecido a Supabase en lo que importa
-------------------------------------
- Las migraciones las aplica `propietario`, un rol sin superusuario pero con
  CREATEROLE y BYPASSRLS, que es como es `postgres` en Supabase. Así una
  migración que en producción fallaría por permisos falla también aquí.
- Existen los roles `anon` y `authenticated` de la API REST.
- Existe un esquema `herramientas` ajeno, para comprobar que el rol de la
  aplicación no lo alcanza.

Requisitos
----------
PostgreSQL 17 instalado. Por defecto se busca en
C:\\Program Files\\PostgreSQL\\17\\bin; otra ruta se indica con PG_BIN.

La URL de producción no se lee en ningún momento. Además, core/db.py da
prioridad a TURNERO_DB_URL, y este archivo la fija apuntando al clúster local
antes de que se importe nada de la aplicación.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

import psycopg2
import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

PG_BIN = Path(os.environ.get("PG_BIN", r"C:\Program Files\PostgreSQL\17\bin"))
APLICAR = RAIZ / "migracion" / "aplicar_migraciones.py"

# Se fijan antes de cualquier import de core.*: así ninguna prueba puede leer
# por accidente la URL de .streamlit/secrets.toml ni replicar sus escrituras en
# la hoja de Google Sheets de producción.
os.environ["TURNERO_DB_URL"] = "postgresql://no-configurado@127.0.0.1:1/ninguna"
os.environ["TURNERO_SIN_ESPEJO"] = "1"


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ejecutar(*args, **kw):
    return subprocess.run([str(a) for a in args], check=True, capture_output=True, text=True, **kw)


class Servidor:
    def __init__(self):
        if not (PG_BIN / "initdb.exe").exists() and not (PG_BIN / "initdb").exists():
            pytest.skip(f"No se encontró PostgreSQL en {PG_BIN}. Indica la ruta con PG_BIN.")
        self.dir = Path(tempfile.mkdtemp(prefix="turnero_pg_"))
        self.datos = self.dir / "datos"
        self.puerto = _puerto_libre()
        _ejecutar(PG_BIN / "initdb", "-D", self.datos, "-U", "super", "--auth=trust",
                  "--encoding=UTF8", "--locale=C")
        # Sin capturar la salida: en Windows el servidor hereda los pipes y
        # subprocess se quedaría esperando a que se cierren, que es nunca. La
        # salida del servidor va a log.txt.
        subprocess.run(
            [str(PG_BIN / "pg_ctl"), "-D", str(self.datos), "-l", str(self.dir / "log.txt"),
             "-w", "-t", "60", "start",
             "-o", f"-p {self.puerto} -c listen_addresses=127.0.0.1 -c fsync=off "
                   f"-c synchronous_commit=off -c full_page_writes=off -c TimeZone=UTC"],
            check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=90,
        )

    def url(self, base="postgres", usuario="super") -> str:
        return f"postgresql://{usuario}@127.0.0.1:{self.puerto}/{base}"

    def conectar(self, base="postgres", usuario="super", autocommit=True):
        c = psycopg2.connect(self.url(base, usuario))
        c.autocommit = autocommit
        return c

    @contextmanager
    def cursor(self, base="postgres", usuario="super"):
        """Cursor en autocommit. No se usa `with conexion:` porque desde
        psycopg2 2.9 abre una transacción aunque la conexión sea autocommit, y
        CREATE DATABASE no puede ir dentro de una."""
        c = self.conectar(base, usuario)
        try:
            with c.cursor() as cur:
                yield cur
        finally:
            c.close()

    def apagar(self):
        subprocess.run([str(PG_BIN / "pg_ctl"), "-D", str(self.datos), "-m", "immediate", "-w", "stop"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=60)
        shutil.rmtree(self.dir, ignore_errors=True)


def aplicar_migraciones(servidor: Servidor, base: str, fase: str = "A") -> subprocess.CompletedProcess:
    env = dict(os.environ, MIGRACION_DB_URL=servidor.url(base, "propietario"), PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, str(APLICAR), "--fase", fase],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )


def crear_base_vacia(servidor: Servidor, nombre: str, plantilla: str = None) -> None:
    with servidor.cursor() as cur:
        extra = f" template {plantilla}" if plantilla else ""
        cur.execute(f"create database {nombre} owner propietario{extra}")
    if not plantilla:
        with servidor.cursor(nombre) as cur:
            # Desde PG15 el esquema public es del dueño de la base; se deja
            # explícito por si la plantilla del clúster cambia.
            cur.execute("alter schema public owner to propietario")
            cur.execute("create schema if not exists herramientas")
            cur.execute("create table herramientas.ajena (id int)")
            cur.execute("revoke all on schema herramientas from public")


@pytest.fixture(scope="session")
def servidor():
    s = Servidor()
    try:
        with s.cursor() as cur:
            cur.execute("create role propietario login createrole bypassrls")
            cur.execute("create role anon nologin")
            cur.execute("create role authenticated nologin")
        # Las plantillas con cada fase aplicada. Copiar una base es mucho más
        # rápido que migrar de cero en cada prueba.
        for fase in ("A", "B"):
            nombre = f"plantilla_{fase.lower()}"
            crear_base_vacia(s, nombre)
            res = aplicar_migraciones(s, nombre, fase)
            if res.returncode != 0:
                raise RuntimeError(f"Migración de la plantilla {fase} falló:\n{res.stdout}\n{res.stderr}")
        yield s
    finally:
        s.apagar()


class Base:
    """Una base de pruebas con conexiones como dueño y como aplicación."""

    def __init__(self, servidor: Servidor, nombre: str):
        self.servidor = servidor
        self.nombre = nombre

    def conectar(self, usuario="propietario", autocommit=True):
        return self.servidor.conectar(self.nombre, usuario, autocommit)

    @contextmanager
    def cursor(self, usuario="propietario", sello=None):
        """Cursor en una transacción. `sello` = (usuario, origen, motivo) deja el
        autor en la sesión igual que core/data.py:_sellar_autor."""
        c = self.conectar(usuario, autocommit=False)
        try:
            with c.cursor() as cur:
                if sello:
                    u, o, m = (list(sello) + [""])[:3]
                    cur.execute("select set_config('app.usuario', %s, true), "
                                "set_config('app.origen', %s, true), "
                                "set_config('app.motivo', %s, true)", (u, o, m))
                yield cur
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def uno(self, sql, params=None, usuario="propietario"):
        with self.cursor(usuario) as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def todos(self, sql, params=None, usuario="propietario"):
        with self.cursor(usuario) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    @property
    def url_app(self) -> str:
        return self.servidor.url(self.nombre, "turnero_app")


def _base_desde(servidor, plantilla):
    nombre = f"t_{uuid.uuid4().hex[:10]}"
    crear_base_vacia(servidor, nombre, plantilla)
    base = Base(servidor, nombre)
    yield base
    with servidor.cursor() as cur:
        cur.execute(f"drop database if exists {nombre} with (force)")


@pytest.fixture
def base_a(servidor):
    """Base con la fase A aplicada: lo que queda en producción antes de desplegar."""
    yield from _base_desde(servidor, "plantilla_a")


@pytest.fixture
def base_b(servidor):
    """Base con las dos fases: el estado final."""
    yield from _base_desde(servidor, "plantilla_b")


# --- La aplicación contra la base de pruebas ----------------------------------

@pytest.fixture
def datos(base_b, monkeypatch):
    """core.data conectado a la base de pruebas como turnero_app, como quedará
    la aplicación en producción. Lo usan test_capa_datos y test_panel."""
    import core.db as db
    import core.data as data

    monkeypatch.setenv("TURNERO_DB_URL", base_b.url_app + "?sslmode=disable")
    if db._engine is not None:
        db._engine.dispose()
    db._engine = None
    # Doble seguro además de TURNERO_SIN_ESPEJO: nada sale hacia Google Sheets.
    monkeypatch.setattr(data.sheets_backup, "espejo_habilitado", lambda: False)
    for f in (data.leer_registros, data.leer_historico, data.leer_ajustes,
              data.leer_diferencias_marcacion, data.leer_bitacora_supervision):
        f.clear()
    yield data
    if db._engine is not None:
        db._engine.dispose()
    db._engine = None



# --- Datos ficticios ---------------------------------------------------------
# Nombres inventados. Las pruebas no usan ni deben usar datos de personas reales.

def insertar_turno(base: Base, nombre="Prueba Uno", entrada="2026-09-10 08:00:00",
                   salida=None, estado=None, observaciones="", usuario="turnero_app",
                   origen="colaborador", autor=None, motivo="") -> int:
    estado = estado or ("Completo" if salida else "Abierto")
    with base.cursor(usuario, sello=(autor or nombre, origen, motivo)) as cur:
        cur.execute(
            "insert into turnos (nombre, area, fecha_turno, ts_entrada, ts_salida, estado, observaciones) "
            "values (%s, 'BODEGA', %s::timestamp::date, %s, %s, %s, %s) returning id",
            (nombre, entrada, entrada, salida, estado, observaciones),
        )
        return cur.fetchone()[0]


def mes_pasado_cerrable(base: Base):
    """Un mes que la función de cierre acepta: el anterior al actual en Ecuador."""
    return base.uno(
        "select extract(year from d)::int, extract(month from d)::int "
        "from (select (date_trunc('month', (now() at time zone 'America/Guayaquil')) "
        "              - interval '1 month')::date as d) x"
    )
