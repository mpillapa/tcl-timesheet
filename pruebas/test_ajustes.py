"""Correcciones y anulaciones como ajustes con motivo (V009, V011, V012, V101).

Desde V012 todo ajuste se aplica en el acto. La aprobación de otro
administrador sigue en la base detrás de la regla 'exigir_aprobacion_ajustes';
las pruebas que la ejercitan la encienden con el fixture `con_aprobacion`.
"""

import psycopg2
import pytest

from conftest import insertar_turno, mes_pasado_cerrable

MOTIVO = "Cierre de vuelo confirmado con operaciones"


@pytest.fixture
def con_aprobacion(base_a):
    """La base como estaba con V011: lo que quita más de 10 min espera."""
    base_a.uno("update reglas_negocio set valor = 1 where clave = 'exigir_aprobacion_ajustes' returning 1")
    return base_a


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
    _, estado, requiere = _corregir(base_a, tid, "2026-09-10 08:00", "2026-09-10 19:25")
    assert (estado, requiere) == ("aplicado", False)
    assert float(base_a.uno("select horas_extra from turnos where id = %s", (tid,))[0]) == 2.42


def _clase(base, aid):
    return base.uno("select clase_de_ajuste(a) from ajustes_turno a where id = %s", (aid,))[0]


def test_la_tolerancia_solo_clasifica_el_cambio(base_a):
    # Diez minutos es un ajuste menor; diez minutos y un segundo, un recorte.
    # Los dos se aplican en el acto.
    tid = _turno_largo(base_a)
    aid, estado, _ = _corregir(base_a, tid, "2026-09-10 08:00", "2026-09-10 19:20")
    assert (estado, _clase(base_a, aid)) == ("aplicado", "menor")
    aid, estado, _ = _corregir(base_a, tid, "2026-09-10 08:00", "2026-09-10 19:09:59")
    assert (estado, _clase(base_a, aid)) == ("aplicado", "recorte")


def test_con_aprobacion_encendida_la_tolerancia_es_de_diez_minutos_exactos(con_aprobacion):
    tid = _turno_largo(con_aprobacion)
    _, estado, _ = _corregir(con_aprobacion, tid, "2026-09-10 08:00", "2026-09-10 19:20")
    assert estado == "aplicado"
    _, estado, _ = _corregir(con_aprobacion, tid, "2026-09-10 08:00", "2026-09-10 19:09:59")
    assert estado == "pendiente"


def test_un_recorte_grande_se_aplica_en_el_acto_y_queda_registrado(base_a):
    tid = _turno_largo(base_a)
    aid, estado, requiere = _corregir(base_a, tid, "2026-09-10 08:00", "2026-09-10 17:00")
    assert (estado, requiere) == ("aplicado", False)
    assert float(base_a.uno("select horas_extra from turnos where id = %s", (tid,))[0]) == 0.0

    # El ajuste guarda el antes, lo nuevo y la diferencia.
    antes, propuesto, delta_extra, por = base_a.uno(
        "select antes ->> 'ts_salida', propuesto ->> 'ts_salida', delta_extra, solicitado_por "
        "from ajustes_turno where id = %s", (aid,))
    assert (antes[:16], propuesto[:16], float(delta_extra), por) == (
        "2026-09-10T19:30", "2026-09-10T17:00", -2.5, "admin_a")
    # La bitácora atribuye el cambio a quien lo hizo, con su motivo y el ajuste.
    assert base_a.uno(
        "select usuario, origen, motivo, ajuste_id from turnos_auditoria where turno_id = %s "
        "order by id desc limit 1", (tid,)
    ) == ("admin_a", "admin", MOTIVO, aid)
    # Y lo que se marcó sigue guardado aparte.
    assert base_a.uno("select count(*) from marcaciones where turno_id = %s", (tid,))[0] == 2


def test_un_recorte_sin_horas_extra_se_aplica(base_a):
    # 8 h a 7 h: no toca horas extra, pero quita una hora trabajada.
    tid = insertar_turno(base_a, entrada="2026-09-10 08:00:00", salida="2026-09-10 16:00:00")
    _, estado, requiere = _corregir(base_a, tid, "2026-09-10 08:00", "2026-09-10 15:00")
    assert (estado, requiere) == ("aplicado", False)


def test_sumar_tiempo_se_aplica_solo(base_a):
    tid = insertar_turno(base_a, entrada="2026-09-10 08:00:00", salida="2026-09-10 16:00:00")
    aid, estado, _ = _corregir(base_a, tid, "2026-09-10 07:00", "2026-09-10 17:00")
    assert (estado, _clase(base_a, aid)) == ("aplicado", "suma")


