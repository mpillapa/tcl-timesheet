# Migraciones de la base de datos

Esta carpeta lleva la base del turnero de un esquema que se pegaba a mano en el
SQL Editor a migraciones versionadas, probadas y con un orden de despliegue.
El motivo está en [auditoria/README.md](../auditoria/README.md): en septiembre
de 2026 se quitaron 38.43 h extra de turnos ya marcados, una parte con el mes
cerrado, sin motivo escrito o reescribiendo el horario entero, y la base
permitía todo eso sin dejar más rastro que la bitácora.

Lo que sigue es un prototipo en proceso de endurecimiento, no un sistema
productivo aprobado por Tecnología. La última sección dice qué falta para eso.

## Qué cambia

| Antes | Después |
|---|---|
| La aplicación conecta como `postgres`, dueño de todo | Conecta como `turnero_app`, que no puede borrar, ni tocar la bitácora, ni ver el esquema `herramientas` de la otra aplicación del proyecto |
| Eliminar un turno es un `DELETE` | Se anula: queda con quién, cuándo y por qué |
| Las horas son columnas que cualquiera escribe | La base las calcula desde el horario |
| Corregir es sobrescribir la fila | Es un ajuste con motivo, que guarda el antes y lo que se cambió. Se aplica en el acto (V012) |
| La marcación del colaborador se pierde al corregir | Se guarda aparte, en una tabla que no se puede editar |
| La bitácora se puede borrar | Es de solo inserción, también para su dueño |
| `archivado` no impide nada | Sigue sin impedir nada. El cierre de V007 existe, pero V012 lo deja apagado |
| La persona es su nombre | Tiene un identificador en `empleados` |
| Un permiso se reconoce por el texto de la observación | Tiene tipo y está atado a su solicitud en `ausencias` |
| Las horas se guardan sin zona | Se guardan como instante (`timestamptz`) |
| Los umbrales viven solo en el código | Están en `reglas_negocio`, con su estado de decisión |
| Esquema pegado a mano, sin pruebas | Migraciones numeradas y 103 pruebas contra una base local |

### Las tablas

| Tabla | Para qué | Quién escribe |
|---|---|---|
| `turnos` | El turno vigente, el que se paga | La aplicación, con las validaciones de la base |
| `marcaciones` | Lo que se marcó en el reloj, una vez por turno | Un trigger. Solo inserción |
| `ajustes_turno` | Cada corrección y anulación, con motivo, antes y lo que se cambió | Las funciones `solicitar_*`. `aprobar_ajuste` y `rechazar_ajuste` siguen en la base, sin uso desde V012 |
| `turnos_auditoria` | Toda modificación de un turno, con autor, origen, motivo y ajuste | Un trigger. Solo inserción |
| `periodos_nomina`, `periodos_eventos` | Estado de cada mes y cada cierre o reapertura. Sin uso desde V012 | `cerrar_periodo`, `reabrir_periodo`. Los eventos son solo inserción |
| `empleados` | Identidad de cada persona: nombre y área, sin PIN | Un trigger y `sincronizar_empleados.py` |
| `ausencias` | La solicitud de un permiso, falta o vacaciones | El panel, en Eventualidades |
| `reglas_negocio` | Jornada, umbrales y banderas | Nadie desde la aplicación; se cambia con un `update` a mano |

## Las migraciones

