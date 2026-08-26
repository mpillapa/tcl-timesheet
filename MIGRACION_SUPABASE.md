# Migración a Supabase (PostgreSQL)

La base de datos pasa a ser la **fuente de verdad**; Google Sheets queda como
**espejo de respaldo** (cada escritura se replica automáticamente al Sheet).
Esta guía te lleva de cero a producción. Tiempo estimado: entre 20 y 30 minutos.

---

## Paso 1. Crear el proyecto en Supabase

1. Entra a [supabase.com](https://supabase.com) con tu cuenta → **New project**.
2. Nombre: `turnero-tcl` (o el que prefieras).
3. **Database password**: usa el generador de Supabase, pero asegúrate de que
   solo tenga **letras y números** (sin `@`, `:`, `/`, `#`, `?`, porque
   esos caracteres rompen la URL de conexión). Guárdala: la necesitas en el paso 2.
4. Región: `East US (North Virginia)` es la más cercana a Ecuador.
5. Espera ~2 minutos a que el proyecto termine de crearse.

## Paso 2. Copiar la URL de conexión

1. En el panel del proyecto, botón **Connect** (arriba).
2. Busca la sección **Session pooler** y copia la URI. Se ve así:

   ```
   postgresql://postgres.abcdefghijkl:[YOUR-PASSWORD]@aws-0-us-east-1.pooler.supabase.com:5432/postgres
   ```

   > Importante: debe ser el **Session pooler**, no "Direct connection".
   > La conexión directa no funciona desde Streamlit Cloud (solo IPv6).

3. Reemplaza `[YOUR-PASSWORD]` por la clave del paso 1 (sin los corchetes).

## Paso 3. Agregar la URL a secrets.toml

Abre `.streamlit/secrets.toml` y agrega estos bloques (puede ser al final):

```toml
[connections.supabase]
url = "postgresql://postgres.abcdefghijkl:TU_CLAVE@aws-0-us-east-1.pooler.supabase.com:5432/postgres"

[backup]
espejo_sheets = true
```

`espejo_sheets = true` mantiene el respaldo automático en Google Sheets.
El bloque `[connections.gsheets]` existente no se toca, porque el espejo lo usa.

## Paso 4. Ejecutar la migración

Desde la carpeta del proyecto (rama `feature/supabase`):

```powershell
.venv\Scripts\python -m migracion.migrar_sheets_a_supabase
```

El script:
- crea las tablas (`turnos`, `horas_esperadas`) con sus índices,
- copia Registros, Historico y Horas Esperadas del Sheet a la base,
- verifica que los totales cuadren y reporta cualquier fila con problemas.

No modifica el Sheet, solo lee, y se puede volver a ejecutar: si algo falla a
mitad, córrelo de nuevo y las filas ya migradas se omiten solas.

## Paso 5. Probar en local

```powershell
.venv\Scripts\streamlit run app.py
```

Checklist de prueba:
- [ ] Login de colaborador y **marcar entrada** → verifica que aparece en
      Supabase (Table Editor → `turnos`) **y** en el Sheet (última fila).
- [ ] **Marcar salida** del mismo turno → se actualiza en ambos lados.
- [ ] Panel super admin: dashboard, filtros y tabla cargan (nota la velocidad).
- [ ] Una corrección manual desde el panel.

## Paso 6. Desplegar a producción

1. Sube la rama y fusiónala:
   ```powershell
   git push -u origin feature/supabase
   # probar, y cuando estés conforme:
   git switch main
   git merge feature/supabase
   git push
   ```
2. En **Streamlit Cloud → Settings → Secrets**, agrega los mismos bloques
   `[connections.supabase]` y `[backup]` del paso 3 (los secrets de la nube
   son independientes del archivo local).
3. La app se redespliega sola con el push a `main`.

---

## Después de migrar

- **Ver/editar datos**: Supabase → Table Editor → tabla `turnos`. Es el
  reemplazo del Sheet como "vista de administrador de datos".
- **Horas esperadas del mes**: ya no se editan en la hoja "Horas Esperadas";
  se editan en la tabla `horas_esperadas` del Table Editor (columnas
  `anio`, `mes` (de 1 a 12), `horas`).
- **El Sheet es solo respaldo**: no edites filas a mano ahí; los cambios
  manuales en el Sheet no llegan a la base de datos.
- **Apagar el espejo** (cuando tengas plena confianza): cambia
  `espejo_sheets = false` en secrets. Las marcaciones se vuelven entre 1 y 2 s
  más rápidas.

## Si algo sale mal (rollback)

La versión anterior sigue intacta en `main` y el Sheet tiene todos los datos
hasta el momento del corte. Para volver atrás en producción basta con
redesplegar `main` sin fusionar esta rama. No hay pérdida posible: la
migración nunca borra ni modifica el Sheet.
