"""
Control de acceso:
  1) Gate de red: device_key URL  ->  IP oficina (vía ipify)  ->  master_passsword.
  2) Selector de rol: colaborador (default) | super_admin.
  3) Login según rol:
       - Colaborador -> PIN personal de 4 dígitos.
       - Super Admin -> usuario + contraseña contra secrets.super_admins.
"""

import json

import streamlit as st
from streamlit_javascript import st_javascript

from core.employees import PIN_A_EMPLEADO, AREA_DE
from core.ui_theme import inject_kiosk_css, inject_login_css


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


# --- Equipo de confianza (token en localStorage) ---------------------------
# Una laptop de confianza guarda un token y deja de pedir la clave maestra al
# ingresar desde fuera de la oficina. No salta el login. Para revocar todos los
# equipos, cambiar 'trusted_device_secret' en secrets. Es por navegador porque
# desde el navegador no se puede leer el hardware (MAC o serial).
def _leer_token_dispositivo():
    """None mientras el navegador responde, "" si no hay token, o el token.

    El centinela '__NONE__' distingue 'sin token' de 'todavía cargando', que de
    otro modo llegarían los dos como None."""
    try:
        res = st_javascript(
            "await (async () => { const v = window.localStorage.getItem('tcl_device_token');"
            " return (v === null) ? '__NONE__' : v; })()",
            key="device_token_get",
        )
    except Exception:
        return ""

    if res in (0, None):
        return None
    if res == "__NONE__" or not isinstance(res, str) or not res.strip():
        return ""
    return res.strip()


def _guardar_token_dispositivo(token: str) -> None:
    st_javascript(
        "await (async () => { window.localStorage.setItem('tcl_device_token', "
        f"{json.dumps(token)}); return 'OK'; }})()",
        key="device_token_set",
    )


def _borrar_token_dispositivo() -> None:
    st_javascript(
        "await (async () => { window.localStorage.removeItem('tcl_device_token');"
        " return 'OK'; })()",
        key="device_token_del",
    )


def confiar_equipo_ui() -> None:
    """Controles para marcar o desmarcar este navegador como equipo de
    confianza. Pensado para las laptops de los super admins."""
    try:
        secret_conf = str(st.secrets["auth"].get("trusted_device_secret", ""))
    except (KeyError, FileNotFoundError):
        secret_conf = ""

    if not secret_conf:
        st.caption(
            "Para habilitar equipos de confianza, define 'trusted_device_secret' "
            "en la sección [auth] de secrets."
        )
        return

    st.caption(
        "Marca esta laptop como de confianza para no pedir la clave maestra al "
        "ingresar desde fuera de la oficina. Afecta solo a este navegador. "
        "Igual deberás ingresar tu usuario y contraseña. Si pierdes el equipo, "
        "cambia 'trusted_device_secret' en secrets para revocar todos los equipos."
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Confiar en este equipo", use_container_width=True, key="trust_device_btn"):
            _guardar_token_dispositivo(secret_conf)
            st.success("Listo. Este equipo no volverá a pedir la clave maestra.")
    with c2:
        if st.button("Quitar confianza", use_container_width=True, key="untrust_device_btn"):
            _borrar_token_dispositivo()
            st.success("Se quitó la confianza de este equipo.")


def logout() -> None:
    for k in (
        "auth_ok", "gate_passed", "gate_via", "rol",
        "usuario", "area", "admin_user", "admin_rol",
        "salida_pendiente",
        "auto_logout_started_at",
        "_barrido_olvidados_hecho",
        "_archivado_auto_hecho",
        "_archivado_backlog",
    ):
        st.session_state.pop(k, None)

    # Por prefijo: si quedaran, el siguiente admin que entre en este navegador
    # heredaría la selección del anterior, áreas que no le competen incluidas.
    for k in [
        k for k in list(st.session_state.keys())
        if str(k).startswith(("filtro_", "_persist_filtro", "_universo_filtro"))
    ]:
        st.session_state.pop(k, None)


# --- Capa 1: gate de red ---------------------------------------------------
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
        st.title("Verificando ubicación…")
        st.caption("Un momento, confirmando que estás en la red autorizada.")
        st.stop()

    if ip_browser and ip_browser in allowed_ips:
        st.session_state["gate_passed"] = True
        st.session_state["gate_via"] = f"IP oficina ({ip_browser})"
        return

    # Solo se evalúa cuando la IP no coincide, porque los equipos de oficina ya
    # pasaron arriba y no hay que penalizar el flujo común.
    secret_conf = str(auth_cfg.get("trusted_device_secret", ""))
    if secret_conf:
        token_disp = _leer_token_dispositivo()
        if token_disp is None:
            st.title("Verificando equipo…")
            st.caption("Un momento, comprobando si este equipo es de confianza.")
            st.stop()
        if token_disp and token_disp == secret_conf:
            st.session_state["gate_passed"] = True
            st.session_state["gate_via"] = "dispositivo de confianza"
            return

    inject_kiosk_css()
    st.title("Acceso al marcador")
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

    with st.expander("Detalles técnicos"):
        st.code(
            f"IP pública (vía navegador): {ip_browser or '(no se pudo obtener))'}\n"
            f"IPs autorizadas: {allowed_ips}"
        )
    st.stop()


# --- Capa 3: login por rol -------------------------------------------------
def _capa3_login_colaborador() -> None:
    inject_login_css()
    c1, c2 = st.columns([4, 1])
    with c1:
        st.title("Marcador de Horas")
    with c2:
        if st.button("Admin", key="go_admin", use_container_width=True):
            st.session_state["rol"] = "super_admin"
            st.rerun()

    st.caption("Ingresa tu PIN personal (últimos 4 dígitos de tu cédula)")

    with st.form("login_colaborador"):
        pin = st.text_input("PIN", type="password", max_chars=4, placeholder="••••")
        submitted = st.form_submit_button("Ingresar", type="primary", use_container_width=True)
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

    inject_kiosk_css()
    st.title("Administrador")
    if st.button("Volver al Marcador", key="back_colab"):
        st.session_state["rol"] = "colaborador"
        st.rerun()
    st.caption("Ingresa tus credenciales de administrador")

    with st.form("login_admin"):
        user = st.text_input("Usuario").strip()
        pwd = st.text_input("Contraseña", type="password")
        submitted = st.form_submit_button("Ingresar", type="primary", use_container_width=True)

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
