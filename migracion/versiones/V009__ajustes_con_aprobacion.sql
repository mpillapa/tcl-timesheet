-- Toda corrección es una solicitud con motivo, y las grandes necesitan a otra persona.
--
-- Hasta ahora corregir un turno era un UPDATE directo: el supervisor cambiaba
-- el horario y el valor anterior se perdía. Desde aquí una corrección o una
-- anulación es un ajuste:
--
--   1. Un administrador lo solicita, con motivo obligatorio. Queda guardado el
--      estado del turno antes y lo que se propone.
--   2. Si quita menos horas extra que el umbral de la regla
--      'aprobacion_desde_recorte_extra_h', se aplica en el acto.
--   3. Si quita ese umbral o más, queda pendiente hasta que otro administrador
--      lo apruebe o lo rechace. Quien lo solicitó no puede aprobarlo: lo impide
--      una restricción de la tabla, no solo el panel.
--
-- Con esto el caso de septiembre que reescribió un turno de 09:13-00:03 a
-- 00:12-09:12, con 5.84 h extra menos y sin motivo, no podría repetirse: haría
-- falta un motivo y una segunda persona que lo firme.
--
-- La regla del umbral es provisional. Ver migracion/README.md.
--
-- Zona horaria
-- ------------
-- Las funciones reciben la hora local de Ecuador sin zona, que es como trabaja
-- la aplicación, y fijan timezone = America/Guayaquil. Así siguen siendo
-- correctas cuando V102 pase las columnas a timestamptz, sin importar la zona
-- de la sesión que las llame.


create table if not exists ajustes_turno (
    id                   bigint generated always as identity primary key,
    turno_id             bigint not null references turnos (id),
    tipo                 text not null check (tipo in ('correccion', 'anulacion')),
    estado               text not null default 'pendiente'
                         check (estado in ('pendiente', 'aplicado', 'rechazado')),
    motivo               text not null,
    solicitado_por       text not null check (length(trim(solicitado_por)) > 0),
    solicitado_en        timestamptz not null default now(),
    -- Estado del turno al solicitar, y lo que se propone. En una anulación,
    -- `propuesto` va vacío.
    antes                jsonb not null,
    propuesto            jsonb not null,
    delta_horas          numeric(6,2) not null,
    delta_extra          numeric(6,2) not null,
    requiere_aprobacion  boolean not null,
    revisado_por         text,
    revisado_en          timestamptz,
    comentario_revision  text not null default '',

    constraint ck_ajustes_revisor_distinto
        check (revisado_por is null or lower(revisado_por) <> lower(solicitado_por)),
    constraint ck_ajustes_revision check (
           (estado = 'pendiente' and revisado_por is null and revisado_en is null)
        or (estado = 'aplicado' and not requiere_aprobacion and revisado_por is null and revisado_en is not null)
        or (estado in ('aplicado', 'rechazado') and requiere_aprobacion
            and revisado_por is not null and revisado_en is not null)
    )
);

create index if not exists ix_ajustes_turno  on ajustes_turno (turno_id);
create index if not exists ix_ajustes_estado on ajustes_turno (estado, solicitado_en desc);
-- Un turno no puede tener dos ajustes esperando a la vez.
create unique index if not exists ux_ajustes_un_pendiente on ajustes_turno (turno_id) where estado = 'pendiente';

select cerrar_a_api('ajustes_turno');

-- Un ajuste se resuelve una sola vez y lo que se pidió no cambia después.
create or replace function fn_ajustes_guardian() returns trigger
language plpgsql
as $$
begin
    if tg_op = 'DELETE' then
        raise exception 'TURNERO: un ajuste no se borra; se rechaza.' using errcode = 'P0001';
    end if;
    if old.estado <> 'pendiente' then
        raise exception 'TURNERO: el ajuste % ya fue %.', old.id, old.estado using errcode = 'P0001';
    end if;
    if (to_jsonb(new) - array['estado', 'revisado_por', 'revisado_en', 'comentario_revision'])
       is distinct from
       (to_jsonb(old) - array['estado', 'revisado_por', 'revisado_en', 'comentario_revision']) then
        raise exception 'TURNERO: lo solicitado en un ajuste no se puede modificar.' using errcode = 'P0001';
    end if;
    return new;
