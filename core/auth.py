"""
Control de acceso:
  1) Gate de red: device_key URL  ->  IP oficina (vía ipify)  ->  master_passsword.
  2) Selector de rol: colaborador (default) | super_admin.
  3) Login según rol:
       - Colaborador -> PIN personal de 4 dígitos.
       - Super Admin -> usuario + contraseña contra secrets.super_admins.
"""

import streamlit as st
from streamlit_javascript import st_javascript

from core.employees import PIN_A_EMPLEADO, AREA_DE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _obtener_ip_publica_browser():
    try:
        res = st_javascript(
            "await fetch('https://api.ipify.org?format=json')"
            ".then(r => r.json()).then(d => d.ip).catch(() => 'ERROR')",
            key="client_ip_ipify",
        )
    except Exception:
        return ""

    if res in (0, None):
        return None
    if res == "ERROR" or not isinstance(res, str) or not res.strip():
        return ""
    return res.strip()


def logout() -> None:
    for k in (
        "auth_ok", "gate_passed", "gate_via", "rol",
        "usuario", "area", "admin_user", "admin_rol",
        "salida_pendiente",
        "auto_logout_started_at",
    ):
        st.session_state.pop(k, None)


# ---------------------------------------------------------------------------
# Capa 1: gate de red
# ---------------------------------------------------------------------------
def _capa1_gate() -> None:
    try:
        auth_cfg = dict(st.secrets["auth"])
    except (KeyError, FileNotFoundError):
        auth_cfg = {}

    allowed_ips = list(auth_cfg.get("allowed_ips", []))
    device_keys = list(auth_cfg.get("device_keys", []))
    master_password = str(auth_cfg.get("master_password", ""))

    try:
        device_key_url = str(st.query_params.get("device_key", "") or "")
    except Exception:
        device_key_url = ""

    if device_key_url and device_key_url in device_keys:
        st.session_state["gate_passed"] = True
        st.session_state["gate_via"] = "device_key"
        return

    ip_browser = _obtener_ip_publica_browser()
    if ip_browser is None:
        st.title("⏳ Verificando ubicación…")
        st.caption("Un momento, confirmando que estás en la red autorizada.")
        st.stop()

    if ip_browser and ip_browser in allowed_ips:
        st.session_state["gate_passed"] = True
        st.session_state["gate_via"] = f"IP oficina ({ip_browser})"
        return

    st.title("🔒 Acceso al marcador")
    if ip_browser:
        st.caption(
            f"Estás fuera de la red autorizada (tu IP: {ip_browser}). "
            "Si eres supervisor, jefe o desarrollador, ingresa la contraseña maestra."
        )
    else:
        st.caption("No se pudo verificar tu IP. Ingresa la contraseña maestra para continuar.")

    with st.form("master_pwd_form"):
        pwd = st.text_input("Contraseña maestra", type="password")
        submitted = st.form_submit_button("Continuar", type="primary", use_container_width=True)
        if submitted:
            if not master_password:
                st.error("Contraseña maestra no configurada en secrets.")
            elif pwd == master_password:
                st.session_state["gate_passed"] = True
                st.session_state["gate_via"] = "master_password"
                st.rerun()
            else:
                st.error("Contraseña maestra incorrecta.")

    with st.expander("🔍 Detalles técnicos"):
        st.code(
            f"IP pública (vía navegador): {ip_browser or '(no se pudo obtener))'}\n"
            f"IPs autorizadas: {allowed_ips}"
        )
    st.stop()


# ---------------------------------------------------------------------------
# Control Login
# ---------------------------------------------------------------------------
def _capa3_login_colaborador() -> None:
    c1, c2 = st.columns([4, 1])
    with c1:
        st.title("⏱️ Marcador de Horas")
    with c2:
        if st.button("⚙️ Admin", key="go_admin", use_container_width=True):
            st.session_state["rol"] = "super_admin"
            st.rerun()

    st.caption("👷 Ingresa tu PIN personal (últimos 4 dígitos de tu cédula)")

    with st.form("login_colaborador"):
        pin = st.text_input("PIN", type="password", max_chars=4, placeholder="••••")
        submitted = st.form_submit_button("🔑 Ingresar", type="primary", use_container_width=True)
        if submitted:
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


def _capa3_login_super_admin() -> None:
    try:
        super_admins = dict(st.secrets["super_admins"])
    except (KeyError, FileNotFoundError):
        super_admins = {}

    st.title("⏱️ Administrador")
    if st.button("← Volver al Marcador", key="back_colab"):
        st.session_state["rol"] = "colaborador"
        st.rerun()
    st.caption("👔 Ingresa tus credenciales de administrador")

    with st.form("login_admin"):
        user = st.text_input("Usuario").strip()
        pwd = st.text_input("Contraseña", type="password")
        submitted = st.form_submit_button("🔑 Ingresar", type="primary", use_container_width=True)

        if submitted:
            admin = super_admins.get(user)
            if not admin or str(admin.get("password", "")) != pwd:
                st.error("Usuario o contraseña incorrectos.")
            else:
                st.session_state["auth_ok"] = True
                st.session_state["admin_user"] = user
                st.session_state["usuario"] = admin.get("nombre", user)
                st.session_state["admin_rol"] = admin.get("rol", "")
                st.rerun()
    st.stop()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def check_access() -> None:
    if st.session_state.get("auth_ok"):
        return
    if not st.session_state.get("gate_passed"):
        _capa1_gate()

    if "rol" not in st.session_state:
        st.session_state["rol"] = "colaborador"

    if st.session_state["rol"] == "colaborador":
        _capa3_login_colaborador()
    else:
        _capa3_login_super_admin()
