"""Correcciones y anulaciones como ajustes con motivo y aprobación (V009, V101)."""

import psycopg2
import pytest

from conftest import insertar_turno

MOTIVO = "Cierre de vuelo confirmado con operaciones"


def _turno_largo(base, nombre="Prueba Uno"):
    # 11.50 h, 2.50 h extra.
    return insertar_turno(base, nombre=nombre, entrada="2026-09-10 08:00:00", salida="2026-09-10 19:30:00")


def _corregir(base, tid, entrada, salida, usuario="admin_a", motivo=MOTIVO, obs=None):
    with base.cursor("turnero_app") as cur:
        cur.execute("select id, estado, requiere_aprobacion from solicitar_correccion(%s, %s, %s, %s, %s, %s)",
                    (tid, entrada, salida, obs, motivo, usuario))
        return cur.fetchone()


def test_una_correccion_chica_se_aplica_sola(base_a):
    tid = _turno_largo(base_a)
    _, estado, requiere = _corregir(base_a, tid, "2026-09-10 08:00", "2026-09-10 19:00")
    assert (estado, requiere) == ("aplicado", False)
    assert float(base_a.uno("select horas_extra from turnos where id = %s", (tid,))[0]) == 2.0


def test_un_recorte_grande_espera_a_otra_persona(base_a):
    tid = _turno_largo(base_a)
    aid, estado, requiere = _corregir(base_a, tid, "2026-09-10 08:00", "2026-09-10 17:00")
    assert (estado, requiere) == ("pendiente", True)
    # El turno sigue igual mientras nadie apruebe.
    assert float(base_a.uno("select horas_extra from turnos where id = %s", (tid,))[0]) == 2.5

    with pytest.raises(psycopg2.Error, match="no puede aprobarlo"):
        with base_a.cursor("turnero_app") as cur:
            cur.execute("select aprobar_ajuste(%s, 'ADMIN_A', 'yo mismo')", (aid,))

    with base_a.cursor("turnero_app") as cur:
        cur.execute("select estado, revisado_por from aprobar_ajuste(%s, 'admin_b', 'visto')", (aid,))
        assert cur.fetchone() == ("aplicado", "admin_b")
    assert float(base_a.uno("select horas_extra from turnos where id = %s", (tid,))[0]) == 0.0

    # La bitácora atribuye el cambio a quien lo pidió, con su motivo y el ajuste.
    assert base_a.uno(
        "select usuario, origen, motivo, ajuste_id from turnos_auditoria where turno_id = %s "
        "order by id desc limit 1", (tid,)
    ) == ("admin_a", "admin", MOTIVO, aid)


def test_el_rechazo_deja_el_turno_intacto_y_exige_explicacion(base_a):
    tid = _turno_largo(base_a)
    aid, _, _ = _corregir(base_a, tid, "2026-09-10 08:00", "2026-09-10 17:00")
    with pytest.raises(psycopg2.Error, match="explicar el motivo"):
        with base_a.cursor("turnero_app") as cur:
            cur.execute("select rechazar_ajuste(%s, 'admin_b', 'no')", (aid,))
    with base_a.cursor("turnero_app") as cur:
        cur.execute("select estado from rechazar_ajuste(%s, 'admin_b', 'El vuelo sí salió tarde, consta en el manifiesto')", (aid,))
        assert cur.fetchone()[0] == "rechazado"
    assert float(base_a.uno("select horas_extra from turnos where id = %s", (tid,))[0]) == 2.5


def test_sin_motivo_no_hay_ajuste(base_a):
    tid = _turno_largo(base_a)
    with pytest.raises(psycopg2.Error, match="al menos 10 caracteres"):
        _corregir(base_a, tid, "2026-09-10 08:00", "2026-09-10 19:00", motivo="corrijo")


def test_un_turno_no_tiene_dos_ajustes_esperando(base_a):
    tid = _turno_largo(base_a)
    _corregir(base_a, tid, "2026-09-10 08:00", "2026-09-10 17:00")
    with pytest.raises(psycopg2.Error, match="ya tiene un ajuste esperando"):
        _corregir(base_a, tid, "2026-09-10 08:00", "2026-09-10 17:30")


def test_lo_solicitado_no_se_puede_reescribir(base_a):
    tid = _turno_largo(base_a)
    aid, _, _ = _corregir(base_a, tid, "2026-09-10 08:00", "2026-09-10 17:00")
    with pytest.raises(psycopg2.Error, match="no se puede modificar"):
        with base_a.cursor("propietario") as cur:
            cur.execute("update ajustes_turno set motivo = 'otro motivo distinto' where id = %s", (aid,))