| Versión | Fase | Qué hace |
|---|---|---|
| [V000](versiones/V000__linea_base.sql) | A | El esquema actual de producción, reunido. En producción no cambia nada |
| [V001](versiones/V001__reglas_de_negocio.sql) | A | `reglas_negocio` y las funciones de cálculo de horas |
| [V002](versiones/V002__anulacion.sql) | A | Columnas de anulación e índice único parcial |
| [V003](versiones/V003__bitacora_inmutable.sql) | A | Bitácora de solo inserción, con motivo e id de ajuste |
| [V004](versiones/V004__horas_calculadas_y_validaciones.sql) | A | La base calcula horas y valida cada turno escrito |
| [V005](versiones/V005__empleados.sql) | A | `empleados` y el vínculo desde `turnos` |
| [V006](versiones/V006__ausencias.sql) | A | `ausencias` y la columna `evento` llena |
| [V007](versiones/V007__periodos_de_nomina.sql) | A | Cierre y reapertura de meses |
| [V008](versiones/V008__marcaciones.sql) | A | Marcaciones originales, con la carga de lo que se puede reconstruir |
| [V009](versiones/V009__ajustes_con_aprobacion.sql) | A | Ajustes con motivo y aprobación |
| [V010](versiones/V010__rol_de_la_aplicacion.sql) | A | El rol `turnero_app`, sin contraseña |
| [V011](versiones/V011__tolerancia_de_recorte.sql) | A | La aprobación se mide en minutos quitados, con 10 min de tolerancia |
| [V012](versiones/V012__sin_aprobacion_ni_cierre.sql) | A | Los ajustes se aplican en el acto y un mes cerrado no bloquea; aplica los pendientes |
| [V013](versiones/V013__evento_de_jornada.sql) | A | El evento de un turno trabajado según su duración: sin novedad, con tolerancia o vacío |
| [V014](versiones/V014__evento_de_jornada_sep_oct.sql) | A | La regla de V013 para los turnos de septiembre y octubre de 2026 ya cerrados |
| [V015](versiones/V015__solo_tolerancia.sql) | A | Solo la tolerancia lleva evento: hasta 9 h queda vacío, también en lo ya marcado |
| [V016](versiones/V016__evento_tolerancia.sql) | A | El evento se llama TOLERANCIA y se aplica a todo el histórico |
| [V101](versiones/V101__exigir_ajustes_y_motivo.sql) | B | Enciende la obligación de ajuste y de motivo |
| [V102](versiones/V102__horarios_con_zona.sql) | B | Entrada y salida a `timestamptz` |

La fase A es compatible con la versión de la aplicación que está hoy en
producción: se puede aplicar sin cortar el servicio. La fase B no: obliga a que
toda corrección pase por un ajuste y la versión actual no los usa. Por eso va
después de desplegar el código nuevo.

Cada archivo explica en su encabezado el porqué de lo que hace. Una migración
aplicada no se edita: el ejecutor guarda su checksum y se detiene si cambia. Para
corregir algo se escribe una migración nueva.

## Cómo se despliega

Los pasos que escriben en producción están marcados. Ninguno se ha ejecutado
todavía: todo lo de esta carpeta se probó solo contra una base local.

### Paso 1. Pruebas locales

Requiere PostgreSQL 17 instalado. Las pruebas levantan un servidor temporal,
nunca leen la URL de producción y apagan el espejo de Google Sheets.

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest pruebas
```

Si PostgreSQL está en otra ruta: `$env:PG_BIN = "D:\ruta\a\PostgreSQL\17\bin"`.

### Paso 2. Respaldo (escribe un archivo con datos de personas)

Supabase guarda respaldos diarios según el plan, pero conviene uno propio justo
antes. `pg_dump` viene con la instalación de PostgreSQL.

```powershell
$env:PGURL = .venv\Scripts\python.exe -c "import tomllib; print(tomllib.load(open('.streamlit/secrets.toml','rb'))['connections']['supabase']['url'])"
& "C:\Program Files\PostgreSQL\17\bin\pg_dump.exe" --dbname="$env:PGURL" --schema=public -Fc -f "$HOME\respaldo_turnero_antes_de_migrar.dump"
Remove-Item Env:PGURL
```

El archivo tiene datos laborales de personas identificadas. Va fuera del
repositorio y se borra cuando la migración esté verificada.

### Paso 3. Revisar lo que el histórico incumple (solo lectura)

```powershell
.venv\Scripts\python.exe migracion\validar_datos_existentes.py --desde-secrets --produccion
```

Al 2026-10-13 da cuatro hallazgos, ninguno bloquea la migración:

- un turno de Velasco Rolando del 2026-04-24 de 37.9 h, que se cruza con el del
  día siguiente: parece una salida olvidada;
- dos registros de Mariscal Juan del 2026-07-26, a dos minutos de distancia y
  con la misma duración: parece un turno contado dos veces;
- un turno de Farinango Nelson del 2026-06-23 cuya entrada es del 24.

Conviene que supervisión los revise. Corregirlos después de migrar es lo
correcto: pasa por un ajuste y queda registrado.

### Paso 4. Fase A (escribe en producción)

```powershell
.venv\Scripts\python.exe migracion\aplicar_migraciones.py --desde-secrets --produccion --plan
.venv\Scripts\python.exe migracion\aplicar_migraciones.py --desde-secrets --produccion
```

La aplicación sigue funcionando igual. Lo que cambia desde este momento, aun con
el código viejo: la base calcula las horas, rechaza turnos de más de 24 h o
cruzados, guarda las marcaciones y no deja tocar la bitácora.

### Paso 5. Contraseña del rol y prueba de conexión (escribe en producción)

Generar una contraseña y fijarla desde el SQL Editor de Supabase:

```powershell
.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

