"""Capa de datos sobre PostgreSQL (Supabase).

La base de datos es la fuente de verdad; Google Sheets queda como espejo de
respaldo best-effort (ver core.sheets_backup).

Los DataFrames devueltos mantienen las 11 columnas en español (COLUMNAS), dtype
object, timestamps como strings TS_FMT y celdas vacías como "", igual que
devolvía la lectura del Sheet, para no romper filtros ni estilos.

El "archivado" ya no hace falta por rendimiento (Postgres aguanta todo el
histórico); se conserva como flag para que el panel siga separando mes activo
de histórico.

Desde las migraciones de migracion/versiones/
---------------------------------------------
- Un turno no se borra: se anula, y los anulados no aparecen en las lecturas.
- Corregir el horario de un turno cerrado o anularlo es un ajuste
  (solicitar_correccion, solicitar_anulacion). Los grandes esperan la
  aprobación de otro administrador.
- La base calcula las horas y valida cada turno. Si rechaza algo por una regla
  de negocio, aquí se convierte en ReglaDeNegocio con un mensaje para el
  usuario; quien llama decide cómo mostrarlo.
- Toda escritura se sella con autor, origen y, si es de un administrador,
  motivo. La bitácora y las marcaciones los toman de ahí.
"""

import re
from contextlib import contextmanager
from datetime import date, datetime

import pandas as pd
import streamlit as st
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

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
# Errores de regla de negocio
# ---------------------------------------------------------------------------
class ReglaDeNegocio(Exception):
    """La base rechazó la operación por una regla de negocio.

    El mensaje está escrito para el usuario final y se puede mostrar tal cual.
    Las funciones de la base lo marcan con el prefijo 'TURNERO:' (ver
    migracion/versiones/V001__reglas_de_negocio.sql, fn_solo_insercion)."""


_PATRON_REGLA = re.compile(r"TURNERO:\s*(.+?)\s*(?:\n|$)")


@contextmanager
def _traducir_errores():
    """Convierte el rechazo de una regla de la base en ReglaDeNegocio. Los
    demás errores siguen su curso: no son algo que el usuario pueda arreglar."""
    try:
        yield
    except DBAPIError as e:
        m = _PATRON_REGLA.search(str(getattr(e, "orig", e)))
        if m:
            raise ReglaDeNegocio(m.group(1)) from None
        raise


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
    # fecha_turno es NOT NULL. La base la recalcula desde la entrada de todos
    # modos (V004); esto solo evita mandar un NULL.
    if params["fecha_turno"] is None and params["ts_entrada"] is not None:
        params["fecha_turno"] = params["ts_entrada"].date()
    return params


def _a_local_naive(v):
    """Las columnas con zona (V102) llegan como datetime con tzinfo, en la zona
    de la sesión, que core/db.py fija en Ecuador. La aplicación trabaja sin
    zona: se descarta el tzinfo sin mover la hora."""
    if isinstance(v, datetime) and v.tzinfo is not None:
        return v.replace(tzinfo=None)
    return v


def _df_desde_filas(rows) -> pd.DataFrame:
    """Filas SQL -> DataFrame en el formato legacy que espera la app."""
    datos = {c: [] for c in COLUMNAS}
    for r in rows:
        for app_col, sql_col in _COL_SQL.items():
            v = r[sql_col]
            if v is None:
                datos[app_col].append("")
            elif app_col in _COLS_TS:
                datos[app_col].append(_a_local_naive(v).strftime(TS_FMT))
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
    "FROM turnos WHERE archivado = :arch AND NOT anulado ORDER BY ts_entrada, id"
)


@reintento_de_lectura
def _leer_turnos(archivado: bool) -> pd.DataFrame:
    with get_engine().connect() as conn:
        rows = conn.execute(text(_SELECT_TURNOS), {"arch": archivado}).mappings().all()
    return _df_desde_filas(rows)


def _df_vacio() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=object) for c in COLUMNAS})


def _limpiar_caches() -> None:
    leer_registros.clear()
    leer_historico.clear()


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
    """Turnos activos (no archivados, no anulados), en orden cronológico."""
    try:
        return _leer_turnos(archivado=False)
    except Exception as e:
        st.error(f"Error leyendo la base de datos: {type(e).__name__}: {e}")
        return _df_vacio()


