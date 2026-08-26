"""Normalización de textos y timestamps, compartida por la capa de datos
(PostgreSQL) y el espejo de respaldo en Google Sheets.

Vive en su propio módulo para que core.data y core.sheets_backup puedan
importarla sin crear imports circulares.
"""

import re

import pandas as pd

_INVISIBLE_RE = re.compile(r"[​‌‍⁠﻿]")


def _normalizar_texto(value) -> str:
    """Elimina invisibles (zero-width, BOM), compacta espacios y recorta, para
    evitar falsos negativos en las comparaciones exactas."""
    s = str(value or "")
    s = _INVISIBLE_RE.sub("", s)
    s = s.replace(" ", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _normalizar_cmp(value) -> str:
    """Normalización para comparaciones textuales case-insensitive."""
    return _normalizar_texto(value).casefold()


def _normalizar_serie(serie: pd.Series) -> pd.Series:
    """Equivalente vectorizado de _normalizar_texto sobre una columna entera."""
    s = serie.astype(object).where(serie.notna(), "").astype(str)
    s = s.str.replace(_INVISIBLE_RE, "", regex=True)
    s = s.str.replace(" ", " ", regex=False)
    s = s.str.replace(r"\s+", " ", regex=True)
    return s.str.strip()


def _ts_key(raw) -> str:
    """Normaliza un timestamp a 'YYYY-MM-DD HH:MM:SS' para poder comparar los
    strings escritos con RAW contra las celdas datetime-typed legacy, cuyo
    display depende del locale ('22/4/2026 9:00:00' en es-EC). Si no se puede
    parsear, devuelve el valor en bruto."""
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
