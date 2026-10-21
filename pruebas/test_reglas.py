"""Cálculo de horas y validaciones de cada turno que se escribe (V001, V004)."""

import psycopg2
import pytest

from conftest import insertar_turno


def test_las_reglas_vigentes_coinciden_con_core_config(base_a):
    from core import config
    reglas = dict(base_a.todos("select clave, valor from reglas_negocio where estado = 'vigente'"))
    assert float(reglas["jornada_base_h"]) == config.HORAS_BASE_TURNO
    assert float(reglas["almuerzo_h"]) == config.HORAS_ALMUERZO
    assert float(reglas["min_horas_para_almuerzo"]) == config.MIN_HORAS_ALMUERZO
    assert float(reglas["tolerancia_horas_extra_h"]) == config.UMBRAL_HORAS_EXTRA - config.HORAS_BASE_TURNO
    assert base_a.uno("select evento_sin_novedad(), evento_con_tolerancia()") == (
        config.EVENTO_SIN_NOVEDAD, config.EVENTO_TOLERANCIA)


def test_las_horas_las_calcula_la_base(base_a):
    tid = insertar_turno(base_a, entrada="2026-09-10 08:00:00", salida="2026-09-10 19:30:00")
    # Aunque alguien intente guardar otras horas, quedan las del horario.
    with base_a.cursor("turnero_app") as cur:
        cur.execute("update turnos set horas_extra = 0, horas_trabajadas = 9 where id = %s", (tid,))
    assert base_a.uno(
        "select horas_trabajadas, horas_efectivas, horas_extra from turnos where id = %s", (tid,)
    ) == (pytest.approx(11.5), pytest.approx(10.5), pytest.approx(2.5))


def test_un_turno_corto_no_descuenta_almuerzo(base_a):
    tid = insertar_turno(base_a, entrada="2026-09-10 08:00:00", salida="2026-09-10 12:00:00")
    assert base_a.uno("select horas_efectivas, horas_extra from turnos where id = %s", (tid,)) == (4, 0)


def test_la_fecha_de_turno_es_la_de_la_entrada(base_a):
    with base_a.cursor("turnero_app", sello=("Prueba Uno", "colaborador")) as cur:
        cur.execute(
            "insert into turnos (nombre, fecha_turno, ts_entrada, ts_salida, estado) "
            "values ('Prueba Uno', '2026-09-01', '2026-09-10 22:00', '2026-09-11 06:00', 'Completo') "
            "returning fecha_turno"
        )
        assert str(cur.fetchone()[0]) == "2026-09-10"


def test_salida_anterior_a_la_entrada_se_rechaza(base_a):
    with pytest.raises(psycopg2.Error, match="posterior a la entrada"):
        insertar_turno(base_a, entrada="2026-09-10 08:00:00", salida="2026-09-10 07:00:00")


def test_un_turno_de_mas_de_24_horas_se_rechaza(base_a):
    # El error de tipeo de septiembre: la salida puesta en el día siguiente.
    with pytest.raises(psycopg2.Error, match="más que el máximo de 24"):
        insertar_turno(base_a, entrada="2026-09-26 13:33:00", salida="2026-09-27 23:18:00")


def test_dos_turnos_cruzados_de_la_misma_persona_se_rechazan(base_a):
    insertar_turno(base_a, entrada="2026-09-10 08:00:00", salida="2026-09-10 17:00:00")
    with pytest.raises(psycopg2.Error, match="se cruza con otro turno"):
        insertar_turno(base_a, entrada="2026-09-10 16:00:00", salida="2026-09-10 20:00:00")
    # Otra persona en el mismo horario, sin problema.
    insertar_turno(base_a, nombre="Prueba Dos", entrada="2026-09-10 16:00:00", salida="2026-09-10 20:00:00")


def test_un_turno_olvidado_no_impide_marcar_el_siguiente(base_a):
    # El turno quedó abierto y pasó a revisión, sin salida. El colaborador
    # vuelve al día siguiente y tiene que poder marcar.
    insertar_turno(base_a, entrada="2026-09-10 08:00:00", estado="Revision")
    insertar_turno(base_a, entrada="2026-09-11 08:00:00")


def test_una_eventualidad_no_frena_la_marcacion(base_a):
    insertar_turno(base_a, entrada="2026-09-12 08:00:00", salida="2026-09-12 17:00:00",
                   observaciones="Solicitud de permiso - Salud (registrado por admin_prueba)",
                   origen="admin", autor="admin_prueba")
    tid = insertar_turno(base_a, entrada="2026-09-12 07:00:00", salida="2026-09-12 15:00:00")
    assert tid


def test_una_eventualidad_sin_evento_se_clasifica_sola(base_a):
    tid = insertar_turno(base_a, entrada="2026-09-15 08:00:00", salida="2026-09-15 17:00:00",
                         observaciones="Vacaciones (registrado por admin_prueba)",
                         origen="admin", autor="admin_prueba")
    assert base_a.uno("select evento from turnos where id = %s", (tid,))[0] == "Vacaciones"


def test_los_estados_validos_estan_restringidos(base_a):
    with pytest.raises(psycopg2.Error, match="ck_turnos_estado"):
        insertar_turno(base_a, entrada="2026-09-10 08:00:00", estado="Cerrado")


def test_un_nombre_desconocido_crea_la_persona_para_revisar(base_a):
    tid = insertar_turno(base_a, nombre="Persona Nueva", entrada="2026-09-10 08:00:00")
    assert base_a.uno(
        "select e.origen from turnos t join empleados e on e.id = t.empleado_id where t.id = %s", (tid,)
    )[0] == "automatico"