```sql
alter role turnero_app password 'LA_CONTRASEÑA_GENERADA';
```

En el `secrets.toml` local, cambiar la URL al rol nuevo. En el pooler de
Supabase el usuario es `turnero_app.<referencia>`, con la misma referencia de
proyecto que hoy acompaña a `postgres`:

```toml
url = "postgresql://turnero_app.xxxxxxxxxxxx:LA_CONTRASEÑA_GENERADA@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
```

y probar:

```powershell
.venv\Scripts\python.exe diagnostico\probar_conexion.py
```

Este es el único paso que no se pudo probar en local: el formato del usuario
del pooler para un rol propio es el que documenta Supabase, pero hay que
confirmarlo contra el proyecto real. Si falla, la aplicación sigue con
`postgres` y nada se rompe.

### Paso 6. Sincronizar el padrón (escribe en producción)

```powershell
.venv\Scripts\python.exe migracion\sincronizar_empleados.py --desde-secrets --produccion
.venv\Scripts\python.exe migracion\sincronizar_empleados.py --desde-secrets --produccion --aplicar
```

Sin `--aplicar` solo muestra los cambios. Copia nombre y área; el PIN no sale
de secrets.

### Paso 7. Desplegar la aplicación

Con el commit y el push del código nuevo, Streamlit Cloud redespliega solo. En
los secrets de Streamlit Cloud, que son independientes del archivo local:

1. cambiar `[connections.supabase] url` a la de `turnero_app`;
2. añadir `ver_auditoria = true` a quien deba ver la pestaña "Modificaciones
   de turnos". `puede_aprobar` y `gestiona_periodos` ya no se usan desde V012.

El código nuevo funciona con cualquiera de las dos URL, así que el orden entre
el push y el cambio de secrets no importa.

### Paso 8. Fase B (escribe en producción)

Solo con el código nuevo ya desplegado y verificado en el panel:

```powershell
.venv\Scripts\python.exe migracion\aplicar_migraciones.py --desde-secrets --produccion --fase B --plan
.venv\Scripts\python.exe migracion\aplicar_migraciones.py --desde-secrets --produccion --fase B
```

Después, reiniciar la aplicación desde Streamlit Cloud para que ninguna conexión
abierta conserve el tipo viejo de las columnas.

### Paso 9. Comprobar

- Un colaborador marca entrada y salida en el quiosco.
- Un administrador corrige un turno quitando más de 10 minutos: se aplica en
  el acto.
- Quien tiene `ver_auditoria` la ve en "Modificaciones de turnos", con el
  horario de antes y el de después, y en "Lo que se marcó contra lo que quedó".
- El análisis de septiembre da las mismas cifras que antes:
  `.venv\Scripts\python.exe auditoria\analisis_mensual.py --mes 2026-09`.

## Cómo volver atrás

Fase A sin fase B: volver a desplegar la versión anterior de la aplicación
basta. La base queda con las tablas nuevas, que la versión anterior ignora.

Fase B, sin deshacer el tipo de columna:

```sql
update reglas_negocio set valor = 0
where clave in ('exigir_ajuste_para_corregir', 'exigir_motivo_admin');
```

Deshacer V102 exige quitar y recrear los dos triggers que nombran las columnas,
como hace la propia migración, y convertir de vuelta con
`using ts_entrada at time zone 'America/Guayaquil'`. Con el código nuevo no hace
falta: funciona con los dos tipos.

Lo que no se vuelve atrás, a propósito: la bitácora, las marcaciones y los
ajustes. Son el registro de lo que pasó.

## Despliegue de V011