@st.cache_data(ttl=300)
def leer_historico() -> pd.DataFrame:
    """Turnos archivados (meses cerrados), sin los anulados."""
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
# El WHERE NOT anulado apunta al índice único parcial de V002, que permite
# rehacer con la misma entrada un turno anulado. Mientras exista el índice viejo
# (hasta V101), Postgres usa los dos como árbitros, y el resultado es el mismo.
_INSERT_TURNO = text(
    "INSERT INTO turnos (nombre, area, fecha_turno, ts_entrada, ts_salida, "
    "horas_trabajadas, horas_efectivas, horas_extra, estado, evento, observaciones) "
    "VALUES (:nombre, :area, :fecha_turno, :ts_entrada, :ts_salida, "
    ":horas_trabajadas, :horas_efectivas, :horas_extra, :estado, :evento, :observaciones) "
    "ON CONFLICT ((lower(nombre)), ts_entrada) WHERE NOT anulado DO NOTHING"
)


def _sellar_autor(conn, autor: str, origen: str, motivo: str = "") -> None:
    """Deja autor, origen y motivo en la sesión, para que los triggers los
    graben en la bitácora y en las marcaciones.

    El tercer argumento de set_config en `true` lo hace local a la transacción,
    de modo que no se filtra a la siguiente petición que reutilice la misma
    conexión del pool. Si no se sella, el trigger registra el cambio con
    origen = 'fuera_de_la_app', que es la señal de una edición hecha por fuera
    de la aplicación.

    El motivo es obligatorio para un administrador desde V101: la base rechaza
    su escritura si falta.
    """
    conn.execute(
        text("select set_config('app.usuario', :u, true), "
             "       set_config('app.origen',  :o, true), "
             "       set_config('app.motivo',  :m, true)"),
        {"u": _normalizar_texto(autor)[:120], "o": origen, "m": _normalizar_texto(motivo)[:1000]},
    )


def append_registro(fila: dict, *, autor: str, origen: str, motivo: str = "") -> bool:
    """Inserta un turno. True si se insertó.

    El índice único (nombre, entrada) hace que un doble clic que intente
    duplicar el mismo turno simplemente no inserte, y eso devuelve False.
    Una regla de la base que rechace el turno levanta ReglaDeNegocio."""
    params = _fila_a_params(fila)
    if params["ts_entrada"] is None:
        raise ReglaDeNegocio("No se pudo interpretar la hora de entrada; no se guardó el turno.")
    with _traducir_errores(), get_engine().begin() as conn:
        _sellar_autor(conn, autor, origen, motivo)
        res = conn.execute(_INSERT_TURNO, params)
    _limpiar_caches()
    if res.rowcount:
        _espejo(sheets_backup.espejo_append, fila)
    return bool(res.rowcount)


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

    Desde V004 la base la impone por su cuenta. Se conserva aquí para que el
    espejo en Sheets reciba la misma fecha que la base."""
    if "Timestamp Entrada" not in cambios or "Fecha de Turno" in cambios:
        return cambios
    ts_ent = _a_ts(cambios["Timestamp Entrada"])
    if ts_ent is None:
        return cambios
    nuevo = dict(cambios)
    nuevo["Fecha de Turno"] = ts_ent.strftime("%Y-%m-%d")
    return nuevo


_WHERE_POR_ENTRADA = "WHERE lower(nombre) = :w_nombre AND ts_entrada = :w_ts AND NOT anulado"


def actualizar_por_entrada(nombre: str, ts_entrada_str: str, cambios: dict,
                           *, autor: str, origen: str, motivo: str = "") -> bool:
    """Actualiza solo las columnas de `cambios` en la fila (Nombre, Timestamp
    Entrada). False si la fila no existe.

    Sirve para la marcación, el envío a revisión y el cierre de un turno en
    revisión. Para corregir el horario de un turno ya cerrado se usa
    solicitar_correccion: desde V101 la base rechaza hacerlo por aquí.

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
    with _traducir_errores(), get_engine().begin() as conn:
        _sellar_autor(conn, autor, origen, motivo)
        res = conn.execute(text(f"UPDATE turnos SET {set_sql} {_WHERE_POR_ENTRADA}"), params)
    if res.rowcount == 0:
        return False
    _limpiar_caches()
    _espejo(sheets_backup.espejo_actualizar, nombre, ts_entrada_str, cambios)
    return True


