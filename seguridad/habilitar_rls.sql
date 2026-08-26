-- Habilitar Row-Level Security en las tablas del schema public
-- ---------------------------------------------------------------------------
-- Contexto: el advisor de Supabase reporta `rls_disabled_in_public` sobre las
-- tablas `turnos` y `horas_esperadas`. Sin RLS, cualquiera que tenga la URL del
-- proyecto y la clave anonima (que por diseno es publica, viaja en clientes web)
-- puede leer, modificar y borrar esas tablas a traves de la API REST
-- (PostgREST), sin pasar por la aplicacion.
--
-- Por que esto NO rompe la aplicacion:
-- La app no usa la API REST de Supabase. Conecta por Postgres directo
-- (SQLAlchemy + pooler, ver core/db.py) con el rol `postgres`, que en Supabase
-- tiene el atributo BYPASSRLS. Las politicas de RLS no se le aplican. Lo mismo
-- vale para `service_role`. RLS solo afecta a `anon` y `authenticated`, roles
-- que esta aplicacion nunca usa.
--
-- Habilitar RLS sin crear ninguna politica es la configuracion mas restrictiva
-- posible: deniega todo a anon/authenticated. Si en el futuro alguna aplicacion
-- necesita entrar por la API REST, habra que escribir politicas explicitas.
--
-- Ejecutar en: Supabase Dashboard > SQL Editor (proyecto proyectos-tcl-uio).
-- Reversible: ver el bloque de reversion al final.
-- ---------------------------------------------------------------------------

-- Paso 0. Verificacion previa. Anota el resultado antes de cambiar nada.
select n.nspname  as esquema,
       c.relname  as tabla,
       c.relrowsecurity as rls_activo,
       (select count(*) from pg_policy p where p.polrelid = c.oid) as politicas
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where c.relkind = 'r'
  and n.nspname = 'public'
order by 2;

-- Paso 1. Habilitar RLS. Sin politicas = nadie que no tenga BYPASSRLS entra.
alter table public.turnos           enable row level security;
alter table public.horas_esperadas  enable row level security;

-- Paso 2. Quitar los permisos que Supabase concede por defecto a los roles de
-- la API. RLS ya bloquea las filas; esto ademas evita que la tabla siquiera
-- aparezca como accesible. Defensa en profundidad.
revoke all on public.turnos          from anon, authenticated;
revoke all on public.horas_esperadas from anon, authenticated;

-- Paso 3. Verificacion posterior: debe mostrar rls_activo = true, politicas = 0.
select n.nspname as esquema,
       c.relname as tabla,
       c.relrowsecurity as rls_activo,
       (select count(*) from pg_policy p where p.polrelid = c.oid) as politicas
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where c.relkind = 'r'
  and n.nspname = 'public'
order by 2;


-- ---------------------------------------------------------------------------
-- REVERSION (solo si algo dejara de funcionar; no deberia)
-- ---------------------------------------------------------------------------
-- alter table public.turnos          disable row level security;
-- alter table public.horas_esperadas disable row level security;
-- grant all on public.turnos          to anon, authenticated;
-- grant all on public.horas_esperadas to anon, authenticated;
