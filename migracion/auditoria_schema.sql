-- Bitácora de cambios sobre turnos: quién cambió qué, cuándo y desde qué valor.
--
-- Por qué existe
-- --------------
-- Hasta ahora la única huella de una corrección era un texto en
-- turnos.observaciones, que registra autor, fecha y motivo pero no el valor
-- anterior. Sin ese valor no se puede responder cuántas horas se modificaron:
-- la auditoría del 2026-09-02 solo pudo acotarlo en 6.12 h contra las 45.12 h
-- que se midieron reconstruyendo el historial de versiones del Sheet espejo,
-- una fuente que Google purga sola y que ya está apagada. Ver auditoria/README.md.
--
-- Esta tabla es el respaldo permanente de las horas que los supervisores
-- recortan. No se purga.
--
-- Cómo se puebla
-- --------------
-- Un trigger es el único escritor. La aplicación no inserta aquí; solo deja el
-- autor en la sesión con set_config('app.usuario', ..., true) y
-- set_config('app.origen', ..., true), ambos locales a la transacción, de modo
-- que no se filtran a otra petición que reutilice la conexión del pool. Ver
-- core/data.py:_sellar_autor.
--
-- La ventaja de hacerlo con trigger y no en el código es que también queda
-- registrado lo que se edite por fuera de la aplicación, desde el Table Editor
-- de Supabase o desde psql. En esos casos no hay autor y se graba
-- origen = 'fuera_de_la_app', que es justo la señal que interesa ver.
--
-- Contrapartida asumida: si el insert en la bitácora falla, falla la escritura
-- del turno. Es un insert sin dependencias, así que el riesgo es bajo, y se
-- prefiere eso a perder la traza en silencio.
--
-- Ejecutar en: Supabase Dashboard, SQL Editor.
-- Idempotente: se puede ejecutar varias veces sin dañar nada.


-- ---------------------------------------------------------------------------
-- Paso 1. La tabla
-- ---------------------------------------------------------------------------
create table if not exists turnos_auditoria (
    id            bigint generated always as identity primary key,

    -- turnos.id del registro afectado. Sin foreign key a propósito: la
    -- bitácora tiene que sobrevivir al borrado del turno, que es justamente
    -- uno de los eventos que interesa auditar.
    turno_id      bigint not null,
    accion        text   not null,           -- 'UPDATE' | 'DELETE'
    momento       timestamptz not null default now(),

    -- Quién. `usuario` es el admin_user de la sesión, el nombre del
    -- colaborador cuando la marcación es propia, o vacío si no hubo sesión.
    -- `origen` desambigua sin depender de convenciones en el texto:
    --   'admin'            corrección o borrado desde el panel
    --   'colaborador'      el propio empleado marcando entrada o salida
    --   'sistema'          barrido automático de turnos olvidados
    --   'fuera_de_la_app'  edición directa en la base
    usuario       text not null default '',
    origen        text not null default 'fuera_de_la_app',

    -- Fila completa antes y después. jsonb para que una columna nueva en
    -- turnos no obligue a migrar la bitácora. En un DELETE, `despues` es null.
    antes         jsonb not null,
    despues       jsonb,

    -- Desnormalizadas porque son las que se consultan siempre. El delta
    -- negativo es una reducción de horas.
    --
    -- Ojo al sumar: en un DELETE, `horas_despues` es null y el delta sale como
    -- la pérdida completa del turno, que es correcto pero no es comparable con
    -- el recorte de un UPDATE. Cualquier total de horas recortadas tiene que
    -- filtrar por accion, no sumar la columna entera.
    horas_antes   numeric(6,2),
    horas_despues numeric(6,2),
    delta_horas   numeric(6,2) generated always as
                  (coalesce(horas_despues, 0) - coalesce(horas_antes, 0)) stored,

    -- Copia del nombre y la fecha de turno para poder filtrar sin abrir el
    -- jsonb ni depender de que el turno siga existiendo.
    nombre        text not null default '',
    fecha_turno   date
);

comment on table turnos_auditoria is
    'Bitácora permanente de cambios sobre turnos. Escrita solo por trigger. Retención indefinida: es el respaldo de las horas recortadas por supervisión.';

create index if not exists ix_turnos_aud_turno   on turnos_auditoria (turno_id);
create index if not exists ix_turnos_aud_usuario on turnos_auditoria (usuario, momento desc);
create index if not exists ix_turnos_aud_nombre  on turnos_auditoria (nombre, fecha_turno);
create index if not exists ix_turnos_aud_momento on turnos_auditoria (momento desc);

