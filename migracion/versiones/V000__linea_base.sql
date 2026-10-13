-- Línea base: el esquema tal como está en producción al 2026-10-13.
--
-- Reúne lo que hasta ahora vivía en tres archivos sueltos que se pegaban a mano
-- en el SQL Editor: migracion/schema.sql, seguridad/habilitar_rls.sql y
-- migracion/auditoria_schema.sql. Esos archivos se conservan como registro de
-- cómo se llegó aquí, pero a partir de ahora la fuente es esta carpeta.
--
-- Es idempotente a propósito. En producción el esquema ya existe y esta
-- migración no cambia nada: recrea los mismos triggers con la misma definición.
-- En una base vacía, como la de pruebas, lo construye desde cero.


-- ---------------------------------------------------------------------------
-- Tablas originales
-- ---------------------------------------------------------------------------
create table if not exists turnos (
    id                bigint generated always as identity primary key,
    nombre            text not null,
    area              text not null default '',
    fecha_turno       date not null,
    -- Hora local de Ecuador, sin zona. V102 las pasa a timestamptz.
    ts_entrada        timestamp not null,
    ts_salida         timestamp,
    horas_trabajadas  numeric(6,2),
    horas_efectivas   numeric(6,2),
    horas_extra       numeric(6,2),
    estado            text not null default 'Abierto',
    evento            text not null default '',
    observaciones     text not null default '',
    archivado         boolean not null default false,
    creado_en         timestamptz not null default now(),
    actualizado_en    timestamptz not null default now()
);

create unique index if not exists ux_turnos_nombre_entrada on turnos (lower(nombre), ts_entrada);
create index if not exists ix_turnos_estado    on turnos (estado);
create index if not exists ix_turnos_fecha     on turnos (fecha_turno);
create index if not exists ix_turnos_archivado on turnos (archivado);

create table if not exists horas_esperadas (
    anio   int not null,
    mes    int not null check (mes between 1 and 12),
    horas  numeric(7,2) not null,
    primary key (anio, mes)
);


-- ---------------------------------------------------------------------------
-- Cierre a la API REST (seguridad/habilitar_rls.sql)
-- ---------------------------------------------------------------------------
alter table turnos          enable row level security;
alter table horas_esperadas enable row level security;

-- Los roles de la API existen en Supabase. En la base de pruebas los crea el
-- arnés; el condicional evita fallar en una base que no los tenga.
do $$
begin
    if exists (select from pg_roles where rolname = 'anon') then
        revoke all on turnos, horas_esperadas from anon;
    end if;
    if exists (select from pg_roles where rolname = 'authenticated') then
        revoke all on turnos, horas_esperadas from authenticated;
    end if;
end $$;


-- ---------------------------------------------------------------------------
-- Bitácora (migracion/auditoria_schema.sql)
-- ---------------------------------------------------------------------------
create table if not exists turnos_auditoria (
    id            bigint generated always as identity primary key,
    turno_id      bigint not null,
    accion        text   not null,
    momento       timestamptz not null default now(),
    usuario       text not null default '',
    origen        text not null default 'fuera_de_la_app',
    antes         jsonb not null,
    despues       jsonb,
    horas_antes   numeric(6,2),
    horas_despues numeric(6,2),
    delta_horas   numeric(6,2) generated always as
                  (coalesce(horas_despues, 0) - coalesce(horas_antes, 0)) stored,
    nombre        text not null default '',
    fecha_turno   date
);

create index if not exists ix_turnos_aud_turno   on turnos_auditoria (turno_id);
create index if not exists ix_turnos_aud_usuario on turnos_auditoria (usuario, momento desc);
create index if not exists ix_turnos_aud_nombre  on turnos_auditoria (nombre, fecha_turno);
create index if not exists ix_turnos_aud_momento on turnos_auditoria (momento desc);
create index if not exists ix_turnos_aud_delta   on turnos_auditoria (delta_horas) where delta_horas <> 0;

create or replace function fn_turnos_auditoria() returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    v_usuario text := coalesce(nullif(current_setting('app.usuario', true), ''), '');
    v_origen  text := coalesce(nullif(current_setting('app.origen',  true), ''), 'fuera_de_la_app');
begin
    if tg_op = 'DELETE' then
        insert into turnos_auditoria (turno_id, accion, usuario, origen, antes, despues,
                                      horas_antes, horas_despues, nombre, fecha_turno)
        values (old.id, 'DELETE', v_usuario, v_origen, to_jsonb(old), null,
                old.horas_trabajadas, null, old.nombre, old.fecha_turno);
    else
        insert into turnos_auditoria (turno_id, accion, usuario, origen, antes, despues,
                                      horas_antes, horas_despues, nombre, fecha_turno)
        values (old.id, 'UPDATE', v_usuario, v_origen, to_jsonb(old), to_jsonb(new),
                old.horas_trabajadas, new.horas_trabajadas, old.nombre, old.fecha_turno);
    end if;
    return null;
end
$$;

drop trigger if exists tg_turnos_auditoria_update on turnos;
create trigger tg_turnos_auditoria_update
after update on turnos
for each row
when (old.nombre           is distinct from new.nombre
   or old.area             is distinct from new.area
   or old.fecha_turno      is distinct from new.fecha_turno
   or old.ts_entrada       is distinct from new.ts_entrada
   or old.ts_salida        is distinct from new.ts_salida
   or old.horas_trabajadas is distinct from new.horas_trabajadas
   or old.horas_efectivas  is distinct from new.horas_efectivas
   or old.horas_extra      is distinct from new.horas_extra
   or old.estado           is distinct from new.estado
   or old.evento           is distinct from new.evento
   or old.observaciones    is distinct from new.observaciones)
execute function fn_turnos_auditoria();

drop trigger if exists tg_turnos_auditoria_delete on turnos;
create trigger tg_turnos_auditoria_delete
after delete on turnos
for each row
execute function fn_turnos_auditoria();

alter table turnos_auditoria enable row level security;
do $$
begin
    if exists (select from pg_roles where rolname = 'anon') then
        revoke all on turnos_auditoria from anon;
    end if;
    if exists (select from pg_roles where rolname = 'authenticated') then
        revoke all on turnos_auditoria from authenticated;
    end if;
end $$;