La fase A se aplicó en producción el 2026-10-13. V011 es una migración de fase A
que llegó después y se despliega en este orden:

1. Respaldo, como en el paso 2.
2. `aplicar_migraciones.py --desde-secrets --produccion --plan`: tiene que
   aparecer solo V011.
3. `aplicar_migraciones.py --desde-secrets --produccion`.
4. Push del código. El panel nuevo llama a `clase_de_ajuste()`, que crea V011:
   si el código llega antes, la pestaña de auditoría falla. Al revés no hay
   problema, porque V011 no cambia ninguna firma que use el código desplegado.

Los ajustes que ya estaban pendientes siguen pendientes: la regla nueva se
aplica a lo que se solicite desde ese momento.

## Despliegue de V012 y V013

Las dos son de fase A y no cambian ninguna firma: el código que está hoy en
producción sigue funcionando con ellas. Se aplican juntas. El orden:

1. Respaldo, como en el paso 2.
2. Ver si hay ajustes esperando, porque V012 los aplica (solo lectura):

   ```sql
   select a.id, t.nombre, t.fecha_turno, a.tipo, a.solicitado_por, a.motivo
   from ajustes_turno a join turnos t on t.id = a.turno_id
   where a.estado = 'pendiente' order by a.solicitado_en;
   ```

3. `aplicar_migraciones.py --desde-secrets --produccion --plan`: tienen que
   aparecer V012 y V013, y V011 si todavía no se aplicó.
4. `aplicar_migraciones.py --desde-secrets --produccion`. Al terminar, el
   aviso de V012 dice cuántos pendientes aplicó y cuántos cerró sin aplicar.
5. Push del código. El panel nuevo ya no tiene la pantalla de aprobación ni la
   de cierre de mes, y cuenta los turnos con evento de jornada como trabajo.
   Con V013 aplicada y el código viejo, esos turnos se verían un rato como
   eventualidades en el dashboard; por eso conviene hacer el push enseguida.

Desde V013, al cerrarse un turno de trabajo la base llena `evento`:

| Duración | Evento |
|---|---|
| Hasta 9 h | Vacío (desde V015; V013 ponía "Turno completo sin novedad") |
| Más de 9 h y hasta 9 h 30 | TOLERANCIA (desde V016; antes "Turno completo con tolerancia de horas extra") |
| Más de 9 h 30 | Vacío: lleva la justificación del colaborador |

Es una etiqueta: las horas extra se calculan igual. Se recalcula cuando cambia
la entrada o la salida, también al corregir. Los turnos de septiembre y octubre
de 2026 registrados antes de V013 los llena V014, con la misma regla; los
anteriores a septiembre quedan con el evento vacío hasta que se corrija su
horario.

### V014

Aplica el evento de jornada a los turnos de trabajo de septiembre y octubre de
2026 que ya estaban cerrados. Solo cambia `evento`; las horas no se mueven. Al
2026-10-21 eran 1307 turnos: 929 sin novedad y 378 con tolerancia. Otros 595
siguen vacíos por pasar de 9 h 30. Cada cambio queda en la bitácora como
`sistema`, usuario `migracion V014`. No necesita push: el código desplegado ya
la entiende.

### V015

Decidido el 2026-10-28: solo la tolerancia lleva evento. La regla deja vacío el
turno de 9 h o menos, y la migración vacía los que V013 y V014 ya habían marcado
como sin novedad: 554 de septiembre y 523 de octubre al 2026-10-28. Los 445 con
tolerancia no cambian. Solo cambia `evento`; cada cambio queda en la bitácora
como `sistema`, usuario `migracion V015`. Tampoco necesita push para
funcionar.

### V016

Decidido el 2026-10-29: el evento se llama TOLERANCIA y vale para toda la base.
Cambia el texto largo en los 449 turnos de septiembre y octubre que lo tenían, y
lo pone en 976 turnos de mayo a agosto que estaban en el rango con el evento
vacío. Solo cambia `evento`; cada cambio queda en la bitácora como `sistema`,
usuario `migracion V016`. Primero el push y después la migración: el panel
anterior no conoce TOLERANCIA y la contaría como una ausencia, mientras que el
nuevo entiende el texto anterior y el nuevo.

