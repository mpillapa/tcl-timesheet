# Marcador de horas extra

Aplicación interna en Streamlit para que el personal marque entrada y salida
desde un quiosco (celular, tablet o PC compartida) y para que las jefaturas
revisen las horas trabajadas y las horas extra de su área.

Los datos viven en PostgreSQL (Supabase), que es la fuente de verdad. Cada
escritura se replica además en una hoja de Google Sheets que funciona como
respaldo; ese espejo se puede apagar desde la configuración sin tocar el
código.

## Qué resuelve

El registro de horas extra se llevaba en hojas de cálculo llenadas a mano, con
tres problemas recurrentes: turnos nocturnos mal calculados, salidas que nadie
marcaba y ninguna forma de ver el acumulado por área antes del cierre de mes.
La aplicación cubre esos tres puntos:

- Guarda timestamps completos, de modo que un turno que cruza la medianoche se
  calcula bien (entrada 21:00 del día 1 y salida 07:00 del día 2 son 10 horas).
- Detecta las salidas olvidadas y manda esos turnos a revisión del supervisor
  en lugar de registrar una duración inventada.
- Muestra el acumulado por persona y por área contra la meta del mes, en
  cualquier momento del período.

## Dos vistas según el rol

Colaborador. Ingresa con un PIN de cuatro dígitos (los últimos cuatro de la
cédula) y ve una sola acción a la vez: si no tiene turno abierto, solo puede
marcar entrada; si lo tiene, solo salida. Tras marcar, la sesión se cierra sola
en unos segundos para que el siguiente use el quiosco.

Super admin. Ingresa con usuario y contraseña. Tiene cinco pestañas:

| Pestaña | Para qué sirve |
|---|---|
| Dashboard | Indicadores del período, horas por área, tendencia diaria, mapa de calor y progreso por funcionario contra la cuota |
| Comparativo horas esperadas | Horas efectivas contra las esperadas, por mes y por funcionario, con el histórico completo |
| Tabla | Detalle de los turnos filtrados, con descarga a CSV |
| Gestión de turnos | Cierre de turnos en revisión, registro manual de entradas o turnos históricos, edición y eliminación |
| Eventualidades | Registro de faltas justificadas, permisos y vacaciones por rango de fechas |

Cada admin ve solo las áreas que tiene asignadas en `AREAS_POR_ADMIN`
(`views/super_admin.py`). Los usuarios marcados con `solo_lectura = true` en la
configuración ven las tres primeras pestañas y no pueden modificar nada.

## Control de acceso

Son dos capas independientes.

La primera es el acceso a la aplicación. Pasa cualquiera de estas condiciones:
un `device_key` válido en la URL (para los equipos fijos de oficina), una IP
dentro de las autorizadas, un navegador previamente marcado como equipo de
confianza, o la contraseña maestra. Para revocar todos los equipos de confianza
basta cambiar `trusted_device_secret` en la configuración.

La segunda es el login por rol: PIN para el colaborador, usuario y contraseña
para el administrador. Pasar la primera capa no da acceso a los datos.

## Reglas de negocio

Están todas en `core/config.py` y se aplican en `core/calculos.py` y
`core/marcado.py`. Los valores vigentes son:

| Regla | Valor | Dónde |
|---|---|---|
| Jornada base por turno | 9 h | `HORAS_BASE_TURNO` |
| Descuento de almuerzo | 1 h, solo si el turno dura 5 h o más | `HORAS_ALMUERZO`, `MIN_HORAS_ALMUERZO` |
| Turno abierto que se considera olvido | más de 15 h | `UMBRAL_OLVIDO_H` |
| Horas que exigen justificación | más de 9.5 h | `UMBRAL_HORAS_EXTRA` |
| Largo mínimo de la justificación | 10 caracteres | `MIN_JUSTIF_CHARS` |
| Tiempo mínimo entre entrada y salida | 5 minutos | `MIN_MINUTOS_TURNO` |

Tres definiciones que conviene tener claras porque se mezclan seguido:

