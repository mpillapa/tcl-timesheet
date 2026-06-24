import streamlit as st
import time

from core.auth import logout
from core.config import UMBRAL_OLVIDO_H
from core.marcado import (
    AUTO_LOGOUT_SECONDS,
    marcar_entrada,
    marcar_salida,
    render_formulario_justificacion,
)


@st.dialog("Turno enviado a revisión")
def _dialogo_revision(info: dict) -> None:
    st.warning(
        f"El turno abierto desde {info['entrada']} lleva "
        f"{info['horas']:.1f} h (más de {UMBRAL_OLVIDO_H} h)."
    )
    st.write(
        "Por superar las horas permitidas se envió a **revisión de tu supervisor** "
        "y **no se registró la salida**. "
        "Puedes marcar tu **entrada** normalmente para iniciar tu jornada de hoy."
    )
    if st.button("Entendido", type="primary", use_container_width=True):
        st.session_state.pop("aviso_revision", None)
        st.rerun()


def _procesar_auto_logout() -> None:
    started_at = st.session_state.get("auto_logout_started_at")
    if started_at is None:
        return

    elapsed = time.time() - float(started_at)
    remaining = AUTO_LOGOUT_SECONDS - elapsed
    if remaining <= 0:
        logout()
        st.rerun()

    st.info(f"Marcación registrada. Cerrando sesión automáticamente en {int(remaining) + 1} s...")
    time.sleep(1)
    st.rerun()

def vista_colaborador() -> None:
    usuario = st.session_state["usuario"]
    area_usuario = st.session_state["area"]

    st.title("Marcador de Horas")

    ch1, ch2 = st.columns([3, 1])
    with ch1:
        st.markdown(f"### {usuario}  \n**{area_usuario}**")
    with ch2:
        if st.button("Cerrar sesión", use_container_width=True):
            logout()
            st.rerun()

    st.divider()

    # El doble-click se controla dentro de marcar_entrada/marcar_salida (debounce
    # por empleado), así que los botones no necesitan lógica de bloqueo aquí.
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Marcar Entrada", use_container_width=True, type="primary"):
            marcar_entrada(usuario)
    with col2:
        if st.button("Marcar Salida", use_container_width=True):
            marcar_salida(usuario)

    render_formulario_justificacion()

    if "aviso_revision" in st.session_state:
        _dialogo_revision(st.session_state["aviso_revision"])

    _procesar_auto_logout()
