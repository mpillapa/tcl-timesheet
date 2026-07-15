"""Reglas de negocio puras para el cálculo de horas.

core.data las re-exporta para mantener la API pública que usan las vistas.
"""

from datetime import datetime

from core.config import HORAS_BASE_TURNO, HORAS_ALMUERZO, MIN_HORAS_ALMUERZO


def calcular_horas(ts_in: datetime, ts_out: datetime) -> float:
    return round((ts_out - ts_in).total_seconds() / 3600, 2)


def calcular_horas_efectivas(horas_trabajadas: float) -> float:
    h = float(horas_trabajadas)
    return round(h - HORAS_ALMUERZO if h >= MIN_HORAS_ALMUERZO else h, 2)


def calcular_horas_extra(horas_trabajadas: float) -> float:
    return round(max(0.0, float(horas_trabajadas) - HORAS_BASE_TURNO), 2)
