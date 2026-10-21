"""La capa de datos de la aplicación (core/data.py) contra la base migrada.

Conecta como `turnero_app`, que es como quedará la aplicación en producción, y
recorre los flujos reales: el colaborador marca, un administrador corrige y
el cambio se aplica en el acto, se anula, se registra un permiso.
"""

from datetime import datetime

import pytest

from conftest import insertar_turno, mes_pasado_cerrable

MOTIVO = "Cierre de vuelo confirmado con operaciones"


def _fila(nombre, entrada, salida="", estado="Abierto", obs=""):
    return {"Nombre": nombre, "Area": "BODEGA", "Fecha de Turno": entrada[:10],
            "Timestamp Entrada": entrada, "Timestamp Salida": salida,
            "Horas Trabajadas": "", "Horas Efectivas": "", "Horas Extra": "",
            "Estado": estado, "Observaciones": obs}


def _marca_y_cierra(datos, nombre="Prueba Uno", entrada="2026-09-10 08:00:13", salida="2026-09-10 19:30:40"):
    assert datos.append_registro(_fila(nombre, entrada), autor=nombre, origen=datos.ORIGEN_COLABORADOR)
    assert datos.actualizar_por_entrada(
        nombre, entrada, {"Timestamp Salida": salida, "Estado": "Completo",
                          "Horas Trabajadas": 1, "Horas Efectivas": 1, "Horas Extra": 0},
        autor=nombre, origen=datos.ORIGEN_COLABORADOR)


def test_la_jornada_del_colaborador(datos, base_b):
    _marca_y_cierra(datos)
    df = datos.leer_registros()
    fila = df.iloc[0]
    # Las horas las calculó la base: lo que mandó la aplicación (1 h) no cuenta.
    assert fila["Horas Trabajadas"] == pytest.approx(11.51)
    assert fila["Horas Extra"] == pytest.approx(2.51)
    # La hora vuelve en hora de Ecuador y con sus segundos, aunque la columna
    # tenga zona.
    assert fila["Timestamp Entrada"] == "2026-09-10 08:00:13"
    assert base_b.uno("select count(*) from marcaciones where origen = 'colaborador'")[0] == 2


def test_un_recorte_grande_se_aplica_y_aparece_en_las_modificaciones(datos):
    _marca_y_cierra(datos)
    # Se lee antes para llenar la caché: la corrección tiene que limpiarla.
    assert datos.leer_ajustes("", datetime(2026, 1, 1).date()).empty
    res = datos.solicitar_correccion(
        "Prueba Uno", "2026-09-10 08:00:13", datetime(2026, 9, 10, 8, 0, 13), datetime(2026, 9, 10, 17, 0),
        "[Corrección por admin_a]: " + MOTIVO, motivo=MOTIVO, autor="admin_a")
    assert res["estado"] == "aplicado" and res["delta_extra"] == pytest.approx(-2.51)
    assert datos.leer_registros().iloc[0]["Horas Extra"] == pytest.approx(0)

    aj = datos.leer_ajustes("", datetime(2026, 1, 1).date())
    assert len(aj) == 1
    fila = aj.iloc[0]
    assert [fila["entrada_antes"], fila["salida_antes"], fila["salida_propuesta"]] == [
        "2026-09-10 08:00", "2026-09-10 19:30", "2026-09-10 17:00"]
    assert (fila["estado"], fila["clase"], fila["solicitado_por"], fila["motivo"]) == (
        "aplicado", "recorte", "admin_a", MOTIVO)

    dif = datos.leer_diferencias_marcacion(datetime(2026, 9, 1).date())
    assert len(dif) == 1
    assert float(dif.iloc[0]["extra_quitada"]) == pytest.approx(2.51)


def test_una_correccion_sin_motivo_suficiente_se_rechaza_con_mensaje(datos):
    _marca_y_cierra(datos)
    with pytest.raises(datos.ReglaDeNegocio, match="al menos 10 caracteres"):
        datos.solicitar_correccion("Prueba Uno", "2026-09-10 08:00:13", datetime(2026, 9, 10, 8),
                                   datetime(2026, 9, 10, 19), "x", motivo="corrijo", autor="admin_a")


