-- La bitácora pasa a ser de solo inserción y guarda el motivo.
--
-- Tres cambios:
--
-- 1. Nadie puede modificar ni borrar una fila de turnos_auditoria, tampoco el
--    dueño de la tabla. Un trigger lo rechaza. La única forma de saltarlo es
--    desactivar el trigger, que es un ALTER TABLE: Postgres registra todo DDL
--    en su log (log_statement = ddl en el proyecto), así que ese salto deja
--    rastro fuera de la propia base.
--
-- 2. Cada fila guarda el motivo del cambio y, si vino de un ajuste aprobado, el
--    id del ajuste. Los dos llegan por la sesión, igual que el autor:
--    set_config('app.motivo', ...) y set_config('app.ajuste_id', ...).
--
-- 3. La anulación se audita. Las columnas de V002 entran en la condición del
--    trigger de UPDATE.


alter table turnos_auditoria
    add column if not exists motivo    text not null default '',
    add column if not exists ajuste_id bigint;

create index if not exists ix_turnos_aud_ajuste on turnos_auditoria (ajuste_id) where ajuste_id is not null;


create or replace function fn_turnos_auditoria() returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    v_usuario text   := coalesce(nullif(current_setting('app.usuario',   true), ''), '');
    v_origen  text   := coalesce(nullif(current_setting('app.origen',    true), ''), 'fuera_de_la_app');
    v_motivo  text   := coalesce(current_setting('app.motivo', true), '');
    v_ajuste  bigint := nullif(current_setting('app.ajuste_id', true), '')::bigint;
begin
    if tg_op = 'DELETE' then
        insert into turnos_auditoria (turno_id, accion, usuario, origen, motivo, ajuste_id,
                                      antes, despues, horas_antes, horas_despues,
                                      nombre, fecha_turno)
        values (old.id, 'DELETE', v_usuario, v_origen, v_motivo, v_ajuste,
                to_jsonb(old), null, old.horas_trabajadas, null,
                old.nombre, old.fecha_turno);
    else
        insert into turnos_auditoria (turno_id, accion, usuario, origen, motivo, ajuste_id,
                                      antes, despues, horas_antes, horas_despues,
                                      nombre, fecha_turno)
        values (old.id, 'UPDATE', v_usuario, v_origen, v_motivo, v_ajuste,
                to_jsonb(old), to_jsonb(new), old.horas_trabajadas, new.horas_trabajadas,
                old.nombre, old.fecha_turno);
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
   or old.observaciones    is distinct from new.observaciones
   or old.anulado          is distinct from new.anulado
   or old.motivo_anulacion is distinct from new.motivo_anulacion)
execute function fn_turnos_auditoria();


-- Solo inserción. Un trigger de fila para UPDATE y DELETE y uno de sentencia
-- para TRUNCATE, que no dispara triggers de fila.
drop trigger if exists tg_turnos_auditoria_inmutable on turnos_auditoria;
create trigger tg_turnos_auditoria_inmutable
before update or delete on turnos_auditoria
for each row execute function fn_solo_insercion();

drop trigger if exists tg_turnos_auditoria_sin_truncate on turnos_auditoria;
create trigger tg_turnos_auditoria_sin_truncate
before truncate on turnos_auditoria
for each statement execute function fn_solo_insercion();

comment on table turnos_auditoria is
    'Bitácora permanente de cambios sobre turnos. Escrita solo por trigger y de solo inserción. Retención: ver seguridad/retencion_y_acceso.md.';
