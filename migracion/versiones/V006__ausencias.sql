-- Las eventualidades dejan de reconocerse por el texto de la observación.
--
-- Un permiso, una falta justificada o unas vacaciones se registran como turnos
-- de 08:00 a 17:00, y hasta ahora la única forma de distinguirlos de un turno
-- trabajado era que la observación empezara con "Vacaciones", "Solicitud de
-- permiso" o "Justificación de falta". La columna `evento` existía pero estaba
-- vacía en todas las filas.
--
-- Por qué no se sacan de turnos
-- -----------------------------
-- Esos turnos suman horas efectivas en el comparativo contra horas esperadas
-- del panel. Moverlos a otra tabla obligaría a reescribir esos cálculos, y si un
-- permiso debe contar como horas es una decisión de los superadmins, no de la
-- base. Siguen en turnos, pero ahora:
--
--   - `evento` dice qué son, sin depender del texto;
--   - `ausencia_id` los ata a la solicitud que los creó, con el rango completo,
--     el motivo y quién la registró;
--   - quedan fuera de la comprobación de solapes de V004.
--
-- La anulación de un día de permiso pasa por el mismo flujo que la de
-- cualquier turno, con motivo y, si corresponde, aprobación. Ese fue el caso de
-- los 81 h de permisos borrados en septiembre de 2026.


create table if not exists ausencias (
    id              bigint generated always as identity primary key,
    empleado_id     bigint references empleados (id),
    nombre          text not null,
    tipo            text not null
                    check (tipo in ('Justificación de falta', 'Solicitud de permiso', 'Vacaciones')),
    subtipo         text not null default '',
    desde           date not null,
    hasta           date not null,
    registrado_por  text not null,
    registrado_en   timestamptz not null default now(),
    -- panel: registrada desde Eventualidades
    -- migrado: reconstruida a partir de un turno anterior a esta versión
    origen          text not null default 'panel' check (origen in ('panel', 'migrado')),
    constraint ck_ausencias_rango check (hasta >= desde)
);

create index if not exists ix_ausencias_empleado on ausencias (empleado_id, desde);
select cerrar_a_api('ausencias');

alter table turnos add column if not exists ausencia_id bigint references ausencias (id);
create index if not exists ix_turnos_ausencia on turnos (ausencia_id) where ausencia_id is not null;


-- Tipo de eventualidad a partir del texto de la observación. Tolera la tilde,
-- que viaja distinta según de dónde salió el texto. Devuelve '' si no es una
-- eventualidad. Mismo criterio que _clasificar_obs en views/super_admin.py.
create or replace function tipo_de_eventualidad(p_obs text) returns text
language sql immutable
as $$
    select case
        when translate(lower(trim(coalesce(p_obs, ''))), 'áéíóú', 'aeiou') like 'vacaciones%'
            then 'Vacaciones'
        when translate(lower(trim(coalesce(p_obs, ''))), 'áéíóú', 'aeiou') like 'justificacion de falta%'
            then 'Justificación de falta'
        when translate(lower(trim(coalesce(p_obs, ''))), 'áéíóú', 'aeiou') like 'solicitud de permiso%'
            then 'Solicitud de permiso'
        else ''
    end
$$;


-- Carga inicial: una ausencia por cada turno-eventualidad existente. El rango
-- original de la solicitud no se guardó en ninguna parte, así que no se
-- reconstruye; cada día queda como su propia ausencia, marcada 'migrado'.
--
-- El cambio de `evento` sí pasa por la bitácora. Se sella como sistema y con
-- motivo, para que se lea como lo que es y no como una edición sin autor.
do $$
declare
    r     record;
    v_id  bigint;
begin
    perform set_config('app.usuario', 'migracion V006', true);
    perform set_config('app.origen',  'sistema', true);
    perform set_config('app.motivo',  'V006: la eventualidad pasa a identificarse por la columna evento', true);

    for r in
        select id, nombre, empleado_id, fecha_turno, observaciones,
               tipo_de_eventualidad(observaciones) as tipo
        from turnos
        where tipo_de_eventualidad(observaciones) <> ''
          and ausencia_id is null
        order by id
    loop
        insert into ausencias (empleado_id, nombre, tipo, subtipo, desde, hasta, registrado_por, origen)
        values (
            r.empleado_id, r.nombre, r.tipo,
            coalesce(substring(r.observaciones from ' - (.*?) \(registrado por'), ''),
            r.fecha_turno, r.fecha_turno,
            coalesce(nullif(substring(r.observaciones from '\(registrado por ([^)]+)\)'), ''), 'desconocido'),
            'migrado'
        )
        returning id into v_id;

        update turnos set ausencia_id = v_id, evento = r.tipo where id = r.id;
    end loop;
end $$;


-- Una eventualidad que llegue sin `evento`, como las que registra la versión
-- actual de la aplicación hasta que se despliegue la nueva, se clasifica sola.
create or replace function fn_turnos_evento() returns trigger
language plpgsql
as $$
begin
    if new.evento = '' then
        new.evento := tipo_de_eventualidad(new.observaciones);
    end if;
    return new;
end
$$;

drop trigger if exists tg_turnos_25_evento on turnos;
create trigger tg_turnos_25_evento
before insert on turnos
for each row execute function fn_turnos_evento();
