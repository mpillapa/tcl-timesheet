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

# Tiempo mínimo entre entrada y salida para permitir el marcado.
MIN_MINUTOS_TURNO = 5

WORKSHEET_HORAS_ESPERADAS = "Horas Esperadas"

# Hoja donde se archivan los registros de meses cerrados para mantener liviana
# la hoja activa (Registros).
WORKSHEET_HISTORICO = "Historico"

# --- Alcance de los administradores por área ---------------------------------
# Cada turno guarda el área que el empleado tenía AL MARCAR (ver core.marcado:
# "Area": AREA_DE.get(nombre, "")). Cuando un empleado se reasigna de área en el
# padrón (secrets.empleados), sus turnos anteriores conservan el área antigua y
# el jefe que lo tiene asignado hoy deja de verlos.
#
# Con este flag activo, el alcance de cada admin y la columna "Area" que ve el
# panel se resuelven con el área VIGENTE del padrón, de modo que un jefe ve todo
# el historial de la gente que tiene asignada hoy. Los turnos de nombres que ya
# no están en el padrón (ex-empleados) conservan el área del registro.
#
# IMPLICACIÓN DE NEGOCIO: los totales por área atribuyen las horas al área
# actual del empleado, no a la que tenía cuando marcó. Si Recursos Humanos
# necesita que los reportes históricos por área queden congelados, pon esto en
# False y las reasignaciones de área volverán a ocultar el historial previo.
SCOPE_POR_PADRON_VIGENTE = True
