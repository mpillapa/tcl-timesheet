"""Constantes globales compartidas por toda la app."""

WORKSHEET_NAME = "Registros"

COLUMNAS = [
    "Nombre",
    "Area",
    "Fecha de Turno",
    "Timestamp Entrada",
    "Timestamp Salida",
    "Horas Trabajadas",
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

# Si un turno lleva más de este tiempo abierto, se considera olvido de salida.
UMBRAL_OLVIDO_H = 18

# Horas por encima de este valor requieren justificación obligatoria.
UMBRAL_HORAS_EXTRA = 9.5
MIN_JUSTIF_CHARS = 20
