# ⏰ Marcador de Horas Extra

App Streamlit para marcar entrada / salida de empleados con persistencia en Google Sheets. Maneja turnos nocturnos correctamente usando timestamps completos.

## 📁 Estructura del proyecto

```
PROYECTO_HORAS_EXTRA/
├── .streamlit/
│   ├── secrets.toml           ← credenciales LOCALES (NO subir a GitHub)
│   └── secrets.toml.example   ← plantilla sí versionada
├── .gitignore
├── app.py                     ← aplicación Streamlit
├── requirements.txt
└── README.md
```

## 🚀 Puesta en marcha local (VS Code)

### 1. Crear entorno virtual e instalar dependencias

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Preparar la hoja de Google Sheets

1. Crea una hoja nueva en Google Sheets.
2. Renombra la pestaña a **`Registros`**.
3. En la fila 1 escribe estos encabezados (en el orden exacto):
   `Nombre | Area | Fecha de Turno | Timestamp Entrada | Timestamp Salida | Horas Trabajadas | Horas Extra | Estado | Observaciones`
4. Copia el ID del spreadsheet (lo que va entre `/d/` y `/edit` en la URL).

### 3. Crear el Service Account en Google Cloud

1. Entra a https://console.cloud.google.com/ → crea (o selecciona) un proyecto.
2. **APIs & Services → Library** → habilita **Google Sheets API** y **Google Drive API**.
3. **IAM & Admin → Service Accounts → Create Service Account**.
4. En la cuenta creada: **Keys → Add Key → Create new key → JSON**. Se descargará un archivo `.json`.
5. Copia el `client_email` del JSON (algo como `nombre@proyecto.iam.gserviceaccount.com`).
6. Abre tu Google Sheet y **compártela** con ese email dando permiso de **Editor**.

### 4. Configurar `secrets.toml` local

Crea el archivo `.streamlit/secrets.toml` (está en `.gitignore`, NO se sube) copiando `secrets.toml.example` y pegando los valores del JSON del Service Account. Respeta el formato del `private_key` con los `\n` literales.

```toml
[connections.gsheets]
spreadsheet = "https://docs.google.com/spreadsheets/d/TU_ID_AQUI/edit"
worksheet = "Registros"

type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "..."
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."
universe_domain = "googleapis.com"
```

### 5. Ejecutar la app

```bash
streamlit run app.py
```

## ☁️ Despliegue en Streamlit Community Cloud

1. Sube el proyecto a un repo **público** de GitHub (verifica que `.streamlit/secrets.toml` NO esté subido; solo `secrets.toml.example`).
2. Entra a https://share.streamlit.io/ e inicia sesión con GitHub.
3. **New app** → selecciona el repo, la rama y el archivo `app.py`.
4. Antes de hacer **Deploy**, haz clic en **Advanced settings → Secrets** y pega **exactamente el mismo contenido** de tu `secrets.toml` local (bloque `[connections.gsheets]` completo).
5. Deploy. Streamlit Cloud inyectará los secrets en tiempo de ejecución — nunca viajan por tu repo.

## 🧠 Lógica de turnos nocturnos

El campo `Timestamp` guarda `YYYY-MM-DD HH:MM:SS`, así que restar dos timestamps maneja correctamente el cruce de medianoche (ej. entrada 21:00 del día 1 y salida 07:00 del día 2 = 10 h). El campo `Fecha de Turno` en la fila de salida **hereda** la fecha de la entrada, lo que permite agrupar reportes por turno aunque el turno cruce dos días naturales.
