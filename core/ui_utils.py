"""Utilidades de interfaz compartidas por las vistas."""

import time as _time

import streamlit as st

# Ventana (segundos) durante la cual un segundo click sobre el mismo botón se
# ignora. Cubre el doble-click accidental (ocurre en <1 s) sin estorbar una
# segunda operación legítima, que en la práctica toma bastante más.
_VENTANA_DOBLE_CLICK = 4.0


def bloquear_doble_click(token: str, ventana_seg: float = _VENTANA_DOBLE_CLICK) -> bool:
    """Anti doble-click para botones que escriben en la base de datos.

    Devuelve True si ESTE click debe ignorarse porque ya se procesó otro con el
    mismo `token` dentro de la ventana. Llamar justo al entrar al handler del
    botón, antes de cualquier escritura::

        if st.button("Confirmar", key="x"):
            if bloquear_doble_click("x"):
                st.rerun()
            else:
                # ... escritura a la BD ...

    El estado vive en st.session_state, que persiste entre reruns de la misma
    sesión, así que protege aunque Streamlit reinicie el script en cada click.
    """
    ahora = _time.time()
    clave = f"_dbl_{token}"
    ultimo = st.session_state.get(clave)
    if ultimo is not None and (ahora - ultimo) < ventana_seg:
        return True
    st.session_state[clave] = ahora
    return False


def set_flash(mensaje: str, icono: str = "✅") -> None:
    """Guarda un mensaje de confirmación para mostrarlo tras el próximo rerun.

    Útil cuando una acción escribe en la BD y luego hace st.rerun(): el toast
    se pierde con el rerun, así que se difiere el mensaje y se muestra en el
    siguiente render con mostrar_flash()."""
    st.session_state["_flash_msg"] = {"mensaje": mensaje, "icono": icono}


def mostrar_flash() -> None:
    """Muestra y consume el mensaje flash pendiente, si lo hay.

    Combina un banner persistente (visible hasta la próxima interacción) con un
    toast flotante, para que la confirmación no pase desapercibida."""
    flash = st.session_state.pop("_flash_msg", None)
    if not flash:
        return
    st.success(flash["mensaje"], icon=flash["icono"])
    try:
        st.toast(flash["mensaje"], icon=flash["icono"])
    except Exception:
        pass
