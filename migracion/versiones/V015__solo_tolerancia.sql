-- Solo la tolerancia lleva evento.
--
-- Cambio pedido el 2026-10-28 sobre V013: un turno de trabajo de 9 h o menos
-- deja el evento vacío, en vez de 'Turno completo sin novedad'. La regla queda:
--
--   hasta 9 h                      vacío
--   más de 9 h y hasta 9 h 30      'Turno completo con tolerancia de horas extra'
--   más de 9 h 30                  vacío: el colaborador tuvo que justificarlo
--
-- Además vacía el evento de los turnos que V013 y V014 ya marcaron como sin
-- novedad: al 2026-10-28 en producción, 554 de septiembre y 523 de octubre.
-- Solo cambia `evento`; las horas no se mueven. Cada cambio queda en la
-- bitácora con origen 'sistema' y usuario 'migracion V015'.
--
-- evento_sin_novedad() y su lugar en es_eventualidad() se conservan: si queda
-- algún turno con ese texto, sigue contando como trabajo y no como ausencia.
--
-- Compatible con la versión de la aplicación en producción: no cambia ninguna
-- firma, y el panel ya cuenta el evento vacío como turno de trabajo.


create or replace function evento_de_jornada(p_horas numeric) returns text
language sql stable
as $$
    select case
        when p_horas is null                                                         then ''
        when p_horas <= regla('jornada_base_h')                                      then ''
        when p_horas <= regla('jornada_base_h') + regla('tolerancia_horas_extra_h') then evento_con_tolerancia()
        else ''
    end
$$;


select set_config('app.usuario', 'migracion V015', true),
       set_config('app.origen',  'sistema',        true),
       set_config('app.motivo',  'V015: el turno de 9 h o menos deja el evento vacío', true);

update turnos
set evento = '',
    actualizado_en = now()
where evento = evento_sin_novedad()
  and not anulado;
