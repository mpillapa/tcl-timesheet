-- FASE B. Aplicar después de V101, con la aplicación ya conectada como
-- `turnero_app` o al menos con la versión nueva desplegada, que fija la zona
-- horaria de cada sesión (core/db.py).
--
-- La entrada y la salida pasan de `timestamp` (hora de Ecuador sin zona) a
-- `timestamptz` (instante absoluto). Hasta ahora una hora guardada era correcta
-- solo si quien la escribía y quien la leía suponían la misma zona. La sesión
-- por defecto de Supabase está en UTC: una edición desde el Table Editor o un
-- script con otra configuración guardaba la hora corrida cinco horas sin aviso.
--
-- Las horas guardadas se interpretan como hora de Ecuador, que es lo que
-- siempre fueron. Ecuador no tiene horario de verano, así que la conversión no
-- tiene casos ambiguos.
--
-- Por qué fase B
-- --------------
-- La versión anterior de la aplicación no fija la zona de la sesión. Con las
-- columnas ya en timestamptz, sus escrituras se leerían como UTC y quedarían
-- corridas cinco horas.


-- Versiones de las utilidades para columnas con zona. Conviven con las de V001:
-- Postgres elige según el tipo, de modo que las funciones y triggers que las
-- llaman no cambian.
create or replace function horas_de(p_entrada timestamptz, p_salida timestamptz) returns numeric
language sql immutable
as $$ select round((extract(epoch from p_salida - p_entrada) / 3600)::numeric, 2) $$;

create or replace function fecha_local(p_ts timestamptz) returns date
language sql immutable
as $$ select (p_ts at time zone 'America/Guayaquil')::date $$;

create or replace function instante(p_ts timestamptz) returns timestamptz
language sql immutable
as $$ select p_ts $$;


-- Postgres no deja cambiar el tipo de una columna que un trigger nombra en su
-- definición. Se quitan los dos que lo hacen y se recrean igual.
drop trigger if exists tg_turnos_auditoria_update on turnos;
drop trigger if exists tg_turnos_marcaciones on turnos;

alter table turnos
    alter column ts_entrada type timestamptz using ts_entrada at time zone 'America/Guayaquil',
    alter column ts_salida  type timestamptz using ts_salida  at time zone 'America/Guayaquil';

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

create trigger tg_turnos_marcaciones
after insert or update of ts_salida on turnos
for each row execute function fn_turnos_marcaciones();
