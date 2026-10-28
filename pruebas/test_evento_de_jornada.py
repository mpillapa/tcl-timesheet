"""El evento que la base pone a un turno de trabajo al cerrarse (V013, V015).

Desde V015 solo la tolerancia lleva evento: hasta 9 h y desde 9 h 31 queda vacío.
"""

import psycopg2
import pytest

from conftest import insertar_turno

SIN_NOVEDAD = "Turno completo sin novedad"
TOLERANCIA = "Turno completo con tolerancia de horas extra"
MOTIVO = "Cierre de vuelo confirmado con operaciones"


def _evento(base, tid):
    return base.uno("select evento from turnos where id = %s", (tid,))[0]


@pytest.mark.parametrize("salida, evento", [
    ("2026-09-10 16:30:00", ""),            # 8 h 30
    ("2026-09-10 17:00:00", ""),            # 9 h justas
    ("2026-09-10 17:20:00", TOLERANCIA),    # 9 h 20
    ("2026-09-10 17:30:00", TOLERANCIA),    # 9 h 30 justas: el quiosco no pide justificación
    ("2026-09-10 17:31:00", ""),            # 9 h 31: ya lleva justificación
    ("2026-09-10 19:30:00", ""),
])
def test_el_evento_sale_de_la_duracion(base_a, salida, evento):
    tid = insertar_turno(base_a, entrada="2026-09-10 08:00:00", salida=salida)
    assert _evento(base_a, tid) == evento


def test_la_tolerancia_no_quita_horas_extra(base_a):
    tid = insertar_turno(base_a, entrada="2026-09-10 08:00:00", salida="2026-09-10 17:20:00")
    assert float(base_a.uno("select horas_extra from turnos where id = %s", (tid,))[0]) == 0.33


def test_al_marcar_la_salida_se_llena_y_la_marcacion_se_guarda(base_a):
    tid = insertar_turno(base_a, entrada="2026-09-10 08:00:00")
    assert _evento(base_a, tid) == ""
    with base_a.cursor("turnero_app", sello=("Prueba Uno", "colaborador")) as cur:
        cur.execute("update turnos set ts_salida = '2026-09-10 17:20', estado = 'Completo' where id = %s", (tid,))
    assert _evento(base_a, tid) == TOLERANCIA
    # Un turno con evento de jornada sigue siendo trabajo: guarda lo que se marcó.
    assert base_a.uno("select count(*) from marcaciones where turno_id = %s", (tid,))[0] == 2


def test_una_correccion_recalcula_el_evento(base_a):
    tid = insertar_turno(base_a, entrada="2026-09-10 08:00:00", salida="2026-09-10 19:30:00")
    with base_a.cursor("turnero_app") as cur:
        cur.execute("select solicitar_correccion(%s, '2026-09-10 08:00', '2026-09-10 17:15', null, %s, 'admin_a')",
                    (tid, MOTIVO))
    assert _evento(base_a, tid) == TOLERANCIA
    with base_a.cursor("turnero_app") as cur:
        cur.execute("select solicitar_correccion(%s, '2026-09-10 08:00', '2026-09-10 16:00', null, %s, 'admin_a')",
                    (tid, MOTIVO))
    assert _evento(base_a, tid) == ""


def test_cambiar_solo_el_comentario_no_toca_el_evento_de_un_turno_viejo(base_a):
    # Los turnos de antes de V013 tienen el evento vacío y así se quedan.
    tid = insertar_turno(base_a, entrada="2026-09-10 08:00:00", salida="2026-09-10 16:00:00")
    with base_a.cursor("propietario") as cur:
        cur.execute("alter table turnos disable trigger tg_turnos_35_evento_jornada")
        cur.execute("update turnos set evento = '' where id = %s", (tid,))
        cur.execute("alter table turnos enable trigger tg_turnos_35_evento_jornada")
    with base_a.cursor("turnero_app", sello=("archivado_mensual", "sistema")) as cur:
        cur.execute("update turnos set archivado = true where id = %s", (tid,))
    with base_a.cursor("turnero_app", sello=("admin_a", "admin")) as cur:
        cur.execute("update turnos set observaciones = 'nota' where id = %s", (tid,))
    assert _evento(base_a, tid) == ""


def test_una_eventualidad_conserva_su_evento(base_a):
    tid = insertar_turno(base_a, entrada="2026-09-10 08:00:00", salida="2026-09-10 17:00:00",
                         observaciones="Vacaciones: 2026-09-10")
    assert _evento(base_a, tid) == "Vacaciones"


def test_dos_turnos_con_evento_de_jornada_siguen_sin_poder_cruzarse(base_a):
    insertar_turno(base_a, entrada="2026-09-10 08:00:00", salida="2026-09-10 16:00:00")
    with pytest.raises(psycopg2.Error, match="se cruza con otro turno"):
        insertar_turno(base_a, entrada="2026-09-10 15:00:00", salida="2026-09-10 18:00:00")


