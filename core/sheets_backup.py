"""Espejo de respaldo en Google Sheets.

Desde la migración a PostgreSQL (Supabase), la base de datos es la fuente de
verdad y este módulo mantiene el Sheet como COPIA DE RESPALDO best-effort:

  - Las funciones espejo_* replican cada escritura de la app en el Sheet.
    Lanzan excepciones hacia arriba; core.data las envuelve en try/except para
    que un fallo del respaldo NUNCA bloquee una marcación (el dato ya quedó
    guardado en la base de datos).
  - Las funciones leer_*_sheets leen el Sheet crudo; las usa el script de
    migración inicial y sirven para auditar el respaldo.

Se puede apagar el espejo sin tocar código con:

    [backup]
    espejo_sheets = false

A diferencia de la versión histórica, espejo_append NO busca la posición
cronológica de la fila (eso costaba una lectura completa de la hoja): en el
respaldo el orden es cosmético y una sola llamada append_row basta.
"""

import gspread
import pandas as pd
import streamlit as st

from core.config import (
    COLUMNAS,
    COLS_TEXTO,
    WORKSHEET_NAME,
    WORKSHEET_HORAS_ESPERADAS,
    WORKSHEET_HISTORICO,
)
from core.calculos import calcular_horas_efectivas, calcular_horas_extra
from core.normalizacion import _normalizar_texto, _normalizar_cmp, _normalizar_serie, _ts_key
from core.time_utils import now_ecuador, parse_fecha_flexible

_SA_KEYS = {
    "type", "project_id", "private_key_id", "private_key",
    "client_email", "client_id", "auth_uri", "token_uri",
    "auth_provider_x509_cert_url", "client_x509_cert_url", "universe_domain",
}

_worksheet = None
_esquema_asegurado = False


def espejo_habilitado() -> bool:
    """True si el respaldo en Sheets está activo (default: activo)."""
    try:
        return bool(st.secrets["backup"].get("espejo_sheets", True))
    except Exception:
        return True


def _get_worksheet():
    """Devuelve (y cachea) el objeto gspread.Worksheet de la hoja Registros."""
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
    """Header real de la hoja sin caché, para evitar desalineaciones si se
    agregan columnas mientras la app está corriendo."""
    return _get_worksheet().row_values(1)


def _asegurar_columnas_esquema() -> None:
    """Garantiza que el encabezado de la hoja tenga todas las columnas de
    COLUMNAS. Las que falten se agregan al final, una sola vez por sesión.

    Las escrituras solo persisten columnas presentes en el encabezado real:
    una columna nueva del esquema no se guardaría hasta existir físicamente
    en la hoja."""
    global _esquema_asegurado
    if _esquema_asegurado:
        return
    ws = _get_worksheet()
    header = ws.row_values(1)
    faltantes = [c for c in COLUMNAS if c not in header]
    if faltantes:
        inicio = len(header) + 1
        a1_ini = gspread.utils.rowcol_to_a1(1, inicio)
        a1_fin = gspread.utils.rowcol_to_a1(1, inicio + len(faltantes) - 1)
        ws.batch_update(
            [{"range": f"{a1_ini}:{a1_fin}", "values": [faltantes]}],
            value_input_option="RAW",
        )
    _esquema_asegurado = True


# ---------------------------------------------------------------------------
# Lecturas crudas (migración / auditoría)
# ---------------------------------------------------------------------------
def _valores_a_df(values) -> pd.DataFrame:
    """Convierte la matriz cruda de una hoja (get_all_values) al DataFrame
    normalizado de la app (mismas 11 columnas, textos limpios, columnas
    derivadas backfilleadas en filas legacy)."""
    if not values:
        return pd.DataFrame({c: pd.Series(dtype=object) for c in COLUMNAS})

    header = [_normalizar_texto(c) for c in values[0]]
    rows = values[1:]
    if rows:
        ancho = len(header)
        rows = [r[:ancho] + [""] * max(0, ancho - len(r)) for r in rows]
        df = pd.DataFrame(rows, columns=header)
        con_contenido = df.apply(lambda c: c.astype(str).str.strip(), axis=0).ne("").any(axis=1)
        df = df[con_contenido]
    else:
        df = pd.DataFrame(columns=header)

    for col in COLUMNAS:
        if col not in df.columns:
            df[col] = ""
    df = df[COLUMNAS].copy()
    df = df.astype(object)
    for col in COLS_TEXTO:
        df[col] = _normalizar_serie(df[col])

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