def test_cambiar_solo_el_comentario_no_mueve_el_turno(base_a):
    tid = insertar_turno(base_a, entrada="2026-09-10 08:00:13", salida="2026-09-10 19:30:40",
                         observaciones="Horas extra justificadas: cierre de vuelo")
    aid, estado, _ = _corregir(base_a, tid, "2026-09-10 08:00:13", "2026-09-10 19:30:40",
                               obs="[Corrección 2026-09-11 por admin_a]: " + MOTIVO
                                   + " | [Orig]: Horas extra justificadas: cierre de vuelo")
    assert (estado, _clase(base_a, aid)) == ("aplicado", "solo_comentario")
    salida, obs = base_a.uno("select to_char(ts_salida, 'HH24:MI:SS'), observaciones from turnos where id = %s", (tid,))
    assert salida == "19:30:40"
    assert obs.startswith("[Corrección 2026-09-11 por admin_a]") and "[Orig]: Horas extra" in obs


def test_perder_segundos_al_corregir_es_un_ajuste_menor(base_a):
    # Lo que hacía el formulario anterior: guardar 19:30 en lugar de 19:30:40.
    tid = insertar_turno(base_a, entrada="2026-09-10 08:00:13", salida="2026-09-10 19:30:40")
    aid, estado, _ = _corregir(base_a, tid, "2026-09-10 08:00", "2026-09-10 19:30")
    assert (estado, _clase(base_a, aid)) == ("aplicado", "menor")


def test_con_aprobacion_encendida_un_recorte_grande_espera_a_otra_persona(con_aprobacion):
    base_a = con_aprobacion
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


def test_el_rechazo_deja_el_turno_intacto_y_exige_explicacion(con_aprobacion):
    base_a = con_aprobacion
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


def test_un_turno_no_tiene_dos_ajustes_esperando(con_aprobacion):
    base_a = con_aprobacion
    tid = _turno_largo(base_a)
    _corregir(base_a, tid, "2026-09-10 08:00", "2026-09-10 17:00")
    with pytest.raises(psycopg2.Error, match="ya tiene un ajuste esperando"):
        _corregir(base_a, tid, "2026-09-10 08:00", "2026-09-10 17:30")


def test_un_ajuste_aplicado_no_se_puede_reescribir_ni_borrar(base_a):
    tid = _turno_largo(base_a)
    aid, _, _ = _corregir(base_a, tid, "2026-09-10 08:00", "2026-09-10 17:00")
    with pytest.raises(psycopg2.Error, match="ya fue aplicado"):
        with base_a.cursor("propietario") as cur:
            cur.execute("update ajustes_turno set motivo = 'otro motivo distinto' where id = %s", (aid,))
    with pytest.raises(psycopg2.Error, match="no se borra"):
        with base_a.cursor("propietario") as cur:
            cur.execute("delete from ajustes_turno where id = %s", (aid,))


def test_lo_solicitado_no_se_puede_reescribir(con_aprobacion):
    base_a = con_aprobacion
    tid = _turno_largo(base_a)
    aid, _, _ = _corregir(base_a, tid, "2026-09-10 08:00", "2026-09-10 17:00")
    with pytest.raises(psycopg2.Error, match="no se puede modificar"):
        with base_a.cursor("propietario") as cur:
            cur.execute("update ajustes_turno set motivo = 'otro motivo distinto' where id = %s", (aid,))


def test_anular_un_turno_con_horas_extra_se_aplica_y_queda_registrado(base_a):
    tid = _turno_largo(base_a)
    with base_a.cursor("turnero_app") as cur:
        cur.execute("select estado, requiere_aprobacion, delta_extra from solicitar_anulacion(%s, %s, 'admin_a')",
                    (tid, MOTIVO))
        estado, requiere, delta_extra = cur.fetchone()
    assert (estado, requiere, float(delta_extra)) == ("aplicado", False, -2.5)
    anulado, por, motivo = base_a.uno("select anulado, anulado_por, motivo_anulacion from turnos where id = %s", (tid,))
    assert (anulado, por, motivo) == (True, "admin_a", MOTIVO)


def test_con_aprobacion_encendida_anular_con_horas_extra_espera(con_aprobacion):
    base_a = con_aprobacion
    tid = _turno_largo(base_a)
    with base_a.cursor("turnero_app") as cur:
        cur.execute("select id, estado from solicitar_anulacion(%s, %s, 'admin_a')", (tid, MOTIVO))
        aid, estado = cur.fetchone()
    assert estado == "pendiente"
    with base_a.cursor("turnero_app") as cur:
        cur.execute("select aprobar_ajuste(%s, 'admin_b', '')", (aid,))
    anulado, por, motivo = base_a.uno("select anulado, anulado_por, motivo_anulacion from turnos where id = %s", (tid,))
    assert (anulado, por, motivo) == (True, "admin_a", MOTIVO)