- Horas trabajadas: la diferencia entre salida y entrada.
- Horas efectivas: las trabajadas menos el almuerzo, cuando aplica. Es la cifra
  que usan los reportes.
- Horas extra: lo que pasa de la jornada base en un mismo turno.

La fecha de turno es siempre la fecha de la entrada, aunque la salida caiga al
día siguiente. Por eso los reportes agrupan bien los turnos nocturnos, y por
eso corregir la hora de entrada de un turno también lo reubica en los gráficos.

Un caso que vale mencionar: `SCOPE_POR_PADRON_VIGENTE` en `core/config.py`
decide si las horas se atribuyen al área actual del empleado o a la que tenía
cuando marcó. Está en `True`, lo que significa que un jefe ve todo el historial
de la gente que tiene asignada hoy. Si Recursos Humanos necesita que los
reportes históricos por área queden congelados, hay que ponerlo en `False`.

## Estructura del proyecto

```
PROYECTO_HORAS_EXTRA/
├── app.py                  punto de entrada, elige la vista según el rol
├── core/
│   ├── config.py           constantes y reglas de negocio
│   ├── calculos.py         cálculo de horas trabajadas, efectivas y extra
│   ├── marcado.py          marcar entrada y salida, justificaciones, olvidos
│   ├── data.py             capa de datos sobre PostgreSQL
│   ├── db.py               conexión y tiempos límite del engine
│   ├── sheets_backup.py    espejo de respaldo en Google Sheets
│   ├── auth.py             control de acceso y login por rol
│   ├── employees.py        padrón de empleados desde secrets
│   ├── normalizacion.py    limpieza de textos y timestamps
│   ├── traza.py            formato del rastro de correcciones en Observaciones
│   ├── time_utils.py       manejo de la zona horaria de Ecuador
│   ├── ui_theme.py         paleta de marca y CSS compartido
│   └── ui_utils.py         anti doble clic y mensajes diferidos
├── views/
│   ├── colaborador.py      quiosco de marcación
│   └── super_admin.py      panel administrativo
├── migracion/              esquema SQL y migración inicial de Sheets a Supabase
├── diagnostico/            script y bitácora de diagnóstico de conexión
├── auditoria/              lectura de la bitácora de cambios y auditoría histórica
├── seguridad/              habilitación de RLS en Supabase
└── .streamlit/
    ├── secrets.toml        credenciales locales, no se versiona
    ├── secrets.toml.example plantilla versionada
    └── config.toml         tema de marca
```

La separación es intencional: las reglas de negocio están en `core/config.py` y
`core/calculos.py`, el acceso a datos en `core/data.py` y `core/db.py`, la
integración con Google en `core/sheets_backup.py` y la interfaz en `views/`.
Ninguna vista habla directo con la base.

## Puesta en marcha local

Necesitas Python 3.11 o superior.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

En Linux o macOS el entorno se activa con `source .venv/bin/activate`.

Luego copia `.streamlit/secrets.toml.example` a `.streamlit/secrets.toml` y
llena los valores. Ese archivo está en `.gitignore` y nunca debe subirse. Los
bloques que necesitas son cuatro:

- `[connections.supabase]` con la URL del Session pooler de Supabase.
- `[backup]` con `espejo_sheets` en `true` o `false`.
- `[connections.gsheets]` y las credenciales del service account, solo si el
  espejo está activo.
- `[super_admins.<usuario>]`, `[auth]` y `[empleados]` con los usuarios, las
  claves de acceso y el padrón.

Para levantar la aplicación:

```powershell
streamlit run app.py
```

Si la conexión falla y no está claro si el problema es la red, las credenciales
o el modo de conexión, corre el diagnóstico:

```powershell
.venv\Scripts\python.exe diagnostico\probar_conexion.py
```

No uses datos productivos en el ambiente local. Trabaja con un proyecto de
Supabase aparte y un padrón de prueba.

## Base de datos

Son tres tablas. Las dos primeras están en `migracion/schema.sql`:

