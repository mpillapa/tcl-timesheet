# Auditoría de cambios en los turnos

Cuatro scripts de solo lectura, más la bitácora que los vuelve casi
innecesarios. Ninguno imprime la cadena de conexión ni la contraseña, y los CSV
que generan van a `auditoria/salida/`, carpeta ignorada por git porque contiene
datos de personas.

| Script | Para qué |
|---|---|
| [bitacora.py](bitacora.py) | Lee `turnos_auditoria`. Es la fuente buena, desde el 2026-09-02 |
| [verificar_bitacora.py](verificar_bitacora.py) | Comprueba contra la base real que la bitácora registra bien |
| [correcciones_por_admin.py](correcciones_por_admin.py) | Lee el rastro de texto en `Observaciones`. Cubre lo anterior a la bitácora |
| [fuentes_de_auditoria.py](fuentes_de_auditoria.py) | Inventaría qué fuentes de auditoría hay en el servidor |
| [comparar_con_sheets.py](comparar_con_sheets.py) | Reconstruye el antes y el después desde el historial de Drive. Ya sin uso futuro: el espejo está apagado |

Para el histórico:

```powershell
.venv\Scripts\python.exe auditoria\correcciones_por_admin.py
.venv\Scripts\python.exe auditoria\correcciones_por_admin.py gproanio --solo-marcados --csv
```

Sin argumentos muestra el resumen por administrador. Con un usuario, su detalle.

## La bitácora de cambios, desde el 2026-09-02

Existe `turnos_auditoria`, la tabla que responde de forma directa quién cambió
qué horas y desde qué valor. Sustituye a todo lo que se describe más abajo, que
queda como registro de cómo se auditaba antes de tenerla y de por qué hizo
falta.

El esquema, con el porqué de cada campo, está en
[migracion/auditoria_schema.sql](../migracion/auditoria_schema.sql). Lo esencial:

- Un trigger es el único escritor. La aplicación no inserta, solo deja el autor
  en la sesión (`core/data.py:_sellar_autor`).
- Guarda la fila completa antes y después en `jsonb`, más `horas_antes`,
  `horas_despues` y un `delta_horas` calculado.
- Registra también lo que se edite por fuera de la aplicación, con
  `origen = 'fuera_de_la_app'`.
- No se purga. Es el respaldo permanente de las horas que recorta supervisión.

Para leerla:

```powershell
.venv\Scripts\python.exe auditoria\bitacora.py
.venv\Scripts\python.exe auditoria\bitacora.py --usuario gproanio --csv
.venv\Scripts\python.exe auditoria\bitacora.py --desde 2026-09-01
```

Para comprobar que sigue registrando bien, por ejemplo después de tocar el
esquema o de un cambio en la capa de datos:

```powershell
.venv\Scripts\python.exe auditoria\verificar_bitacora.py
```

Son 23 comprobaciones contra la base real, con un turno de prueba que se crea y
se borra en la misma corrida. Pasaron todas al aplicar el esquema.

### Tres cosas que hay que tener presentes

La marcación normal del colaborador también queda registrada, y eso es
deliberado. Es la única forma de tener el valor original contra el que comparar
una corrección posterior. Es precisamente lo que no existía y lo que obligó a
reconstruir 45 h desde el historial de Drive.

Al sumar horas hay que filtrar por `accion`. En un `DELETE` no hay valor
posterior, así que `delta_horas` sale como la pérdida completa del turno. Es
correcto, pero no es comparable con el recorte de un `UPDATE`. Las consultas de
`bitacora.py` ya lo separan.

Mientras Streamlit Cloud no redespliegue con el código que sella el autor, todo
cambio entra como `origen = 'fuera_de_la_app'` y sin usuario. El trigger lo
captura igual, pero sin responsable. La primera fila de la bitácora, una salida
marcada el 2026-09-02 a las 19:06 UTC, es justo ese caso.

## Dónde vive la traza

