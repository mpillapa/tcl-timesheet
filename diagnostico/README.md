# Diagnóstico de conexión

## Incidente del 2026-08-25: "Running leer_registros()" sin fin

### Síntoma

La aplicación en Streamlit Cloud se quedaba mostrando `Running
leer_registros()` indefinidamente. No aparecía ningún mensaje de error. Pasaba
a ratos: al reiniciar la aplicación volvía a funcionar, y al cabo de un rato se
repetía.

### Lo que se descartó

**No era el RLS.** Se habilitó el 2026-08-26, un día después de que empezaran
los cuelgues, y guardar un turno siguió funcionando. Ver [`../seguridad/`](../seguridad/).

**No era el pooler de sesión.** La primera hipótesis fue que el puerto 5432
había dejado de autenticar, como le pasó a HERRAMIENTAS_DOCUMENTALES_WEB el
2026-08-24. Se probó con [`probar_conexion.py`](probar_conexion.py) y resultó
falsa: el 5432 conecta en 1,7 s y lee las 1032 filas sin problema.

De paso, esa prueba dejó claro lo contrario de lo que se suponía: **el puerto
6543 no responde desde la red de la empresa**, ni siquiera lo suficiente para
devolver un error. Migrar a él, que era la corrección propuesta, habría dejado
la aplicación peor. Está anotado en [`../core/db.py`](../core/db.py) para que
nadie lo intente de nuevo.

### La causa

El engine de SQLAlchemy no tenía ningún tiempo límite: ni de conexión, ni de
consulta, ni keepalives de TCP. `pool_recycle` estaba en 30 minutos.

Cuando el pooler de Supabase descarta una conexión inactiva sin cerrarla
limpiamente, el socket del cliente queda medio abierto: el sistema operativo
cree que sigue viva y cualquier lectura sobre ella espera para siempre.
`pool_pre_ping` estaba activo, pero no ayuda, porque el propio ping se queda
esperando en esa misma conexión muerta.

El cuelgue se propagaba a toda la aplicación por `st.cache_data`, que serializa
las llamadas a una función con un lock: el hilo atascado retenía el lock de
`leer_registros` y cualquier otra sesión que llamara a esa función se quedaba
esperando en el mismo punto. Por eso el síntoma era idéntico para todos los
usuarios, y por eso reiniciar lo resolvía: se descarta el pool entero.

### El arreglo

En [`../core/db.py`](../core/db.py):

| Parámetro | Antes | Ahora | Para qué |
|---|---|---|---|
| keepalives de TCP | ninguno | activos, ~60 s | detectar el socket muerto en vez de esperar |
| `connect_timeout` | ninguno | 10 s | limitar el establecimiento de la conexión |
| `statement_timeout` | 2 min (default de Supabase) | 30 s | limitar la consulta del lado del servidor |
| `pool_timeout` | 30 s (implícito) | 15 s | limitar la espera por una conexión libre |
| `pool_recycle` | 1800 s | 240 s | renovar antes de que el pooler la descarte |

| `tcp_user_timeout` | ninguno | 15 s | cortar si la conexión muere a mitad de una consulta |
| reintento de lecturas | ninguno | 3 intentos | recuperarse solo, sin mostrar error |

Los keepalives son la pieza central. Los demás son defensa en profundidad.

El reintento (`reintento_de_lectura` en `core/db.py`) es lo que hace que el
usuario no se entere de nada: cuando una lectura falla por conexión caída,
descarta el pool completo, abre conexiones nuevas y vuelve a intentar. Es lo
mismo que se lograba reiniciando la aplicación a mano, pero automático y en
un segundo.

Se aplica solo a lecturas, a propósito. Reintentar una escritura podría
insertar el mismo turno dos veces, y eso es peor que mostrar un error. Las
escrituras sí se benefician de los keepalives y de los tiempos límite, que es
lo que impide que se queden colgadas.

Comprobado el 2026-08-26 contra la base real: se cerró la conexión del pool por
debajo, a la fuerza, y `_leer_turnos` devolvió las 1032 filas en 1,2 s sin
error visible.

El `statement_timeout` se aplica en el evento `checkout` del pool, no en
`connect`. Con `connect` no funcionaba: la primera consulta salía con 30 s y la
siguiente volvía a 2 min, porque el pooler descarta el estado de sesión al
reciclar la conexión.

### Qué esperar ahora

Hay cuatro capas, de la que actúa antes a la que actúa después:

1. `pool_recycle` de 4 minutos: la conexión se renueva antes de que el pooler
   la descarte. En la mayoría de los casos el problema no llega a ocurrir.
2. Keepalives: si aun así muere, el sistema operativo lo detecta en lugar de
   esperar sin fin.
3. `pool_pre_ping` más el reintento: la conexión muerta se descarta y la lectura
   se repite con una nueva. El usuario no ve nada.
4. Tiempos límite: si nada de lo anterior funciona porque la base está
   realmente caída, aparece un error en pantalla en menos de un minuto.

El cuelgue indefinido, que era el problema, ya no puede darse: ninguna espera
es infinita. Lo que sí puede pasar es que Supabase esté caído de verdad, y en
ese caso la aplicación lo dirá en vez de quedarse pensando.

Si el cuelgue se repitiera pese a esto, la causa es otra y habrá un mensaje
concreto que leer en los logs de Streamlit Cloud.

## Uso del script

```
.venv\Scripts\python.exe diagnostico\probar_conexion.py
```

Prueba los dos puertos del pooler con un límite de 10 segundos e informa cuál
responde. Útil cuando la aplicación no conecta y no está claro si el problema
es la red, las credenciales o el modo de conexión. No imprime la contraseña.
