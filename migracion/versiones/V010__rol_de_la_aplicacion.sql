-- La aplicación deja de conectarse como `postgres`.
--
-- Hasta ahora la aplicación usa el rol dueño de todo: puede borrar turnos,
-- desactivar triggers, vaciar la bitácora y tocar el esquema `herramientas`,
-- que es de otra aplicación que comparte este proyecto de Supabase. Esta
-- migración crea `turnero_app`, con lo justo para operar:
--
--   turnos            leer, insertar y actualizar; no borrar
--   ausencias         leer e insertar
--   resto de tablas   solo leer
--   ajustes, periodos solo a través de las funciones, que validan y registran
--
-- No tiene acceso al esquema `herramientas` ni a nada fuera de `public`.
--
-- Lo que esta migración no hace
-- -----------------------------
-- No pone contraseña. Una contraseña no va en el repositorio. Se fija aparte,
-- desde el SQL Editor, con:
--
--     alter role turnero_app password '<una contraseña generada>';
--
-- y la cadena de conexión nueva va en los secrets de Streamlit Cloud. Los pasos
-- están en migracion/README.md. Mientras no se haga, el rol existe pero no
-- puede entrar, y la aplicación sigue con `postgres` sin enterarse.


do $$
begin
    if not exists (select from pg_roles where rolname = 'turnero_app') then
        create role turnero_app login noinherit;
    end if;
end $$;

-- Valores por defecto de cada sesión del rol. La zona horaria hace que una
-- hora sin zona que llega desde la aplicación se lea como hora de Ecuador, que
-- es lo que importa cuando V102 pase las columnas a timestamptz.
alter role turnero_app set timezone = 'America/Guayaquil';
alter role turnero_app set statement_timeout = '30s';
alter role turnero_app set idle_in_transaction_session_timeout = '60s';


-- ---------------------------------------------------------------------------
-- Permisos sobre tablas
-- ---------------------------------------------------------------------------
grant usage on schema public to turnero_app;

grant select, insert, update on turnos    to turnero_app;
grant select, insert         on ausencias to turnero_app;

grant select on horas_esperadas, reglas_negocio, turnos_auditoria, marcaciones,
                ajustes_turno, periodos_nomina, periodos_eventos, empleados
      to turnero_app;

-- Las secuencias de las identidades en las que la aplicación inserta.
do $$
declare
    v_seq text;
begin
    foreach v_seq in array array[
        pg_get_serial_sequence('turnos', 'id'),
        pg_get_serial_sequence('ausencias', 'id')
    ] loop
        execute format('grant usage, select on sequence %s to turnero_app', v_seq);
    end loop;
end $$;

-- Explícito, aunque no se haya concedido nunca: deja escrita la intención.
revoke delete, truncate on turnos from turnero_app;


-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------
-- Todas las tablas tienen RLS activo y sin políticas, que es lo que las cierra
-- a la API REST. `postgres` lo salta por tener BYPASSRLS; `turnero_app` no lo
-- tiene, así que necesita políticas propias. Son abiertas porque el control de
-- qué ve cada administrador lo hace la aplicación por área, no la base.
do $$
declare
    v_tabla text;
begin
    foreach v_tabla in array array[
        'turnos', 'ausencias', 'horas_esperadas', 'reglas_negocio', 'turnos_auditoria',
        'marcaciones', 'ajustes_turno', 'periodos_nomina', 'periodos_eventos', 'empleados'
    ] loop
        execute format('drop policy if exists turnero_app_acceso on %I', v_tabla);
        execute format(
            'create policy turnero_app_acceso on %I for all to turnero_app using (true) with check (true)',
            v_tabla);
    end loop;
end $$;


-- ---------------------------------------------------------------------------
-- Funciones
-- ---------------------------------------------------------------------------
-- Postgres concede EXECUTE a PUBLIC por defecto. En Supabase eso incluye a
-- `anon`, que puede invocar funciones del esquema public por la API REST. Una
-- función SECURITY DEFINER al alcance de `anon` sería una puerta trasera, así
-- que se revoca a todos y se concede solo a la aplicación.
do $$
declare
    f regprocedure;
begin
    for f in
        select p.oid::regprocedure
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.proname in (
              'solicitar_correccion', 'solicitar_anulacion', 'aprobar_ajuste', 'rechazar_ajuste',
              '_aplicar_ajuste', 'cerrar_periodo', 'reabrir_periodo', 'cerrar_a_api',
              'fn_turnos_auditoria', 'fn_turnos_empleado', 'fn_turnos_marcaciones'
          )
    loop
        execute format('revoke execute on function %s from public', f);
        if exists (select from pg_roles where rolname = 'anon') then
            execute format('revoke execute on function %s from anon', f);
        end if;
        if exists (select from pg_roles where rolname = 'authenticated') then
            execute format('revoke execute on function %s from authenticated', f);
        end if;
    end loop;
end $$;

grant execute on function
    solicitar_correccion(bigint, timestamp, timestamp, text, text, text),
    solicitar_anulacion(bigint, text, text),
    aprobar_ajuste(bigint, text, text),
    rechazar_ajuste(bigint, text, text),
    cerrar_periodo(int, int, text, text),
    reabrir_periodo(int, int, text, text)
to turnero_app;
