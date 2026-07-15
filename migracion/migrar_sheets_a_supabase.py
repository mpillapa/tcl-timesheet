"""Migracion inicial: Google Sheets -> PostgreSQL (Supabase).

Copia Registros (archivado=false), Historico (archivado=true) y Horas
Esperadas a la base de datos. NO modifica el Sheet: solo lee.

Es IDEMPOTENTE: se puede ejecutar varias veces; las filas que ya existen en
la base (misma clave Nombre + Timestamp Entrada) se omiten sin error.

Uso, desde la carpeta del proyecto:

    .venv\\Scripts\\python -m migracion.migrar_sheets_a_supabase
"""

import sys

from sqlalchemy import text

from core.db import get_engine
from core.data import _fila_a_params
from core.normalizacion import _normalizar_cmp
from core import sheets_backup

INSERT_SQL = text(
    "INSERT INTO turnos (nombre, area, fecha_turno, ts_entrada, ts_salida, "
    "horas_trabajadas, horas_efectivas, horas_extra, estado, evento, "
    "observaciones, archivado) "
    "VALUES (:nombre, :area, :fecha_turno, :ts_entrada, :ts_salida, "
    ":horas_trabajadas, :horas_efectivas, :horas_extra, :estado, :evento, "
    ":observaciones, :archivado) "
    "ON CONFLICT ((lower(nombre)), ts_entrada) DO NOTHING"
)


def _preparar_lote(df, archivado: bool):
    """DataFrame del Sheet -> (lote de params SQL, problemas, duplicados).

    - problemas: filas cuyo Timestamp Entrada no se pudo interpretar (se
      excluyen y se reportan para corregirlas a mano).
    - duplicados: filas repetidas dentro del propio Sheet (misma clave
      Nombre + Timestamp Entrada); se migra solo la primera."""
    lote, problemas, duplicados = [], [], []
    vistos = set()
    for _, fila in df.iterrows():
        params = _fila_a_params(fila.to_dict())
        if params["ts_entrada"] is None:
            problemas.append(f"  - {fila['Nombre']} | {fila['Fecha de Turno']} | "
                             f"entrada={fila['Timestamp Entrada']!r}")
            continue
        clave = (_normalizar_cmp(params["nombre"]), params["ts_entrada"])
        if clave in vistos:
            duplicados.append(f"  - {fila['Nombre']} | entrada={fila['Timestamp Entrada']}")
            continue
        vistos.add(clave)
        params["archivado"] = archivado
        lote.append(params)
    return lote, problemas, duplicados


def main() -> int:
    print("=== Migracion Google Sheets -> Supabase ===")

    # 1) Esquema (CREATE TABLE IF NOT EXISTS: seguro re-ejecutar).
    print("[1/5] Aplicando esquema (tablas e indices)...")
    from pathlib import Path
    schema_sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    engine = get_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql(schema_sql)
    print("      OK")

    # 2) Leer el Sheet.
    print("[2/5] Leyendo Google Sheets (Registros, Historico, Horas Esperadas)...")
    df_reg = sheets_backup.leer_registros_sheets()
    df_hist = sheets_backup.leer_historico_sheets()
    df_horas = sheets_backup.leer_horas_esperadas_sheets()
    print(f"      Registros: {len(df_reg)} filas | Historico: {len(df_hist)} filas | "
          f"Horas Esperadas: {len(df_horas)} filas")

    # 3) Preparar e insertar turnos.
    print("[3/5] Insertando turnos en la base de datos...")
    lote_reg, prob_reg, dup_reg = _preparar_lote(df_reg, archivado=False)
    lote_hist, prob_hist, dup_hist = _preparar_lote(df_hist, archivado=True)
    lote = lote_reg + lote_hist

    with engine.connect() as conn:
        antes = conn.execute(text("SELECT count(*) FROM turnos")).scalar()
    if lote:
        with engine.begin() as conn:
            conn.execute(INSERT_SQL, lote)
    with engine.connect() as conn:
        despues = conn.execute(text("SELECT count(*) FROM turnos")).scalar()
    nuevos = despues - antes
    print(f"      Validas en Sheet: {len(lote)} | Insertadas ahora: {nuevos} | "
          f"Ya existian en la base: {len(lote) - nuevos}")

    # 4) Horas esperadas (upsert: si ya existe el mes, actualiza el valor).
    print("[4/5] Migrando horas esperadas...")
    if not df_horas.empty:
        filas_h = [{"anio": int(r["Año"]), "mes": int(r["Mes"]), "horas": float(r["Horas"])}
                   for _, r in df_horas.iterrows()]
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO horas_esperadas (anio, mes, horas) "
                     "VALUES (:anio, :mes, :horas) "
                     "ON CONFLICT (anio, mes) DO UPDATE SET horas = excluded.horas"),
                filas_h,
            )
        print(f"      {len(filas_h)} meses migrados")
    else:
        print("      (hoja vacia o inexistente, nada que migrar)")

    # 5) Verificacion.
    print("[5/5] Verificando totales...")
    with engine.connect() as conn:
        n_act = conn.execute(text("SELECT count(*) FROM turnos WHERE archivado = false")).scalar()
        n_arc = conn.execute(text("SELECT count(*) FROM turnos WHERE archivado = true")).scalar()
        suma_db = conn.execute(
            text("SELECT coalesce(sum(horas_trabajadas), 0) FROM turnos")
        ).scalar()
    import pandas as pd
    suma_sheet = float(
        pd.to_numeric(df_reg["Horas Trabajadas"], errors="coerce").fillna(0).sum()
        + pd.to_numeric(df_hist["Horas Trabajadas"], errors="coerce").fillna(0).sum()
    )
    print(f"      Base de datos -> activos: {n_act} | archivados: {n_arc}")
    print(f"      Suma de Horas Trabajadas -> Sheet: {suma_sheet:.2f} | Base: {float(suma_db):.2f}")

    problemas = prob_reg + prob_hist
    duplicados = dup_reg + dup_hist
    if problemas:
        print(f"\nATENCION: {len(problemas)} fila(s) con Timestamp Entrada ilegible "
              "NO se migraron (corregir en el Sheet y re-ejecutar):")
        print("\n".join(problemas))
    if duplicados:
        print(f"\nATENCION: {len(duplicados)} fila(s) duplicadas dentro del Sheet "
              "(misma persona y mismo Timestamp Entrada); se migro solo la primera:")
        print("\n".join(duplicados))
    if not problemas and not duplicados and abs(suma_sheet - float(suma_db)) < 0.01:
        print("\nMigracion completada sin observaciones. Totales cuadran.")
    else:
        print("\nMigracion completada. Revisa las observaciones de arriba.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
