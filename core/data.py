"""Acceso a Google Sheets y utilidades de bajo nivel sobre los registros.

Las escrituras (append/update) usan `gspread` a nivel de fila/celda, NO el
patrón "leer hoja entera → modificar → reescribir hoja entera". Esto evita
que escrituras concurrentes de distintos usuarios se pisen entre sí.
"""

from datetime import datetime
import re

import gspread
import pandas as pd
import streamlit as st

from core.config import COLUMNAS, COLS_TEXTO, HORAS_BASE_TURNO, WORKSHEET_NAME

_SA_KEYS = {
    "type", "project_id", "private_key_id", "private_key",
    "client_email", "client_id", "auth_uri", "token_uri",
    "auth_provider_x509_cert_url", "client_x509_cert_url", "universe_domain",
}

_worksheet = None
_header_cache = None
_datetime_format_applied = False


_INVISIBLE_RE = re.compile(r"[​‌‍⁠﻿]")


def _normalizar_texto(value) -> str:
    """Normaliza textos para comparaciones robustas.

    Elimina caracteres invisibles (zero-width/BOM), compacta espacios y
    recorta extremos para evitar falsos negativos en comparaciones exactas.
    """
    s = str(value or "")
    s = _INVISIBLE_RE.sub("", s)
    s = s.replace(" ", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _normalizar_cmp(value) -> str:
    """Normalización para comparaciones textuales case-insensitive."""
    return _normalizar_texto(value).casefold()


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


def _aplicar_formato_fecha_hora() -> None:
    """Aplica formato de fecha/hora a columnas de timestamps en Google Sheets.

    Se ejecuta una sola vez por sesión de app para evitar requests repetitivos.
    """
    global _datetime_format_applied
    if _datetime_format_applied:
        return

    ws = _get_worksheet()
    header = _get_header()
    requests = []

    def _add_format_request(col_name: str, pattern: str) -> None:
        if col_name not in header:
            return
        col_idx = header.index(col_name)
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 1,
                    "startColumnIndex": col_idx,
                    "endColumnIndex": col_idx + 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {
                            "type": "DATE_TIME",
                            "pattern": pattern,
                        }
                    }
                },
                "fields": "userEnteredFormat.numberFormat",
            }
        })

    _add_format_request("Fecha de Turno", "yyyy-mm-dd")
    _add_format_request("Timestamp Entrada", "yyyy-mm-dd hh:mm:ss")
    _add_format_request("Timestamp Salida", "yyyy-mm-dd hh:mm:ss")

    if requests:
        ws.spreadsheet.batch_update({"requests": requests})
    _datetime_format_applied = True


def leer_registros() -> pd.DataFrame:
    """Lee la hoja forzando 'object' en columnas de texto (evita TypeError al
    escribir strings en columnas float) y normalizando espacios en los
    headers y en los valores de texto (evita que un ' ' invisible en una
    celda haga fallar las comparaciones == 'Abierto' o == nombre)."""
    try:
        ws = _get_worksheet()
        values = ws.get_all_values()
        if not values:
            return pd.DataFrame({c: pd.Series(dtype=object) for c in COLUMNAS})

        header = [_normalizar_texto(c) for c in values[0]]
        rows = values[1:]
        if rows:
            ancho = len(header)
            rows = [r[:ancho] + [""] * max(0, ancho - len(r)) for r in rows]
            df = pd.DataFrame(rows, columns=header)
            df = df[df.apply(lambda row: any(_normalizar_texto(v) for v in row), axis=1)]
        else:
            df = pd.DataFrame(columns=header)

        for col in COLUMNAS:
            if col not in df.columns:
                df[col] = ""
        df = df[COLUMNAS].copy()
        for col in COLS_TEXTO:
            df[col] = df[col].astype(object).where(df[col].notna(), "")
            df[col] = df[col].map(_normalizar_texto)

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
    _aplicar_formato_fecha_hora()
    header = _get_header()
    row_values = [fila.get(col, "") for col in header]
    ws.append_row(row_values, value_input_option="USER_ENTERED", table_range="A1")


def _ts_key(raw) -> str:
    """Normaliza un timestamp a 'YYYY-MM-DD HH:MM:SS' para comparar de forma
    robusta entre:
      - strings escritos con RAW (formato canónico),
      - celdas datetime-typed legacy cuyo display depende del locale
        (ej. '22/4/2026 9:00:00' en es-EC).
    Si no se puede parsear, devuelve el valor en bruto como fallback.
    """
    s = _normalizar_texto(raw)
    if not s:
        return ""
    try:
        return pd.to_datetime(s).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            return pd.to_datetime(s, dayfirst=True).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return s


def actualizar_por_entrada(nombre: str, ts_entrada_str: str, cambios: dict) -> bool:
    """Actualiza SOLO las celdas indicadas en `cambios` de la fila que matchea
    (Nombre, Timestamp Entrada). Devuelve False si la fila no existe.

    No reescribe el resto de la hoja, así que cualquier marcación concurrente
    en otras filas se preserva. La clave es estable: ni Nombre ni Timestamp
    Entrada se modifican nunca tras la creación.
    """
    ws = _get_worksheet()
    _aplicar_formato_fecha_hora()
    all_values = ws.get_all_values()
    if len(all_values) < 2:
        return False

    header = all_values[0]
    try:
        i_nombre = header.index("Nombre")
        i_entrada = header.index("Timestamp Entrada")
    except ValueError:
        return False

    nombre_norm = _normalizar_cmp(nombre)
    key = _ts_key(ts_entrada_str)
    target_row = None  # índice 1-based en la hoja (fila 1 = header)
    for offset, row in enumerate(all_values[1:], start=2):
        if i_nombre >= len(row) or i_entrada >= len(row):
            continue
        if _normalizar_cmp(row[i_nombre]) != nombre_norm:
            continue
        if _ts_key(row[i_entrada]) == key:
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
        ws.batch_update(updates, value_input_option="USER_ENTERED")
    return True


def calcular_horas(ts_in: datetime, ts_out: datetime) -> float:
    return round((ts_out - ts_in).total_seconds() / 3600, 2)


def calcular_horas_extra(horas_trabajadas: float) -> float:
    return round(max(0.0, float(horas_trabajadas) - HORAS_BASE_TURNO), 2)


def buscar_turno_abierto_idx(df: pd.DataFrame, nombre: str):
    """Devuelve el índice del turno abierto del empleado, o None.

    Incluye un fallback para filas legacy que quedaron con "Abierto" en la
    columna Observaciones y Estado vacío, producto de un bug histórico de
    desalineo de columnas (ya corregido). Sin este fallback esas filas
    quedarían inmarcables (imposible cerrar el turno). Al cerrarlas, las
    celdas Estado y Observaciones se sobrescriben con valores correctos, así
    que la fila se auto-repara en el próximo marcado de salida.
    """
    if df.empty:
        return None

    nombre_norm = _normalizar_cmp(nombre)
    df_nombre = df["Nombre"].fillna("").map(_normalizar_cmp)
    estado = df["Estado"].fillna("").map(_normalizar_cmp)
    obs = df["Observaciones"].fillna("").map(_normalizar_cmp)
    nombre_mask = df_nombre == nombre_norm

    primary = nombre_mask & (estado == "abierto")
    idxs = df.index[primary].tolist()
    if idxs:
        return idxs[0]

    legacy = nombre_mask & (obs == "abierto") & (estado == "")
    idxs = df.index[legacy].tolist()
    return idxs[0] if idxs else None