def _anular(base, tid):
    with base.cursor("turnero_app") as cur:
        cur.execute("select estado from solicitar_anulacion(%s, %s, 'admin_a')", (tid, MOTIVO))
        return cur.fetchone()[0]


def test_anular_un_turno_de_trabajo_sin_extra_se_aplica(base_a):
    tid = insertar_turno(base_a, entrada="2026-09-10 08:00:00", salida="2026-09-10 17:00:00")
    assert _anular(base_a, tid) == "aplicado"


def test_los_pendientes_de_antes_se_aplican_al_retirar_la_aprobacion(con_aprobacion):
    # Lo que hace V012 con los ajustes que quedaron esperando en producción.
    base = con_aprobacion
    tid = insertar_turno(base, entrada="2026-08-10 08:00:00", salida="2026-08-10 19:30:00")
    aid, estado, _ = _corregir(base, tid, "2026-08-10 08:00", "2026-08-10 17:00")
    assert estado == "pendiente"

    # Otro que ya no se puede aplicar: su mes se cerró con el bloqueo encendido.
    anio, mes = mes_pasado_cerrable(base)
    tid2 = insertar_turno(base, nombre="Prueba Dos", entrada=f"{anio}-{mes:02d}-10 08:00:00",
                          salida=f"{anio}-{mes:02d}-10 19:30:00")
    aid2, estado, _ = _corregir(base, tid2, f"{anio}-{mes:02d}-10 08:00", f"{anio}-{mes:02d}-10 17:00")
    assert estado == "pendiente"
    base.uno("update reglas_negocio set valor = 1 where clave = 'bloquear_periodos_cerrados' returning 1")
    with base.cursor("turnero_app") as cur:
        cur.execute("select cerrar_periodo(%s, %s, 'admin_rrhh', 'Cierre de prueba')", (anio, mes))

    base.uno("update reglas_negocio set valor = 0 where clave = 'exigir_aprobacion_ajustes' returning 1")
    assert base.uno("select aplicados, rechazados from aplicar_ajustes_pendientes('sistema: V012')") == (1, 1)

    assert base.uno("select estado, revisado_por from ajustes_turno where id = %s", (aid,)) == (
        "aplicado", "sistema: V012")
    assert float(base.uno("select horas_extra from turnos where id = %s", (tid,))[0]) == 0.0
    # La bitácora lo atribuye a quien lo pidió, no al sistema.
    assert base.uno("select usuario from turnos_auditoria where ajuste_id = %s", (aid,))[0] == "admin_a"

    estado, comentario = base.uno("select estado, comentario_revision from ajustes_turno where id = %s", (aid2,))
    assert estado == "rechazado" and "está cerrado" in comentario
    assert float(base.uno("select horas_extra from turnos where id = %s", (tid2,))[0]) == 2.5


def test_anular_un_turno_abierto_o_muy_corto_se_aplica_solo(base_a):
    # El caso de los turnos que quedaron abiertos por error.
    assert _anular(base_a, insertar_turno(base_a, entrada="2026-09-10 08:00:00")) == "aplicado"
    assert _anular(base_a, insertar_turno(base_a, nombre="Prueba Dos", entrada="2026-09-10 08:00:00",
                                          salida="2026-09-10 08:05:00")) == "aplicado"


def test_anular_una_eventualidad_se_aplica_sola(base_a):
    tid = insertar_turno(base_a, entrada="2026-09-10 08:00:00", salida="2026-09-10 17:00:00",
                         observaciones="Vacaciones: 2026-09-10")
    assert _anular(base_a, tid) == "aplicado"


def test_un_turno_anulado_no_se_edita_ni_se_reactiva(base_a):
    tid = insertar_turno(base_a, entrada="2026-09-10 08:00:00", salida="2026-09-10 08:05:00")
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
    tid = insertar_turno(base_b, entrada="2026-09-10 08:00:00", salida="2026-09-10 08:05:00")
    with base_b.cursor("turnero_app") as cur:
        cur.execute("select solicitar_anulacion(%s, %s, 'admin_a')", (tid, MOTIVO))
    insertar_turno(base_b, entrada="2026-09-10 08:00:00", salida="2026-09-10 16:00:00")


def test_cerrar_un_turno_en_revision_no_necesita_ajuste(base_b):
    tid = insertar_turno(base_b, entrada="2026-09-10 08:00:00", estado="Revision")
    with base_b.cursor("turnero_app", sello=("admin_a", "admin", "Olvidó marcar la salida, confirmado")) as cur:
        cur.execute("update turnos set ts_salida = '2026-09-10 17:00', estado = 'Completo' where id = %s", (tid,))