def actualizar_varios_por_entrada(cambios_por_entrada: list,
                                  *, autor: str, origen: str, motivo: str = "") -> int:
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
    with _traducir_errores(), get_engine().begin() as conn:
        # Una sola vez: es local a la transacción y cubre todo el lote.
        _sellar_autor(conn, autor, origen, motivo)
        for nombre, ts_entrada_str, cambios in cambios_por_entrada:
            ts = _a_ts(ts_entrada_str)
            if ts is None:
                continue
            set_sql, params = _armar_update(cambios)
            if set_sql is None:
                continue
            params["w_nombre"] = _normalizar_cmp(nombre)
            params["w_ts"] = ts
            res = conn.execute(text(f"UPDATE turnos SET {set_sql} {_WHERE_POR_ENTRADA}"), params)
            actualizados += res.rowcount
    if actualizados:
        _limpiar_caches()
        _espejo(sheets_backup.espejo_actualizar_varios, cambios_por_entrada)
    return actualizados


def archivar_historico(solo_un_mes: bool) -> dict:
    """Marca como archivados los turnos 'Completo' con Fecha de Turno anterior
    al primer día del mes actual. 'Abierto'/'Revision' nunca se archivan.

    Si `solo_un_mes` es True (uso automático) y lo pendiente abarca más de un
    mes calendario, no archiva y devuelve bloqueado=True (el backlog inicial se
    resuelve a mano). Devuelve {'archivadas', 'bloqueado', 'meses'}.

    Archivar no es cerrar el periodo: solo separa el histórico en el panel.
    Cerrar un mes, para que no admita más cambios, es cerrar_periodo."""
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
            # `archivado` y `actualizado_en`, y el de periodos los deja pasar
            # aunque el mes esté cerrado. Se sella igual, para que quede
            # atribuido si algún día la condición del trigger se amplía.
            _sellar_autor(conn, "archivado_mensual", ORIGEN_SISTEMA)
            res = conn.execute(
                text("UPDATE turnos SET archivado = true, actualizado_en = now() "
                     "WHERE archivado = false AND lower(estado) = 'completo' "
                     "AND fecha_turno < :corte"),
                {"corte": inicio_mes},
            )
        _limpiar_caches()
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


# ---------------------------------------------------------------------------
# Ajustes: corrección y anulación con motivo y, si corresponde, aprobación
# ---------------------------------------------------------------------------
# La regla que decide si un ajuste necesita aprobación vive en la base
# (V009, regla 'aprobacion_desde_recorte_extra_h'). Aquí no se replica: la
# respuesta de la base dice si quedó aplicado o pendiente.

def _id_por_entrada(conn, nombre: str, ts) -> int | None:
    return conn.execute(
        text(f"SELECT id FROM turnos {_WHERE_POR_ENTRADA}"),
        {"w_nombre": _normalizar_cmp(nombre), "w_ts": ts},
    ).scalar()


def _resultado_ajuste(fila) -> dict:
    return {"id": fila["id"], "estado": fila["estado"],
            "requiere_aprobacion": bool(fila["requiere_aprobacion"]),
            "delta_extra": float(fila["delta_extra"])}


def solicitar_correccion(nombre: str, ts_entrada_str: str, nueva_entrada: datetime,
                         nueva_salida: datetime, observacion: str, *, motivo: str,
                         autor: str) -> dict | None:
    """Solicita corregir el horario de un turno cerrado.

    Devuelve {'id', 'estado', 'requiere_aprobacion', 'delta_extra'}; estado es
    'aplicado' si la corrección se hizo en el acto o 'pendiente' si espera a
    otro administrador. None si el turno ya no existe. Levanta ReglaDeNegocio
    si la base la rechaza (motivo corto, periodo cerrado, duración imposible)."""
    ts = _a_ts(ts_entrada_str)
    if ts is None:
        return None
    with _traducir_errores(), get_engine().begin() as conn:
        tid = _id_por_entrada(conn, nombre, ts)
        if tid is None:
            return None
        fila = conn.execute(
            text("SELECT id, estado, requiere_aprobacion, delta_extra "
                 "FROM solicitar_correccion(:id, :e, :s, :o, :m, :u)"),
            {"id": tid, "e": nueva_entrada, "s": nueva_salida, "o": observacion,
             "m": motivo, "u": autor},
        ).mappings().one()
    resultado = _resultado_ajuste(fila)
    if resultado["estado"] == "aplicado":
        _limpiar_caches()
        _espejo(sheets_backup.espejo_actualizar, nombre, ts_entrada_str, {
            "Timestamp Entrada": nueva_entrada.strftime(TS_FMT),
            "Timestamp Salida": nueva_salida.strftime(TS_FMT),
            "Observaciones": observacion,
        })
    return resultado