end
$$;

drop trigger if exists tg_ajustes_guardian on ajustes_turno;
create trigger tg_ajustes_guardian
before update or delete on ajustes_turno
for each row execute function fn_ajustes_guardian();

drop trigger if exists tg_ajustes_sin_truncate on ajustes_turno;
create trigger tg_ajustes_sin_truncate
before truncate on ajustes_turno
for each statement execute function fn_solo_insercion();


-- ---------------------------------------------------------------------------
-- Funciones
-- ---------------------------------------------------------------------------

create or replace function _estado_del_turno(t turnos) returns jsonb
language sql stable
as $$
    select jsonb_build_object(
        'ts_entrada',       t.ts_entrada,
        'ts_salida',        t.ts_salida,
        'fecha_turno',      t.fecha_turno,
        'horas_trabajadas', t.horas_trabajadas,
        'horas_extra',      t.horas_extra,
        'estado',           t.estado,
        'observaciones',    t.observaciones
    )
$$;

create or replace function _validar_solicitante(p_usuario text, p_motivo text) returns void
language plpgsql stable
as $$
begin
    if length(trim(coalesce(p_usuario, ''))) = 0 then
        raise exception 'TURNERO: falta el usuario que solicita el ajuste.' using errcode = 'P0001';
    end if;
    if length(trim(coalesce(p_motivo, ''))) < regla('motivo_min_caracteres') then
        raise exception 'TURNERO: el motivo tiene que tener al menos % caracteres.',
            regla('motivo_min_caracteres') using errcode = 'P0001';
    end if;
end
$$;


-- Aplica un ajuste sobre el turno. Interna: la llaman las funciones de
-- solicitud y de aprobación, nunca la aplicación directamente.
--
-- Sella la sesión para que la bitácora atribuya el cambio a quien lo solicitó,
-- con su motivo y el id del ajuste. Quien lo aprobó queda en el propio ajuste.
-- Al terminar restaura lo que la sesión tenía, para no contaminar el resto de
-- la transacción del llamador.
create or replace function _aplicar_ajuste(p_id bigint, p_revisor text, p_comentario text)
returns void
language plpgsql
security definer
set search_path = public
set timezone = 'America/Guayaquil'
as $$
declare
    a              ajustes_turno;
    v_prev_usuario text := current_setting('app.usuario',   true);
    v_prev_origen  text := current_setting('app.origen',    true);
    v_prev_motivo  text := current_setting('app.motivo',    true);
    v_prev_ajuste  text := current_setting('app.ajuste_id', true);
begin
    select * into a from ajustes_turno where id = p_id for update;
    if a.estado <> 'pendiente' then
        raise exception 'TURNERO: el ajuste % ya fue %.', a.id, a.estado using errcode = 'P0001';
    end if;

    perform set_config('app.usuario',   a.solicitado_por, true);
    perform set_config('app.origen',    'admin',          true);
    perform set_config('app.motivo',    a.motivo,         true);
    perform set_config('app.ajuste_id', a.id::text,       true);

    if a.tipo = 'correccion' then
        update turnos
        set ts_entrada     = (a.propuesto ->> 'ts_entrada')::timestamp,
            ts_salida      = (a.propuesto ->> 'ts_salida')::timestamp,
            observaciones  = a.propuesto ->> 'observaciones',
            actualizado_en = now()
        where id = a.turno_id;
    else
        update turnos
        set anulado          = true,
            anulado_en       = now(),
            anulado_por      = a.solicitado_por,
            motivo_anulacion = a.motivo,
            actualizado_en   = now()
        where id = a.turno_id;
    end if;

    update ajustes_turno
    set estado              = 'aplicado',
        revisado_por        = p_revisor,
        revisado_en         = now(),
        comentario_revision = coalesce(p_comentario, '')
    where id = p_id;

    perform set_config('app.usuario',   coalesce(v_prev_usuario, ''), true);
    perform set_config('app.origen',    coalesce(v_prev_origen,  ''), true);
    perform set_config('app.motivo',    coalesce(v_prev_motivo,  ''), true);
    perform set_config('app.ajuste_id', coalesce(v_prev_ajuste,  ''), true);