def test_el_panel_los_cuenta_como_trabajo():
    import pandas as pd
    from views.super_admin import _clase_de_turno
    df = pd.DataFrame({"Evento": [SIN_NOVEDAD, TOLERANCIA, "", "Vacaciones", "Solicitud de permiso"]})
    assert list(_clase_de_turno(df)) == ["trabajo", "trabajo", "trabajo", "vacaciones", "evento"]


def test_v014_llena_septiembre_y_octubre_y_nada_mas(servidor, base_a):
    from conftest import aplicar_migraciones
    # Turnos de antes de V013: con el evento vacío, como están en producción.
    casos = {
        "sep_corto": ("Prueba Uno", "2026-09-10 08:00:00", "2026-09-10 16:00:00"),
        "oct_tolerancia": ("Prueba Uno", "2026-10-05 08:00:00", "2026-10-05 17:20:00"),
        "oct_largo": ("Prueba Uno", "2026-10-06 08:00:00", "2026-10-06 19:00:00"),
        "ago_corto": ("Prueba Uno", "2026-08-10 08:00:00", "2026-08-10 16:00:00"),
        "oct_abierto": ("Prueba Dos", "2026-10-07 08:00:00", None),
    }
    ids = {k: insertar_turno(base_a, nombre=n, entrada=e, salida=s) for k, (n, e, s) in casos.items()}
    vac = insertar_turno(base_a, nombre="Prueba Dos", entrada="2026-09-15 08:00:00",
                         salida="2026-09-15 17:00:00", observaciones="Vacaciones: 2026-09-15")
    with base_a.cursor("propietario") as cur:
        cur.execute("alter table turnos disable trigger tg_turnos_35_evento_jornada")
        cur.execute("update turnos set evento = '' where id = any(%s)", (list(ids.values()),))
        cur.execute("alter table turnos enable trigger tg_turnos_35_evento_jornada")

    base_a.uno("delete from schema_migraciones where version = 'V014' returning 1")
    res = aplicar_migraciones(servidor, base_a.nombre, "A")
    assert res.returncode == 0, res.stdout + res.stderr

    assert {k: _evento(base_a, i) for k, i in ids.items()} == {
        # Con la regla de V015 vigente, el turno corto ya no lleva evento.
        "sep_corto": "", "oct_tolerancia": TOLERANCIA, "oct_largo": "",
        "ago_corto": "", "oct_abierto": "",
    }
    assert _evento(base_a, vac) == "Vacaciones"
    # Las horas no se movieron y el cambio quedó atribuido a la migración.
    assert float(base_a.uno("select horas_extra from turnos where id = %s", (ids["oct_tolerancia"],))[0]) == 0.33
    assert base_a.uno(
        "select usuario, origen from turnos_auditoria where turno_id = %s order by id desc limit 1",
        (ids["oct_tolerancia"],)) == ("migracion V014", "sistema")


def test_v015_vacia_los_sin_novedad_y_deja_la_tolerancia(servidor, base_a):
    from conftest import aplicar_migraciones
    # Como quedó producción después de V014.
    corto = insertar_turno(base_a, entrada="2026-09-10 08:00:00", salida="2026-09-10 16:00:00")
    tol = insertar_turno(base_a, entrada="2026-09-11 08:00:00", salida="2026-09-11 17:20:00")
    vac = insertar_turno(base_a, nombre="Prueba Dos", entrada="2026-09-15 08:00:00",
                         salida="2026-09-15 17:00:00", observaciones="Vacaciones: 2026-09-15")
    with base_a.cursor("propietario") as cur:
        cur.execute("alter table turnos disable trigger tg_turnos_35_evento_jornada")
        cur.execute("update turnos set evento = %s where id = %s", (SIN_NOVEDAD, corto))
        cur.execute("alter table turnos enable trigger tg_turnos_35_evento_jornada")
    horas_antes = base_a.todos("select id, horas_trabajadas, horas_extra from turnos order by id")

    base_a.uno("delete from schema_migraciones where version = 'V015' returning 1")
    res = aplicar_migraciones(servidor, base_a.nombre, "A")
    assert res.returncode == 0, res.stdout + res.stderr

    assert (_evento(base_a, corto), _evento(base_a, tol), _evento(base_a, vac)) == ("", TOLERANCIA, "Vacaciones")
    assert base_a.todos("select id, horas_trabajadas, horas_extra from turnos order by id") == horas_antes
    assert base_a.uno(
        "select usuario, origen from turnos_auditoria where turno_id = %s order by id desc limit 1",
        (corto,)) == ("migracion V015", "sistema")
