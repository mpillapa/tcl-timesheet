"""Constantes globales compartidas por toda la app."""

WORKSHEET_NAME = "Registros"

COLUMNAS = [
    "Nombre",
    "Area",
    "Fecha de Turno",
    "Timestamp Entrada",
    "Timestamp Salida",
    "Horas Trabajadas",
    "Horas Efectivas",
    "Horas Extra",
    "Estado",
    "Observaciones",
]

COLS_TEXTO = [
    "Nombre",
    "Area",
    "Fecha de Turno",
    "Timestamp Entrada",
    "Timestamp Salida",
    "Estado",
    "Observaciones",
]

TS_FMT = "%Y-%m-%d %H:%M:%S"

# Jornada base usada para calcular horas extra por turno.
HORAS_BASE_TURNO = 9.0

# Descuento de almuerzo: se aplica solo si el turno dura >= MIN_HORAS_ALMUERZO.
HORAS_ALMUERZO = 1.0
MIN_HORAS_ALMUERZO = 5.0

# Si un turno lleva más de este tiempo abierto, se considera olvido de salida.
UMBRAL_OLVIDO_H = 18

# Horas por encima de este valor requieren justificación obligatoria.
UMBRAL_HORAS_EXTRA = HORAS_BASE_TURNO
MIN_JUSTIF_CHARS = 10
