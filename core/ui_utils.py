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
