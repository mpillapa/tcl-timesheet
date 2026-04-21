"""
Marcador de Entrada y Salida - Horas Extra
==========================================
App Streamlit con persistencia en Google Sheets via st.connection.
Esquema: UNA FILA POR TURNO (entrada y salida en la misma fila).
Soporta turnos nocturnos usando Timestamps completos (YYYY-MM-DD HH:MM:SS).
Incluye deteccion de olvidos y seccion de correccion manual.
"""

import streamlit as st

st.set_page_config(page_title="Marcador de Horas", page_icon="⏱️", layout="centered")

from auth import check_access
from vista_colaborador import vista_colaborador
from vista_super_admin import vista_super_admin

def main():
    check_access()

    if st.session_state.get("rol") == "super_admin":
        vista_super_admin()
    else:
        vista_colaborador()

if __name__ == "__main__":
    main()