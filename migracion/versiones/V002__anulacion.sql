-- Anular en vez de borrar.
--
-- Hasta ahora "eliminar un turno" era un DELETE. La fila sobrevivía en la
-- bitácora, pero desaparecía de la tabla que se consulta, y nada impedía borrar
-- también la bitácora. Desde aquí un turno no se borra: se marca como anulado,
-- con quién, cuándo y por qué. El rol de la aplicación pierde el permiso de
-- DELETE en V010.
--
-- Compatibilidad con la versión actual de la aplicación: las columnas nuevas
-- tienen valor por defecto y nadie las escribe hasta desplegar el código nuevo,
-- así que la aplicación en producción no nota el cambio.


alter table turnos
    add column if not exists anulado          boolean not null default false,
    add column if not exists anulado_en       timestamptz,
    add column if not exists anulado_por      text,
    add column if not exists motivo_anulacion text;

-- Un turno anulado tiene autor, fecha y motivo. Uno vigente no tiene ninguno.
alter table turnos drop constraint if exists ck_turnos_anulacion;
alter table turnos add constraint ck_turnos_anulacion check (
       (not anulado and anulado_en is null and anulado_por is null and motivo_anulacion is null)
    or (anulado and anulado_en is not null
                and length(trim(coalesce(anulado_por, ''))) > 0
                and length(trim(coalesce(motivo_anulacion, ''))) > 0)
);

-- La clave natural de la app, (nombre, entrada), tiene que seguir siendo única
-- entre los turnos vigentes, pero no puede impedir rehacer un turno que se anuló
-- con la misma hora de entrada. Por eso el índice nuevo es parcial.
--
-- El índice viejo, que cubre también los anulados, se queda hasta V101. La
-- versión actual de la aplicación inserta con ON CONFLICT ((lower(nombre)),
-- ts_entrada), y esa cláusula necesita un índice sin predicado para resolverse.
-- La versión nueva añade WHERE NOT anulado, que funciona con los dos.
create unique index if not exists ux_turnos_vigente_nombre_entrada
    on turnos (lower(nombre), ts_entrada)
    where not anulado;

create index if not exists ix_turnos_anulados on turnos (anulado_en) where anulado;