def solicitar_anulacion(nombre: str, ts_entrada_str: str, *, motivo: str, autor: str) -> dict | None:
    """Solicita anular un turno. Mismo contrato que solicitar_correccion."""
    ts = _a_ts(ts_entrada_str)
    if ts is None:
        return None
    with _traducir_errores(), get_engine().begin() as conn:
        tid = _id_por_entrada(conn, nombre, ts)
        if tid is None:
            return None
        fila = conn.execute(
            text("SELECT id, estado, requiere_aprobacion, delta_extra "
                 "FROM solicitar_anulacion(:id, :m, :u)"),
            {"id": tid, "m": motivo, "u": autor},
        ).mappings().one()
    resultado = _resultado_ajuste(fila)
    if resultado["estado"] == "aplicado":
        _limpiar_caches()
        _espejo(sheets_backup.espejo_eliminar, nombre, ts_entrada_str)
    return resultado


def aprobar_ajuste(ajuste_id: int, *, usuario: str, comentario: str = "") -> None:
    with _traducir_errores(), get_engine().begin() as conn:
        conn.execute(text("SELECT aprobar_ajuste(:id, :u, :c)"),
                     {"id": int(ajuste_id), "u": usuario, "c": comentario})
    _limpiar_caches()
    leer_ajustes.clear()


def rechazar_ajuste(ajuste_id: int, *, usuario: str, comentario: str) -> None:
    with _traducir_errores(), get_engine().begin() as conn:
        conn.execute(text("SELECT rechazar_ajuste(:id, :u, :c)"),
                     {"id": int(ajuste_id), "u": usuario, "c": comentario})
    leer_ajustes.clear()


# Los horarios de `antes` y `propuesto` son texto dentro de un jsonb. El de
# `antes` lleva zona desde V102 y el de `propuesto` no; pasarlos por
# timestamptz y de vuelta a la hora de Ecuador los deja iguales en los dos casos.
_HORA_JSON = "to_char(({c})::timestamptz AT TIME ZONE 'America/Guayaquil', 'YYYY-MM-DD HH24:MI')"

_SELECT_AJUSTES = f"""
SELECT a.id, t.nombre, t.area, t.fecha_turno, a.tipo, a.estado, a.motivo,
       a.solicitado_por, a.solicitado_en AT TIME ZONE 'America/Guayaquil' AS solicitado_en,
       {_HORA_JSON.format(c="a.antes ->> 'ts_entrada'")}     AS entrada_antes,
       {_HORA_JSON.format(c="a.antes ->> 'ts_salida'")}      AS salida_antes,
       {_HORA_JSON.format(c="a.propuesto ->> 'ts_entrada'")} AS entrada_propuesta,
       {_HORA_JSON.format(c="a.propuesto ->> 'ts_salida'")}  AS salida_propuesta,
       (a.antes ->> 'horas_trabajadas')::numeric AS horas_antes,
       a.delta_horas, a.delta_extra, a.requiere_aprobacion,
       a.revisado_por, a.revisado_en AT TIME ZONE 'America/Guayaquil' AS revisado_en,
       a.comentario_revision
FROM ajustes_turno a
JOIN turnos t ON t.id = a.turno_id
WHERE (:estado = '' OR a.estado = :estado)
  AND a.solicitado_en >= :desde
ORDER BY a.solicitado_en DESC
"""


