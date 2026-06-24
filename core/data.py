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

from core.config import COLUMNAS, COLS_TEXTO, HORAS_BASE_TURNO, HORAS_ALMUERZO, MIN_HORAS_ALMUERZO, WORKSHEET_NAME, WORKSHEET_HORAS_ESPERADAS

_SA_KEYS = {
    "type", "project_id", "private_key_id", "private_key",
    "client_email", "client_id", "auth_uri", "token_uri",
    "auth_provider_x509_cert_url", "client_x509_cert_url", "universe_domain",
}

_worksheet = None
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


def _normalizar_serie(serie: pd.Series) -> pd.Series:
    """Equivalente vectorizado de _normalizar_texto sobre una columna entera.

    Hace el mismo trabajo (quitar invisibles, nbsp->espacio, compactar espacios
    y recortar) pero con operaciones .str a nivel de columna en vez de aplicar
    una función Python celda por celda. Importa en hojas grandes: leer_registros
    procesa todas las filas en cada lectura no cacheada."""
    s = serie.astype(object).where(serie.notna(), "").astype(str)
    s = s.str.replace(_INVISIBLE_RE, "", regex=True)
    s = s.str.replace(" ", " ", regex=False)
    s = s.str.replace(r"\s+", " ", regex=True)
    return s.str.strip()


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
    """Devuelve el header real de la hoja sin caché, para evitar desalineaciones
    si se agregan columnas mientras la app está corriendo."""
    return _get_worksheet().row_values(1)


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


@st.cache_data(ttl=300)
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
            # Descartar filas completamente vacías. Vectorizado por columna
            # (mucho más rápido que apply(axis=1) cuando la hoja crece).
            con_contenido = df.apply(lambda c: c.astype(str).str.strip(), axis=0).ne("").any(axis=1)
            df = df[con_contenido]
        else:
            df = pd.DataFrame(columns=header)

        for col in COLUMNAS:
            if col not in df.columns:
                df[col] = ""
        df = df[COLUMNAS].copy()
        df = df.astype(object)  # evita StringDtype en pandas 3.x al asignar valores numéricos
        for col in COLS_TEXTO:
            df[col] = _normalizar_serie(df[col])

        # Compatibilidad: calcular columnas derivadas en filas antiguas que las tengan vacías.
        horas_num = pd.to_numeric(df["Horas Trabajadas"], errors="coerce")
        horas_efect_num = pd.to_numeric(df["Horas Efectivas"], errors="coerce")
        horas_extra_num = pd.to_numeric(df["Horas Extra"], errors="coerce")
        mask_falta_efect = horas_num.notna() & horas_efect_num.isna()
        if mask_falta_efect.any():
            df.loc[mask_falta_efect, "Horas Efectivas"] = horas_num[mask_falta_efect].apply(calcular_horas_efectivas)
        mask_falta_extra = horas_num.notna() & horas_extra_num.isna()
        if mask_falta_extra.any():
            df.loc[mask_falta_extra, "Horas Extra"] = horas_num[mask_falta_extra].apply(calcular_horas_extra)
        return df
    except Exception as e:
        st.error(f"Error leyendo Google Sheets: {type(e).__name__}: {e}")
        return pd.DataFrame({c: pd.Series(dtype=object) for c in COLUMNAS})


def append_registro(fila: dict) -> None:
    """Inserta una fila en posición cronológica según Timestamp Entrada.

    Si el nuevo registro es el más reciente, usa append_row (atómico).
    Si debe intercalarse entre filas existentes, usa insert_row en la
    posición correcta para mantener el orden cronológico en la hoja.
    """
    ws = _get_worksheet()
    _aplicar_formato_fecha_hora()
    all_values = ws.get_all_values()
    header = all_values[0] if all_values else _get_header()
    row_values = [fila.get(col, "") for col in header]

    ts_nueva = _ts_key(fila.get("Timestamp Entrada", ""))
    insert_idx = None

    if ts_nueva and len(all_values) > 1:
        try:
            i_entrada = header.index("Timestamp Entrada")
        except ValueError:
            i_entrada = None

        if i_entrada is not None:
            for row_idx, row in enumerate(all_values[1:], start=2):
                ts_fila = _ts_key(row[i_entrada]) if i_entrada < len(row) else ""
                if ts_fila and ts_fila > ts_nueva:
                    insert_idx = row_idx
                    break

    if insert_idx is not None:
        ws.insert_row(row_values, index=insert_idx, value_input_option="USER_ENTERED")
    else:
        ws.append_row(row_values, value_input_option="USER_ENTERED", table_range="A1")
    leer_registros.clear()


