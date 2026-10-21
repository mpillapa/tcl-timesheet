-- El evento de un turno trabajado dice cómo terminó la jornada.
--
-- Pedido el 2026-10-21. Al cerrarse un turno de trabajo, la base llena la
-- columna `evento` según su duración:
--
--   hasta 9 h                      'Turno completo sin novedad'
--   más de 9 h y hasta 9 h 30      'Turno completo con tolerancia de horas extra'
--   más de 9 h 30                  vacío: el colaborador tuvo que justificarlo
--
-- Los 30 minutos son la tolerancia en la que el quiosco no pide justificación
-- (core/config.py, UMBRAL_HORAS_EXTRA - HORAS_BASE_TURNO). Es una etiqueta: las
-- horas extra se calculan igual, y un turno de 9 h 20 suma 0:20 de extra.
--
-- Cuándo se calcula
-- -----------------
-- Al crear el turno y cada vez que cambia su entrada o su salida: al marcar la
-- salida, al cerrar un turno en revisión, al registrarlo a mano o al corregirlo.
-- Un turno sin salida queda con el evento vacío. Los turnos ya registrados no
-- se tocan: se recalculan solo si se corrige su horario.
--
-- Por qué hacía falta tocar otras funciones
-- -----------------------------------------
-- Hasta aquí, un evento distinto de vacío significaba eventualidad (vacaciones,
-- permiso, falta). Con eso, un turno trabajado con evento se habría contado
-- como ausencia: sin marcación guardada (V008), fuera de la validación de
-- cruces (V004) y, con la aprobación encendida, anulable sin aprobación (V012).
-- Las tres pasan a preguntar a `es_eventualidad()`, que conoce los dos eventos
-- de jornada. El panel hace lo mismo en views/super_admin.py:_clase_de_turno.
--
-- Compatible con la versión de la aplicación en producción: no cambia ninguna
-- firma y la aplicación no escribe `evento` en los turnos de trabajo.


insert into reglas_negocio (clave, valor, unidad, descripcion, estado, decidido_por) values
    ('tolerancia_horas_extra_h', 0.5, 'h',
     'Minutos por encima de la jornada en que el colaborador no justifica la salida. El turno lleva el evento ''Turno completo con tolerancia de horas extra''; las horas extra cuentan igual. Igual a core/config.py UMBRAL_HORAS_EXTRA - HORAS_BASE_TURNO.',
     'vigente', 'regla existente en la aplicación')
on conflict (clave) do nothing;


create or replace function evento_sin_novedad() returns text
language sql immutable
as $$ select 'Turno completo sin novedad'::text $$;

create or replace function evento_con_tolerancia() returns text
language sql immutable
as $$ select 'Turno completo con tolerancia de horas extra'::text $$;


-- Vacaciones, permiso o falta. Un turno de trabajo tiene el evento vacío o uno
-- de los de jornada.
create or replace function es_eventualidad(p_evento text) returns boolean
language sql immutable
as $$
    select coalesce(p_evento, '') not in ('', evento_sin_novedad(), evento_con_tolerancia())
$$;


create or replace function evento_de_jornada(p_horas numeric) returns text
language sql stable
as $$
    select case
        when p_horas is null                                                 then ''
        when p_horas <= regla('jornada_base_h')                              then evento_sin_novedad()
        when p_horas <= regla('jornada_base_h') + regla('tolerancia_horas_extra_h') then evento_con_tolerancia()
        else ''
    end
$$;


-- Va después de tg_turnos_30_validar, que acaba de calcular horas_trabajadas.
create or replace function fn_turnos_evento_jornada() returns trigger
language plpgsql
as $$
begin
    if new.anulado or es_eventualidad(new.evento) then
        return new;
    end if;
    if tg_op = 'UPDATE'
       and new.ts_entrada is not distinct from old.ts_entrada
       and new.ts_salida  is not distinct from old.ts_salida then
        return new;
    end if;
    new.evento := evento_de_jornada(new.horas_trabajadas);
    return new;
end
$$;

drop trigger if exists tg_turnos_35_evento_jornada on turnos;
create trigger tg_turnos_35_evento_jornada
before insert or update on turnos
for each row execute function fn_turnos_evento_jornada();


-- V004, con la regla 6 preguntando por eventualidades en vez de por evento vacío.
create or replace function fn_turnos_validar() returns trigger
language plpgsql
as $$
declare
    v_origen       text := coalesce(nullif(current_setting('app.origen', true), ''), 'fuera_de_la_app');
    v_motivo       text := coalesce(current_setting('app.motivo', true), '');
    v_ajuste       text := coalesce(current_setting('app.ajuste_id', true), '');
    v_min_motivo   int  := regla('motivo_min_caracteres');
    v_toca_horario boolean;
    v_horas        numeric;
