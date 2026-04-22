"""Acceso a Google Sheets y utilidades de bajo nivel sobre los registros."""

from datetime import datetime

import pandas as pd
import streamlit as st
from streamlit_gsheets import GSheetsConnection

from config import COLUMNAS, COLS_TEXTO, HORAS_BASE_TURNO, WORKSHEET_NAME

_conn = st.connection("gsheets", type=GSheetsConnection)


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


def escribir_registros(df: pd.DataFrame) -> None:
    _conn.update(worksheet=WORKSHEET_NAME, data=df)


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
