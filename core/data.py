"""Capa de datos sobre PostgreSQL (Supabase).

La base de datos es la fuente de verdad; Google Sheets queda como espejo de
respaldo best-effort (ver core.sheets_backup). Este módulo conserva la MISMA
API pública que la versión sobre Sheets, así que las vistas no cambian:

    leer_registros, leer_historico, leer_horas_esperadas,
    append_registro, append_registros_batch,
    actualizar_por_entrada, actualizar_varios_por_entrada,
    eliminar_por_entrada, archivar_historico,
    calcular_horas, calcular_horas_efectivas, calcular_horas_extra,
    buscar_turno_abierto_idx

Los DataFrames devueltos mantienen las 11 columnas en español (COLUMNAS),
dtype object, timestamps como strings TS_FMT y celdas vacías como "" — igual
que devolvía la lectura del Sheet, para no romper filtros ni estilos.

El "archivado" dejó de ser una necesidad de rendimiento (Postgres maneja sin
problema todo el histórico); se conserva como flag booleano para que el panel
siga separando mes activo vs histórico sin cambios.
"""

from datetime import date, datetime

import pandas as pd
import streamlit as st
from sqlalchemy import text

from core.config import COLUMNAS, TS_FMT
from core.db import get_engine
from core.time_utils import now_ecuador, parse_timestamp_flexible, parse_fecha_flexible
from core import sheets_backup

# Re-exports para mantener la API que ya importan marcado.py y las vistas.
from core.calculos import calcular_horas, calcular_horas_efectivas, calcular_horas_extra  # noqa: F401
from core.normalizacion import _normalizar_texto, _normalizar_cmp, _normalizar_serie  # noqa: F401


# Columna de la app (español) -> columna SQL de la tabla turnos.
_COL_SQL = {
    "Nombre": "nombre",
    "Area": "area",
    "Fecha de Turno": "fecha_turno",
    "Timestamp Entrada": "ts_entrada",
    "Timestamp Salida": "ts_salida",
    "Horas Trabajadas": "horas_trabajadas",
    "Horas Efectivas": "horas_efectivas",
    "Horas Extra": "horas_extra",
    "Estado": "estado",
    "Evento": "evento",
    "Observaciones": "observaciones",
}
_COLS_TS = {"Timestamp Entrada", "Timestamp Salida"}
_COLS_NUM = {"Horas Trabajadas", "Horas Efectivas", "Horas Extra"}
_COLS_FECHA = {"Fecha de Turno"}


# ---------------------------------------------------------------------------
# Conversión de valores app <-> SQL
# ---------------------------------------------------------------------------
def _a_ts(val):
    """'' -> None; str/datetime -> datetime naive (hora local Ecuador)."""
    if val is None or isinstance(val, datetime):
        return val
    s = _normalizar_texto(val)
    if not s:
        return None
    return parse_timestamp_flexible(s)


def _a_fecha(val):
    """'' -> None; str/date -> date."""
    if val is None or isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    s = _normalizar_texto(val)
    if not s:
        return None
    return parse_fecha_flexible(s)


