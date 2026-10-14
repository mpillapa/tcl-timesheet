# Limpieza de requirements.txt

Pendiente técnico, sin urgencia. Registrado el 2026-10-14.

## Qué sobra y qué falta

`requirements.txt` lista dos paquetes que el código no importa en ningún sitio:

- `st-gsheets-connection`, de cuando la app leía Google Sheets directamente.
- `streamlit-aggrid`, que nunca se usó.

Y le faltan dos que sí se importan y hoy llegan solo como dependencia de
`st-gsheets-connection`:

- `gspread`, que usa `core/sheets_backup.py` para el espejo.
- `google-auth`, que usa `auditoria/comparar_con_sheets.py`.

Si alguien quita `st-gsheets-connection` sin añadir esos dos, el espejo deja de
importar y la aplicación no arranca.

## Versión propuesta

```
streamlit
pandas
streamlit-javascript
tzdata
sqlalchemy>=2.0,<2.1
psycopg2-binary
gspread>=5.12,<6
google-auth>=2.15,<3
```

`gspread` va por debajo de 6 porque la 6 cambia el orden de los argumentos de
`Worksheet.update`, que usa `espejo_archivar`. Es el mismo límite que hoy impone
`st-gsheets-connection`.

Se probó en un entorno limpio con Python 3.13: instala streamlit 1.65 y pandas
3.0.6, los módulos importan y pasan las 79 pruebas.

## Por qué no se desplegó con la limpieza de código

Cambiar `requirements.txt` hace que Streamlit Cloud reinstale todo el entorno.
Como `streamlit` y `pandas` no tienen versión fija, el despliegue instalaría las
últimas versiones, distintas de las que corren hoy. La limpieza de código se
desplegó sin tocar el entorno para que cualquier problema tuviera una sola
causa posible.

## Cómo desplegarlo

1. Antes, decidir si se fijan `streamlit` y `pandas` a una versión probada
   (hoy el entorno local usa streamlit 1.56.0 y pandas 3.0.2). Fijarlas evita
   que un despliegue cualquiera traiga una versión nueva sin aviso.
2. Hacer el cambio en un commit solo, en un horario de poco uso.
3. Después del push, seguir el log de despliegue en Streamlit Cloud hasta que
   la app arranque, y probar una marcación de entrada y salida.
4. Si falla, `git revert HEAD` y `git push` devuelven el entorno anterior.
