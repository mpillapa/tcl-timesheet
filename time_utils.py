"""Utilidades de tiempo para operar en zona horaria de Ecuador (UTC-5)."""

from datetime import datetime, timedelta, timezone, date

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None


def now_ecuador() -> datetime:
    """Devuelve la fecha/hora actual de Ecuador como datetime naive local."""
    if ZoneInfo is not None:
        return datetime.now(timezone.utc).astimezone(ZoneInfo("America/Guayaquil")).replace(tzinfo=None)
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-5))).replace(tzinfo=None)


def today_ecuador() -> date:
    """Devuelve la fecha actual de Ecuador."""
    return now_ecuador().date()
