-- El evento de jornada de V013 para los turnos de septiembre y octubre de 2026.
--
-- V013 llena el evento al cerrarse un turno, desde el 2026-10-21. Pedido ese
-- mismo día: aplicar la misma regla a los turnos de trabajo de septiembre y
-- octubre que ya estaban cerrados. Lo anterior a septiembre queda con el
-- evento vacío, a propósito.
--
-- Qué toca y qué no
-- -----------------
-- Solo la columna `evento`, y solo en turnos de trabajo vigentes, con salida y
-- con el evento vacío. Las horas no cambian: la regla es la de V013, que mira
-- la duración. Los turnos de más de 9 h 30 siguen vacíos, igual que los
-- abiertos, los anulados y las eventualidades.
--
-- Al 2026-10-21 en producción: 929 quedan sin novedad, 378 con tolerancia y 595
-- siguen vacíos por pasar de 9 h 30.
--
-- Cada cambio queda en la bitácora con origen 'sistema' y usuario
-- 'migracion V014', como hizo V006. La pestaña "Modificaciones de turnos" no los
-- muestra: lista los cambios de supervisión y los hechos por fuera de la
-- aplicación.
--
-- Volver a correrla no cambia nada: después de la primera vez ya no quedan
-- turnos del rango con el evento vacío que la regla pueda llenar.


select set_config('app.usuario', 'migracion V014', true),
       set_config('app.origen',  'sistema',        true),
       set_config('app.motivo',  'V014: evento de jornada para los turnos de septiembre y octubre de 2026', true);

update turnos
set evento = evento_de_jornada(horas_trabajadas),
    actualizado_en = now()
where not anulado
  and ts_salida is not null
  and evento = ''
  and fecha_turno >= date '2026-09-01'
  and fecha_turno <  date '2026-11-01'
  and evento_de_jornada(horas_trabajadas) <> '';
