-- El evento de la tolerancia se llama TOLERANCIA, en toda la base.
--
-- Pedido el 2026-10-29: en lugar de 'Turno completo con tolerancia de horas
-- extra', el evento es solo 'TOLERANCIA', y vale para todo el histórico, no
-- solo para septiembre y octubre como hizo V014. La regla no cambia: lleva el
-- evento el turno de trabajo de más de 9 h y hasta 9 h 30; los demás quedan
-- vacíos (V015).
--
-- Al 2026-10-29 en producción:
--   449 turnos de septiembre y octubre cambian el texto largo por TOLERANCIA;
--   976 turnos de mayo a agosto, en el rango y con el evento vacío, lo reciben.
--
-- Solo cambia `evento`; las horas no se mueven. Cada cambio queda en la
-- bitácora con origen 'sistema' y usuario 'migracion V016'.
--
-- Los textos anteriores siguen en es_eventualidad(): si alguno sobrevive, cuenta
-- como trabajo y no como ausencia.


create or replace function evento_con_tolerancia() returns text
language sql immutable
as $$ select 'TOLERANCIA'::text $$;

create or replace function es_eventualidad(p_evento text) returns boolean
language sql immutable
as $$
    select coalesce(p_evento, '') not in (
        '', evento_con_tolerancia(),
        -- Nombres anteriores: V013 hasta V015 y V016.
        'Turno completo sin novedad', 'Turno completo con tolerancia de horas extra'
    )
$$;


select set_config('app.usuario', 'migracion V016', true),
       set_config('app.origen',  'sistema',        true),
       set_config('app.motivo',  'V016: el evento de la tolerancia es TOLERANCIA, en todo el histórico', true);

update turnos
set evento = evento_con_tolerancia(),
    actualizado_en = now()
where not anulado
  and (   evento = 'Turno completo con tolerancia de horas extra'
       or (evento = ''
           and ts_salida is not null
           and evento_de_jornada(horas_trabajadas) = evento_con_tolerancia()));
