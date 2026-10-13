"""Copia el padrón de secrets.toml a la tabla `empleados`. Nombre y área, nunca el PIN.

El padrón vive en el bloque [empleados] de secrets, con el PIN de cada
colaborador como clave. El PIN son los últimos cuatro dígitos de la cédula, así
que este script lo descarta al leer: a la base solo llegan el nombre y el área.

Qué hace
--------
- Una persona del padrón que no está en la tabla se crea como 'padron'.
- Una que ya está (por ejemplo como 'historico' o 'automatico') pasa a
  'padron', queda activa y toma el área del padrón.
- Una persona de la tabla que ya no está en el padrón queda inactiva. No se
  borra: sus turnos siguen apuntando a ella.

Sin --aplicar solo muestra lo que cambiaría.

Uso
---
    .venv\\Scripts\\python.exe migracion\\sincronizar_empleados.py --desde-secrets --produccion
    .venv\\Scripts\\python.exe migracion\\sincronizar_empleados.py --desde-secrets --produccion --aplicar

--secrets RUTA lee el padrón de otro archivo; lo usan las pruebas.
"""

import argparse
import sys
import tomllib
from pathlib import Path

import psycopg2

from aplicar_migraciones import RUTA_SECRETS, describir_destino, resolver_url


def leer_padron(ruta: Path) -> dict:
    """{nombre: area}. El PIN, que es la clave del bloque, se descarta aquí."""
    cfg = tomllib.load(open(ruta, "rb"))
    padron = {}
    for _pin, info in cfg.get("empleados", {}).items():
        nombre = str(info.get("nombre", "")).strip()
        area = str(info.get("area", "")).strip()
        if nombre and area:
            padron[nombre] = area
    return padron


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--desde-secrets", action="store_true")
    ap.add_argument("--produccion", action="store_true")
    ap.add_argument("--aplicar", action="store_true", help="Sin esto solo muestra los cambios.")
    ap.add_argument("--secrets", type=Path, default=RUTA_SECRETS, help="Archivo del que leer el padrón.")
    args = ap.parse_args()

    padron = leer_padron(args.secrets)
    if not padron:
        print("El padrón está vacío. No se toca nada.")
        return 1

    url = resolver_url(args)
    print(f"Destino: {describir_destino(url)}")
    conn = psycopg2.connect(url, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("select lower(nombre), nombre, area, origen, activo from empleados")
            actual = {r[0]: r[1:] for r in cur.fetchall()}

            nuevos, confirmados, inactivos = [], [], []
            padron_cmp = {n.lower(): (n, a) for n, a in padron.items()}
            for clave, (nombre, area) in padron_cmp.items():
                if clave not in actual:
                    nuevos.append((nombre, area))
                else:
                    _, area_bd, origen, activo = actual[clave]
                    if (area_bd, origen, activo) != (area, "padron", True):
                        confirmados.append((nombre, area))
            for clave, (nombre, _area, _origen, activo) in actual.items():
                if clave not in padron_cmp and activo:
                    inactivos.append(nombre)

            print(f"\nA crear desde el padrón: {len(nuevos)}")
            for n, a in nuevos:
                print(f"  + {n} ({a})")
            print(f"A confirmar o actualizar con el padrón: {len(confirmados)}")
            for n, a in confirmados:
                print(f"  = {n} ({a})")
            print(f"A marcar inactivos, ya no están en el padrón: {len(inactivos)}")
            for n in inactivos:
                print(f"  - {n}")

            if not args.aplicar:
                print("\nNo se aplicó nada. Agrega --aplicar para escribir.")
                conn.rollback()
                return 0

            for nombre, area in nuevos:
                cur.execute("insert into empleados (nombre, area, origen) values (%s, %s, 'padron')",
                            (nombre, area))
            for nombre, area in confirmados:
                cur.execute("update empleados set area = %s, origen = 'padron', activo = true, "
                            "actualizado_en = now() where lower(nombre) = lower(%s)", (area, nombre))
            for nombre in inactivos:
                cur.execute("update empleados set activo = false, actualizado_en = now() "
                            "where lower(nombre) = lower(%s)", (nombre,))
        conn.commit()
        print("\nAplicado.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
