"""Utilidades de tiempo para operar en zona horaria de Ecuador (UTC-5)."""

from datetime import datetime, timedelta, timezone, date

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
