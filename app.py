"""Punto de entrada de la app. Elige la vista según el rol autenticado."""

import streamlit as st

st.set_page_config(page_title="Marcador de Horas", page_icon="⏱️", layout="centered")

from core.auth import check_access
from views.colaborador import vista_colaborador
from views.super_admin import vista_super_admin

def main():
    check_access()

    if st.session_state.get("rol") == "super_admin":
        vista_super_admin()
    else:
        vista_colaborador()

if __name__ == "__main__":
    main()