-- Índice parcial para la consulta que más se va a correr: los cambios que
-- movieron horas, que son una fracción del total.
create index if not exists ix_turnos_aud_delta
    on turnos_auditoria (delta_horas)
    where delta_horas <> 0;


-- ---------------------------------------------------------------------------
-- Paso 2. La función del trigger
-- ---------------------------------------------------------------------------
-- Las dos ramas están separadas a propósito. En un trigger de DELETE la
-- variable NEW no está asignada, y referenciarla, aunque sea dentro de un CASE
-- que no se va a cumplir, revienta con "record new is not assigned yet". Por
-- eso no hay un solo INSERT con condicionales.
create or replace function fn_turnos_auditoria() returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    v_usuario text := coalesce(nullif(current_setting('app.usuario', true), ''), '');
    v_origen  text := coalesce(nullif(current_setting('app.origen',  true), ''), 'fuera_de_la_app');
begin
    if tg_op = 'DELETE' then
        insert into turnos_auditoria (
            turno_id, accion, usuario, origen,
            antes, despues, horas_antes, horas_despues,
            nombre, fecha_turno
        )
        values (
            old.id, 'DELETE', v_usuario, v_origen,
            to_jsonb(old), null,
            old.horas_trabajadas, null,
            old.nombre, old.fecha_turno
        );
    else
        insert into turnos_auditoria (
            turno_id, accion, usuario, origen,
            antes, despues, horas_antes, horas_despues,
            nombre, fecha_turno
        )
        values (
            old.id, 'UPDATE', v_usuario, v_origen,
            to_jsonb(old), to_jsonb(new),
            old.horas_trabajadas, new.horas_trabajadas,
            old.nombre, old.fecha_turno
        );
    end if;
    return null;  -- trigger AFTER: el valor de retorno se ignora
end
$$;

comment on function fn_turnos_auditoria is
    'Único escritor de turnos_auditoria. Lee el autor de app.usuario y app.origen, ambos locales a la transacción.';


-- ---------------------------------------------------------------------------
-- Paso 3. Los triggers
-- ---------------------------------------------------------------------------
-- El UPDATE se audita solo si cambió algo del turno. Quedan fuera `archivado`
-- y `actualizado_en`, porque el cierre de mes los mueve en bloque sobre miles
-- de filas y eso no es un cambio de datos del turno.
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
   or old.observaciones    is distinct from new.observaciones)
execute function fn_turnos_auditoria();

drop trigger if exists tg_turnos_auditoria_delete on turnos;
create trigger tg_turnos_auditoria_delete
after delete on turnos
for each row
execute function fn_turnos_auditoria();

-- El INSERT no se audita. Un alta ya queda descrita por la propia fila de
-- turnos, y auditarla duplicaría la tabla entera sin añadir información.


-- ---------------------------------------------------------------------------
-- Paso 4. Cerrar la tabla a la API REST
-- ---------------------------------------------------------------------------
-- Mismo criterio que en seguridad/habilitar_rls.sql: RLS sin políticas cierra
-- la API REST sin afectar a la aplicación, que conecta por Postgres directo
-- con un rol que tiene BYPASSRLS.
alter table turnos_auditoria enable row level security;
revoke all on turnos_auditoria from anon, authenticated;


-- ---------------------------------------------------------------------------
-- Paso 5. Verificación
-- ---------------------------------------------------------------------------
select 'tabla'    as objeto, count(*)::text as valor from turnos_auditoria
union all
select 'triggers', count(*)::text from pg_trigger
    where tgrelid = 'turnos'::regclass and tgname like 'tg_turnos_auditoria%'
union all
select 'rls_activo', c.relrowsecurity::text from pg_class c
    where c.oid = 'turnos_auditoria'::regclass
union all
select 'politicas', count(*)::text from pg_policy p
    where p.polrelid = 'turnos_auditoria'::regclass;
-- Esperado: triggers = 2, rls_activo = true, politicas = 0.


-- ---------------------------------------------------------------------------
-- REVERSIÓN (solo si algo dejara de funcionar)
-- ---------------------------------------------------------------------------
-- Quitar los triggers deja de auditar sin tocar lo ya registrado. No borrar la
-- tabla: es el respaldo de las horas recortadas.
--
-- drop trigger if exists tg_turnos_auditoria_update on turnos;
-- drop trigger if exists tg_turnos_auditoria_delete on turnos;
