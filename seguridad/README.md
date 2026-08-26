# Seguridad de la base de datos

## RLS en el schema `public`

El advisor de Supabase reporta `rls_disabled_in_public` sobre `turnos` y
`horas_esperadas`. El script [`habilitar_rls.sql`](habilitar_rls.sql) lo corrige.

### Que expone el problema

Todo proyecto de Supabase publica una API REST (PostgREST) en
`https://<ref>.supabase.co/rest/v1/<tabla>` para los schemas marcados como
expuestos, que por defecto incluyen `public`. Esa API se consume con la clave
anonima, que no es un secreto: esta pensada para incrustarse en clientes web.
La unica barrera prevista entre esa clave y las filas de una tabla es RLS. Sin
RLS habilitado, quien tenga URL y clave anonima puede leer, escribir y borrar
todo, sin pasar por la autenticacion de la aplicacion.

### Por que habilitarlo no rompe la aplicacion

La aplicacion no toca la API REST. Conecta por Postgres directo con SQLAlchemy
a traves del pooler (ver [`core/db.py`](../core/db.py)) usando el rol
`postgres`, que en Supabase tiene el atributo `BYPASSRLS`: las politicas de RLS
no se le evaluan. RLS solo condiciona a los roles `anon` y `authenticated`, que
esta aplicacion nunca usa.

Consecuencia practica: habilitar RLS sin escribir ninguna politica cierra la
puerta de la API y deja la aplicacion intacta.

### Limitacion que esto introduce

Si mas adelante alguna aplicacion necesita leer estas tablas por la API REST
(supabase-js, supabase-py, un frontend), no funcionara hasta que se escriban
politicas explicitas para el rol que corresponda. Es una decision consciente:
hoy ninguna app lo necesita.

### Comprobacion antes y despues

Con la clave anonima del proyecto en una variable de entorno:

```bash
curl "https://<ref>.supabase.co/rest/v1/turnos?select=*&limit=1" \
  -H "apikey: $SUPABASE_ANON_KEY"
```

Antes del cambio devuelve filas. Despues debe devolver una lista vacia o un
error de permisos. Nunca pegues la clave en un archivo del repositorio.

### Estado

Aplicado el 2026-08-26 en el proyecto `proyectos-tcl-uio`. La verificacion
posterior devolvio:

| esquema | tabla           | rls_activo | politicas |
| ------- | --------------- | ---------- | --------- |
| public  | horas_esperadas | true       | 0         |
| public  | turnos          | true       | 0         |

Prueba de humo del 2026-08-26: se creo un turno desde la aplicacion y se
guardo sin errores. Queda confirmado en la practica que el rol `postgres` pasa
por encima de RLS y que la aplicacion no se ve afectada por este cambio.

### Pendiente

- Evaluar si conviene dejar de exponer `public` en Settings > API, dado que
  ninguna aplicacion del proyecto usa la API REST.

### Nota sobre el otro proyecto del mismo Supabase

HERRAMIENTAS_DOCUMENTALES_WEB comparte el proyecto de Supabase pero vive en el
schema `herramientas`, que no esta expuesto en la API REST. Por eso no aparece
en el advisor y el script de aqui no lo toca.
