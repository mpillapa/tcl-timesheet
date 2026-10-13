"""Bitácora de solo inserción y permisos del rol de la aplicación (V003, V008, V010)."""

import psycopg2
import pytest

from conftest import insertar_turno


def _con_un_cambio_en_bitacora(base):
    tid = insertar_turno(base, entrada="2026-09-10 08:00:00")
    with base.cursor("turnero_app", sello=("Prueba Uno", "colaborador")) as cur:
        cur.execute("update turnos set ts_salida = '2026-09-10 17:00', estado = 'Completo' where id = %s", (tid,))
    return tid


@pytest.mark.parametrize("sentencia", [
    "update turnos_auditoria set usuario = 'otro'",
    "delete from turnos_auditoria",
    "truncate turnos_auditoria",
])
def test_la_bitacora_no_se_modifica_ni_siquiera_por_su_duenio(base_a, sentencia):
    _con_un_cambio_en_bitacora(base_a)
    with pytest.raises(psycopg2.Error, match="solo inserción"):
        with base_a.cursor("propietario") as cur:
            cur.execute(sentencia)


@pytest.mark.parametrize("sentencia", [
    "update marcaciones set momento = now()",
    "delete from marcaciones",
])
def test_las_marcaciones_no_se_modifican(base_a, sentencia):
    _con_un_cambio_en_bitacora(base_a)
    with pytest.raises(psycopg2.Error, match="solo inserción"):
        with base_a.cursor("propietario") as cur:
            cur.execute(sentencia)


def test_la_bitacora_guarda_el_motivo_de_la_sesion(base_a):
    tid = insertar_turno(base_a, entrada="2026-09-10 08:00:00", estado="Revision")
    with base_a.cursor("turnero_app", sello=("admin_prueba", "admin", "cierre manual de prueba")) as cur:
        cur.execute("update turnos set ts_salida = '2026-09-10 17:00', estado = 'Completo' where id = %s", (tid,))
    assert base_a.uno(
        "select usuario, origen, motivo from turnos_auditoria where turno_id = %s", (tid,)
    ) == ("admin_prueba", "admin", "cierre manual de prueba")


def test_la_aplicacion_no_puede_borrar_turnos(base_a):
    tid = insertar_turno(base_a, entrada="2026-09-10 08:00:00")
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        with base_a.cursor("turnero_app") as cur:
            cur.execute("delete from turnos where id = %s", (tid,))


@pytest.mark.parametrize("sentencia", [
    "insert into turnos_auditoria (turno_id, accion, antes) values (1, 'UPDATE', '{}')",
    "update reglas_negocio set valor = 0",
    "insert into periodos_nomina (anio, mes, estado) values (2026, 1, 'cerrado')",
    "update ajustes_turno set estado = 'aplicado'",
    "insert into marcaciones (turno_id, nombre, tipo, momento, origen) values (1, 'x', 'entrada', now(), 'admin')",
    "select * from herramientas.ajena",
    "select _aplicar_ajuste(1, 'x', 'x')",
])
def test_la_aplicacion_no_alcanza_lo_que_no_le_toca(base_a, sentencia):
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        with base_a.cursor("turnero_app") as cur:
            cur.execute(sentencia)


def test_la_aplicacion_lee_con_rls_activo(base_a):
    insertar_turno(base_a, entrada="2026-09-10 08:00:00")
    assert base_a.uno("select count(*) from turnos", usuario="turnero_app")[0] == 1


def test_la_api_rest_no_alcanza_las_funciones_que_escriben(base_a):
    # anon es el rol de la clave pública de Supabase.
    for firma in ["solicitar_correccion(bigint, timestamp, timestamp, text, text, text)",
                  "solicitar_anulacion(bigint, text, text)", "aprobar_ajuste(bigint, text, text)",
                  "cerrar_periodo(int, int, text, text)", "reabrir_periodo(int, int, text, text)"]:
        assert base_a.uno("select has_function_privilege('anon', %s, 'execute')", (firma,))[0] is False


def test_el_rol_trabaja_en_hora_de_ecuador(base_a):
    assert base_a.uno("show timezone", usuario="turnero_app")[0] == "America/Guayaquil"