- `turnos`, con un registro por turno. La clave natural es el nombre más el
  timestamp de entrada, con un índice único que además evita que un doble clic
  duplique el turno. La columna `archivado` separa el mes activo del histórico.
- `horas_esperadas`, con la meta de horas por año y mes. Se edita desde el Table
  Editor de Supabase.

La tercera está en `migracion/auditoria_schema.sql`:

- `turnos_auditoria`, la bitácora de cambios. Un trigger sobre `turnos` graba
  cada modificación y cada borrado con la fila completa antes y después, el
  usuario que lo hizo y el origen del cambio. Retención indefinida: es el
  respaldo de las horas que recorta supervisión. La aplicación no escribe en
  ella, solo deja el autor en la sesión con `core.data._sellar_autor`.

  Se creó el 2026-09-02, después de una auditoría que no pudo responder cuántas
  horas se habían modificado porque la base no guardaba el valor anterior. El
  detalle está en [auditoria/README.md](auditoria/README.md).

La conexión usa el Session pooler de Supabase en el puerto 5432. No lo cambies
al pooler de transacción del puerto 6543: desde la red de la empresa ese puerto
no responde. El detalle está anotado en `core/db.py` y en
[diagnostico/README.md](diagnostico/README.md).

Los tiempos límite del engine, los keepalives de TCP y el reintento de lecturas
existen por un incidente concreto de cuelgues indefinidos. Antes de tocar esos
valores, lee [diagnostico/README.md](diagnostico/README.md).

RLS está habilitado en las tres tablas sin políticas, lo que cierra la API REST
de Supabase sin afectar a la aplicación, que conecta por Postgres directo. Ver
[seguridad/README.md](seguridad/README.md).

## Despliegue

La aplicación corre en Streamlit Community Cloud, apuntada a la rama `main`.
Los secretos se configuran en Settings, Secrets del panel de Streamlit Cloud,
con el mismo contenido del `secrets.toml` local. Son independientes del archivo
local y no viajan por el repositorio. Cada push a `main` redespliega.

## Documentación relacionada

- [MIGRACION_SUPABASE.md](MIGRACION_SUPABASE.md): paso a paso de la migración de
  Google Sheets a Supabase.
- [diagnostico/README.md](diagnostico/README.md): incidente de cuelgues del
  2026-08-25, causa, arreglo y uso del script de diagnóstico.
- [seguridad/README.md](seguridad/README.md): RLS en el schema público, qué
  expone el problema y por qué habilitarlo no rompe la aplicación.
- [auditoria/README.md](auditoria/README.md): la bitácora de cambios y cómo
  leerla, más la auditoría del 2026-09-02 que la motivó, con lo que se pudo
  medir de las correcciones anteriores y lo que ya no se puede.

## Limitaciones conocidas

- El padrón de empleados y las credenciales de los administradores viven en
  `secrets.toml`. Funciona, pero no está integrado con el directorio corporativo
  ni con SAP, y cada alta o baja se hace a mano.
- El alcance por área está escrito en `AREAS_POR_ADMIN` dentro del código, no en
  configuración.
- Las horas esperadas del mes se cargan a mano en la tabla `horas_esperadas`.
- El espejo en Google Sheets es unidireccional. Editar el Sheet a mano no
  cambia nada en la base.
- La bitácora solo cubre desde el 2026-09-02. Lo anterior a esa fecha no tiene
  valor previo registrado y no se puede reconstruir, salvo lo que ya quedó
  medido en [auditoria/README.md](auditoria/README.md).
- No hay regla de negocio que limite una corrección. Un administrador puede
  reducir las horas de un turno cerrado sin tope y sin segunda aprobación.
  Ahora queda registrado, pero no impedido.
- No hay una vista de la bitácora en el panel. Se consulta desde Supabase o
  con `auditoria/bitacora.py`.
- No hay pruebas automatizadas. `auditoria/verificar_bitacora.py` y
  `diagnostico/probar_conexion.py` son comprobaciones que se corren a mano.

## Licencia

MIT. Ver [LICENSE](LICENSE).
