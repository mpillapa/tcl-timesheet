-- Los ajustes se aplican en el acto y los meses no se cierran.
--
-- Decidido el 2026-10-21 con la gerencia y el jefe de carga: corregir o anular
-- un turno no necesita la aprobación de otro administrador dentro del sistema,
-- y el cierre de mes no bloquea cambios. El control se hace por fuera, con la
-- revisión de lo que queda registrado.
--
-- El registro no cambia. Cada corrección y anulación sigue siendo un ajuste con
-- motivo obligatorio, con el estado de antes y lo que se cambió; la bitácora
-- (turnos_auditoria) y las marcaciones siguen siendo de solo inserción. Lo que
-- desaparece es la espera: `requiere_aprobacion` queda en false y el ajuste se
-- aplica en la misma llamada.
--
-- Las dos cosas quedan detrás de una regla, no borradas:
--
--   exigir_aprobacion_ajustes   0  con 1 vuelve la regla de V011: espera
--                                  aprobación lo que quita más de
--                                  'tolerancia_recorte_min'
--   bloquear_periodos_cerrados  0  con 1 un mes cerrado vuelve a rechazar
--                                  cambios, como en V007
--
-- Volver a encenderlas es un `update` y no requiere desplegar nada, pero el
-- panel ya no tiene la pantalla de aprobación ni la de cierre: habría que
-- restaurarlas.
--
-- Los ajustes que estaban pendientes al migrar se aplican aquí, a nombre de
-- quien los pidió y con revisado_por = 'sistema: V012'. Si alguno ya no se puede
-- aplicar, por ejemplo porque el turno quedaría cruzado con otro, se cierra
-- como rechazado con el error en el comentario, y la migración sigue.
--
-- Compatible con la versión de la aplicación en producción: las firmas y el
-- tipo de retorno de las funciones no cambian. Se recrean con create or replace,
-- que conserva los permisos de V010.


insert into reglas_negocio (clave, valor, unidad, descripcion, estado, decidido_por) values
    ('exigir_aprobacion_ajustes', 0, 'booleano',
     'Con 1, una corrección que quita más de tolerancia_recorte_min, o la anulación de un turno de trabajo más largo que eso, espera la aprobación de otro administrador. Con 0 todo ajuste se aplica en el acto y queda registrado.',
     'vigente', 'gerencia y jefe de carga, 2026-10-21'),
    ('bloquear_periodos_cerrados', 0, 'booleano',
     'Con 1, un mes cerrado en periodos_nomina rechaza crear, corregir o anular turnos. Con 0 el cierre no bloquea nada.',
     'vigente', 'gerencia y jefe de carga, 2026-10-21')
on conflict (clave) do nothing;


-- La usan el guardián de V007 y las funciones de ajuste: apagarla aquí apaga
-- el bloqueo en todos los sitios a la vez.
create or replace function periodo_cerrado(p_fecha date) returns boolean
language sql stable
as $$
    select regla('bloquear_periodos_cerrados') = 1
       and exists (
           select 1 from periodos_nomina
           where anio = extract(year from p_fecha)::int
             and mes  = extract(month from p_fecha)::int
             and estado = 'cerrado'
       )
$$;


-- Igual que en V011, salvo la condición de `requiere_aprobacion`.
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
        regla('exigir_aprobacion_ajustes') = 1
            and v_quita > regla('tolerancia_recorte_min')
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
        regla('exigir_aprobacion_ajustes') = 1
            and coalesce(t.evento, '') = ''
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


-- Aplica los ajustes que quedaron esperando. Devuelve cuántos se aplicaron y
-- cuántos se cerraron como rechazados porque ya no se podían aplicar.
--
-- No es parte de la API de la aplicación: la llama esta migración, y quedaría
-- para el caso de que alguien encienda y vuelva a apagar
-- 'exigir_aprobacion_ajustes'. Cada ajuste va en su propio bloque con
-- excepción, así que uno que falla no deshace los demás.
create or replace function aplicar_ajustes_pendientes(p_revisor text, out aplicados int, out rechazados int)
language plpgsql
set timezone = 'America/Guayaquil'
as $$
declare
    v_id bigint;
begin
    aplicados := 0;
    rechazados := 0;
    for v_id in select id from ajustes_turno where estado = 'pendiente' order by solicitado_en, id loop
        begin
            perform _aplicar_ajuste(v_id, p_revisor,
                'Aplicado sin aprobación: desde el 2026-10-21 los ajustes no esperan a otro administrador.');
            aplicados := aplicados + 1;
        exception when others then
            update ajustes_turno
            set estado = 'rechazado', revisado_por = p_revisor, revisado_en = now(),
                comentario_revision = 'No se pudo aplicar al retirar la aprobación: ' || sqlerrm
            where id = v_id;
            rechazados := rechazados + 1;
        end;
    end loop;
end
$$;

do $$
begin
    execute 'revoke execute on function aplicar_ajustes_pendientes(text) from public';
    if exists (select from pg_roles where rolname = 'anon') then
        execute 'revoke execute on function aplicar_ajustes_pendientes(text) from anon';
    end if;
    if exists (select from pg_roles where rolname = 'authenticated') then
        execute 'revoke execute on function aplicar_ajustes_pendientes(text) from authenticated';
    end if;
end $$;

do $$
declare
    r record;
begin
    select * into r from aplicar_ajustes_pendientes('sistema: V012');
    raise notice 'V012: % ajuste(s) pendiente(s) aplicados, % cerrados como rechazados.', r.aplicados, r.rechazados;
end $$;