def _a_num(val):
    """'' -> None; numérico/str -> float.

    Acepta coma decimal ('9,62'): el Sheet en locale es-EC entregaba así casi
    todas las horas legacy, y sin esto se migrarían/guardarían como nulas."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        pass
    try:
        return float(str(val).strip().replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def _valor_sql(col_app: str, val):
    """Convierte un valor de la app al tipo que espera la columna SQL."""
    if col_app in _COLS_TS:
        return _a_ts(val)
    if col_app in _COLS_FECHA:
        return _a_fecha(val)
    if col_app in _COLS_NUM:
        return _a_num(val)
    return _normalizar_texto(val)


def _fila_a_params(fila: dict) -> dict:
    """Dict de la app (claves en español) -> params SQL para INSERT."""
    params = {sql_col: _valor_sql(app_col, fila.get(app_col, "")) for app_col, sql_col in _COL_SQL.items()}
    # Red de seguridad: fecha_turno es NOT NULL en la tabla. Estado vacío se
    # preserva tal cual ('' cumple NOT NULL): hay filas legacy con Estado vacío
    # y "Abierto" en Observaciones que buscar_turno_abierto_idx maneja aparte.
    if params["fecha_turno"] is None and params["ts_entrada"] is not None:
        params["fecha_turno"] = params["ts_entrada"].date()
    return params


def _df_desde_filas(rows) -> pd.DataFrame:
    """Filas SQL -> DataFrame con el formato legacy exacto de la app:
    columnas COLUMNAS, dtype object, '' para nulos, timestamps TS_FMT."""
    datos = {c: [] for c in COLUMNAS}
    for r in rows:
        for app_col, sql_col in _COL_SQL.items():
            v = r[sql_col]
            if v is None:
                datos[app_col].append("")
            elif app_col in _COLS_TS:
                datos[app_col].append(v.strftime(TS_FMT))
            elif app_col in _COLS_FECHA:
                datos[app_col].append(v.strftime("%Y-%m-%d"))
            elif app_col in _COLS_NUM:
                datos[app_col].append(float(v))
            else:
                datos[app_col].append(str(v))
    df = pd.DataFrame(datos, columns=COLUMNAS)
    return df.astype(object)


_SELECT_TURNOS = (
    "SELECT nombre, area, fecha_turno, ts_entrada, ts_salida, "
    "horas_trabajadas, horas_efectivas, horas_extra, estado, evento, observaciones "
    "FROM turnos WHERE archivado = :arch ORDER BY ts_entrada, id"
)


def _leer_turnos(archivado: bool) -> pd.DataFrame:
    with get_engine().connect() as conn:
        rows = conn.execute(text(_SELECT_TURNOS), {"arch": archivado}).mappings().all()
    return _df_desde_filas(rows)


def _df_vacio() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=object) for c in COLUMNAS})


# ---------------------------------------------------------------------------
# Espejo de respaldo (best-effort: nunca bloquea la operación principal)
# ---------------------------------------------------------------------------
def _espejo(func, *args) -> None:
    if not sheets_backup.espejo_habilitado():
        return
    try:
        func(*args)
    except Exception as e:
        st.warning(
            "El dato quedó guardado en la base de datos, pero el respaldo en "
            f"Google Sheets falló ({type(e).__name__}). Puedes re-sincronizar "
            "el respaldo más tarde; la operación fue exitosa."
        )


# ---------------------------------------------------------------------------
# Lecturas
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300)
def leer_registros() -> pd.DataFrame:
    """Turnos activos (no archivados), en orden cronológico de entrada."""
    try:
        return _leer_turnos(archivado=False)
    except Exception as e:
        st.error(f"Error leyendo la base de datos: {type(e).__name__}: {e}")
        return _df_vacio()


@st.cache_data(ttl=300)
def leer_historico() -> pd.DataFrame:
    """Turnos archivados (meses cerrados)."""
    try:
        return _leer_turnos(archivado=True)
    except Exception as e:
        st.error(f"Error leyendo histórico: {type(e).__name__}: {e}")
        return _df_vacio()


@st.cache_data(ttl=300)
def leer_horas_esperadas() -> pd.DataFrame:
    """Tabla horas_esperadas -> DataFrame [Año (int), Mes (int), Horas (float)]."""
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(
                text("SELECT anio, mes, horas FROM horas_esperadas ORDER BY anio, mes")
            ).all()
        return pd.DataFrame(
            {"Año": [int(r[0]) for r in rows],
             "Mes": [int(r[1]) for r in rows],
             "Horas": [float(r[2]) for r in rows]}
        )
    except Exception as e:
        st.warning(f"No se pudo leer horas esperadas: {type(e).__name__}: {e}")
        return pd.DataFrame(columns=["Año", "Mes", "Horas"])


# ---------------------------------------------------------------------------
# Escrituras
# ---------------------------------------------------------------------------
_INSERT_TURNO = text(
    "INSERT INTO turnos (nombre, area, fecha_turno, ts_entrada, ts_salida, "
    "horas_trabajadas, horas_efectivas, horas_extra, estado, evento, observaciones) "
    "VALUES (:nombre, :area, :fecha_turno, :ts_entrada, :ts_salida, "
    ":horas_trabajadas, :horas_efectivas, :horas_extra, :estado, :evento, :observaciones) "
    "ON CONFLICT ((lower(nombre)), ts_entrada) DO NOTHING"
)


def append_registro(fila: dict) -> None:
    """Inserta un turno. El índice único (nombre, ts_entrada) hace que un
    doble-click que intente duplicar el mismo turno simplemente no inserte."""
    params = _fila_a_params(fila)
    if params["ts_entrada"] is None:
        st.error("No se pudo interpretar el Timestamp Entrada del registro; no se guardó.")
        return
    with get_engine().begin() as conn:
        conn.execute(_INSERT_TURNO, params)
    leer_registros.clear()
    _espejo(sheets_backup.espejo_append, fila)


def append_registros_batch(filas: list) -> int:
    """Inserta varios turnos en una sola transacción. Devuelve cuántos se
    intentaron insertar (los duplicados exactos se omiten silenciosamente)."""
    if not filas:
        return 0
    lote = []
    for fila in filas:
        params = _fila_a_params(fila)
        if params["ts_entrada"] is None:
            continue
        lote.append(params)
    if not lote:
        return 0
    with get_engine().begin() as conn:
        conn.execute(_INSERT_TURNO, lote)
    leer_registros.clear()
    _espejo(sheets_backup.espejo_append_batch, filas)
    return len(lote)


def _armar_update(cambios: dict):
    """(SET sql, params) a partir de un dict de cambios con claves en español.
    Devuelve (None, None) si ningún cambio aplica a columnas conocidas."""
    sets = []
    params = {}
    for i, (col_app, val) in enumerate(cambios.items()):
        sql_col = _COL_SQL.get(col_app)
        if sql_col is None:
            continue
        pname = f"v{i}"
        sets.append(f"{sql_col} = :{pname}")
        params[pname] = _valor_sql(col_app, val)
    if not sets:
        return None, None
    sets.append("actualizado_en = now()")
    return ", ".join(sets), params


def actualizar_por_entrada(nombre: str, ts_entrada_str: str, cambios: dict) -> bool:
    """Actualiza SOLO las columnas indicadas en `cambios` de la fila que matchea
    (Nombre, Timestamp Entrada). Devuelve False si la fila no existe.
    `cambios` puede incluir un nuevo Timestamp Entrada (edición de turnos):
    la fila se localiza por el timestamp ORIGINAL."""
    ts = _a_ts(ts_entrada_str)
    if ts is None:
        return False
    set_sql, params = _armar_update(cambios)
    if set_sql is None:
        return False
    params["w_nombre"] = _normalizar_cmp(nombre)
    params["w_ts"] = ts
    with get_engine().begin() as conn:
        res = conn.execute(
            text(f"UPDATE turnos SET {set_sql} "
                 "WHERE lower(nombre) = :w_nombre AND ts_entrada = :w_ts"),
            params,
        )
    if res.rowcount == 0:
        return False
    leer_registros.clear()
    leer_historico.clear()
    _espejo(sheets_backup.espejo_actualizar, nombre, ts_entrada_str, cambios)
    return True


def actualizar_varios_por_entrada(cambios_por_entrada: list) -> int:
    """Aplica varios cambios en UNA transacción. Cada elemento es
    (nombre, ts_entrada_str, cambios). Devuelve cuántas filas se actualizaron."""
    if not cambios_por_entrada:
        return 0
    actualizados = 0
    with get_engine().begin() as conn:
        for nombre, ts_entrada_str, cambios in cambios_por_entrada:
            ts = _a_ts(ts_entrada_str)
            if ts is None:
                continue
            set_sql, params = _armar_update(cambios)
            if set_sql is None:
                continue
            params["w_nombre"] = _normalizar_cmp(nombre)
            params["w_ts"] = ts
            res = conn.execute(
                text(f"UPDATE turnos SET {set_sql} "
                     "WHERE lower(nombre) = :w_nombre AND ts_entrada = :w_ts"),
                params,
            )
            actualizados += res.rowcount
    if actualizados:
        leer_registros.clear()
        leer_historico.clear()
        _espejo(sheets_backup.espejo_actualizar_varios, cambios_por_entrada)
    return actualizados


def eliminar_por_entrada(nombre: str, ts_entrada_str: str) -> bool:
    """Elimina la fila que matchea (Nombre, Timestamp Entrada). Destructivo."""
    ts = _a_ts(ts_entrada_str)
    if ts is None:
        return False
    with get_engine().begin() as conn:
        res = conn.execute(
            text("DELETE FROM turnos WHERE lower(nombre) = :n AND ts_entrada = :t"),
            {"n": _normalizar_cmp(nombre), "t": ts},
        )
    if res.rowcount == 0:
        return False
    leer_registros.clear()
    leer_historico.clear()
    _espejo(sheets_backup.espejo_eliminar, nombre, ts_entrada_str)
    return True


def archivar_historico(solo_un_mes: bool) -> dict:
    """Marca como archivados los turnos 'Completo' con Fecha de Turno anterior
    al primer día del mes actual. 'Abierto'/'Revision' nunca se archivan.

    Si `solo_un_mes` es True (uso automático) y lo pendiente abarca más de un
    mes calendario, NO archiva y devuelve bloqueado=True (backlog inicial se
    resuelve manualmente). Devuelve {'archivadas', 'bloqueado', 'meses'}."""
    inicio_mes = now_ecuador().date().replace(day=1)
    try:
        with get_engine().connect() as conn:
            fechas = conn.execute(
                text("SELECT fecha_turno FROM turnos "
                     "WHERE archivado = false AND lower(estado) = 'completo' "
                     "AND fecha_turno < :corte"),
                {"corte": inicio_mes},
            ).scalars().all()
        if not fechas:
            return {"archivadas": 0, "bloqueado": False, "meses": []}
        meses = sorted({(f.year, f.month) for f in fechas})
        if solo_un_mes and len(meses) > 1:
            return {"archivadas": 0, "bloqueado": True, "meses": meses}

        with get_engine().begin() as conn:
            res = conn.execute(
                text("UPDATE turnos SET archivado = true, actualizado_en = now() "
                     "WHERE archivado = false AND lower(estado) = 'completo' "
                     "AND fecha_turno < :corte"),
                {"corte": inicio_mes},
            )
        leer_registros.clear()
        leer_historico.clear()
        _espejo(sheets_backup.espejo_archivar)
        return {"archivadas": res.rowcount, "bloqueado": False, "meses": meses}
    except Exception as e:
        st.error(f"Error archivando históricos: {type(e).__name__}: {e}")
        return {"archivadas": 0, "bloqueado": False, "meses": []}


# ---------------------------------------------------------------------------
# Utilidades sobre DataFrames (sin cambios respecto a la versión Sheets)
# ---------------------------------------------------------------------------
def buscar_turno_abierto_idx(df: pd.DataFrame, nombre: str):
    """Devuelve el índice del turno abierto del empleado, o None.

    Incluye un fallback para filas legacy que quedaron con "Abierto" en la
    columna Observaciones y Estado vacío, producto de un bug histórico de
    desalineo de columnas (ya corregido)."""
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
