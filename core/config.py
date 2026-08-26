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
    "Evento",
    "Observaciones",
]

COLS_TEXTO = [
    "Nombre",
    "Area",
    "Fecha de Turno",
    "Timestamp Entrada",
    "Timestamp Salida",
    "Estado",
    "Evento",
    "Observaciones",
]

TS_FMT = "%Y-%m-%d %H:%M:%S"

# Jornada base usada para calcular horas extra por turno.
HORAS_BASE_TURNO = 9.0

# Descuento de almuerzo: se aplica solo si el turno dura >= MIN_HORAS_ALMUERZO.
HORAS_ALMUERZO = 1.0
MIN_HORAS_ALMUERZO = 5.0

# Si un turno lleva más de este tiempo abierto, se considera olvido de salida
# y se envía a revisión del supervisor.
UMBRAL_OLVIDO_H = 15

# Horas por encima de este valor requieren justificación obligatoria.
UMBRAL_HORAS_EXTRA = 9.5
MIN_JUSTIF_CHARS = 10

MIN_MINUTOS_TURNO = 5

WORKSHEET_HORAS_ESPERADAS = "Horas Esperadas"

# Meses cerrados, para mantener liviana la hoja activa (Registros).
WORKSHEET_HISTORICO = "Historico"

# Con el flag activo, el alcance de cada admin y la columna "Area" del panel se
# resuelven con el área vigente del padrón (secrets.empleados) en vez del área
# que el turno guardó al marcarse, de modo que un jefe ve todo el historial de
# la gente que tiene asignada hoy. Los ex-empleados conservan el área del
# registro, porque ya no están en el padrón.
#
# Implicación de negocio: los totales por área atribuyen las horas al área
# actual del empleado. Si Recursos Humanos necesita los reportes históricos
# congelados por área, hay que ponerlo en False.
SCOPE_POR_PADRON_VIGENTE = True
