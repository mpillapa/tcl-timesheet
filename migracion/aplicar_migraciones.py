"""Aplica las migraciones versionadas de migracion/versiones/ en orden.

Reemplaza la costumbre de pegar archivos SQL en el SQL Editor de Supabase. Cada
migración se aplica en su propia transacción y queda anotada en la tabla
`schema_migraciones` con su checksum, de modo que:

- volver a correr el script no aplica dos veces lo mismo;
- si alguien edita una migración ya aplicada, el script se detiene en vez de
  dejar la base y el repositorio contando historias distintas.

Fases
-----
    V0xx  fase A  compatible con la versión actual de la aplicación
    V1xx  fase B  solo después de desplegar la versión nueva

El orden completo de despliegue está en migracion/README.md.

Uso
---
Contra una base de pruebas (lo normal):

    $env:MIGRACION_DB_URL = "postgresql://usuario:clave@127.0.0.1:5432/turnero_pruebas"
    .venv\\Scripts\\python.exe migracion\\aplicar_migraciones.py --plan
    .venv\\Scripts\\python.exe migracion\\aplicar_migraciones.py

Contra producción hay que pedirlo de forma explícita. Sin --produccion el
script se niega a conectarse a un host de Supabase:

    .venv\\Scripts\\python.exe migracion\\aplicar_migraciones.py --desde-secrets --produccion --plan
    .venv\\Scripts\\python.exe migracion\\aplicar_migraciones.py --desde-secrets --produccion
    .venv\\Scripts\\python.exe migracion\\aplicar_migraciones.py --desde-secrets --produccion --fase B

--desde-secrets toma la URL de .streamlit/secrets.toml. Nunca imprime la URL ni
la contraseña.
"""

import argparse
import hashlib
import os
import re
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlparse

import psycopg2

AQUI = Path(__file__).resolve().parent
DIR_VERSIONES = AQUI / "versiones"
RUTA_SECRETS = AQUI.parent / ".streamlit" / "secrets.toml"

PATRON = re.compile(r"^V(\d{3})__([a-z0-9_]+)\.sql$")

SQL_TABLA_REGISTRO = """
create table if not exists schema_migraciones (
    version      text primary key,
    nombre       text not null,
    checksum     text not null,
    aplicado_en  timestamptz not null default now(),
    aplicado_por text not null default current_user
);
alter table schema_migraciones enable row level security;
"""


class Migracion:
    def __init__(self, ruta: Path):
        m = PATRON.match(ruta.name)
        if not m:
            raise ValueError(f"Nombre de migración inválido: {ruta.name}")
        self.ruta = ruta
        self.version = f"V{m.group(1)}"
        self.numero = int(m.group(1))
        self.nombre = m.group(2)
        self.sql = ruta.read_text(encoding="utf-8")
        # Normaliza fin de línea para que el checksum no cambie entre Windows
        # y un clon con otra configuración de git.
        self.checksum = hashlib.sha256(self.sql.replace("\r\n", "\n").encode("utf-8")).hexdigest()

    @property
    def fase(self) -> str:
        return "A" if self.numero < 100 else "B"


def listar_migraciones() -> list:
    migs = [Migracion(p) for p in sorted(DIR_VERSIONES.glob("V*.sql"))]
    versiones = [m.version for m in migs]
    if len(versiones) != len(set(versiones)):
        raise SystemExit("Hay dos migraciones con el mismo número de versión.")
    return sorted(migs, key=lambda m: m.numero)


def es_supabase(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return "supabase" in host


def resolver_url(args) -> str:
    if args.desde_secrets:
        cfg = tomllib.load(open(RUTA_SECRETS, "rb"))
        url = str(cfg["connections"]["supabase"]["url"]).strip()
    else:
        url = os.environ.get("MIGRACION_DB_URL", "").strip()
        if not url:
            raise SystemExit("Falta MIGRACION_DB_URL, o usa --desde-secrets para producción.")
    if es_supabase(url) and not args.produccion:
        raise SystemExit(
            "La URL apunta a Supabase. Para migrar producción hay que pasar --produccion "
            "de forma explícita. Revisa antes migracion/README.md."
        )
    # psycopg2 no entiende el prefijo de driver de SQLAlchemy.
    return url.replace("postgresql+psycopg2://", "postgresql://")


def describir_destino(url: str) -> str:
    p = urlparse(url)
    return f"{p.hostname}:{p.port or 5432}/{(p.path or '/').lstrip('/')}"


def aplicadas(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(SQL_TABLA_REGISTRO)
        cur.execute("select version, checksum from schema_migraciones")
        filas = dict(cur.fetchall())
    conn.commit()
    return filas


def main() -> int:
    ap = argparse.ArgumentParser(description="Aplica las migraciones de migracion/versiones/.")
    ap.add_argument("--fase", choices=["A", "B"], default="A",
                    help="A aplica V0xx. B aplica también V1xx. Por defecto A.")
    ap.add_argument("--plan", action="store_true", help="Solo muestra qué se aplicaría.")
    ap.add_argument("--desde-secrets", action="store_true",
                    help="Toma la URL de .streamlit/secrets.toml (producción).")
    ap.add_argument("--produccion", action="store_true",
                    help="Confirma que se quiere migrar una base de Supabase.")
    args = ap.parse_args()

    url = resolver_url(args)
    migs = listar_migraciones()
    objetivo = [m for m in migs if m.fase == "A" or args.fase == "B"]

    print(f"Destino: {describir_destino(url)}")
    conn = psycopg2.connect(url, connect_timeout=15)
    try:
        hechas = aplicadas(conn)

        # Una migración ya aplicada no puede haber cambiado.
        for m in migs:
            if m.version in hechas and hechas[m.version] != m.checksum:
                print(f"\nERROR: {m.ruta.name} cambió después de aplicarse.")
                print("Una migración aplicada no se edita: se escribe una nueva que corrija.")
                return 2

        pendientes = [m for m in objetivo if m.version not in hechas]
        if not pendientes:
            print("No hay migraciones pendientes para la fase pedida.")
            return 0

        print("Pendientes:")
        for m in pendientes:
            print(f"  {m.version}  fase {m.fase}  {m.nombre}")
        if args.plan:
            return 0

        for m in pendientes:
            print(f"Aplicando {m.version} {m.nombre} ...", end=" ", flush=True)
            try:
                with conn.cursor() as cur:
                    cur.execute(m.sql)
                    cur.execute(
                        "insert into schema_migraciones (version, nombre, checksum) values (%s, %s, %s)",
                        (m.version, m.nombre, m.checksum),
                    )
                conn.commit()
                print("listo")
            except Exception as e:
                conn.rollback()
                print("FALLÓ")
                print(f"\n{type(e).__name__}: {e}")
                print("La migración se revirtió entera. Las anteriores quedaron aplicadas.")
                return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
