-- Habilitar Row-Level Security en las tablas del schema public.
--
-- Sin RLS, cualquiera con la URL del proyecto y la clave anonima (que por
-- diseno es publica) puede leer, modificar y borrar estas tablas por la API
-- REST, sin pasar por la aplicacion. Habilitarlo sin politicas no la rompe,
-- porque conecta por Postgres directo con el rol `postgres`, que tiene
-- BYPASSRLS. El detalle esta en seguridad/README.md.
--
-- Ejecutar en: Supabase Dashboard > SQL Editor (proyecto proyectos-tcl-uio).
-- Reversible: ver el bloque del final.

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
-- la API, para que la tabla no aparezca ni como accesible.
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
