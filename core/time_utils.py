"""Utilidades de tiempo para operar en zona horaria de Ecuador (UTC-5)."""

from datetime import datetime, timedelta, timezone, date

import pandas as pd

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

ECUADOR_OFFSET = timezone(timedelta(hours=-5))


def _ecuador_tz():
    """Devuelve el tzinfo de Ecuador. Prefiere ZoneInfo; cae a offset fijo -5 si falla."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo("America/Guayaquil")
        except Exception:
            pass
    return ECUADOR_OFFSET


def now_ecuador() -> datetime:
    """Devuelve la fecha/hora actual de Ecuador como datetime naive local (UTC-5)."""
    return datetime.now(timezone.utc).astimezone(_ecuador_tz()).replace(tzinfo=None)


def today_ecuador() -> date:
    """Devuelve la fecha actual de Ecuador."""
    return now_ecuador().date()


def parse_timestamp_flexible(raw):
    """Parsea un timestamp desde los formatos que puede devolver Sheets.

    Intenta primero el formato canónico (ISO) y cae a dayfirst (dd/mm) para
    filas legacy. Devuelve un datetime naive o None si no se puede interpretar.
    """
    ts = pd.to_datetime(raw, errors="coerce")
    if pd.isna(ts):
        ts = pd.to_datetime(raw, errors="coerce", dayfirst=True)
    if pd.isna(ts):
        return None
    return ts.to_pydatetime()


def parse_fecha_flexible(raw):
    """Igual que parse_timestamp_flexible pero devuelve solo la fecha (o None)."""
    dt = parse_timestamp_flexible(raw)
    return dt.date() if dt is not None else None
