-- Un mes cerrado no se toca.
--
-- `archivado` era una marca que no impedía nada: de los cambios de supervisión
-- sobre turnos de septiembre de 2026, 100 de 220 se hicieron en octubre, con el
-- mes ya archivado. Desde aquí un periodo puede cerrarse, y mientras esté
-- cerrado la base rechaza crear, corregir o anular turnos de ese mes.
--
-- Reabrir es posible, a propósito: hay correcciones legítimas que llegan tarde.
-- Pero reabrir queda registrado con quién y por qué, y el registro es de solo
-- inserción. Así el atraso deja de ser invisible.
--
-- Lo que esta versión no decide
-- -----------------------------
-- Ningún periodo queda cerrado al migrar: cerrar es una acción explícita desde
-- el panel. Cuándo se cierra cada mes, quién puede cerrarlo y quién puede
-- reabrirlo son decisiones de Recursos Humanos. El panel restringe ambas
-- acciones a los administradores con `gestiona_periodos = true` en secrets.


create table if not exists periodos_nomina (
    anio         int  not null,
    mes          int  not null check (mes between 1 and 12),
    estado       text not null default 'abierto' check (estado in ('abierto', 'cerrado')),
    cambiado_por text not null default '',
    cambiado_en  timestamptz not null default now(),
    primary key (anio, mes)
);

create table if not exists periodos_eventos (
    id       bigint generated always as identity primary key,
    anio     int  not null,
    mes      int  not null,
    accion   text not null check (accion in ('cierre', 'reapertura')),
    usuario  text not null check (length(trim(usuario)) > 0),
    motivo   text not null,
    momento  timestamptz not null default now()
);

select cerrar_a_api('periodos_nomina');
select cerrar_a_api('periodos_eventos');

drop trigger if exists tg_periodos_eventos_inmutable on periodos_eventos;
create trigger tg_periodos_eventos_inmutable
before update or delete on periodos_eventos
for each row execute function fn_solo_insercion();

drop trigger if exists tg_periodos_eventos_sin_truncate on periodos_eventos;
create trigger tg_periodos_eventos_sin_truncate
before truncate on periodos_eventos
for each statement execute function fn_solo_insercion();


create or replace function periodo_cerrado(p_fecha date) returns boolean
language sql stable
as $$
    select exists (
        select 1 from periodos_nomina
        where anio = extract(year from p_fecha)::int
          and mes  = extract(month from p_fecha)::int
          and estado = 'cerrado'
    )
$$;


-- Cierre y reapertura. SECURITY DEFINER porque el rol de la aplicación solo
-- puede leer estas tablas: la única forma de cambiarlas es por estas funciones,
-- que validan y dejan el registro.
create or replace function cerrar_periodo(p_anio int, p_mes int, p_usuario text, p_motivo text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    v_hoy date := (now() at time zone 'America/Guayaquil')::date;
begin
    if length(trim(coalesce(p_usuario, ''))) = 0 then
        raise exception 'TURNERO: falta el usuario que cierra el periodo.' using errcode = 'P0001';
    end if;
    -- Solo meses terminados. Cerrar el mes en curso dejaría a los
    -- colaboradores sin poder marcar.
    if make_date(p_anio, p_mes, 1) + interval '1 month' > v_hoy then
        raise exception 'TURNERO: solo se puede cerrar un mes que ya terminó.' using errcode = 'P0001';
    end if;
    if exists (select 1 from periodos_nomina where anio = p_anio and mes = p_mes and estado = 'cerrado') then
        raise exception 'TURNERO: el periodo %-% ya está cerrado.', p_anio, lpad(p_mes::text, 2, '0')
            using errcode = 'P0001';
    end if;

    insert into periodos_nomina (anio, mes, estado, cambiado_por, cambiado_en)
    values (p_anio, p_mes, 'cerrado', p_usuario, now())
    on conflict (anio, mes) do update
        set estado = 'cerrado', cambiado_por = excluded.cambiado_por, cambiado_en = now();

    insert into periodos_eventos (anio, mes, accion, usuario, motivo)
    values (p_anio, p_mes, 'cierre', p_usuario, coalesce(p_motivo, ''));
end
$$;

create or replace function reabrir_periodo(p_anio int, p_mes int, p_usuario text, p_motivo text)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
    if length(trim(coalesce(p_usuario, ''))) = 0 then
        raise exception 'TURNERO: falta el usuario que reabre el periodo.' using errcode = 'P0001';
    end if;
    if length(trim(coalesce(p_motivo, ''))) < regla('motivo_min_caracteres') then
        raise exception 'TURNERO: reabrir un periodo cerrado exige un motivo de al menos % caracteres.',
            regla('motivo_min_caracteres') using errcode = 'P0001';
    end if;
    if not exists (select 1 from periodos_nomina where anio = p_anio and mes = p_mes and estado = 'cerrado') then
        raise exception 'TURNERO: el periodo %-% no está cerrado.', p_anio, lpad(p_mes::text, 2, '0')
            using errcode = 'P0001';
    end if;

    update periodos_nomina
    set estado = 'abierto', cambiado_por = p_usuario, cambiado_en = now()
    where anio = p_anio and mes = p_mes;

    insert into periodos_eventos (anio, mes, accion, usuario, motivo)
    values (p_anio, p_mes, 'reapertura', p_usuario, p_motivo);
end
$$;


-- El guardián. Mira la fecha de turno de antes y la de después: mover un turno
-- de un mes cerrado a uno abierto también es tocar el mes cerrado.
--
-- Deja pasar los cambios técnicos, sobre todo el archivado del cierre de mes,
-- que marca en bloque los turnos de meses terminados.
create or replace function fn_turnos_periodo() returns trigger
language plpgsql
as $$
declare
    v_fecha date;
begin
    if tg_op = 'UPDATE'
       and datos_del_turno(to_jsonb(new)) is not distinct from datos_del_turno(to_jsonb(old)) then
        return new;
    end if;

    if tg_op in ('UPDATE', 'DELETE') and periodo_cerrado(old.fecha_turno) then
        v_fecha := old.fecha_turno;
    elsif tg_op in ('INSERT', 'UPDATE') and periodo_cerrado(fecha_local(new.ts_entrada)) then
        v_fecha := fecha_local(new.ts_entrada);
    end if;

    if v_fecha is not null then
        raise exception 'TURNERO: el periodo % está cerrado. Para cambiar un turno de ese mes hay que reabrirlo, y la reapertura queda registrada.',
            to_char(v_fecha, 'YYYY-MM') using errcode = 'P0001';
    end if;

    if tg_op = 'DELETE' then
        return old;
    end if;
    return new;
end
$$;

drop trigger if exists tg_turnos_10_periodo on turnos;
create trigger tg_turnos_10_periodo
before insert or update or delete on turnos
for each row execute function fn_turnos_periodo();