def test_anular_saca_el_turno_de_las_lecturas_y_queda_en_la_bitacora(datos):
    _marca_y_cierra(datos)
    res = datos.solicitar_anulacion("Prueba Uno", "2026-09-10 08:00:13", motivo=MOTIVO, autor="admin_a")
    assert res["estado"] == "aplicado"
    assert datos.leer_registros().empty
    bit = datos.leer_bitacora_supervision(datetime(2026, 1, 1).date())
    assert list(bit["accion"]) == ["anulacion"]
    assert bit.iloc[0]["motivo"] == MOTIVO


def test_un_registro_manual_sin_motivo_se_rechaza(datos):
    with pytest.raises(datos.ReglaDeNegocio, match="falta el motivo"):
        datos.append_registro(_fila("Prueba Uno", "2026-09-10 08:00:00"), autor="admin_a", origen=datos.ORIGEN_ADMIN)
    assert datos.append_registro(_fila("Prueba Uno", "2026-09-10 08:00:00"), autor="admin_a",
                                 origen=datos.ORIGEN_ADMIN, motivo="El lector no registró la entrada")


def test_un_permiso_queda_atado_a_su_solicitud(datos, base_b):
    filas = [_fila("Prueba Dos", f"2026-09-{d:02d} 08:00:00", f"2026-09-{d:02d} 17:00:00", "Completo",
                   "Solicitud de permiso - Salud (registrado por admin_a)") for d in (15, 16, 17)]
    n = datos.registrar_ausencia("Prueba Dos", "BODEGA", "Solicitud de permiso", "Salud", filas,
                                 autor="admin_a", motivo="Solicitud de permiso - Salud")
    assert n == 3
    assert base_b.uno("select desde::text, hasta::text, registrado_por from ausencias") == \
        ("2026-09-15", "2026-09-17", "admin_a")
    assert base_b.uno("select count(*) from turnos where ausencia_id is not null and evento = 'Solicitud de permiso'")[0] == 3
    # Viene a trabajar uno de esos días: puede marcar.
    assert datos.append_registro(_fila("Prueba Dos", "2026-09-16 07:00:00"), autor="Prueba Dos",
                                 origen=datos.ORIGEN_COLABORADOR)


def test_una_regla_de_la_base_llega_como_mensaje_claro(datos, base_b):
    # El bloqueo de meses cerrados está apagado desde V012; se enciende aquí
    # solo para tener un rechazo de la base que traducir.
    base_b.uno("update reglas_negocio set valor = 1 where clave = 'bloquear_periodos_cerrados' returning 1")
    anio, mes = mes_pasado_cerrable(base_b)
    entrada = f"{anio}-{mes:02d}-10 08:00:00"
    insertar_turno(base_b, entrada=entrada)
    with base_b.cursor("turnero_app") as cur:
        cur.execute("select cerrar_periodo(%s, %s, 'admin_rrhh', 'Nómina enviada')", (anio, mes))
    with pytest.raises(datos.ReglaDeNegocio, match=f"{anio}-{mes:02d} está cerrado"):
        datos.actualizar_por_entrada("Prueba Uno", entrada, {"Estado": "Revision"},
                                     autor="Prueba Uno", origen=datos.ORIGEN_COLABORADOR)


def test_conservar_segundos():
    from core.time_utils import conservar_segundos
    original = datetime(2026, 9, 10, 8, 0, 13)
    assert conservar_segundos(original, datetime(2026, 9, 10, 8, 0)) == original
    assert conservar_segundos(original, datetime(2026, 9, 10, 8, 5)) == datetime(2026, 9, 10, 8, 5)
    assert conservar_segundos(None, datetime(2026, 9, 10, 8, 5)) == datetime(2026, 9, 10, 8, 5)


def test_la_hora_de_los_ajustes_es_la_de_ecuador_aunque_la_sesion_este_en_utc(datos, base_b):
    # Un ajuste se ve igual leído desde la aplicación que desde una sesión en UTC.
    _marca_y_cierra(datos)
    datos.solicitar_correccion("Prueba Uno", "2026-09-10 08:00:13", datetime(2026, 9, 10, 8, 0, 13),
                               datetime(2026, 9, 10, 19, 25), "x " + MOTIVO, motivo=MOTIVO, autor="admin_a")
    hora = base_b.uno("set time zone 'UTC'; select to_char(ts_salida at time zone 'America/Guayaquil', 'HH24:MI') "
                      "from turnos")[0]
    assert hora == "19:25"
