"""
Marcador de Entrada y Salida - Horas Extra
==========================================
App Streamlit con persistencia en Google Sheets vía st.connection (GSheetsConnection).
Esquema: UNA FILA POR TURNO (entrada y salida en la misma fila).
Soporta turnos nocturnos usando Timestamps completos (YYYY-MM-DD HH:MM:SS).
Incluye detección de olvidos y sección de corrección manual.
"""

import ipaddress

import streamlit as st
import pandas as pd
from datetime import datetime, date, time
from streamlit_gsheets import GSheetsConnection

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
EMPLEADOS_POR_AREA = {
    "SUPERVISORES": [
        "Jaramillo Napoleon",
        "Farinango Nelson",
        "Gonzaga Edison",
        "Hidalgo Bolivar",
        "Maldonado Patricio",
        "Velasco Jorge",
    ],
    "BODEGA": [
        "Reyes Edgar",
        "Almagro David",
        "Arellano Romel",
        "Collaguazo Darwin",
        "Nango Patricio",
        "Panimboza Javier",
        "Pazuna Pablo",
        "Rosario Diofer",
        "Tipantiza Luis",
        "Yanza Cristina",
    ],
    "IMPORT": [
        "Almeida Carlos",
        "Granda Melissa",
        "Mariscal Juan",
        "Quishpe Jose",
        "Sanchez Karla",
        "Santiana Christhopper",
    ],
    "DOCUMENTAL": [
        "Carvajal Omar",
        "Chancusig Danilo",
        "Conforme Jordy",
        "Monta Mayra",
        "Quingalombo Adrian",
        "Salinas Paola",
        "Taipe Angelo",
        "Villacres Stefany",
    ],
}
AREAS = list(EMPLEADOS_POR_AREA.keys())
AREA_DE = {emp: area for area, emps in EMPLEADOS_POR_AREA.items() for emp in emps}

# PIN personal (últimos 4 dígitos de cédula) → nombre del empleado.
# Si querés ocultar los PINs del repo público, movelos a secrets.toml → sección [pins].
PIN_A_EMPLEADO = {
    "3399": "Jaramillo Napoleon",
    "7404": "Yanza Cristina",
    "6607": "Almagro David",
    "7536": "Almeida Carlos",
    "9136": "Arellano Romel",
    "5311": "Carvajal Omar",
    "2183": "Chancusig Danilo",
    "0025": "Collaguazo Darwin",
    "1164": "Conforme Jordy",
    "7916": "Farinango Nelson",
    "8915": "Gonzaga Edison",
    "1959": "Granda Melissa",
    "4507": "Hidalgo Bolivar",
    "0797": "Maldonado Patricio",
    "0052": "Mariscal Juan",
    "9836": "Monta Mayra",
    "9206": "Nango Patricio",
    "8676": "Panimboza Javier",
    "0090": "Pazuna Pablo",
    "8037": "Quingalombo Adrian",
    "5442": "Quishpe Jose",
    "1067": "Reyes Edgar",
    "1133": "Rosario Diofer",
    "0433": "Salinas Paola",
    "1063": "Sanchez Karla",
    "7455": "Santiana Christhopper",
    "9745": "Taipe Angelo",
    "3229": "Tipantiza Luis",
    "1321": "Velasco Jorge",
    "4903": "Villacres Stefany",
}

WORKSHEET_NAME = "Registros"
COLUMNAS = [
    "Nombre",
    "Area",
    "Fecha de Turno",
    "Timestamp Entrada",
    "Timestamp Salida",
    "Horas Trabajadas",
    "Estado",
    "Observaciones",
]
TS_FMT = "%Y-%m-%d %H:%M:%S"

# Turno nocturno máximo razonable (21:00 → 07:00 = 10h) + margen de seguridad.
# Si un turno lleva más de esto abierto, casi seguro es un olvido de salida.
UMBRAL_OLVIDO_H = 18

# Si las horas trabajadas superan este valor, es obligatorio justificar el exceso.
UMBRAL_HORAS_EXTRA = 9.5
MIN_JUSTIF_CHARS = 20

st.set_page_config(page_title="Marcador de Horas", page_icon="⏰", layout="centered")


