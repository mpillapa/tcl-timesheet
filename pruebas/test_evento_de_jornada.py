"""El evento que la base pone a un turno de trabajo al cerrarse (V013)."""

import psycopg2
import pytest

from conftest import insertar_turno

SIN_NOVEDAD = "Turno completo sin novedad"
TOLERANCIA = "Turno completo con tolerancia de horas extra"
MOTIVO = "Cierre de vuelo confirmado con operaciones"


def _evento(base, tid):
    return base.uno("select evento from turnos where id = %s", (tid,))[0]


@pytest.mark.parametrize("salida, evento", [
    ("2026-09-10 16:30:00", SIN_NOVEDAD),   # 8 h 30
    ("2026-09-10 17:00:00", SIN_NOVEDAD),   # 9 h justas
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
    assert _evento(base_a, tid) == SIN_NOVEDAD


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
