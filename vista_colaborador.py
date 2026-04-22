import streamlit as st
import time

from auth import logout
from marcado import (
    AUTO_LOGOUT_SECONDS,
    marcar_entrada,
    marcar_salida,
    render_formulario_justificacion,
)


def _procesar_auto_logout() -> None:
    started_at = st.session_state.get("auto_logout_started_at")
    if started_at is None:
        return

    elapsed = time.time() - float(started_at)
    remaining = AUTO_LOGOUT_SECONDS - elapsed
    if remaining <= 0:
        logout()
        st.rerun()

    st.info(f"✅ Marcación registrada. Cerrando sesión automáticamente en {int(remaining) + 1} s...")
    time.sleep(1)
    st.rerun()

def vista_colaborador() -> None:
    usuario = st.session_state["usuario"]
    area_usuario = st.session_state["area"]

    st.title("⏱️ Marcador de Horas")

    ch1, ch2 = st.columns([3, 1])
    with ch1:
        st.markdown(f"### 👤 {usuario}  \n🟢 **{area_usuario}**")
    with ch2:
        if st.button("Cerrar sesión", use_container_width=True):
            logout()
            st.rerun()

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🟢 Marcar Entrada", use_container_width=True, type="primary"):
            marcar_entrada(usuario)
    with col2:
        if st.button("🔴 Marcar Salida", use_container_width=True):
            marcar_salida(usuario)

    render_formulario_justificacion()
    _procesar_auto_logout()