def leer_registros_sheets() -> pd.DataFrame:
    """Hoja Registros completa, normalizada. Sin caché."""
    return _valores_a_df(_get_worksheet().get_all_values())


def leer_historico_sheets() -> pd.DataFrame:
    """Hoja Historico completa, normalizada. Vacío si no existe."""
    sh = _get_worksheet().spreadsheet
    try:
        hist = sh.worksheet(WORKSHEET_HISTORICO)
    except gspread.WorksheetNotFound:
        return pd.DataFrame({c: pd.Series(dtype=object) for c in COLUMNAS})
    return _valores_a_df(hist.get_all_values())


def leer_horas_esperadas_sheets() -> pd.DataFrame:
    """Hoja 'Horas Esperadas' cruda -> DataFrame [Año, Mes, Horas].
    Vacío si la hoja no existe o no tiene filas válidas."""
    meses_num = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
        "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
        "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    }
    sh = _get_worksheet().spreadsheet
    try:
        ws = sh.worksheet(WORKSHEET_HORAS_ESPERADAS)
    except gspread.WorksheetNotFound:
        return pd.DataFrame(columns=["Año", "Mes", "Horas"])
    values = ws.get_all_values()
    if len(values) < 2:
        return pd.DataFrame(columns=["Año", "Mes", "Horas"])

    header = [_normalizar_texto(c).lower() for c in values[0]]
    df = pd.DataFrame(values[1:], columns=header)

    df["Año"] = pd.to_numeric(df.get("año", pd.Series(dtype=object)), errors="coerce")
    df["Mes"] = df.get("mes", pd.Series(dtype=object)).astype(str).str.strip().str.lower().map(meses_num)
    horas_raw = df.get("horas", pd.Series(dtype=object)).astype(str).str.strip().replace("", pd.NA)
    df["Horas"] = pd.to_numeric(horas_raw, errors="coerce")

    df = df.dropna(subset=["Año", "Mes", "Horas"])
    df["Año"] = df["Año"].astype(int)
    df["Mes"] = df["Mes"].astype(int)
    return df[["Año", "Mes", "Horas"]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Escrituras espejo (best-effort; core.data captura las excepciones)
# ---------------------------------------------------------------------------
def espejo_append(fila: dict) -> None:
    """Agrega la fila al final de Registros (1 llamada API)."""
    ws = _get_worksheet()
    _asegurar_columnas_esquema()
    header = _get_header()
    ws.append_row(
        [fila.get(col, "") for col in header],
        value_input_option="USER_ENTERED",
        table_range="A1",
    )


def espejo_append_batch(filas: list) -> None:
    """Agrega varias filas al final de Registros (1 llamada API)."""
    if not filas:
        return
    ws = _get_worksheet()
    _asegurar_columnas_esquema()
    header = _get_header()
    ws.append_rows(
        [[fila.get(col, "") for col in header] for fila in filas],
        value_input_option="USER_ENTERED",
        table_range="A1",
    )


def _localizar_fila(all_values, header, nombre: str, ts_entrada_str: str):
    """Índice 1-based de la fila que matchea (Nombre, Timestamp Entrada), o None."""
    try:
        i_nombre = header.index("Nombre")
        i_entrada = header.index("Timestamp Entrada")
    except ValueError:
        return None
    nombre_norm = _normalizar_cmp(nombre)
    key = _ts_key(ts_entrada_str)
    for offset, row in enumerate(all_values[1:], start=2):
        if i_nombre >= len(row) or i_entrada >= len(row):
            continue
        if _normalizar_cmp(row[i_nombre]) != nombre_norm:
            continue
        if _ts_key(row[i_entrada]) == key:
            return offset
    return None


def espejo_actualizar(nombre: str, ts_entrada_str: str, cambios: dict) -> bool:
    """Actualiza en el Sheet las celdas indicadas de la fila que matchea
    (Nombre, Timestamp Entrada). False si la fila no existe en el respaldo."""
    ws = _get_worksheet()
    _asegurar_columnas_esquema()
    all_values = ws.get_all_values()
    if len(all_values) < 2:
        return False
    header = all_values[0]
    target_row = _localizar_fila(all_values, header, nombre, ts_entrada_str)
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


def espejo_actualizar_varios(cambios_por_entrada: list) -> int:
    """Aplica varios cambios en UNA lectura + UNA escritura batch.
    Cada elemento es (nombre, ts_entrada_str, cambios)."""
    if not cambios_por_entrada:
        return 0
    ws = _get_worksheet()
    _asegurar_columnas_esquema()
    all_values = ws.get_all_values()
    if len(all_values) < 2:
        return 0
    header = all_values[0]
    try:
        i_nombre = header.index("Nombre")
        i_entrada = header.index("Timestamp Entrada")
    except ValueError:
        return 0

    indice = {}
    for offset, row in enumerate(all_values[1:], start=2):
        if i_nombre >= len(row) or i_entrada >= len(row):
            continue
        indice.setdefault((_normalizar_cmp(row[i_nombre]), _ts_key(row[i_entrada])), offset)

    updates = []
    actualizados = 0
    for nombre, ts_entrada_str, cambios in cambios_por_entrada:
        target_row = indice.get((_normalizar_cmp(nombre), _ts_key(ts_entrada_str)))
        if target_row is None:
            continue
        aplico = False
        for col_name, val in cambios.items():
            if col_name not in header:
                continue
            col_idx = header.index(col_name) + 1
            a1 = gspread.utils.rowcol_to_a1(target_row, col_idx)
            updates.append({"range": a1, "values": [[val if val is not None else ""]]})
            aplico = True
        if aplico:
            actualizados += 1

    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")
    return actualizados


def espejo_eliminar(nombre: str, ts_entrada_str: str) -> bool:
    """Elimina del Sheet la fila que matchea (Nombre, Timestamp Entrada)."""
    ws = _get_worksheet()
    all_values = ws.get_all_values()
    if len(all_values) < 2:
        return False
    target_row = _localizar_fila(all_values, all_values[0], nombre, ts_entrada_str)
    if target_row is None:
        return False
    ws.delete_rows(target_row)
    return True


def _agrupar_contiguos(indices: list) -> list:
    """[2,3,4,7,8,10] -> [(2,4),(7,8),(10,10)]; minimiza llamadas delete_rows."""
    if not indices:
        return []
    indices = sorted(indices)
    rangos = []
    ini = prev = indices[0]
    for x in indices[1:]:
        if x == prev + 1:
            prev = x
        else:
            rangos.append((ini, prev))
            ini = prev = x
    rangos.append((ini, prev))
    return rangos


def _get_worksheet_historico():
    """Hoja Historico, creándola (con el mismo encabezado que Registros) si falta."""
    ws = _get_worksheet()
    sh = ws.spreadsheet
    try:
        return sh.worksheet(WORKSHEET_HISTORICO)
    except gspread.WorksheetNotFound:
        header = ws.row_values(1)
        hist = sh.add_worksheet(title=WORKSHEET_HISTORICO, rows=1, cols=max(len(header), 1))
        if header:
            hist.update([header], value_input_option="RAW")
        return hist


def espejo_archivar() -> int:
    """Mueve a Historico las filas 'Completo' con Fecha de Turno anterior al mes
    actual. Mantiene liviana la hoja Registros para que las actualizaciones
    espejo (que la leen completa) sigan siendo rápidas. Copia primero y borra
    después, por rangos contiguos de mayor a menor índice."""
    ws = _get_worksheet()
    all_values = ws.get_all_values()
    if len(all_values) < 2:
        return 0
    header = all_values[0]
    try:
        i_estado = header.index("Estado")
        i_fecha = header.index("Fecha de Turno")
    except ValueError:
        return 0

    inicio_mes = now_ecuador().date().replace(day=1)
    a_archivar = []
    for offset, row in enumerate(all_values[1:], start=2):
        if i_estado >= len(row) or _normalizar_cmp(row[i_estado]) != "completo":
            continue
        f = parse_fecha_flexible(row[i_fecha]) if i_fecha < len(row) else None
        if f is None or f >= inicio_mes:
            continue
        a_archivar.append((offset, row))

    if not a_archivar:
        return 0

    hist = _get_worksheet_historico()
    hist.append_rows([r for _, r in a_archivar], value_input_option="USER_ENTERED", table_range="A1")
    for start, end in sorted(_agrupar_contiguos([idx for idx, _ in a_archivar]), reverse=True):
        ws.delete_rows(start, end)
    return len(a_archivar)
