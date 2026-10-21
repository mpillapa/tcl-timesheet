"""El panel de administración ejecutado de verdad, sin navegador.

Usa AppTest de Streamlit contra la base de pruebas, con administradores y
padrón ficticios inyectados como secrets. Comprueba que la vista arranca sin
errores con el esquema nuevo y que la pestaña de modificaciones muestra lo
que se corrigió, sin flujo de aprobación.
"""

from datetime import date

import pytest

from streamlit.testing.v1 import AppTest

from conftest import insertar_turno

MOTIVO = "Cierre de vuelo confirmado con operaciones"

# Los dos usuarios tienen acceso total en AREAS_POR_ADMIN de views/super_admin.py;
# uno que no esté ahí no ve ningún área. Las credenciales son ficticias.
SECRETS = {
    "super_admins": {
        "fherrera": {"password": "x", "nombre": "Admin Prueba A", "rol": "Supervisor"},
        "mpillapa": {"password": "x", "nombre": "Admin Prueba B", "rol": "Jefatura", "ver_auditoria": True},
    },
    "empleados": {
        "1111": {"nombre": "Prueba Uno", "area": "BODEGA"},
        "2222": {"nombre": "Prueba Dos", "area": "BODEGA"},
    },
    "backup": {"espejo_sheets": False},
    "auth": {},
}


def _panel():
    import streamlit as st
    usuario = st.session_state.get("_usuario_prueba", "mpillapa")
    st.session_state["auth_ok"] = True
    st.session_state["rol"] = "super_admin"
    st.session_state["admin_user"] = usuario
    st.session_state["usuario"] = usuario
    st.session_state["admin_rol"] = "Prueba"
    from views.super_admin import vista_super_admin
    vista_super_admin()


def _app(usuario):
    at = AppTest.from_function(_panel, default_timeout=60)
    for clave, valor in SECRETS.items():
        at.secrets[clave] = valor
    at.session_state["_usuario_prueba"] = usuario
    return at


@pytest.fixture
def recorte(datos, base_b):
    """Un recorte de 2:30 h hecho por fherrera, que se aplica en el acto."""
    tid = insertar_turno(base_b, entrada="2026-09-10 08:00:00", salida="2026-09-10 19:30:00")
    with base_b.cursor("turnero_app") as cur:
        cur.execute("select id, estado from solicitar_correccion(%s, '2026-09-10 08:00', '2026-09-10 17:00', null, %s, 'fherrera')",
                    (tid, MOTIVO))
        aid, estado = cur.fetchone()
    assert estado == "aplicado"
    return aid


def _textos(elementos):
    return " ".join(str(e.value) for e in elementos)


def test_no_queda_rastro_del_flujo_de_aprobacion(recorte):
    at = _app("mpillapa").run()
    assert not at.exception, at.exception
    assert "aprobación" not in _textos(at.warning)
    assert not [b for b in at.button if b.label in ("Aprobar", "Rechazar")]
    assert "Ajustes y auditoría" not in [t.label for t in at.tabs]


def test_sin_ver_auditoria_no_se_ve_la_pestana(recorte):
    at = _app("fherrera").run()
    assert not at.exception, at.exception
    assert "Modificaciones de turnos" not in [t.label for t in at.tabs]


def test_la_pestana_muestra_la_modificacion_y_el_horario_cambiado(recorte):
    at = _app("mpillapa")
    at.run()
    assert "Modificaciones de turnos" in [t.label for t in at.tabs]
    # Filtra desde hace 60 días; el turno de prueba es de septiembre de 2026,
    # así que se amplía el rango.
    at.date_input(key="aud_desde").set_value(date(2026, 1, 1)).run()
    assert not at.exception, at.exception
    metricas = {m.label: m.value for m in at.metric}
    assert metricas["Modificaciones"] == "1"
    assert metricas["Tiempo quitado"] == "-2:30"
    assert metricas["Turnos con horario cambiado"] == "1"
    assert metricas["Horas extra quitadas"] == "2.50 h"

    tablas = [df.value for df in at.dataframe]
    modificaciones = next(t for t in tablas if "Hecho por" in t.columns)
    fila = modificaciones.iloc[0]
    assert (fila["Funcionario"], fila["Hecho por"], fila["Motivo"]) == ("Prueba Uno", "fherrera", MOTIVO)
    assert (fila["Antes"], fila["Después"], fila["Horas"]) == (
        "2026-09-10 08:00 → 2026-09-10 19:30", "2026-09-10 08:00 → 2026-09-10 17:00", "-2:30")
    assert "Estado" not in modificaciones.columns
