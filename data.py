"""Acceso a Google Sheets y utilidades de bajo nivel sobre los registros.

Las escrituras (append/update) usan `gspread` a nivel de fila/celda, NO el
patrón "leer hoja entera → modificar → reescribir hoja entera". Esto evita
que escrituras concurrentes de distintos usuarios se pisen entre sí.
"""

from datetime import datetime

import gspread
import pandas as pd
import streamlit as st
from streamlit_gsheets import GSheetsConnection

from config import COLUMNAS, COLS_TEXTO, HORAS_BASE_TURNO, WORKSHEET_NAME

_conn = st.connection("gsheets", type=GSheetsConnection)

_SA_KEYS = {
    "type", "project_id", "private_key_id", "private_key",
    "client_email", "client_id", "auth_uri", "token_uri",
    "auth_provider_x509_cert_url", "client_x509_cert_url", "universe_domain",
}

_worksheet = None
_header_cache = None


def _get_worksheet():
    """Devuelve (y cachea) el objeto gspread.Worksheet usado para escrituras
    atómicas por fila. Usa las mismas credenciales del bloque
    [connections.gsheets] de secrets."""
    global _worksheet
    if _worksheet is not None:
        return _worksheet

    secrets = st.secrets["connections"]["gsheets"]
    sa_info = {k: v for k, v in secrets.items() if k in _SA_KEYS}
    gc = gspread.service_account_from_dict(sa_info)

    spreadsheet_ref = str(secrets["spreadsheet"])
    sh = gc.open_by_url(spreadsheet_ref) if spreadsheet_ref.startswith("http") else gc.open_by_key(spreadsheet_ref)

    ws_name = secrets.get("worksheet", WORKSHEET_NAME) if hasattr(secrets, "get") else WORKSHEET_NAME
    _worksheet = sh.worksheet(ws_name)
    return _worksheet


def _get_header() -> list:
    """Devuelve (y cachea) el header real de la hoja. Se usa para ordenar
    los valores al escribir: así da igual el orden de columnas en la hoja,
    cada valor cae bajo su nombre real."""
    global _header_cache
    if _header_cache is not None:
        return _header_cache
    ws = _get_worksheet()
    _header_cache = ws.row_values(1)
    return _header_cache


def leer_registros() -> pd.DataFrame:
    """Lee la hoja forzando 'object' en columnas de texto (evita TypeError al escribir strings en columnas float)."""
    try:
        df = _conn.read(worksheet=WORKSHEET_NAME, ttl=0)
        df = df.dropna(how="all")
        for col in COLUMNAS:
            if col not in df.columns:
                df[col] = ""
        df = df[COLUMNAS].copy()
        for col in COLS_TEXTO:
            df[col] = df[col].astype(object).where(df[col].notna(), "")

        # Compatibilidad: si hay filas antiguas sin "Horas Extra", se calcula en memoria.
        horas_num = pd.to_numeric(df["Horas Trabajadas"], errors="coerce")
        horas_extra_num = pd.to_numeric(df["Horas Extra"], errors="coerce")
        mask_falta_extra = horas_num.notna() & horas_extra_num.isna()
        if mask_falta_extra.any():
            df.loc[mask_falta_extra, "Horas Extra"] = horas_num[mask_falta_extra].apply(calcular_horas_extra)
        return df
    except Exception:
        return pd.DataFrame({c: pd.Series(dtype=object) for c in COLUMNAS})


def append_registro(fila: dict) -> None:
    """Añade una fila atómicamente vía `Worksheet.append_row`.

    Google Sheets serializa los appends del lado del servidor, por lo que no
    existe race condition: dos usuarios marcando entrada al mismo tiempo
    generan dos filas nuevas, nunca una pisa a la otra.

    Los valores se ordenan según el HEADER REAL de la hoja (no según la
    constante COLUMNAS), para que cada valor caiga bajo su nombre de columna
    sin importar el orden físico de las columnas en el sheet. `table_range`
    ancla el append a la columna A para evitar desalineaciones si el
    autodetect de Sheets falla.
    """
    ws = _get_worksheet()
    header = _get_header()
    row_values = [fila.get(col, "") for col in header]
    ws.append_row(row_values, value_input_option="RAW", table_range="A1")


def actualizar_por_entrada(nombre: str, ts_entrada_str: str, cambios: dict) -> bool:
    """Actualiza SOLO las celdas indicadas en `cambios` de la fila que matchea
    (Nombre, Timestamp Entrada). Devuelve False si la fila no existe.

    No reescribe el resto de la hoja, así que cualquier marcación concurrente
    en otras filas se preserva. La clave es estable: ni Nombre ni Timestamp
    Entrada se modifican nunca tras la creación.
    """
    ws = _get_worksheet()
    all_values = ws.get_all_values()
    if len(all_values) < 2:
        return False

    header = all_values[0]
    try:
        i_nombre = header.index("Nombre")
        i_entrada = header.index("Timestamp Entrada")
    except ValueError:
        return False

    target_row = None  # índice 1-based en la hoja (fila 1 = header)
    for offset, row in enumerate(all_values[1:], start=2):
        if (i_nombre < len(row) and i_entrada < len(row)
                and row[i_nombre] == nombre
                and str(row[i_entrada]) == str(ts_entrada_str)):
            target_row = offset
            break
    if target_row is None:
        return False

    updates = []
    for col_name, val in cambios.items():
        if col_name not in header:
            continue
        col_idx = header.index(col_name) + 1
        a1 = gspread.utils.rowcol_to_a1(target_row, col_idx)
        updates.append({"range": a1, "values": [[val if val is not None else ""]]})
    if updates:
        ws.batch_update(updates, value_input_option="RAW")
    return True


def calcular_horas(ts_in: datetime, ts_out: datetime) -> float:
    return round((ts_out - ts_in).total_seconds() / 3600, 2)


def calcular_horas_extra(horas_trabajadas: float) -> float:
    return round(max(0.0, float(horas_trabajadas) - HORAS_BASE_TURNO), 2)


def buscar_turno_abierto_idx(df: pd.DataFrame, nombre: str):
    """Devuelve el índice del turno abierto del empleado, o None."""
    if df.empty:
        return None
    mask = (df["Nombre"] == nombre) & (df["Estado"] == "Abierto")
    idxs = df.index[mask].tolist()
    return idxs[0] if idxs else None