end
$$;


create or replace function solicitar_correccion(
    p_turno_id    bigint,
    p_entrada     timestamp,
    p_salida      timestamp,
    p_observacion text,
    p_motivo      text,
    p_usuario     text
) returns ajustes_turno
language plpgsql
security definer
set search_path = public
set timezone = 'America/Guayaquil'
as $$
declare
    t        turnos;
    a        ajustes_turno;
    v_horas  numeric;
    v_extra  numeric;
    v_obs    text;
begin
    perform _validar_solicitante(p_usuario, p_motivo);

    select * into t from turnos where id = p_turno_id for update;
    if not found then
        raise exception 'TURNERO: el turno no existe.' using errcode = 'P0001';
    end if;
    if t.anulado then
        raise exception 'TURNERO: el turno está anulado.' using errcode = 'P0001';
    end if;
    if t.ts_salida is null then
        raise exception 'TURNERO: el turno no está cerrado. Ciérralo desde los turnos en revisión.' using errcode = 'P0001';
    end if;
    if p_entrada is null or p_salida is null or p_salida <= p_entrada then
        raise exception 'TURNERO: la salida tiene que ser posterior a la entrada.' using errcode = 'P0001';
    end if;
    if periodo_cerrado(t.fecha_turno) or periodo_cerrado(p_entrada::date) then
        raise exception 'TURNERO: el periodo del turno está cerrado. Hay que reabrirlo antes de corregir.' using errcode = 'P0001';
    end if;
    if exists (select 1 from ajustes_turno where turno_id = t.id and estado = 'pendiente') then
        raise exception 'TURNERO: este turno ya tiene un ajuste esperando aprobación.' using errcode = 'P0001';
    end if;

    v_horas := horas_de(p_entrada, p_salida);
    if v_horas > regla('duracion_maxima_turno_h') then
        raise exception 'TURNERO: el turno quedaría de % h, más que el máximo de % h. Revisa la fecha y la hora de salida.',
            v_horas, regla('duracion_maxima_turno_h') using errcode = 'P0001';
    end if;
    v_extra := horas_extra_de(v_horas);
    v_obs   := coalesce(p_observacion, t.observaciones);

    if p_entrada = t.ts_entrada and p_salida = t.ts_salida and v_obs = t.observaciones then
        raise exception 'TURNERO: la corrección no cambia nada del turno.' using errcode = 'P0001';
    end if;

    insert into ajustes_turno (turno_id, tipo, motivo, solicitado_por, antes, propuesto,
                               delta_horas, delta_extra, requiere_aprobacion)
    values (
        t.id, 'correccion', trim(p_motivo), trim(p_usuario),
        _estado_del_turno(t),
        jsonb_build_object('ts_entrada', p_entrada, 'ts_salida', p_salida, 'observaciones', v_obs),
        v_horas - coalesce(t.horas_trabajadas, 0),
        v_extra - coalesce(t.horas_extra, 0),
        coalesce(t.horas_extra, 0) - v_extra >= regla('aprobacion_desde_recorte_extra_h')
    )
    returning * into a;

    if not a.requiere_aprobacion then
        perform _aplicar_ajuste(a.id, null, '');
        select * into a from ajustes_turno where id = a.id;
    end if;
    return a;
end
$$;


create or replace function solicitar_anulacion(p_turno_id bigint, p_motivo text, p_usuario text)
returns ajustes_turno
language plpgsql
security definer
set search_path = public
set timezone = 'America/Guayaquil'
as $$
declare
    t  turnos;
    a  ajustes_turno;