No hay tabla de auditoría. La única huella la escribe
[views/super_admin.py:2179](../views/super_admin.py#L2179) dentro de la columna
`turnos.observaciones`:

```
[Corrección 2026-08-11 por gproanio]: CIERRE DE VUELO. | [Orig]: Horas extra justificadas: CUADRE DE VUELOS
```

Quedan registrados el usuario, la fecha de la corrección, el motivo que escribió
el administrador y la observación previa del colaborador. No queda el valor
anterior de las horas ni de los timestamps: el `UPDATE` los sobrescribe.

Las otras dos huellas del panel no identifican al autor de la misma forma. El
cierre manual de un turno en revisión y el registro manual de entradas usan el
prefijo `Registro manual:` sin usuario, y las eventualidades usan el sufijo
`(registrado por <usuario>)`. Solo el tag `[Corrección ...]` corresponde a la
edición de un turno que ya estaba cerrado.

`actualizado_en` no sirve como fecha de corrección.
[core/data.py:archivar_historico](../core/data.py) también lo actualiza al
cerrar el mes, de modo que cientos de filas comparten el mismo valor. La fecha
fiable es la del tag.

## Qué se podía afirmar antes de la bitácora, y qué no

El conteo de correcciones es exacto. El delta de horas no es recuperable desde
la base, porque el valor anterior no se guarda en ninguna parte. El espejo en
Google Sheets tampoco ayuda de forma directa: `espejo_actualizar` sobrescribe la
misma celda.

Antes de dar eso por cerrado se revisaron todas las fuentes del servidor. La
comprobación está en la sección siguiente.

Hay una cota inferior para un subconjunto de casos. La app solo le pide
justificación al colaborador cuando el turno pasa de `UMBRAL_HORAS_EXTRA`
(9.5 h, ver [core/marcado.py:208](../core/marcado.py#L208)). Por tanto, si la
observación preservada como `[Orig]` empieza con "Horas extra justificadas", ese
turno tenía más de 9.5 h antes de la corrección. Si hoy tiene 9.5 h o menos, la
corrección recortó horas, y el recorte fue de al menos
`9.5 - horas_actuales`. Para los turnos que siguen sobre 9.5 h después de la
corrección no hay forma de saber si las horas cambiaron.

Esa cota es lo que reporta la sección "Cota inferior de horas recortadas". Es un
piso, no una medición.

## Por qué los logs de Supabase no tienen el antes y el después

Se comprobó contra el servidor el 2026-09-02, no por conjetura. Postgres 17.6.

| Fuente | Estado | Por qué no sirve |
|---|---|---|
| Logs de Postgres | `log_statement = ddl` | Solo se registran `CREATE`, `ALTER` y `DROP`. Los `UPDATE` de datos nunca se escribieron en el log, de modo que no es un problema de retención. |
| Logs por consulta lenta | `log_min_duration_statement = -1` | Desactivado. Tampoco hay captura por ese camino. |
| `pgaudit` | Disponible, no instalado | Registraría desde el día que se instale, nunca hacia atrás. |
| `supa_audit` | Ni disponible en el proyecto | Es la extensión que guarda `old_record` y `record` en JSONB. No está. |
| Triggers en `turnos` | Ninguno | No hay tabla espejo ni bitácora propia. |
| Esquema `audit` | No existe | Los esquemas del proyecto son `public`, `auth`, `storage`, `realtime`, `graphql`, `vault`, `extensions` y `herramientas`. |
| `pg_stat_statements` | Activo desde 2026-07-15 | Guarda la consulta normalizada (`SET ts_entrada = $1`), sin los valores. Sirve para contar ejecuciones, no para reconstruir nada. |
| WAL y PITR | `archive_mode = on`, `wal_level = logical` | El WAL sí existe, pero no se consulta por SQL. PITR es add-on de plan y su ventana habitual es de 7 días, muy por detrás de las correcciones de junio a agosto. |
| Respaldos locales de la migración | No hay | `migracion/migrar_sheets_a_supabase.py` lee del Sheet y escribe en Postgres sin dejar copia intermedia, y no queda ningún CSV ni XLSX del corte. |

Resumen: en la base no hay antes y después, y no hay forma de fabricarlo hacia
atrás.

## Sí se puede reconstruir desde el Sheet espejo

Esto se comprobó, no se supone. Drive conserva una revisión del archivo por cada
tanda de escrituras del espejo y la API permite exportar cualquier revisión
pasada a CSV, con `canReadRevisions = true` para la cuenta de servicio que ya
usa la app. Al 2026-09-02 hay del orden de 630 revisiones disponibles, unas
quince por día.

Eso hace medible el antes y el después: se compara la última revisión del día
anterior a la corrección contra la última revisión del día de la corrección.
Lo automatiza [comparar_con_sheets.py](comparar_con_sheets.py):

```powershell
.venv\Scripts\python.exe auditoria\comparar_con_sheets.py gproanio --csv
```

Detalles de implementación que conviene conocer antes de tocarlo:

- Empareja por Nombre más Fecha de Turno, no por la clave natural de la app
  (Nombre más Timestamp Entrada), porque la corrección puede mover justamente el
  timestamp. Se verificó que entre los turnos corregidos no hay ninguna persona
  con dos turnos la misma fecha. Si algún día aparece, el script lo marca en vez
  de adivinar.
- Lee las hojas Registros e Historico de cada revisión, porque un turno pasa de
  la primera a la segunda al cerrar el mes.
- El tag de `Observaciones` solo trae la fecha, no la hora, de modo que la
  ventana de comparación es el día completo en hora de Ecuador (05:00Z a 05:00Z).
  Si un mismo turno se corrigió dos veces el mismo día, el delta que sale es el
  neto de ese día.
- Descarga solo las revisiones frontera que hacen falta, no las 630, y las
  guarda en `salida/revisiones/` como caché. El endpoint de exportación de
  Sheets responde 429 con facilidad, por eso hay pausa entre descargas y espera
  creciente.
- Un turno todavía abierto en la revisión previa no tiene horas. Eso se reporta
  como tal y no se confunde con una fila ausente.

Sobre la autoría: el espejo escribe con la cuenta de servicio, así que en el
Sheet toda edición aparece a nombre del service account, no de `gproanio`. El
valor y la fecha están; el autor se cruza con el tag de `Observaciones`.

## Los dos huecos de cobertura

La ventana útil del historial va del 2026-07-14 al 2026-08-26, y deja fuera 93
de las 258 correcciones de gproanio.

| Tramo | Correcciones | Motivo |
|---|---|---|
| Anterior al 2026-07-14 | 73 | Drive no conserva revisiones más viejas de este archivo. Google consolida y purga el historial por su cuenta, y el número exacto de revisiones cambia entre consultas. |
| Del 2026-07-14 al 2026-08-26 | 165 | Reconstruibles. Es lo que procesa el script. |
| Posterior al 2026-08-26 | 18 | El espejo dejó de escribir en el Sheet. |

Los dos huecos merecen atención distinta.

El primero se agrava con el tiempo: el historial de Drive se purga solo, de modo
que lo reconstruible de hoy puede no estarlo en unos meses. Si esta información
va a hacer falta, hay que sacar el CSV ahora y guardarlo, no dejarlo en Drive.

El segundo es un problema en curso. La última escritura del Sheet es del
2026-08-26T14:03Z, mientras que la base registró actividad hasta hoy y hay
correcciones fechadas hasta el 2026-09-01. O el espejo está apagado en los
secretos de Streamlit Cloud, que son independientes del `secrets.toml` local, o
está fallando en silencio, porque `core.data._espejo` se traga los errores del
respaldo a propósito para que no bloqueen una marcación. Conviene revisar el
panel de errores del espejo en la vista de admin y el valor de
`backup.espejo_sheets` en Streamlit Cloud. Mientras siga así, el respaldo no es
respaldo y esta vía de auditoría tampoco cubre lo nuevo.


## Resultado del corte del 2026-09-02

Correcciones de turnos cerrados desde que existe la traza:

| Administrador | Turnos | Personas | Rango de turnos |
|---|---|---|---|
| gproanio | 258 | 10, todas de BODEGA | 2026-06-02 a 2026-08-31 |
| pmena | 11 | 9 | 2026-06-05 a 2026-07-24 |
| dbuestan | 9 | 4 | 2026-06-02 a 2026-08-23 |
| mpillapa | 1 | 1 | 2026-05-26 |

### Cómo se cerró cada turno que gproanio corrigió

| Origen del cierre | Turnos |
|---|---|
| Marcación del colaborador, con justificación por pasar de 9.5 h | 250 |
| Marcación del colaborador, sin justificación por no pasar del umbral | 2 |
| Cierre o alta hecha por un administrador (`Registro manual:`) | 6 |

Los 252 primeros son los turnos con entrada y salida marcadas de verdad por el
colaborador, y son el universo relevante para revisar horas extra. Los 6
restantes no tuvieron marcación propia. Ninguno de los 258 tiene salida nula ni
estado distinto de Completo.

### Horas modificadas, medidas contra el Sheet

Sobre esos 252 turnos:

| | Turnos |
|---|---|
| Dentro de la ventana del historial de Drive | 161 |
| Con antes y después legibles | 123 |
| Con recorte real de horas | 39 |
| Con variación de 0.01 h, ruido de reescritura de celda | 41 |
| Solo cambió la observación, horas intactas | 43 |
| Sin dato suficiente | 38 |

**Horas recortadas medidas: 45.12 h en 39 turnos, con una media de 1.16 h por
turno afectado.** No hay ni un aumento real de horas: los 41 casos al alza son
todos de 0.01 h.

| Funcionario | Turnos recortados | Horas |
|---|---|---|
| Almagro David | 7 | 9.74 |
| Panimboza Javier | 9 | 9.40 |
| Tipantiza Luis | 8 | 8.20 |
| Arellano Romel | 6 | 8.01 |
| Pazuna Pablo | 3 | 4.98 |
| Nango Patricio | 2 | 2.27 |
| Collaguazo Darwin | 2 | 1.49 |
| Yanza Cristina | 2 | 1.03 |

El patrón no es de ajuste fino. De los 39 recortes, 26 son de una hora o más y
la mayoría cae en valores exactos de 1.00 o 2.00 h. Las horas resultantes se
agrupan entre 9.2 y 9.7, es decir apenas por encima de la jornada base de 9 h,
lo que deja la hora extra del turno cerca de cero. Los motivos escritos son casi
siempre "SOPORTE EN OPS." o "CIERRE DE VUELO.".

Los recortes más grandes:

| Funcionario | Turno | Corregido | Antes | Después | Delta |
|---|---|---|---|---|---|
| Tipantiza Luis | 2026-08-18 | 2026-08-19 | 12.23 | 9.23 | −3.00 |
| Pazuna Pablo | 2026-08-17 | 2026-08-19 | 12.71 | 9.72 | −2.99 |
| Almagro David | 2026-07-29 | 2026-07-30 | 12.05 | 9.72 | −2.33 |
| Panimboza Javier | 2026-08-13 | 2026-08-14 | 12.96 | 10.95 | −2.01 |
| Almagro David | 2026-08-01 | 2026-08-03 | 11.49 | 9.48 | −2.01 |
| Arellano Romel | 2026-07-26 | 2026-07-27 | 11.93 | 9.93 | −2.00 |
| Panimboza Javier | 2026-08-14 | 2026-08-15 | 11.32 | 9.32 | −2.00 |
| Arellano Romel | 2026-08-18 | 2026-08-19 | 11.73 | 9.73 | −2.00 |

### Lo que la cifra no cubre

Las 45.12 h salen de 123 de los 252 turnos, menos de la mitad. Los 129 restantes
no son casos sin recorte, son casos sin medición: 91 caen fuera de la ventana
del historial de Drive y 38 no dejaron un estado intermedio comparable.

Manteniendo la proporción observada, 39 recortes por cada 123 turnos medidos a
1.16 h de media, el total sobre los 252 rondaría los 90 h. Es una extrapolación,
no una medición, y como tal no sirve para una discusión formal. Lo medido son
45.12 h, y esa cifra es un piso.

Para referencia, la cota que se podía calcular solo con la base era de 6.12 h.
La medición contra el Sheet la multiplica por siete, lo que da la dimensión real
de lo que se pierde por no guardar el valor anterior.

### Los 38 sin dato, y por qué

| Motivo | Turnos |
|---|---|
| La salida y la corrección cayeron en la misma revisión de Drive | 20 |
| No se encontró el tag en las revisiones de esa fecha | 13 |
| No hay revisión anterior en el historial | 2 |
| El turno no estaba en esa versión | 2 |
| Clave ambigua, dos turnos de la misma persona esa fecha | 1 |

Los 20 primeros son un límite de Google, no del método: cuando el colaborador
marca la salida y el supervisor corrige pocos minutos después, Drive agrupa las
dos escrituras en una sola revisión y el estado intermedio no llega a existir.


## Dos cosas que convendría arreglar en la app

Salen a la vista al montar esta consulta y no están resueltas:

1. Al corregir dos veces el mismo turno, el tag se anida y el campo crece sin
   límite. Hay al menos una fila con
   `[Corrección ...] | [Orig]: [Corrección ...] | [Orig]: Horas extra ...`, lo
   que además hace que el conteo por expresión regular difiera en uno del conteo
   por `ilike`.
2. La traza no guarda el valor anterior. Es la corrección de fondo si la
   trazabilidad de horas extra va a sostener una discusión con Recursos Humanos
   o con el colaborador, y hoy no hay nada en el servidor que la supla.

   Dos formas de resolverlo, ninguna implementada todavía:

   - Un trigger `after update on turnos` que escriba `OLD` y `NEW` en una tabla
     `turnos_auditoria`. Captura todo, incluso una edición hecha a mano desde el
     Table Editor de Supabase. El punto flojo es el autor: el trigger no sabe
     quién es el admin, salvo que `core/data.actualizar_por_entrada` haga un
     `set_config('app.usuario', ...)` en la misma transacción.
   - Capturar el antes en la propia aplicación, en
     `core/data.actualizar_por_entrada`, leyendo la fila con `RETURNING` dentro
     de la transacción y guardándola en `turnos_auditoria` junto al usuario que
     ya tiene a mano. Más simple de escribir y con el autor resuelto, pero no
     registra los cambios hechos por fuera de la app.

   Cualquiera de las dos es un cambio de esquema con migración y prueba, no un
   parche. Requiere decidir antes cuánto tiempo se conserva la bitácora y quién
   puede leerla, porque son datos laborales de personas identificadas.
