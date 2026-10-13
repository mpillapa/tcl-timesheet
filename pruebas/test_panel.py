"""El panel de administración ejecutado de verdad, sin navegador.

Usa AppTest de Streamlit contra la base de pruebas, con administradores y
padrón ficticios inyectados como secrets. Comprueba que la vista arranca sin
errores con el esquema nuevo y que el flujo de aprobación funciona desde la
pantalla, no solo desde la capa de datos.
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
        "fherrera": {"password": "x", "nombre": "Admin Prueba A", "rol": "Supervisor", "puede_aprobar": True},
        "mpillapa": {"password": "x", "nombre": "Admin Prueba B", "rol": "Jefatura", "puede_aprobar": True,
                    "gestiona_periodos": True, "ver_auditoria": True},
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
def recorte_pendiente(datos, base_b):
    tid = insertar_turno(base_b, entrada="2026-09-10 08:00:00", salida="2026-09-10 19:30:00")
    with base_b.cursor("turnero_app") as cur:
        cur.execute("select id from solicitar_correccion(%s, '2026-09-10 08:00', '2026-09-10 17:00', null, %s, 'fherrera')",
                    (tid, MOTIVO))
        return cur.fetchone()[0]


def _textos(elementos):
    return " ".join(str(e.value) for e in elementos)


def test_el_aprobador_ve_el_ajuste_y_lo_aprueba(recorte_pendiente, base_b):
    at = _app("mpillapa").run()
    assert not at.exception, at.exception
    assert "esperando tu aprobación" in _textos(at.warning)
    assert "Ajustes y auditoría" in [t.label for t in at.tabs]

    aprobar = [b for b in at.button if b.label == "Aprobar"]
    assert len(aprobar) == 1
    aprobar[0].click().run()
    assert not at.exception, at.exception
    assert base_b.uno("select estado, revisado_por from ajustes_turno where id = %s",
                      (recorte_pendiente,)) == ("aplicado", "mpillapa")


def test_quien_solicito_no_ve_boton_para_aprobarse(recorte_pendiente):
    at = _app("fherrera").run()
    assert not at.exception, at.exception
    assert not [b for b in at.button if b.label == "Aprobar"]
    assert "solicitud(es) tuya(s)" in _textos(at.info)


def test_la_auditoria_muestra_el_horario_cambiado(datos, base_b):
    tid = insertar_turno(base_b, entrada="2026-09-10 08:00:00", salida="2026-09-10 19:30:00")
    with base_b.cursor("turnero_app") as cur:
        cur.execute("select id from solicitar_correccion(%s, '2026-09-10 08:00', '2026-09-10 17:00', null, %s, 'fherrera')",
                    (tid, MOTIVO))
        cur.execute("select aprobar_ajuste(%s, 'mpillapa', '')", (cur.fetchone()[0],))

    at = _app("mpillapa")
    at.run()
    # La auditoría filtra desde hace 60 días; el turno de prueba es de
    # septiembre de 2026, así que se amplía el rango.
    at.date_input(key="aud_desde").set_value(date(2026, 1, 1)).run()
    assert not at.exception, at.exception
    metricas = {m.label: m.value for m in at.metric}
    assert metricas["Turnos con horario cambiado"] == "1"
    assert metricas["Horas extra quitadas"] == "2.50 h"