def test_anular_un_turno_con_horas_extra_requiere_aprobacion(base_a):
    tid = _turno_largo(base_a)
    with base_a.cursor("turnero_app") as cur:
        cur.execute("select id, estado from solicitar_anulacion(%s, %s, 'admin_a')", (tid, MOTIVO))
        aid, estado = cur.fetchone()
    assert estado == "pendiente"
    with base_a.cursor("turnero_app") as cur:
        cur.execute("select aprobar_ajuste(%s, 'admin_b', '')", (aid,))
    anulado, por, motivo = base_a.uno("select anulado, anulado_por, motivo_anulacion from turnos where id = %s", (tid,))
    assert (anulado, por, motivo) == (True, "admin_a", MOTIVO)


def test_anular_un_turno_sin_extra_se_aplica_solo(base_a):
    tid = insertar_turno(base_a, entrada="2026-09-10 08:00:00", salida="2026-09-10 17:00:00")
    with base_a.cursor("turnero_app") as cur:
        cur.execute("select estado from solicitar_anulacion(%s, %s, 'admin_a')", (tid, MOTIVO))
        assert cur.fetchone()[0] == "aplicado"


def test_un_turno_anulado_no_se_edita_ni_se_reactiva(base_a):
    tid = insertar_turno(base_a, entrada="2026-09-10 08:00:00", salida="2026-09-10 17:00:00")
    with base_a.cursor("turnero_app") as cur:
        cur.execute("select solicitar_anulacion(%s, %s, 'admin_a')", (tid, MOTIVO))
    for cambio in ["anulado = false, anulado_en = null, anulado_por = null, motivo_anulacion = null",
                   "observaciones = 'otra cosa'"]:
        with pytest.raises(psycopg2.Error, match="está anulado"):
            with base_a.cursor("propietario") as cur:
                cur.execute(f"update turnos set {cambio} where id = %s", (tid,))
    # El archivado de fin de mes sí pasa.
    with base_a.cursor("propietario") as cur:
        cur.execute("update turnos set archivado = true where id = %s", (tid,))


def test_fase_a_todavia_admite_la_correccion_directa(base_a):
    # La versión de la aplicación en producción corrige con UPDATE directo.
    tid = _turno_largo(base_a)
    with base_a.cursor("turnero_app", sello=("admin_a", "admin")) as cur:
        cur.execute("update turnos set ts_salida = '2026-09-10 17:00' where id = %s", (tid,))


def test_fase_b_solo_corrige_por_ajuste(base_b):
    tid = _turno_largo(base_b)
    with pytest.raises(psycopg2.Error, match="mediante un ajuste"):
        with base_b.cursor("turnero_app", sello=("admin_a", "admin", MOTIVO)) as cur:
            cur.execute("update turnos set ts_salida = '2026-09-10 17:00' where id = %s", (tid,))
    # Tampoco por fuera de la aplicación.
    with pytest.raises(psycopg2.Error, match="mediante un ajuste"):
        with base_b.cursor("propietario") as cur:
            cur.execute("update turnos set ts_salida = '2026-09-10 17:00' where id = %s", (tid,))
    _corregir(base_b, tid, "2026-09-10 08:00", "2026-09-10 19:00")


def test_fase_b_exige_motivo_a_toda_escritura_de_admin(base_b):
    with pytest.raises(psycopg2.Error, match="falta el motivo"):
        insertar_turno(base_b, entrada="2026-09-10 08:00:00", origen="admin", autor="admin_a")
    insertar_turno(base_b, entrada="2026-09-10 08:00:00", origen="admin", autor="admin_a",
                   motivo="Olvidó marcar entrada, confirmado por el jefe")


def test_fase_b_permite_rehacer_un_turno_anulado_con_la_misma_entrada(base_b):
    tid = insertar_turno(base_b, entrada="2026-09-10 08:00:00", salida="2026-09-10 17:00:00")
    with base_b.cursor("turnero_app") as cur:
        cur.execute("select solicitar_anulacion(%s, %s, 'admin_a')", (tid, MOTIVO))
    insertar_turno(base_b, entrada="2026-09-10 08:00:00", salida="2026-09-10 16:00:00")


def test_cerrar_un_turno_en_revision_no_necesita_ajuste(base_b):
    tid = insertar_turno(base_b, entrada="2026-09-10 08:00:00", estado="Revision")
    with base_b.cursor("turnero_app", sello=("admin_a", "admin", "Olvidó marcar la salida, confirmado")) as cur:
        cur.execute("update turnos set ts_salida = '2026-09-10 17:00', estado = 'Completo' where id = %s", (tid,))
