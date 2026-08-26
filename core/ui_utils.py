"""Utilidades de interfaz compartidas por las vistas."""

import time as _time

import streamlit as st

# Segundos durante los que se ignora un segundo clic sobre el mismo botón. El
# doble clic accidental ocurre en menos de 1 s; una operación legítima tarda más.
_VENTANA_DOBLE_CLICK = 4.0


def bloquear_doble_click(token: str, ventana_seg: float = _VENTANA_DOBLE_CLICK) -> bool:
    """Devuelve True si el clic debe ignorarse porque ya se procesó otro con el
    mismo `token` dentro de la ventana. Llamar al entrar al handler del botón,
    antes de cualquier escritura. El estado vive en st.session_state, que
    sobrevive a los reruns de la sesión."""
    ahora = _time.time()
    clave = f"_dbl_{token}"
    ultimo = st.session_state.get(clave)
    if ultimo is not None and (ahora - ultimo) < ventana_seg:
        return True
    st.session_state[clave] = ahora
    return False


def set_flash(mensaje: str, icono: str = "") -> None:
    """Difiere un mensaje de confirmación hasta el próximo render, porque un
    toast lanzado antes de st.rerun() se pierde. Lo muestra mostrar_flash()."""
    st.session_state["_flash_msg"] = {"mensaje": mensaje, "icono": icono}


def mostrar_flash() -> None:
    """Muestra y consume el mensaje flash pendiente. Combina banner y toast para
    que la confirmación no pase desapercibida."""
    flash = st.session_state.pop("_flash_msg", None)
    if not flash:
        return
    icono = flash.get("icono") or None
    st.success(flash["mensaje"], icon=icono)
    try:
        st.toast(flash["mensaje"], icon=icono)
    except Exception:
        pass