begin
    perform _validar_solicitante(p_usuario, p_motivo);

    select * into t from turnos where id = p_turno_id for update;
    if not found then
        raise exception 'TURNERO: el turno no existe.' using errcode = 'P0001';
    end if;
    if t.anulado then
        raise exception 'TURNERO: el turno ya está anulado.' using errcode = 'P0001';
    end if;
    if periodo_cerrado(t.fecha_turno) then
        raise exception 'TURNERO: el periodo del turno está cerrado. Hay que reabrirlo antes de anular.' using errcode = 'P0001';
    end if;
    if exists (select 1 from ajustes_turno where turno_id = t.id and estado = 'pendiente') then
        raise exception 'TURNERO: este turno ya tiene un ajuste esperando aprobación.' using errcode = 'P0001';
    end if;

    insert into ajustes_turno (turno_id, tipo, motivo, solicitado_por, antes, propuesto,
                               delta_horas, delta_extra, requiere_aprobacion)
    values (
        t.id, 'anulacion', trim(p_motivo), trim(p_usuario),
        _estado_del_turno(t), '{}'::jsonb,
        -coalesce(t.horas_trabajadas, 0),
        -coalesce(t.horas_extra, 0),
        coalesce(t.horas_extra, 0) > 0
            and coalesce(t.horas_extra, 0) >= regla('aprobacion_desde_recorte_extra_h')
    )
    returning * into a;

    if not a.requiere_aprobacion then
        perform _aplicar_ajuste(a.id, null, '');
        select * into a from ajustes_turno where id = a.id;
    end if;
    return a;
end
$$;


create or replace function aprobar_ajuste(p_id bigint, p_usuario text, p_comentario text)
returns ajustes_turno
language plpgsql
security definer
set search_path = public
set timezone = 'America/Guayaquil'
as $$
declare
    a  ajustes_turno;
begin
    if length(trim(coalesce(p_usuario, ''))) = 0 then
        raise exception 'TURNERO: falta el usuario que aprueba.' using errcode = 'P0001';
    end if;
    select * into a from ajustes_turno where id = p_id for update;
    if not found then
        raise exception 'TURNERO: el ajuste no existe.' using errcode = 'P0001';
    end if;
    if a.estado <> 'pendiente' then
        raise exception 'TURNERO: el ajuste ya fue %.', a.estado using errcode = 'P0001';
    end if;
    if lower(trim(p_usuario)) = lower(a.solicitado_por) then
        raise exception 'TURNERO: quien solicita un ajuste no puede aprobarlo.' using errcode = 'P0001';
    end if;

    perform _aplicar_ajuste(p_id, trim(p_usuario), p_comentario);
    select * into a from ajustes_turno where id = p_id;
    return a;
end
$$;


create or replace function rechazar_ajuste(p_id bigint, p_usuario text, p_comentario text)
returns ajustes_turno
language plpgsql
security definer
set search_path = public
as $$
declare
    a  ajustes_turno;
begin
    if length(trim(coalesce(p_usuario, ''))) = 0 then
        raise exception 'TURNERO: falta el usuario que rechaza.' using errcode = 'P0001';
    end if;
    if length(trim(coalesce(p_comentario, ''))) < regla('motivo_min_caracteres') then
        raise exception 'TURNERO: el rechazo tiene que explicar el motivo, con al menos % caracteres.',
            regla('motivo_min_caracteres') using errcode = 'P0001';
    end if;
    select * into a from ajustes_turno where id = p_id for update;
    if not found then
        raise exception 'TURNERO: el ajuste no existe.' using errcode = 'P0001';
    end if;
    if a.estado <> 'pendiente' then
        raise exception 'TURNERO: el ajuste ya fue %.', a.estado using errcode = 'P0001';
    end if;
    if lower(trim(p_usuario)) = lower(a.solicitado_por) then
        raise exception 'TURNERO: quien solicita un ajuste no puede resolverlo.' using errcode = 'P0001';
    end if;

    update ajustes_turno
    set estado = 'rechazado', revisado_por = trim(p_usuario), revisado_en = now(),
        comentario_revision = trim(p_comentario)
    where id = p_id
    returning * into a;
    return a;
end
$$;