Los pendientes se aplican a nombre de quien los pidió, con
`revisado_por = 'sistema: V012'`. Uno que ya no se puede aplicar, por ejemplo
porque quedaría cruzado con otro turno, se cierra como rechazado con el error
en `comentario_revision` y aparece en la pestaña con estado "No aplicado".

Para volver a la regla anterior sin desplegar nada en la base:

```sql
update reglas_negocio set valor = 1 where clave = 'exigir_aprobacion_ajustes';
update reglas_negocio set valor = 1 where clave = 'bloquear_periodos_cerrados';
```

El panel ya no tendría con qué aprobar ni cerrar meses: habría que restaurar
`render_ajustes_pendientes` y `render_periodos` de `views/auditoria_panel.py`
desde el commit anterior a V012.

## Decisiones pendientes

El turnero es una herramienta interna de control: estas decisiones las toman
los superadmins responsables, no otra área. Las reglas de esta tabla están
marcadas como `provisional` en `reglas_negocio`. Funcionan con el valor
indicado, que se eligió para poder construir el mecanismo. Cambiarlas es un
`update` y no requiere desplegar nada.

| Decisión | Valor provisional |
|---|---|
| Qué superadmins ven la auditoría | Nadie hasta configurar `ver_auditoria` |
| Duración máxima de un turno | 24 h |
| Largo mínimo del motivo | 10 caracteres |
| Si un permiso cuenta como horas en el comparativo | Cuenta, como hasta ahora |
| Cuánto tiempo se conserva la bitácora | Indefinido; ver [seguridad/retencion_y_acceso.md](../seguridad/retencion_y_acceso.md) |

Decidido el 2026-10-21 con la gerencia y el jefe de carga, en reemplazo de lo
decidido el 2026-10-14: ninguna corrección ni anulación necesita la aprobación
de otro superadmin, y un mes cerrado no bloquea cambios. El control se hace
después, sobre lo que queda registrado. Son las reglas
`exigir_aprobacion_ajustes` y `bloquear_periodos_cerrados`, las dos en 0; ver
[V012](versiones/V012__sin_aprobacion_ni_cierre.sql). `tolerancia_recorte_min`
sigue en la tabla, pero solo para clasificar cada ajuste como menor o recorte.

Para ver qué valores están vigentes:

```sql
select clave, valor, unidad, estado, decidido_por from reglas_negocio order by estado, clave;
```

## Personas creadas automáticamente

Si un turno llega con un nombre que no está en `empleados`, la base crea la
persona como `automatico` en vez de rechazar la marcación. Revisarlas cada
tanto:

```sql
select nombre, area, creado_en from empleados where origen = 'automatico' order by creado_en desc;
```

El reporte del paso 3 también las lista.

## Herramientas de esta carpeta

| Archivo | Qué hace | Escribe |
|---|---|---|
| [aplicar_migraciones.py](aplicar_migraciones.py) | Aplica las versiones pendientes en orden | Sí |
| [validar_datos_existentes.py](validar_datos_existentes.py) | Lista turnos que las reglas nuevas no admitirían | No |
| [sincronizar_empleados.py](sincronizar_empleados.py) | Padrón de secrets a `empleados`, sin PIN | Con `--aplicar` |
| [schema.sql](schema.sql), [auditoria_schema.sql](auditoria_schema.sql) | El esquema anterior. Histórico: lo reemplaza V000. `pruebas/test_migraciones.py` los carga para simular la base anterior | No se aplican |

## Lo que esto no resuelve

- El turnero sigue siendo un prototipo. Si sus horas van a alimentar la nómina
  de forma oficial, necesita la evaluación de Tecnología: arquitectura,
  autenticación, integración con el sistema de nómina, monitoreo y soporte.
- La autenticación de administradores son usuarios y contraseñas en secrets,
  y los colaboradores entran con un PIN derivado de su cédula. La base ahora
  registra quién hizo qué, pero confía en el nombre que le pasa la aplicación.
- Los permisos de cada administrador por área siguen en el código
  (`AREAS_POR_ADMIN`), no en la base.
- Las marcaciones anteriores a V008 son reconstruidas. Para turnos corregidos
  antes del 2026-09-02, la marcación reconstruida puede ser ya el valor
  corregido: antes de la bitácora no se guardaba el original.