@st.cache_data(ttl=60)
def leer_ajustes(estado: str = "", desde: date | None = None) -> pd.DataFrame:
    """Ajustes solicitados, del más reciente al más viejo. `estado` vacío trae
    todos. Sin `desde`, los de los últimos 90 días."""
    if desde is None:
        desde = (pd.Timestamp(now_ecuador().date()) - pd.Timedelta(days=90)).date()
    with get_engine().connect() as conn:
        rows = conn.execute(text(_SELECT_AJUSTES), {"estado": estado, "desde": desde}).mappings().all()
    return pd.DataFrame([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Periodos de nómina
# ---------------------------------------------------------------------------
@st.cache_data(ttl=60)
def leer_periodos() -> pd.DataFrame:
    """Un renglón por cada mes con turnos, con su estado de cierre y el último
    movimiento. Un mes sin fila en periodos_nomina está abierto."""
    sql = """
    WITH meses AS (
        SELECT DISTINCT extract(year from fecha_turno)::int AS anio,
                        extract(month from fecha_turno)::int AS mes
        FROM turnos WHERE NOT anulado
    )
    SELECT m.anio, m.mes, coalesce(p.estado, 'abierto') AS estado,
           p.cambiado_por, p.cambiado_en AT TIME ZONE 'America/Guayaquil' AS cambiado_en,
           (SELECT count(*) FROM periodos_eventos e
             WHERE e.anio = m.anio AND e.mes = m.mes AND e.accion = 'reapertura') AS reaperturas
    FROM meses m
    LEFT JOIN periodos_nomina p ON p.anio = m.anio AND p.mes = m.mes
    ORDER BY m.anio DESC, m.mes DESC
    """
    with get_engine().connect() as conn:
        rows = conn.execute(text(sql)).mappings().all()
    return pd.DataFrame([dict(r) for r in rows])


def cerrar_periodo(anio: int, mes: int, *, usuario: str, motivo: str = "") -> None:
    with _traducir_errores(), get_engine().begin() as conn:
        conn.execute(text("SELECT cerrar_periodo(:a, :m, :u, :mot)"),
                     {"a": int(anio), "m": int(mes), "u": usuario, "mot": motivo})
    leer_periodos.clear()


def reabrir_periodo(anio: int, mes: int, *, usuario: str, motivo: str) -> None:
    with _traducir_errores(), get_engine().begin() as conn:
        conn.execute(text("SELECT reabrir_periodo(:a, :m, :u, :mot)"),
                     {"a": int(anio), "m": int(mes), "u": usuario, "mot": motivo})
    leer_periodos.clear()


# ---------------------------------------------------------------------------
# Ausencias (eventualidades)
# ---------------------------------------------------------------------------
_INSERT_TURNO_AUSENCIA = text(
    "INSERT INTO turnos (nombre, area, fecha_turno, ts_entrada, ts_salida, "
    "horas_trabajadas, horas_efectivas, horas_extra, estado, evento, observaciones, ausencia_id) "
    "VALUES (:nombre, :area, :fecha_turno, :ts_entrada, :ts_salida, "
    ":horas_trabajadas, :horas_efectivas, :horas_extra, :estado, :evento, :observaciones, :ausencia_id) "
    "ON CONFLICT ((lower(nombre)), ts_entrada) WHERE NOT anulado DO NOTHING"
)


def registrar_ausencia(nombre: str, area: str, tipo: str, subtipo: str, filas: list,
                       *, autor: str, motivo: str) -> int:
    """Registra una eventualidad: la solicitud en `ausencias` y un turno por
    día en `turnos`, atados por ausencia_id, en una sola transacción.

    `filas` son los turnos ya armados por el panel, con las claves en español.
    Devuelve cuántos días se insertaron; los que ya existían se omiten."""
    lote = [p for p in (_fila_a_params(f) for f in filas) if p["ts_entrada"] is not None]
    if not lote:
        return 0
    fechas = [p["fecha_turno"] for p in lote]
    with _traducir_errores(), get_engine().begin() as conn:
        _sellar_autor(conn, autor, ORIGEN_ADMIN, motivo)
        ausencia_id = conn.execute(
            text("INSERT INTO ausencias (empleado_id, nombre, tipo, subtipo, desde, hasta, registrado_por) "
                 "VALUES ((SELECT id FROM empleados WHERE lower(nombre) = lower(:n)), "
                 "        :n, :t, :s, :d, :h, :u) RETURNING id"),
            {"n": nombre, "t": tipo, "s": subtipo or "", "d": min(fechas), "h": max(fechas), "u": autor},
        ).scalar()
        insertados = 0
        for p in lote:
            res = conn.execute(_INSERT_TURNO_AUSENCIA, dict(p, evento=tipo, ausencia_id=ausencia_id))
            insertados += res.rowcount
    _limpiar_caches()
    if insertados:
        _espejo(sheets_backup.espejo_append_batch, filas)
    return insertados


# ---------------------------------------------------------------------------
# Auditoría (lectura para la pestaña del panel)
# ---------------------------------------------------------------------------
# Turnos cuyo horario vigente no coincide con lo que se marcó en el reloj. Es
# la consulta que responde "a quién le cambiaron las horas": la diferencia
# entre `marcaciones` (inmutable) y `turnos` (vigente). El umbral de 2 minutos
# absorbe el truncado de segundos de correcciones anteriores a esta versión.
_SELECT_DIFERENCIAS = """
SELECT t.nombre, t.area, t.fecha_turno,
       to_char(me.momento AT TIME ZONE 'America/Guayaquil', 'YYYY-MM-DD HH24:MI') AS marco_entrada,
       to_char(ms.momento AT TIME ZONE 'America/Guayaquil', 'YYYY-MM-DD HH24:MI') AS marco_salida,
       to_char(t.ts_entrada, 'YYYY-MM-DD HH24:MI') AS vigente_entrada,
       to_char(t.ts_salida,  'YYYY-MM-DD HH24:MI') AS vigente_salida,
       round((extract(epoch from ms.momento - me.momento) / 3600)::numeric, 2) AS horas_marcadas,
       t.horas_trabajadas AS horas_vigentes,
       greatest(0, round((extract(epoch from ms.momento - me.momento) / 3600)::numeric, 2)
                   - regla('jornada_base_h')) - coalesce(t.horas_extra, 0) AS extra_quitada,
       me.origen AS origen_marcacion
FROM turnos t
JOIN marcaciones me ON me.turno_id = t.id AND me.tipo = 'entrada'
JOIN marcaciones ms ON ms.turno_id = t.id AND ms.tipo = 'salida'
WHERE NOT t.anulado
  AND t.fecha_turno >= :desde
  AND (abs(extract(epoch from (t.ts_entrada::timestamptz - me.momento))) > 120
    OR abs(extract(epoch from (t.ts_salida::timestamptz  - ms.momento))) > 120)
ORDER BY extra_quitada DESC, t.fecha_turno DESC
"""


@st.cache_data(ttl=60)
def leer_diferencias_marcacion(desde: date) -> pd.DataFrame:
    with get_engine().connect() as conn:
        rows = conn.execute(text(_SELECT_DIFERENCIAS), {"desde": desde}).mappings().all()
    return pd.DataFrame([dict(r) for r in rows])


_SELECT_BITACORA = """
SELECT a.momento AT TIME ZONE 'America/Guayaquil' AS momento, a.usuario, a.origen,
       CASE WHEN a.accion = 'DELETE' THEN 'borrado'
            WHEN (a.despues ->> 'anulado')::boolean AND NOT coalesce((a.antes ->> 'anulado')::boolean, false)
                THEN 'anulacion'
            ELSE 'cambio' END AS accion,
       a.nombre, a.fecha_turno, a.horas_antes, a.horas_despues, a.delta_horas,
       (a.antes ->> 'horas_extra')::numeric   AS extra_antes,
       (a.despues ->> 'horas_extra')::numeric AS extra_despues,
       a.motivo, a.ajuste_id
FROM turnos_auditoria a
WHERE a.origen IN ('admin', 'fuera_de_la_app')
  AND a.momento >= :desde
ORDER BY a.momento DESC
LIMIT 2000
"""


@st.cache_data(ttl=60)
def leer_bitacora_supervision(desde: date) -> pd.DataFrame:
    """Cambios de supervisión y ediciones hechas por fuera de la aplicación."""
    with get_engine().connect() as conn:
        rows = conn.execute(text(_SELECT_BITACORA), {"desde": desde}).mappings().all()
    return pd.DataFrame([dict(r) for r in rows])
