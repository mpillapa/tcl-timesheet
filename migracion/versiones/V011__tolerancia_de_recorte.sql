-- La aprobación se mide en minutos quitados, con una tolerancia de 10 minutos.
--
-- V009 pedía aprobación cuando una corrección quitaba 1 h extra o más. Con los
-- datos de septiembre y octubre de 2026 eso dejaba dos huecos:
--
--   - Un recorte de 20 o 50 minutos se aplicaba sin que nadie más lo viera.
--   - Solo contaban las horas extra. Recortar un turno que no llega a la
--     jornada no pedía aprobación, aunque quitara horas.
--
-- Y mostró un ruido que no es un recorte: unas 60 correcciones al mes que solo
-- buscaban actualizar el comentario y movían el turno unos segundos, porque el
-- formulario anterior trabajaba en minutos y truncaba los segundos al guardar.
--
-- Desde aquí, con la regla 'tolerancia_recorte_min':
--
--   - Una corrección que quita más de la tolerancia, medida sobre el tiempo
--     trabajado y no solo sobre las horas extra, espera a otro administrador.
--   - Una que quita hasta la tolerancia, que suma tiempo o que solo cambia el
--     comentario se aplica en el acto, con su motivo y su registro.
--   - Anular un turno de trabajo con más de la tolerancia también espera
--     aprobación. Anular una eventualidad (vacaciones, permiso, falta) no: la
--     registró un administrador y no hay marcación del colaborador que se pierda.
--
-- La regla 'aprobacion_desde_recorte_extra_h' de V001 se retira: ninguna
-- función la usa después de esta migración.
--
-- Compatible con la versión de la aplicación en producción: las firmas y el
-- tipo de retorno de las funciones no cambian. Las funciones se recrean con
-- create or replace, que conserva los permisos de V010.


insert into reglas_negocio (clave, valor, unidad, descripcion, estado, decidido_por) values
    ('tolerancia_recorte_min', 10, 'min',
     'Una corrección que quita más de estos minutos de tiempo trabajado, o la anulación de un turno de trabajo más largo que esto, necesita la aprobación de otro administrador. Hasta este valor es un ajuste menor: segundos o minutos que se mueven al actualizar el comentario.',
     'vigente', 'superadmins del turnero, 2026-10-14')
on conflict (clave) do nothing;


-- Minutos que una corrección le quita a un turno. Negativo si le suma.
-- Trabaja con intervalos, así que sirve igual con columnas timestamp (hoy) y
-- timestamptz (después de V102).
create or replace function minutos_quitados(p_antes interval, p_despues interval) returns numeric
language sql immutable
as $$ select round((extract(epoch from p_antes - p_despues) / 60)::numeric, 2) $$;


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
    v_quita  numeric;
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

    v_quita := minutos_quitados(t.ts_salida - t.ts_entrada, p_salida - p_entrada);

    insert into ajustes_turno (turno_id, tipo, motivo, solicitado_por, antes, propuesto,
                               delta_horas, delta_extra, requiere_aprobacion)
    values (
        t.id, 'correccion', trim(p_motivo), trim(p_usuario),
        _estado_del_turno(t),
        jsonb_build_object('ts_entrada', p_entrada, 'ts_salida', p_salida, 'observaciones', v_obs),
        v_horas - coalesce(t.horas_trabajadas, 0),
        v_extra - coalesce(t.horas_extra, 0),
        v_quita > regla('tolerancia_recorte_min')
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
        -- Un turno sin salida no tiene tiempo que quitar: es el caso de los
        -- turnos que quedaron abiertos por error.
        coalesce(t.evento, '') = ''
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


-- Qué clase de cambio es un ajuste, para leer los reportes sin el ruido de
-- los segundos:
--   anulacion        el turno deja de contar
--   solo_comentario  el horario no cambia
--   menor            mueve hasta la tolerancia, en cualquier sentido
--   suma             agrega más que la tolerancia
--   recorte          quita más que la tolerancia
-- Usa la tolerancia vigente al leer: si la regla cambia, los ajustes viejos se
-- reclasifican con el valor nuevo. Si necesitan aprobación lo decidió el valor
-- de cuando se solicitaron, que queda en `requiere_aprobacion`.
create or replace function clase_de_ajuste(a ajustes_turno) returns text
language plpgsql stable
set timezone = 'America/Guayaquil'
as $$
declare
    v_quita numeric;
begin
    if a.tipo = 'anulacion' then
        return 'anulacion';
    end if;
    if (a.antes ->> 'ts_entrada')::timestamptz = (a.propuesto ->> 'ts_entrada')::timestamptz
       and (a.antes ->> 'ts_salida')::timestamptz = (a.propuesto ->> 'ts_salida')::timestamptz then
        return 'solo_comentario';
    end if;
    -- Los horarios de `antes` llevan zona desde V102 y los de `propuesto` no;
    -- con la zona fijada arriba, el texto sin zona se lee como hora de Ecuador.
    v_quita := minutos_quitados(
        (a.antes ->> 'ts_salida')::timestamptz - (a.antes ->> 'ts_entrada')::timestamptz,
        (a.propuesto ->> 'ts_salida')::timestamptz - (a.propuesto ->> 'ts_entrada')::timestamptz);
    if abs(v_quita) <= regla('tolerancia_recorte_min') then
        return 'menor';
    elsif v_quita < 0 then
        return 'suma';
    end if;
    return 'recorte';
end
$$;


delete from reglas_negocio where clave = 'aprobacion_desde_recorte_extra_h';