# ---------------------------------------------------------------------------
# Control de acceso: IP allowlist + PIN de respaldo
# ---------------------------------------------------------------------------
def _es_ip_publica(ip_str: str) -> bool:
    """True si la IP es pública ruteable (no privada/loopback/reservada)."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    except ValueError:
        return False


def _leer_xff() -> str:
    """Devuelve el contenido crudo del header X-Forwarded-For, o cadena vacía."""
    try:
        h = st.context.headers
        return h.get("X-Forwarded-For") or h.get("x-forwarded-for") or ""
    except Exception:
        return ""


def _obtener_ip_cliente() -> str:
    """
    Extrae la IP del cliente recorriendo X-Forwarded-For y devolviendo la primera IP
    PÚBLICA. Los proxies pueden insertar IPs privadas al inicio de la cadena (ej.
    LAN 192.168.x.x del cliente o infraestructura interna del cloud); esas se saltan.
    """
    xff = _leer_xff()
    if xff:
        for ip in (i.strip() for i in xff.split(",") if i.strip()):
            if _es_ip_publica(ip):
                return ip
        # Si ninguna es pública, devolver la primera (mejor que nada para diagnóstico)
        primeras = [i.strip() for i in xff.split(",") if i.strip()]
        if primeras:
            return primeras[0]
    try:
        h = st.context.headers
        return h.get("X-Real-IP") or h.get("x-real-ip") or ""
    except Exception:
        return ""


def check_access() -> None:
    """
    1) Valida que la IP esté en secrets.auth.allowed_ips. Si no, bloquea.
    2) Pide PIN personal (4 dígitos) → identifica al empleado → guarda usuario en sesión.
    """
    if st.session_state.get("auth_ok"):
        return

    auth_cfg = st.secrets.get("auth", {})
    allowed_ips = list(auth_cfg.get("allowed_ips", []))
    ip_cliente = _obtener_ip_cliente()

    # -------- Paso 1: IP allowlist --------
    # Si hay una lista configurada y detectamos una IP que no está en ella, bloquear.
    # (Si ip_cliente está vacío —ej. corriendo local sin proxy— se permite seguir.)
    if allowed_ips and ip_cliente and ip_cliente not in allowed_ips:
        st.title("🚫 Acceso denegado")
        st.error(
            f"Tu IP (`{ip_cliente}`) no está autorizada. "
            "El marcador solo puede usarse desde la red de la oficina. "
            "Si necesitas acceso desde otra ubicación, contacta al administrador "
            "para que agregue tu IP a la lista."
        )
        with st.expander("🔎 Detalles técnicos (para el administrador)"):
            st.code(f"IP detectada: {ip_cliente}\nX-Forwarded-For: {_leer_xff() or '(vacío)'}")
        st.stop()

    # -------- Paso 2: login por PIN personal --------
    st.title("⏰ Marcador de Horas")
    st.caption("Ingresa tu PIN personal (últimos 4 dígitos de tu cédula)")

    pin = st.text_input(
        "PIN",
        type="password",
        max_chars=4,
        key="pin_input",
        placeholder="••••",
    )
    if st.button("🔓 Ingresar", type="primary", use_container_width=True):
        if not (pin.isdigit() and len(pin) == 4):
            st.error("El PIN debe ser de 4 dígitos numéricos.")
        elif pin not in PIN_A_EMPLEADO:
            st.error("PIN incorrecto. Verifica con tu supervisor.")
        else:
            nombre = PIN_A_EMPLEADO[pin]
            st.session_state["auth_ok"] = True
            st.session_state["usuario"] = nombre
            st.session_state["area"] = AREA_DE[nombre]
            st.rerun()
    st.stop()


check_access()

conn = st.connection("gsheets", type=GSheetsConnection)


# ---------------------------------------------------------------------------
# Acceso a datos
# ---------------------------------------------------------------------------
COLS_TEXTO = [
    "Nombre",
    "Area",
    "Fecha de Turno",
    "Timestamp Entrada",
    "Timestamp Salida",
    "Estado",
    "Observaciones",
]


def leer_registros() -> pd.DataFrame:
    """
    Lee la hoja forzando tipos 'object' en columnas de texto. Si una columna
    está vacía en Sheets, pandas la inferiría como float64 y rompería al
    asignarle strings (ej. timestamps).
    """
    try:
        df = conn.read(worksheet=WORKSHEET_NAME, ttl=0)
        df = df.dropna(how="all")
        for col in COLUMNAS:
            if col not in df.columns:
                df[col] = ""
        df = df[COLUMNAS].copy()
        for col in COLS_TEXTO:
            df[col] = df[col].astype(object).where(df[col].notna(), "")
        return df
    except Exception:
        return pd.DataFrame({c: pd.Series(dtype=object) for c in COLUMNAS})


def escribir_registros(df: pd.DataFrame) -> None:
    conn.update(worksheet=WORKSHEET_NAME, data=df)


def calcular_horas(ts_in: datetime, ts_out: datetime) -> float:
    return round((ts_out - ts_in).total_seconds() / 3600, 2)


def buscar_turno_abierto_idx(df: pd.DataFrame, nombre: str) -> int | None:
    """Devuelve el índice del turno abierto del empleado, o None."""
    if df.empty:
        return None
    mask = (df["Nombre"] == nombre) & (df["Estado"] == "Abierto")
    idxs = df.index[mask].tolist()
    return idxs[0] if idxs else None


# ---------------------------------------------------------------------------
# Lógica de marcado normal
# ---------------------------------------------------------------------------
def marcar_entrada(nombre: str) -> None:
    df = leer_registros()
    idx_abierto = buscar_turno_abierto_idx(df, nombre)

    if idx_abierto is not None:
        ts_prev = pd.to_datetime(df.loc[idx_abierto, "Timestamp Entrada"])
        horas_abiertas = (datetime.now() - ts_prev).total_seconds() / 3600
        if horas_abiertas > UMBRAL_OLVIDO_H:
            st.error(
                f"⚠️ Parece que **{nombre}** olvidó marcar salida del turno iniciado el "
                f"{ts_prev.strftime('%Y-%m-%d %H:%M')} "
                f"({horas_abiertas:.1f} h abiertas). Ve a **'Corregir turno olvidado'** "
                "abajo para cerrarlo antes de abrir uno nuevo."
            )
        else:
            st.warning(
                f"⚠️ {nombre} ya tiene un turno abierto desde "
                f"{ts_prev.strftime('%Y-%m-%d %H:%M')}. Marca la salida primero."
            )
        return

    ahora = datetime.now()
    nueva = pd.DataFrame([{
        "Nombre": nombre,
        "Area": AREA_DE.get(nombre, ""),
        "Fecha de Turno": ahora.strftime("%Y-%m-%d"),
        "Timestamp Entrada": ahora.strftime(TS_FMT),
        "Timestamp Salida": "",
        "Horas Trabajadas": "",
        "Estado": "Abierto",
        "Observaciones": "",
    }])
    escribir_registros(pd.concat([df, nueva], ignore_index=True))
    st.success(f"✅ Entrada registrada para **{nombre}** a las {ahora.strftime('%H:%M:%S')}")


def _guardar_salida(df: pd.DataFrame, idx: int, ts_salida: datetime,
                    horas: float, observacion: str) -> None:
    df.loc[idx, "Timestamp Salida"] = ts_salida.strftime(TS_FMT)
    df.loc[idx, "Horas Trabajadas"] = horas
    df.loc[idx, "Estado"] = "Completo"
    df.loc[idx, "Observaciones"] = observacion
    escribir_registros(df)


def marcar_salida(nombre: str) -> None:
    df = leer_registros()
    idx = buscar_turno_abierto_idx(df, nombre)

    if idx is None:
        st.error(
            f"❌ No se encontró un turno abierto para **{nombre}**. "
            "Si olvidaron marcar entrada, usa **'Corregir turno olvidado'** abajo."
        )
        return

    ahora = datetime.now()
    ts_entrada = pd.to_datetime(df.loc[idx, "Timestamp Entrada"])
    horas = calcular_horas(ts_entrada, ahora)

    # Si excede el umbral, diferir guardado y pedir justificación en otro render.
    if horas > UMBRAL_HORAS_EXTRA:
        st.session_state["salida_pendiente"] = {
            "nombre": nombre,
            "ts_entrada_str": df.loc[idx, "Timestamp Entrada"],
            "ts_salida_str": ahora.strftime(TS_FMT),
            "horas": horas,
        }
        return

    _guardar_salida(df, idx, ahora, horas, "")
    st.success(
        f"✅ Salida registrada para **{nombre}**. "
        f"Entrada: {ts_entrada.strftime('%Y-%m-%d %H:%M')} → "
        f"Salida: {ahora.strftime('%Y-%m-%d %H:%M')} = **{horas} h**"
    )


def render_formulario_justificacion() -> None:
    """Formulario que aparece cuando una salida excede UMBRAL_HORAS_EXTRA."""
    if "salida_pendiente" not in st.session_state:
        return

    pend = st.session_state["salida_pendiente"]
    st.warning(
        f"⏱️ **{pend['nombre']}** trabajó **{pend['horas']} h** "
        f"(excede {UMBRAL_HORAS_EXTRA} h). "
        "Debes ingresar una justificación válida para guardar la salida."
    )
    justif = st.text_area(
        f"Justificación obligatoria (mínimo {MIN_JUSTIF_CHARS} caracteres)",
        key="justif_horas_extra",
        height=100,
        placeholder="Ej: Cierre urgente de inventario solicitado por el supervisor Juan Gómez...",
    )

    c1, c2 = st.columns(2)
    with c1:
        confirmar = st.button("💾 Confirmar salida", type="primary", use_container_width=True)
    with c2:
        cancelar = st.button("✖️ Cancelar", use_container_width=True)

    if cancelar:
        del st.session_state["salida_pendiente"]
        st.rerun()

    if confirmar:
        if len(justif.strip()) < MIN_JUSTIF_CHARS:
            st.error(
                f"La justificación debe tener al menos {MIN_JUSTIF_CHARS} caracteres. "
                "Describe el motivo concreto del exceso."
            )
            return

        df = leer_registros()
        mask = (df["Nombre"] == pend["nombre"]) & (df["Timestamp Entrada"] == pend["ts_entrada_str"])
        idxs = df.index[mask].tolist()
        if not idxs:
            st.error("No se encontró el turno a cerrar. Recarga la página.")
            return

        idx = idxs[0]
        ts_salida = datetime.strptime(pend["ts_salida_str"], TS_FMT)
        obs = f"Horas extra justificadas: {justif.strip()}"
        _guardar_salida(df, idx, ts_salida, pend["horas"], obs)

        del st.session_state["salida_pendiente"]
        st.success(f"✅ Salida registrada con justificación. Horas: **{pend['horas']}**")
        st.rerun()


# ---------------------------------------------------------------------------
# UI principal — usuario ya autenticado
# ---------------------------------------------------------------------------
usuario = st.session_state["usuario"]
area_usuario = st.session_state["area"]

st.title("⏰ Marcador de Horas")

ch1, ch2 = st.columns([3, 1])
with ch1:
    st.markdown(f"### 👤 {usuario}  \n🏢 **{area_usuario}**")
with ch2:
    if st.button("Cerrar sesión", use_container_width=True):
        for k in ("auth_ok", "usuario", "area", "salida_pendiente"):
            st.session_state.pop(k, None)
        st.rerun()

st.divider()

col1, col2 = st.columns(2)
with col1:
    if st.button("🟢 Marcar Entrada", use_container_width=True, type="primary"):
        marcar_entrada(usuario)
with col2:
    if st.button("🔴 Marcar Salida", use_container_width=True):
        marcar_salida(usuario)

# Formulario de justificación (se muestra solo si hay salida pendiente por horas extra)
render_formulario_justificacion()


# ---------------------------------------------------------------------------
# Sección de corrección de olvidos — SOLO supervisores
# ---------------------------------------------------------------------------
if area_usuario != "SUPERVISORES":
    st.stop()

st.divider()
with st.expander("🛠️ Corregir turno olvidado / Registro manual (solo supervisores)"):
    st.caption(
        "Úsalo cuando un empleado olvidó marcar entrada, salida o ambas. "
        "Toda corrección queda registrada en 'Observaciones' para auditoría."
    )

    ca_corr, ce_corr = st.columns(2)
    with ca_corr:
        area_corr = st.selectbox("Área", AREAS, key="area_corr")
    with ce_corr:
        emp_corr = st.selectbox("Empleado a corregir", EMPLEADOS_POR_AREA[area_corr], key="emp_corr")

    modo = st.radio(
        "¿Qué quieres hacer?",
        [
            "Cerrar un turno abierto (olvido de SALIDA)",
            "Crear registro histórico completo (olvido de ENTRADA y/o SALIDA)",
        ],
        key="modo_corr",
    )

    df_actual = leer_registros()
    ahora_min = datetime.now().replace(second=0, microsecond=0).time()

    # -------- Modo 1: cerrar turno abierto --------
    if modo.startswith("Cerrar"):
        df_abiertos = df_actual[
            (df_actual["Nombre"] == emp_corr) & (df_actual["Estado"] == "Abierto")
        ]
        if df_abiertos.empty:
            st.info(f"{emp_corr} no tiene turnos abiertos.")
        else:
            opciones = {
                f"Entrada {row['Timestamp Entrada']} (turno {row['Fecha de Turno']})": idx
                for idx, row in df_abiertos.iterrows()
            }
            elegido = st.selectbox("Turno abierto a cerrar", list(opciones.keys()), key="turno_sel")
            idx_obj = opciones[elegido]

            c1, c2 = st.columns(2)
            with c1:
                f_sal = st.date_input("Fecha de salida", value=date.today(), key="f_sal_close")
            with c2:
                h_sal = st.time_input("Hora de salida", value=ahora_min, key="h_sal_close")

            st.markdown("**Observación** (el prefijo *Registro manual:* se añade automáticamente)")
            cp, cd = st.columns([1, 3])
            with cp:
                st.text_input(
                    "Prefijo",
                    value="Registro manual:",
                    disabled=True,
                    key="pref_close",
                    label_visibility="collapsed",
                )
            with cd:
                obs_det = st.text_input(
                    "Detalle",
                    key="obs_close_det",
                    placeholder="Describe el motivo del cierre manual...",
                    label_visibility="collapsed",
                )

            if st.button("💾 Cerrar turno", key="btn_close"):
                ts_sal = datetime.combine(f_sal, h_sal)
                ts_ent = pd.to_datetime(df_actual.loc[idx_obj, "Timestamp Entrada"])
                det = obs_det.strip()
                if ts_sal <= ts_ent:
                    st.error("La salida debe ser posterior a la entrada.")
                elif not det:
                    st.error("⚠️ Ingresa un detalle válido en la observación (no puede quedar vacío).")
                else:
                    horas = calcular_horas(ts_ent, ts_sal)
                    if horas > UMBRAL_HORAS_EXTRA and len(det) < MIN_JUSTIF_CHARS:
                        st.error(
                            f"Las {horas} h exceden {UMBRAL_HORAS_EXTRA} h. "
                            f"El detalle debe tener al menos {MIN_JUSTIF_CHARS} caracteres."
                        )
                    else:
                        _guardar_salida(df_actual, idx_obj, ts_sal, horas, f"Registro manual: {det}")
                        st.success(f"✅ Turno cerrado. Horas trabajadas: {horas}")
                        st.rerun()

    # -------- Modo 2: registro histórico completo --------
    else:
        st.caption("Ambas marcas se ingresan manualmente. Úsalo solo para turnos ya pasados.")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Entrada**")
            f_ent = st.date_input("Fecha", value=date.today(), key="f_ent_m")
            h_ent = st.time_input("Hora", value=time(7, 0), key="h_ent_m")
        with c2:
            st.markdown("**Salida**")
            f_sal = st.date_input("Fecha", value=date.today(), key="f_sal_m")
            h_sal = st.time_input("Hora", value=time(17, 0), key="h_sal_m")

        st.markdown("**Observación** (el prefijo *Registro manual:* se añade automáticamente)")
        cp2, cd2 = st.columns([1, 3])
        with cp2:
            st.text_input(
                "Prefijo",
                value="Registro manual:",
                disabled=True,
                key="pref_m",
                label_visibility="collapsed",
            )
        with cd2:
            obs_det = st.text_input(
                "Detalle",
                key="obs_m_det",
                placeholder="Describe por qué se ingresa manualmente...",
                label_visibility="collapsed",
            )

        if st.button("💾 Crear registro", key="btn_m"):
            ts_in = datetime.combine(f_ent, h_ent)
            ts_out = datetime.combine(f_sal, h_sal)
            det = obs_det.strip()

            if ts_out <= ts_in:
                st.error("La salida debe ser posterior a la entrada.")
            elif not det:
                st.error("⚠️ Ingresa un detalle válido en la observación (no puede quedar vacío).")
            elif buscar_turno_abierto_idx(df_actual, emp_corr) is not None:
                st.error(
                    f"{emp_corr} tiene un turno abierto. Ciérralo primero en el modo anterior."
                )
            else:
                horas = calcular_horas(ts_in, ts_out)
                if horas > UMBRAL_HORAS_EXTRA and len(det) < MIN_JUSTIF_CHARS:
                    st.error(
                        f"Las {horas} h exceden {UMBRAL_HORAS_EXTRA} h. "
                        f"El detalle debe tener al menos {MIN_JUSTIF_CHARS} caracteres."
                    )
                else:
                    nueva = pd.DataFrame([{
                        "Nombre": emp_corr,
                        "Area": AREA_DE.get(emp_corr, ""),
                        "Fecha de Turno": ts_in.strftime("%Y-%m-%d"),
                        "Timestamp Entrada": ts_in.strftime(TS_FMT),
                        "Timestamp Salida": ts_out.strftime(TS_FMT),
                        "Horas Trabajadas": horas,
                        "Estado": "Completo",
                        "Observaciones": f"Registro manual: {det}",
                    }])
                    escribir_registros(pd.concat([df_actual, nueva], ignore_index=True))
                    st.success(f"✅ Registro creado. Horas trabajadas: {horas}")
                    st.rerun()
