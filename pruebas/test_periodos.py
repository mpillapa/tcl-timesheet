"""Cierre y reapertura de periodos de nómina (V007)."""

import psycopg2
import pytest

from conftest import insertar_turno, mes_pasado_cerrable

MOTIVO = "Cierre de nómina enviado a Recursos Humanos"


def _fecha(anio, mes, dia=10, hora="08:00:00"):
    return f"{anio}-{mes:02d}-{dia:02d} {hora}"


def _cerrar(base, anio, mes):
    with base.cursor("turnero_app") as cur:
        cur.execute("select cerrar_periodo(%s, %s, 'admin_rrhh', %s)", (anio, mes, MOTIVO))


def test_un_mes_cerrado_no_admite_cambios(base_a):
    anio, mes = mes_pasado_cerrable(base_a)
    tid = insertar_turno(base_a, entrada=_fecha(anio, mes), salida=_fecha(anio, mes, hora="19:00:00"))
    _cerrar(base_a, anio, mes)

    with pytest.raises(psycopg2.Error, match="está cerrado"):
        insertar_turno(base_a, nombre="Prueba Dos", entrada=_fecha(anio, mes, 11))
    with pytest.raises(psycopg2.Error, match="está cerrado"):
        with base_a.cursor("turnero_app", sello=("admin_a", "admin")) as cur:
            cur.execute("update turnos set ts_salida = %s where id = %s", (_fecha(anio, mes, hora="17:00:00"), tid))
    with pytest.raises(psycopg2.Error, match="está cerrado"):
        with base_a.cursor("turnero_app") as cur:
            cur.execute("select solicitar_correccion(%s, %s, %s, null, %s, 'admin_a')",
                        (tid, _fecha(anio, mes), _fecha(anio, mes, hora="17:00:00"), MOTIVO))
    with pytest.raises(psycopg2.Error, match="está cerrado"):
        with base_a.cursor("turnero_app") as cur:
            cur.execute("select solicitar_anulacion(%s, %s, 'admin_a')", (tid, MOTIVO))


def test_el_archivado_pasa_aunque_el_mes_este_cerrado(base_a):
    anio, mes = mes_pasado_cerrable(base_a)
    tid = insertar_turno(base_a, entrada=_fecha(anio, mes), salida=_fecha(anio, mes, hora="17:00:00"))
    _cerrar(base_a, anio, mes)
    with base_a.cursor("turnero_app", sello=("archivado_mensual", "sistema")) as cur:
        cur.execute("update turnos set archivado = true, actualizado_en = now() where id = %s", (tid,))


def test_no_se_cierra_el_mes_en_curso(base_a):
    anio, mes = base_a.uno(
        "select extract(year from d)::int, extract(month from d)::int "
        "from (select (now() at time zone 'America/Guayaquil')::date d) x")
    with pytest.raises(psycopg2.Error, match="ya terminó"):
        _cerrar(base_a, anio, mes)


def test_reabrir_exige_motivo_y_queda_registrado(base_a):
    anio, mes = mes_pasado_cerrable(base_a)
    _cerrar(base_a, anio, mes)
    with pytest.raises(psycopg2.Error, match="exige un motivo"):
        with base_a.cursor("turnero_app") as cur:
            cur.execute("select reabrir_periodo(%s, %s, 'admin_rrhh', 'tarde')", (anio, mes))
    with base_a.cursor("turnero_app") as cur:
        cur.execute("select reabrir_periodo(%s, %s, 'admin_rrhh', %s)",
                    (anio, mes, "Corrección de un turno reportada por el jefe de área"))
    eventos = base_a.todos("select accion, usuario from periodos_eventos order by id")
    assert eventos == [("cierre", "admin_rrhh"), ("reapertura", "admin_rrhh")]
    # Reabierto, ya admite cambios.
    insertar_turno(base_a, entrada=_fecha(anio, mes, 12))

    with pytest.raises(psycopg2.Error, match="solo inserción"):
        with base_a.cursor("propietario") as cur:
            cur.execute("delete from periodos_eventos")


def test_un_ajuste_pendiente_no_se_aplica_si_el_mes_se_cerro_entre_medio(base_a):
    anio, mes = mes_pasado_cerrable(base_a)
    tid = insertar_turno(base_a, entrada=_fecha(anio, mes), salida=_fecha(anio, mes, hora="19:30:00"))
    with base_a.cursor("turnero_app") as cur:
        cur.execute("select id from solicitar_correccion(%s, %s, %s, null, %s, 'admin_a')",
                    (tid, _fecha(anio, mes), _fecha(anio, mes, hora="17:00:00"), MOTIVO))
        aid = cur.fetchone()[0]
    _cerrar(base_a, anio, mes)
    with pytest.raises(psycopg2.Error, match="está cerrado"):
        with base_a.cursor("turnero_app") as cur:
            cur.execute("select aprobar_ajuste(%s, 'admin_b', '')", (aid,))
