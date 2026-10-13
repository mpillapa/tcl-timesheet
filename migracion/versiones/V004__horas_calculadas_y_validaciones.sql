-- La base calcula las horas y valida cada turno que se escribe.
--
-- Hasta ahora horas_trabajadas, horas_efectivas y horas_extra eran columnas
-- que cualquiera podía escribir con cualquier valor, sin relación con la
-- entrada y la salida. Desde aquí se calculan siempre a partir del horario, de
-- modo que no se pueden cambiar horas sin cambiar el horario, y el cambio de
-- horario queda en la bitácora.
--
-- Comprobado contra producción el 2026-10-13: en las 6284 filas las horas ya
-- coinciden con el horario, así que el cálculo no cambia ningún valor
-- existente. Ver migracion/README.md.
--
-- Por qué un trigger y no restricciones CHECK
-- -------------------------------------------
-- Las reglas valen para lo que se escribe desde ahora, no para el histórico.
-- Hay un turno de 37.9 h y dos pares de turnos solapados de antes de esta
-- versión. Un CHECK obligaría a corregirlos antes de migrar; el trigger solo
-- mira la fila que se está escribiendo. migracion/validar_datos_existentes.py
-- lista lo que el histórico incumple, para limpiarlo con calma.
--
-- La excepción son las dos reglas que el histórico ya cumple entero: estado
-- válido y salida posterior a la entrada. Esas van como CHECK validado, que
-- además protege de una escritura que salte el trigger.
--
-- Orden de los triggers
-- ---------------------
-- Postgres dispara los BEFORE en orden alfabético. Los nombres llevan número
-- para que el orden sea explícito: 10 periodo cerrado (V007), 20 empleado
-- (V005), 25 eventualidad (V006), 30 esta validación.


-- Columnas que cambian sin que cambie el turno: el cierre de mes, los vínculos
-- que mantienen los triggers y la marca de actualización.
create or replace function datos_del_turno(p_fila jsonb) returns jsonb
language sql immutable
as $$ select p_fila - array['archivado', 'actualizado_en', 'empleado_id', 'ausencia_id'] $$;


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
    --    cruza la medianoche. Es la regla de core/data.py
    --    (_con_fecha_turno_derivada); aquí se impone en vez de confiar en que
    --    cada sitio que escribe la respete.
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
    --    salida cuenta como el instante de su entrada: si contara hasta el
    --    infinito, el turno olvidado que queda en revisión impediría marcar la
    --    entrada siguiente.
    --
    --    Las eventualidades (evento distinto de vacío, ver V006) quedan fuera
    --    en los dos sentidos: un colaborador que viene a trabajar un día con
    --    permiso registrado tiene que poder marcar. Ese cruce es un dato a
    --    revisar por supervisión, no algo que deba frenar la marcación.
    if new.evento = '' and exists (
        select 1
        from turnos t
        where lower(t.nombre) = lower(new.nombre)
          and t.id <> new.id
          and not t.anulado
          and t.evento = ''
          and t.ts_entrada < coalesce(new.ts_salida, new.ts_entrada + interval '1 second')
          and new.ts_entrada < coalesce(t.ts_salida, t.ts_entrada + interval '1 second')
    ) then
        raise exception 'TURNERO: el horario se cruza con otro turno de % ya registrado.', new.nombre
            using errcode = 'P0001';
    end if;

    return new;
end
$$;

drop trigger if exists tg_turnos_30_validar on turnos;
create trigger tg_turnos_30_validar
before insert or update on turnos
for each row execute function fn_turnos_validar();


-- Las dos reglas que el histórico ya cumple, como restricción validada.
alter table turnos drop constraint if exists ck_turnos_estado;
alter table turnos add constraint ck_turnos_estado
    check (estado in ('Abierto', 'Completo', 'Revision')) not valid;
alter table turnos validate constraint ck_turnos_estado;

alter table turnos drop constraint if exists ck_turnos_salida_posterior;
alter table turnos add constraint ck_turnos_salida_posterior
    check (ts_salida is null or ts_salida > ts_entrada) not valid;
alter table turnos validate constraint ck_turnos_salida_posterior;

-- La comprobación de solapes busca por persona y entrada entre los vigentes.
-- No necesita índice propio: le sirve ux_turnos_vigente_nombre_entrada (V002).