begin
    -- 1. Motivo obligatorio en toda escritura de un administrador. Apagado
    --    hasta V101, porque la versión actual de la aplicación no lo envía.
    if v_origen = 'admin' and regla('exigir_motivo_admin') = 1
       and length(trim(v_motivo)) < v_min_motivo then
        raise exception 'TURNERO: falta el motivo del cambio, de al menos % caracteres.', v_min_motivo
            using errcode = 'P0001';
    end if;

    if tg_op = 'UPDATE' then
        -- 2. Un turno anulado no vuelve atrás ni se edita. Sí admite los
        --    cambios técnicos, como el archivado del cierre de mes.
        if old.anulado then
            if datos_del_turno(to_jsonb(new)) is distinct from datos_del_turno(to_jsonb(old)) then
                raise exception 'TURNERO: el turno está anulado y no admite cambios. Si hace falta, registra un turno nuevo.'
                    using errcode = 'P0001';
            end if;
            return new;
        end if;

        -- 3. Corregir el horario de un turno cerrado, o anular cualquier turno,
        --    solo a través de un ajuste (V009). Apagado hasta V101.
        if v_ajuste = '' and regla('exigir_ajuste_para_corregir') = 1
           and (   new.anulado
                or (old.ts_salida is not null
                    and (new.ts_entrada is distinct from old.ts_entrada
                      or new.ts_salida  is distinct from old.ts_salida))) then
            raise exception 'TURNERO: un turno cerrado solo se corrige o se anula mediante un ajuste, que queda registrado con su motivo.'
                using errcode = 'P0001';
        end if;

        -- La anulación no toca el horario: no hay nada más que validar.
        if new.anulado then
            return new;
        end if;

        v_toca_horario := new.ts_entrada       is distinct from old.ts_entrada
                       or new.ts_salida        is distinct from old.ts_salida
                       or new.nombre           is distinct from old.nombre
                       or new.fecha_turno      is distinct from old.fecha_turno
                       or new.horas_trabajadas is distinct from old.horas_trabajadas
                       or new.horas_efectivas  is distinct from old.horas_efectivas
                       or new.horas_extra      is distinct from old.horas_extra;
        if not v_toca_horario then
            return new;
        end if;
    elsif new.anulado then
        raise exception 'TURNERO: un turno no puede crearse ya anulado.' using errcode = 'P0001';
    end if;

    -- 4. La fecha de turno es la fecha de la entrada, también si el turno
    --    cruza la medianoche.
    new.fecha_turno := fecha_local(new.ts_entrada);

    -- 5. Horas calculadas a partir del horario, y límites del horario.
    if new.ts_salida is null then
        new.horas_trabajadas := null;
        new.horas_efectivas  := null;
        new.horas_extra      := null;
    else
        if new.ts_salida <= new.ts_entrada then
            raise exception 'TURNERO: la salida tiene que ser posterior a la entrada.' using errcode = 'P0001';
        end if;
        v_horas := horas_de(new.ts_entrada, new.ts_salida);
        if v_horas > regla('duracion_maxima_turno_h') then
            raise exception 'TURNERO: el turno quedaría de % h, más que el máximo de % h. Revisa la fecha y la hora de salida.',
                v_horas, regla('duracion_maxima_turno_h') using errcode = 'P0001';
        end if;
        new.horas_trabajadas := v_horas;
        new.horas_efectivas  := horas_efectivas_de(v_horas);
        new.horas_extra      := horas_extra_de(v_horas);
    end if;

    -- 6. Dos turnos de trabajo de la misma persona no se cruzan. Un turno sin
    --    salida cuenta como el instante de su entrada. Las eventualidades
    --    quedan fuera en los dos sentidos: un colaborador que viene a trabajar
    --    un día con permiso registrado tiene que poder marcar.
    if not es_eventualidad(new.evento) and exists (
        select 1
        from turnos t
        where lower(t.nombre) = lower(new.nombre)
          and t.id <> new.id
          and not t.anulado
          and not es_eventualidad(t.evento)
          and t.ts_entrada < coalesce(new.ts_salida, new.ts_entrada + interval '1 second')
          and new.ts_entrada < coalesce(t.ts_salida, t.ts_entrada + interval '1 second')
    ) then
        raise exception 'TURNERO: el horario se cruza con otro turno de % ya registrado.', new.nombre
            using errcode = 'P0001';
    end if;

    return new;
end
$$;


-- V008, con la misma pregunta: un turno de trabajo con evento de jornada
-- también guarda su marcación.
create or replace function fn_turnos_marcaciones() returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    v_usuario text := coalesce(nullif(current_setting('app.usuario', true), ''), '');
    v_origen  text := coalesce(nullif(current_setting('app.origen',  true), ''), 'fuera_de_la_app');
begin
    if es_eventualidad(new.evento) then
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


-- V012, con la misma pregunta en la condición de aprobación.
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
        regla('exigir_aprobacion_ajustes') = 1
            and not es_eventualidad(t.evento)
            and t.ts_salida is not null
            and minutos_quitados(t.ts_salida - t.ts_entrada, interval '0') > regla('tolerancia_recorte_min')
    )
    returning * into a;

    if not a.requiere_aprobacion then
        perform _aplicar_ajuste(a.id, null, '');
        select * into a from ajustes_turno where id = a.id;
    end if;
    return a;
end
$$;
