-- La persona deja de ser solo su nombre.
--
-- Hasta ahora un turno se ataba a su dueño por el texto del nombre, y todos los
-- cruces se hacían por lower(nombre). Un cambio de nombre, una tilde de más o
-- dos personas que se llamen igual rompen la historia sin aviso. Esta tabla da
-- a cada persona un identificador estable.
--
-- Lo que no cambia todavía
-- ------------------------
-- El padrón sigue viviendo en secrets.toml: el PIN de cada colaborador son los
-- últimos cuatro dígitos de su cédula, y moverlo a la base es una decisión de
-- seguridad aparte. migracion/sincronizar_empleados.py copia a esta tabla solo
-- el nombre y el área, nunca el PIN. La columna `nombre` de turnos se conserva:
-- la aplicación sigue buscando por ella, y el id se mantiene al día solo.
--
-- El id de esta tabla no es el de nómina. Atarlo al sistema de nómina o al ERP
-- es una integración que hay que validar con Tecnología; no se supone aquí.


create table if not exists empleados (
    id              bigint generated always as identity primary key,
    nombre          text not null,
    area            text not null default '',
    activo          boolean not null default true,
    -- padron: viene de secrets.toml vía sincronizar_empleados.py
    -- historico: solo aparece en turnos viejos, ya no está en el padrón
    -- automatico: lo creó el trigger al llegar un nombre desconocido; revisar
    origen          text not null default 'padron'
                    check (origen in ('padron', 'historico', 'automatico')),
    creado_en       timestamptz not null default now(),
    actualizado_en  timestamptz not null default now()
);

create unique index if not exists ux_empleados_nombre on empleados (lower(nombre));

select cerrar_a_api('empleados');


alter table turnos add column if not exists empleado_id bigint references empleados (id);
create index if not exists ix_turnos_empleado on turnos (empleado_id, fecha_turno);


-- Carga inicial desde los turnos existentes. Para el área se toma la del turno
-- más reciente de cada persona. Quedan como 'historico' hasta que la
-- sincronización con el padrón los confirme.
insert into empleados (nombre, area, origen)
select distinct on (lower(nombre)) nombre, area, 'historico'
from turnos
order by lower(nombre), fecha_turno desc, id desc
on conflict ((lower(nombre))) do nothing;

-- El UPDATE no ensucia la bitácora: empleado_id no está en la condición del
-- trigger de auditoría, y la validación de V004 lo trata como cambio técnico.
update turnos t
set empleado_id = e.id
from empleados e
where lower(e.nombre) = lower(t.nombre)
  and t.empleado_id is distinct from e.id;


-- Mantiene empleado_id al día en cada alta o cambio de nombre. Si el nombre no
-- existe, crea la persona como 'automatico' en vez de rechazar el turno: un
-- colaborador que marca no puede quedarse sin registro por un problema del
-- padrón. Esos casos se revisan con la consulta de migracion/README.md.
create or replace function fn_turnos_empleado() returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    v_id bigint;
begin
    select id into v_id from empleados where lower(nombre) = lower(new.nombre);
    if v_id is null then
        insert into empleados (nombre, area, origen)
        values (new.nombre, new.area, 'automatico')
        on conflict ((lower(nombre))) do nothing
        returning id into v_id;
        -- Otra transacción pudo crearlo entre el select y el insert.
        if v_id is null then
            select id into v_id from empleados where lower(nombre) = lower(new.nombre);
        end if;
    end if;
    new.empleado_id := v_id;
    return new;
end
$$;

drop trigger if exists tg_turnos_20_empleado on turnos;
create trigger tg_turnos_20_empleado
before insert or update of nombre on turnos
for each row execute function fn_turnos_empleado();