def append_registros_batch(filas: list) -> int:
    """Inserta varias filas manteniendo el orden cronológico de la hoja, cada
    una en su posición correcta (no como bloque contiguo).

    Pensado para cargas masivas administrativas (p. ej. un rango de vacaciones).
    Para cada fila se calcula —sobre la hoja original— la primera fila existente
    con Timestamp Entrada mayor:
      - las que no tienen ninguna posterior (caso típico: vacaciones futuras) se
        agregan TODAS juntas al final en una sola llamada (append_rows);
      - las que sí deben intercalarse se insertan una a una con insert_row, en
        orden descendente de índice para que los índices menores no se invaliden
        y los empates queden cronológicos.
    Así el caso normal cuesta 1 llamada y solo los rangos retroactivos/intercalados
    pagan inserciones extra. Devuelve el número de filas insertadas."""
    if not filas:
        return 0
    ws = _get_worksheet()
    _aplicar_formato_fecha_hora()
    all_values = ws.get_all_values()
    header = all_values[0] if all_values else _get_header()

    try:
        i_entrada = header.index("Timestamp Entrada")
    except ValueError:
        i_entrada = None

    # ts de cada fila existente, en orden de hoja (índice 1-based; fila 1 = header)
    existentes = []
    for row_idx, row in enumerate(all_values[1:], start=2):
        ts = _ts_key(row[i_entrada]) if (i_entrada is not None and i_entrada < len(row)) else ""
        existentes.append((ts, row_idx))

    def _pos_para(ts_nuevo):
        """Primer índice de hoja cuyo ts existente es > ts_nuevo; None = va al final."""
        if not ts_nuevo or i_entrada is None:
            return None
        for ts, row_idx in existentes:
            if ts and ts > ts_nuevo:
                return row_idx
        return None

    # plan: (insert_idx | None, ts_nuevo, row_values)
    plan = []
    for fila in filas:
        ts_nuevo = _ts_key(fila.get("Timestamp Entrada", ""))
        row_values = [fila.get(col, "") for col in header]
        plan.append((_pos_para(ts_nuevo), ts_nuevo, row_values))

    intercaladas = [p for p in plan if p[0] is not None]
    al_final = [p for p in plan if p[0] is None]

    # Insertar intercaladas de mayor a menor índice (y ts desc para empates).
    intercaladas.sort(key=lambda p: (p[0], p[1]), reverse=True)
    for insert_idx, _ts, row_values in intercaladas:
        ws.insert_row(row_values, index=insert_idx, value_input_option="USER_ENTERED")

    # Las que van al final, todas en una sola llamada y en orden cronológico.
    if al_final:
        al_final.sort(key=lambda p: p[1])
        rows = [p[2] for p in al_final]
        ws.append_rows(rows, value_input_option="USER_ENTERED", table_range="A1")

    leer_registros.clear()
    return len(plan)


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
    leer_registros.clear()
    return True


def eliminar_por_entrada(nombre: str, ts_entrada_str: str) -> bool:
    """Elimina la fila que matchea (Nombre, Timestamp Entrada). Devuelve False
    si no existe. Operación destructiva: borra la fila completa de la hoja."""
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

    ws.delete_rows(target_row)
    leer_registros.clear()
    return True


def calcular_horas(ts_in: datetime, ts_out: datetime) -> float:
    return round((ts_out - ts_in).total_seconds() / 3600, 2)


def calcular_horas_efectivas(horas_trabajadas: float) -> float:
    h = float(horas_trabajadas)
    return round(h - HORAS_ALMUERZO if h >= MIN_HORAS_ALMUERZO else h, 2)


def calcular_horas_extra(horas_trabajadas: float) -> float:
    return round(max(0.0, float(horas_trabajadas) - HORAS_BASE_TURNO), 2)


_MESES_NUM = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


@st.cache_data(ttl=300)
def leer_horas_esperadas() -> pd.DataFrame:
    """Lee la hoja 'Horas Esperadas' y devuelve un DataFrame con columnas
    Año (int), Mes (int 1-12), Horas (float). Solo incluye filas con Horas definidas."""
    try:
        sh = _get_worksheet().spreadsheet
        ws = sh.worksheet(WORKSHEET_HORAS_ESPERADAS)
        values = ws.get_all_values()
        if len(values) < 2:
            return pd.DataFrame(columns=["Año", "Mes", "Horas"])

        header = [_normalizar_texto(c).lower() for c in values[0]]
        rows = values[1:]
        df = pd.DataFrame(rows, columns=header)

        df["Año"] = pd.to_numeric(df.get("año", pd.Series(dtype=object)), errors="coerce")
        df["Mes"] = df.get("mes", pd.Series(dtype=object)).astype(str).str.strip().str.lower().map(_MESES_NUM)
        horas_raw = df.get("horas", pd.Series(dtype=object)).astype(str).str.strip().replace("", pd.NA)
        df["Horas"] = pd.to_numeric(horas_raw, errors="coerce")

        df = df.dropna(subset=["Año", "Mes", "Horas"])
        df["Año"] = df["Año"].astype(int)
        df["Mes"] = df["Mes"].astype(int)
        return df[["Año", "Mes", "Horas"]].reset_index(drop=True)
    except Exception as e:
        st.warning(f"No se pudo leer la hoja de horas esperadas: {type(e).__name__}: {e}")
        return pd.DataFrame(columns=["Año", "Mes", "Horas"])


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
