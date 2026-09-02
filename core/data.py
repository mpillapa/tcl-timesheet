"""Capa de datos sobre PostgreSQL (Supabase).

La base de datos es la fuente de verdad; Google Sheets queda como espejo de
respaldo best-effort (ver core.sheets_backup).

Los DataFrames devueltos mantienen las 11 columnas en español (COLUMNAS), dtype
object, timestamps como strings TS_FMT y celdas vacías como "", igual que
devolvía la lectura del Sheet, para no romper filtros ni estilos.

El "archivado" ya no hace falta por rendimiento (Postgres aguanta todo el
histórico); se conserva como flag para que el panel siga separando mes activo
de histórico.
"""

from datetime import date, datetime

import pandas as pd
import streamlit as st
from sqlalchemy import text

from core.config import COLUMNAS, TS_FMT
from core.db import get_engine, reintento_de_lectura
from core.time_utils import now_ecuador, parse_timestamp_flexible, parse_fecha_flexible
from core import sheets_backup

# Re-exports para mantener la API que ya importan marcado.py y las vistas.
from core.calculos import calcular_horas, calcular_horas_efectivas, calcular_horas_extra  # noqa: F401
from core.normalizacion import _normalizar_texto, _normalizar_cmp, _normalizar_serie  # noqa: F401


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

# Valores de `origen` que entiende la bitácora de auditoría. La lista completa,
# con el significado de cada uno, está en migracion/auditoria_schema.sql.
ORIGEN_ADMIN = "admin"
ORIGEN_COLABORADOR = "colaborador"
ORIGEN_SISTEMA = "sistema"


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
    """'' -> None; numérico/str -> float. Acepta coma decimal ('9,62'), que es
    como el Sheet en locale es-EC entregaba casi todas las horas legacy."""
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
    # fecha_turno es NOT NULL. El Estado vacío sí se preserva: hay filas legacy
    # con "Abierto" en Observaciones que buscar_turno_abierto_idx maneja aparte.
    if params["fecha_turno"] is None and params["ts_entrada"] is not None:
        params["fecha_turno"] = params["ts_entrada"].date()
    return params


def _df_desde_filas(rows) -> pd.DataFrame:
    """Filas SQL -> DataFrame en el formato legacy que espera la app."""
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


@reintento_de_lectura
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
    """Encola la réplica en Sheets para ejecutarla en segundo plano. En síncrono
    hacía esperar a cada marcación entre 0.8 y 2.7 s por Google."""
    if not sheets_backup.espejo_habilitado():
        return
    sheets_backup.encolar(func, *args)


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


@reintento_de_lectura
def _leer_horas_esperadas_bd():
    with get_engine().connect() as conn:
        return conn.execute(
            text("SELECT anio, mes, horas FROM horas_esperadas ORDER BY anio, mes")
        ).all()


@st.cache_data(ttl=300)
def leer_horas_esperadas() -> pd.DataFrame:
    """Tabla horas_esperadas -> DataFrame [Año (int), Mes (int), Horas (float)]."""
    try:
        rows = _leer_horas_esperadas_bd()
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
    """(SET sql, params), o (None, None) si ningún cambio toca una columna
    conocida."""
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


def _con_fecha_turno_derivada(cambios: dict) -> dict:
    """Regla de negocio: la Fecha de Turno es siempre la fecha de la entrada,
    también cuando el turno cruza la medianoche.

    Si una edición mueve el Timestamp Entrada sin fijar la Fecha de Turno, se
    recalcula aquí, para que la fecha por la que agrupan los gráficos no quede
    desfasada. Devuelve un dict nuevo, con la fecha como texto '%Y-%m-%d' para
    que el espejo en Sheets reciba el formato que ya usa esa columna."""
    if "Timestamp Entrada" not in cambios or "Fecha de Turno" in cambios:
        return cambios
    ts_ent = _a_ts(cambios["Timestamp Entrada"])
    if ts_ent is None:
        return cambios
    nuevo = dict(cambios)
    nuevo["Fecha de Turno"] = ts_ent.strftime("%Y-%m-%d")
    return nuevo


