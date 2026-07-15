-- Esquema del turnero en PostgreSQL (Supabase).
-- Idempotente: se puede ejecutar varias veces sin dañar nada.

create table if not exists turnos (
    id                bigint generated always as identity primary key,
    nombre            text not null,
    area              text not null default '',
    fecha_turno       date not null,
    -- Timestamps en hora local de Ecuador, sin zona horaria (igual que la app
    -- y que los datos históricos del Sheet).
    ts_entrada        timestamp not null,
    ts_salida         timestamp,
    horas_trabajadas  numeric(6,2),
    horas_efectivas   numeric(6,2),
    horas_extra       numeric(6,2),
    estado            text not null default 'Abierto',
    evento            text not null default '',
    observaciones     text not null default '',
    -- true = mes cerrado (equivale a la antigua hoja "Historico").
    archivado         boolean not null default false,
    creado_en         timestamptz not null default now(),
    actualizado_en    timestamptz not null default now()
);

-- Clave natural de la app: (Nombre, Timestamp Entrada), insensible a mayúsculas.
-- Además blinda contra doble-click: un duplicado exacto no puede insertarse.
create unique index if not exists ux_turnos_nombre_entrada
    on turnos (lower(nombre), ts_entrada);

create index if not exists ix_turnos_estado on turnos (estado);
create index if not exists ix_turnos_fecha on turnos (fecha_turno);
create index if not exists ix_turnos_archivado on turnos (archivado);

-- Meta de horas por mes (antes: hoja "Horas Esperadas").
-- Se edita desde el Table Editor de Supabase.
create table if not exists horas_esperadas (
    anio   int not null,
    mes    int not null check (mes between 1 and 12),
    horas  numeric(7,2) not null,
    primary key (anio, mes)
);
