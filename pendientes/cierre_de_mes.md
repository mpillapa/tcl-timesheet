# Cierre de mes en el turnero

Tema pendiente, con prioridad alta. Hay que acordarlo con la gerencia y los
jefes de área antes de activar el cierre de mes. Registrado el 2026-10-14.

## Por qué hace falta

Cuando termina un mes, sus turnos se marcan como archivados, pero esa marca no
impide cambiarlos. Un turno puede modificarse después de que la gerencia revisó
los datos o de que se calculó la nómina, y nadie se entera salvo que revise la
bitácora.

Lo que pasó con septiembre de 2026:

| Cuándo se hizo el cambio | Cambios de supervisión | Efecto neto en horas trabajadas | Quiénes |
|---|---|---|---|
| Durante septiembre | 120 | +128,26 h | dbuestan, ecamposano, gproanio, mpillapa |
| Del 1 al 7 de octubre | 100 | −30,03 h | dbuestan, gproanio, pmena |
| Del 8 al 14 de octubre | 0 | | |

Casi la mitad de los cambios llegó después de terminado el mes, todos en la
primera semana de octubre, que es cuando los jefes preparan la reunión con la
gerencia. En conjunto quitaron horas. Después del 7 de octubre no hubo ningún
cambio más a septiembre, lo que coincide con el proceso descrito abajo. Es un
solo mes de datos: conviene confirmarlo con octubre.

## Cómo funciona hoy el proceso

1. Termina el mes.
2. Durante la primera semana del mes siguiente, los jefes de área hacen las
   correcciones del mes que terminó.
3. Los jefes llegan a la reunión con la gerencia, en esa misma primera semana,
   con las correcciones ya hechas.
4. Después de la reunión no debería haber más correcciones. En el peor caso,
   alguna llega dentro de los primeros 15 días del mes siguiente.

Cerrar el mes el día 1 bloquearía las correcciones previas a la reunión. El
cierre tiene que ir después de la reunión, con un margen hasta el día 15 como
máximo. Un cambio después de esa fecha ya no corresponde al proceso normal y
debería quedar justificado.

## Qué ya está construido

El turnero ya tiene el mecanismo listo, sin usar
([migracion/versiones/V007__periodos_de_nomina.sql](../migracion/versiones/V007__periodos_de_nomina.sql)):

- `periodos_nomina` guarda si cada mes está abierto o cerrado.
- `periodos_eventos` guarda el historial de cierres y reaperturas, con
  usuario, motivo y hora. No se puede borrar ni editar.
- Con un mes cerrado, la base rechaza crear, corregir o anular turnos de ese
  mes, tanto desde la aplicación como desde Supabase.
- Un mes cerrado se puede reabrir dando un motivo. La reapertura queda en el
  historial.
- Solo se puede cerrar un mes que ya terminó. El mes en curso nunca se
  bloquea.
- Nadie cierra nada solo: cierra un superadmin con `gestiona_periodos = true`
  en los secrets, desde la pestaña "Ajustes y auditoría".

Hoy ningún mes está cerrado y nadie tiene ese permiso, así que el mecanismo no
tiene ningún efecto.

## Propuesta para discutir

```
Mes de septiembre
│
├─ 30-sep    Termina el mes                     → abierto
├─ 1–7 oct   Jefes hacen las correcciones       → abierto, con motivo y, si quitan
│                                                  más de 10 min, con aprobación
├─ 1.a sem.  Reunión con la gerencia, con las   → abierto
│            correcciones ya hechas
├─ 8–15 oct  Margen para algún caso pendiente   → abierto
├─ 15 oct    Fecha de corte                     → se cierra septiembre
└─ después   Cambio fuera del proceso           → reabrir con motivo registrado
```

## Lo que hay que decidir en la reunión

1. **Fecha de corte.** El día del mes siguiente en que los datos quedan
   como definitivos. Tiene que caer después de la reunión con la gerencia.
   Propuesta: el día 15, que deja margen aunque la reunión se retrase. Si se
   quiere más estricto, el día siguiente a la reunión.
2. **Quién cierra el mes.** Uno o dos superadmins con `gestiona_periodos`.
3. **Quién puede reabrir y en qué casos.** Por ejemplo, solo con un motivo
   que cite la autorización de la gerencia.
4. **Si se cierra o solo se vigila.** Una alternativa sin bloqueo: un
   indicador en la pestaña de auditoría con los cambios hechos a meses ya
   terminados, con fecha y autor. No impide nada, pero deja ver cuánto se
   corrige después de la reunión. También puede usarse unos meses para
   confirmar con datos que el día 15 es suficiente.

## Qué falta construir según lo que se decida

| Decisión | Trabajo técnico |
|---|---|
| Cerrar con fecha de corte | Nada en la base. Configurar `gestiona_periodos` en los secrets y documentar la fecha |
| Solo vigilar | El indicador de cambios a meses anteriores, solo en el panel, sin migración |
| Cierre automático en la fecha de corte | Una tarea programada. Hay que evaluar dónde corre y quién la mantiene |
| No hacer nada | Nada. Las tablas quedan vacías y no afectan al funcionamiento |