def _sellar_autor(conn, autor: str, origen: str) -> None:
    """Deja el autor del cambio en la sesión, para que el trigger de auditoría
    lo grabe en turnos_auditoria (ver migracion/auditoria_schema.sql).

    El tercer argumento de set_config en `true` lo hace local a la transacción,
    de modo que no se filtra a la siguiente petición que reutilice la misma
    conexión del pool. Si no se sella, el trigger registra el cambio con
    origen = 'fuera_de_la_app', que es la señal de una edición hecha por fuera
    de la aplicación.
    """
    conn.execute(
        text("select set_config('app.usuario', :u, true), "
             "       set_config('app.origen',  :o, true)"),
        {"u": _normalizar_texto(autor)[:120], "o": origen},
    )


def actualizar_por_entrada(nombre: str, ts_entrada_str: str, cambios: dict,
                           *, autor: str, origen: str) -> bool:
    """Actualiza solo las columnas de `cambios` en la fila (Nombre, Timestamp
    Entrada). False si la fila no existe. `cambios` puede traer un Timestamp
    Entrada nuevo: la fila se localiza por el original y la Fecha de Turno se
    recalcula sola (ver _con_fecha_turno_derivada).

    `autor` y `origen` son obligatorios y por nombre: van a la bitácora de
    auditoría, y se exigen para que un sitio de llamada nuevo falle en vez de
    registrar el cambio sin responsable.
    """
    ts = _a_ts(ts_entrada_str)
    if ts is None:
        return False
    cambios = _con_fecha_turno_derivada(cambios)
    set_sql, params = _armar_update(cambios)
    if set_sql is None:
        return False
    params["w_nombre"] = _normalizar_cmp(nombre)
    params["w_ts"] = ts
    with get_engine().begin() as conn:
        _sellar_autor(conn, autor, origen)
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


def actualizar_varios_por_entrada(cambios_por_entrada: list,
                                  *, autor: str, origen: str) -> int:
    """Aplica varios cambios en una sola transacción. Cada elemento es
    (nombre, ts_entrada_str, cambios). Devuelve cuántas filas se actualizaron.

    Todos los cambios del lote comparten autor, que es el caso real de uso: un
    barrido automático o una acción de un admin sobre varias filas."""
    if not cambios_por_entrada:
        return 0
    # Derivar la lista antes hace que el UPDATE y el espejo escriban lo mismo.
    cambios_por_entrada = [
        (nombre, ts_str, _con_fecha_turno_derivada(cambios))
        for nombre, ts_str, cambios in cambios_por_entrada
    ]
    actualizados = 0
    with get_engine().begin() as conn:
        # Una sola vez: es local a la transacción y cubre todo el lote.
        _sellar_autor(conn, autor, origen)
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


def eliminar_por_entrada(nombre: str, ts_entrada_str: str,
                         *, autor: str, origen: str) -> bool:
    """Elimina la fila que matchea (Nombre, Timestamp Entrada). Destructivo.

    La fila borrada queda completa en turnos_auditoria, que no tiene foreign
    key contra turnos justamente para sobrevivir a este caso."""
    ts = _a_ts(ts_entrada_str)
    if ts is None:
        return False
    with get_engine().begin() as conn:
        _sellar_autor(conn, autor, origen)
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
    mes calendario, no archiva y devuelve bloqueado=True (el backlog inicial se
    resuelve a mano). Devuelve {'archivadas', 'bloqueado', 'meses'}."""
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
            # El trigger de auditoría ignora los cambios que solo tocan
            # `archivado` y `actualizado_en`, así que este UPDATE en bloque no
            # ensucia la bitácora. Se sella igual, para que quede atribuido si
            # algún día la condición del trigger se amplía.
            _sellar_autor(conn, "archivado_mensual", ORIGEN_SISTEMA)
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


def buscar_turno_abierto_idx(df: pd.DataFrame, nombre: str):
    """Índice del turno abierto del empleado, o None.

    El fallback cubre las filas legacy con "Abierto" en Observaciones y Estado
    vacío, que dejó un desalineo de columnas ya corregido."""
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
