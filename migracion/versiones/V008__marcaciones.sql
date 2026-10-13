-- La marcación original, guardada aparte y sin posibilidad de editarla.
--
-- Este es el cambio de fondo. Hasta ahora la entrada y la salida que marcó el
-- colaborador vivían en la misma fila que después corregía el supervisor, y la
-- corrección las sobrescribía. La bitácora rescata el valor anterior, pero la
-- evidencia seguía siendo la fila editable.
--
-- Desde aquí cada marcación queda además en `marcaciones`, una tabla de solo
-- inserción. `turnos` sigue siendo la vista vigente del turno, la que se paga;
-- `marcaciones` es lo que pasó en el reloj. Si difieren, la diferencia es
-- exactamente una corrección, y tiene que haber un ajuste que la explique (V009).
--
-- Qué cuenta como marcación
-- -------------------------
-- La primera vez que un turno recibe entrada y la primera vez que recibe
-- salida. Cambios posteriores del horario no son marcaciones, son
-- correcciones: van a la bitácora y a ajustes_turno. Las eventualidades no
-- generan marcaciones, porque nadie marcó nada.
--
-- `origen` dice quién la registró: el colaborador en el quiosco, un
-- administrador cuando la marcación fue manual, o 'reconstruido' para las que
-- se cargaron al crear la tabla (ver abajo).


create table if not exists marcaciones (
    id              bigint generated always as identity primary key,
    -- Sin foreign key, igual que la bitácora: tiene que sobrevivir a cualquier
    -- cosa que le pase al turno.
    turno_id        bigint not null,
    empleado_id     bigint,
    nombre          text not null,
    tipo            text not null check (tipo in ('entrada', 'salida')),
    momento         timestamptz not null,
    origen          text not null
                    check (origen in ('colaborador', 'admin', 'sistema', 'fuera_de_la_app', 'reconstruido')),
    registrado_por  text not null default '',
    registrado_en   timestamptz not null default now()
);

create index if not exists ix_marcaciones_turno    on marcaciones (turno_id);
create index if not exists ix_marcaciones_empleado on marcaciones (empleado_id, momento);
-- Una sola entrada y una sola salida por turno.
create unique index if not exists ux_marcaciones_turno_tipo on marcaciones (turno_id, tipo);

select cerrar_a_api('marcaciones');

drop trigger if exists tg_marcaciones_inmutable on marcaciones;
create trigger tg_marcaciones_inmutable
before update or delete on marcaciones
for each row execute function fn_solo_insercion();

drop trigger if exists tg_marcaciones_sin_truncate on marcaciones;
create trigger tg_marcaciones_sin_truncate
before truncate on marcaciones
for each statement execute function fn_solo_insercion();


create or replace function fn_turnos_marcaciones() returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    v_usuario text := coalesce(nullif(current_setting('app.usuario', true), ''), '');
    v_origen  text := coalesce(nullif(current_setting('app.origen',  true), ''), 'fuera_de_la_app');
begin
    if new.evento <> '' then
        return null;
    end if;

    if tg_op = 'INSERT' then
        insert into marcaciones (turno_id, empleado_id, nombre, tipo, momento, origen, registrado_por)
        values (new.id, new.empleado_id, new.nombre, 'entrada', instante(new.ts_entrada), v_origen, v_usuario)
        on conflict (turno_id, tipo) do nothing;
    end if;

    -- La salida se registra la primera vez que aparece: al marcarla en el
    -- quiosco, al cerrar un turno en revisión, o en el alta de un turno
    -- histórico completo hecho por un administrador.
    if new.ts_salida is not null
       and (tg_op = 'INSERT' or old.ts_salida is null) then
        insert into marcaciones (turno_id, empleado_id, nombre, tipo, momento, origen, registrado_por)
        values (new.id, new.empleado_id, new.nombre, 'salida', instante(new.ts_salida), v_origen, v_usuario)
        on conflict (turno_id, tipo) do nothing;
    end if;

    return null;
end
$$;

drop trigger if exists tg_turnos_marcaciones on turnos;
create trigger tg_turnos_marcaciones
after insert or update of ts_salida on turnos
for each row execute function fn_turnos_marcaciones();


-- Carga inicial. Lo que se puede saber del pasado, marcado como 'reconstruido':
--
-- Entrada: si la bitácora registró un cambio de entrada, el valor anterior al
-- primero de esos cambios; si no, la entrada actual.
--
-- Salida: si la bitácora registró el momento en que el colaborador la marcó,
-- ese valor; si registró correcciones de la salida, el valor anterior a la
-- primera; si no, la salida actual.
--
-- Advertencia que repite migracion/README.md: para turnos corregidos antes del
-- 2026-09-02, fecha en que empieza la bitácora, el "valor actual" puede ser ya
-- un valor corregido. No hay forma de saberlo desde la base.
with
primer_cambio_entrada as (
    select distinct on (turno_id) turno_id, (antes ->> 'ts_entrada')::timestamp as valor
    from turnos_auditoria
    where accion = 'UPDATE' and antes ->> 'ts_entrada' is distinct from despues ->> 'ts_entrada'
    order by turno_id, momento, id
),
salida_marcada as (
    select distinct on (turno_id) turno_id, (despues ->> 'ts_salida')::timestamp as valor
    from turnos_auditoria
    where accion = 'UPDATE' and antes ->> 'ts_salida' is null and despues ->> 'ts_salida' is not null
    order by turno_id, momento, id
),
primer_cambio_salida as (
    select distinct on (turno_id) turno_id, (antes ->> 'ts_salida')::timestamp as valor
    from turnos_auditoria
    where accion = 'UPDATE' and antes ->> 'ts_salida' is not null
      and antes ->> 'ts_salida' is distinct from despues ->> 'ts_salida'
    order by turno_id, momento, id
)
insert into marcaciones (turno_id, empleado_id, nombre, tipo, momento, origen, registrado_por, registrado_en)
select t.id, t.empleado_id, t.nombre, x.tipo, instante(x.valor), 'reconstruido', 'migracion V008', t.creado_en
from turnos t
left join primer_cambio_entrada pe on pe.turno_id = t.id
left join salida_marcada        sm on sm.turno_id = t.id
left join primer_cambio_salida  ps on ps.turno_id = t.id
cross join lateral (values
    ('entrada', coalesce(pe.valor, t.ts_entrada)),
    ('salida',  coalesce(sm.valor, ps.valor, t.ts_salida))
) as x (tipo, valor)
where t.evento = ''
  and x.valor is not null
on conflict (turno_id, tipo) do nothing;
