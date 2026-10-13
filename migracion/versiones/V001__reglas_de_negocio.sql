-- Reglas de negocio en una tabla, no repartidas entre el código y la base.
--
-- Hasta ahora la jornada base, el almuerzo y los umbrales vivían solo en
-- core/config.py. Desde esta versión la base también calcula horas y decide
-- cuándo una corrección necesita aprobación, de modo que necesita esos valores.
-- Ponerlos en una tabla evita copiar constantes en cada función y deja a la
-- vista cuáles están decididas y cuáles no.
--
-- La columna `estado` es la parte importante. 'vigente' significa que el valor
-- ya regía en la aplicación antes de esta migración. 'provisional' significa
-- que lo propuso Tecnología para poder construir el mecanismo y que falta que
-- lo decida quien corresponda. La lista de pendientes está en
-- migracion/README.md.
--
-- Los valores de 'vigente' tienen que coincidir con core/config.py. Lo
-- comprueba pruebas/test_reglas.py.


-- ---------------------------------------------------------------------------
-- Utilidades que usan las migraciones siguientes
-- ---------------------------------------------------------------------------

-- Cierra una tabla a la API REST de Supabase: RLS sin políticas para los roles
-- de la API y sin permisos. Mismo criterio que seguridad/habilitar_rls.sql.
create or replace function cerrar_a_api(p_tabla regclass) returns void
language plpgsql
as $$
begin
    execute format('alter table %s enable row level security', p_tabla);
    if exists (select from pg_roles where rolname = 'anon') then
        execute format('revoke all on %s from anon', p_tabla);
    end if;
    if exists (select from pg_roles where rolname = 'authenticated') then
        execute format('revoke all on %s from authenticated', p_tabla);
    end if;
end
$$;

-- Rechaza cualquier modificación. Se usa en las tablas que son de solo
-- inserción: la bitácora, las marcaciones y el registro de cierres.
--
-- El prefijo 'TURNERO:' en el mensaje es el contrato con la aplicación:
-- core/data.py reconoce los errores que empiezan así como reglas de negocio y
-- se los muestra al usuario tal cual, sin traza técnica.
create or replace function fn_solo_insercion() returns trigger
language plpgsql
as $$
begin
    raise exception 'TURNERO: % es de solo inserción; no admite %.', tg_table_name, lower(tg_op)
        using errcode = 'P0001';
end
$$;


-- ---------------------------------------------------------------------------
-- La tabla de reglas
-- ---------------------------------------------------------------------------
create table if not exists reglas_negocio (
    clave           text primary key,
    valor           numeric not null,
    unidad          text not null default '',
    descripcion     text not null,
    estado          text not null default 'provisional'
                    check (estado in ('vigente', 'provisional')),
    decidido_por    text not null default '',
    actualizado_en  timestamptz not null default now()
);

comment on table reglas_negocio is
    'Parámetros de negocio que usa la base. estado = provisional significa que falta la decisión de Recursos Humanos u Operaciones; ver migracion/README.md.';

-- on conflict do nothing: si alguien ya ajustó un valor en producción, volver
-- a correr la migración no lo pisa.
insert into reglas_negocio (clave, valor, unidad, descripcion, estado, decidido_por) values
    ('jornada_base_h', 9, 'h',
     'Horas de la jornada. Lo que excede es hora extra. Igual a core/config.py HORAS_BASE_TURNO.',
     'vigente', 'regla existente en la aplicación'),
    ('almuerzo_h', 1, 'h',
     'Descuento de almuerzo para las horas efectivas. Igual a core/config.py HORAS_ALMUERZO.',
     'vigente', 'regla existente en la aplicación'),
    ('min_horas_para_almuerzo', 5, 'h',
     'Desde cuántas horas trabajadas se descuenta el almuerzo. Igual a core/config.py MIN_HORAS_ALMUERZO.',
     'vigente', 'regla existente en la aplicación'),
    ('motivo_min_caracteres', 10, 'caracteres',
     'Largo mínimo del motivo de una corrección o anulación. Toma el mismo valor que la justificación de horas extra del colaborador (MIN_JUSTIF_CHARS).',
     'provisional', ''),
    ('duracion_maxima_turno_h', 24, 'h',
     'Un turno más largo se rechaza. Atrapa errores de tipeo como poner la salida en el día equivocado. En la historia hay un solo turno de más de 24 h.',
     'provisional', ''),
    ('aprobacion_desde_recorte_extra_h', 1, 'h',
     'Una corrección o anulación que quita al menos estas horas extra necesita la aprobación de otro administrador. En septiembre de 2026, 26 de 39 recortes fueron de 1 h o más.',
     'provisional', ''),
    ('exigir_ajuste_para_corregir', 0, 'booleano',
     'Con 1, la base rechaza cambiar el horario de un turno cerrado o anularlo si no es a través de un ajuste. Lo enciende V101, después de desplegar la versión de la aplicación que usa ajustes.',
     'vigente', 'mecanismo técnico'),
    ('exigir_motivo_admin', 0, 'booleano',
     'Con 1, toda escritura de un administrador tiene que traer motivo. Lo enciende V101.',
     'vigente', 'mecanismo técnico')
on conflict (clave) do nothing;

-- Lectura de una regla. Si la clave no existe falla, en vez de devolver null y
-- dejar que un cálculo siga con un valor vacío.
create or replace function regla(p_clave text) returns numeric
language plpgsql
stable
as $$
declare
    v numeric;
begin
    select valor into v from reglas_negocio where clave = p_clave;
    if not found then
        raise exception 'Regla de negocio inexistente: %', p_clave;
    end if;
    return v;
end
$$;

select cerrar_a_api('reglas_negocio');


-- ---------------------------------------------------------------------------
-- Cálculo de horas, en un solo sitio
-- ---------------------------------------------------------------------------
-- Las tres funciones replican core/calculos.py. Con el trigger de V004 pasan a
-- ser la autoridad: la aplicación puede seguir calculando para mostrar una
-- vista previa, pero lo que se guarda es lo que sale de aquí.
create or replace function horas_de(p_entrada timestamp, p_salida timestamp) returns numeric
language sql immutable
as $$ select round((extract(epoch from p_salida - p_entrada) / 3600)::numeric, 2) $$;

create or replace function horas_efectivas_de(p_horas numeric) returns numeric
language sql stable
as $$
    select case when p_horas >= regla('min_horas_para_almuerzo')
                then p_horas - regla('almuerzo_h')
                else p_horas end
$$;

create or replace function horas_extra_de(p_horas numeric) returns numeric
language sql stable
as $$ select greatest(0, p_horas - regla('jornada_base_h')) $$;

-- Fecha de un instante en hora de Ecuador. Con las columnas sin zona es la
-- fecha tal cual; V102 añade la versión para timestamptz.
create or replace function fecha_local(p_ts timestamp) returns date
language sql immutable
as $$ select p_ts::date $$;

-- El instante absoluto de una hora local de Ecuador. Se usa para guardar en
-- columnas timestamptz, como marcaciones.momento, sin depender de la zona
-- horaria de la sesión que escribe.
create or replace function instante(p_ts timestamp) returns timestamptz
language sql immutable
as $$ select p_ts at time zone 'America/Guayaquil' $$;
