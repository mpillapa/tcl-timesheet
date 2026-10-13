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
    """Fecha y hora de Ecuador como datetime naive (sin tzinfo)."""
    return datetime.now(timezone.utc).astimezone(_ecuador_tz()).replace(tzinfo=None)


def today_ecuador() -> date:
    return now_ecuador().date()


def parse_timestamp_flexible(raw):
    """Intenta el formato canónico (ISO) y cae a dayfirst (dd/mm) para las filas
    legacy del Sheet. Devuelve None si no se puede interpretar."""
    ts = pd.to_datetime(raw, errors="coerce")
    if pd.isna(ts):
        ts = pd.to_datetime(raw, errors="coerce", dayfirst=True)
    if pd.isna(ts):
        return None
    return ts.to_pydatetime()


def parse_fecha_flexible(raw):
    """Igual que parse_timestamp_flexible, pero solo la fecha."""
    dt = parse_timestamp_flexible(raw)
    return dt.date() if dt is not None else None


def conservar_segundos(original, nuevo: datetime) -> datetime:
    """El formulario de corrección edita horas y minutos, no segundos. Si el
    administrador no tocó la hora, se conserva la original completa.

    Antes se truncaba a cero, y cada corrección movía la marca unos segundos
    sin que nadie lo decidiera: 136 de los cambios de entrada de septiembre de
    2026 eran solo eso, y había que filtrarlos a mano en la auditoría."""
    if original is not None and original.replace(second=0, microsecond=0) == nuevo:
        return original
    return nuevo
