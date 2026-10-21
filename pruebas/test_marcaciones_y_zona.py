"""Marcaciones originales (V008) y horarios con zona (V102)."""

from conftest import insertar_turno

MOTIVO = "Cierre de vuelo confirmado con operaciones"


def _marcaciones(base, tid):
    return base.todos(
        "select tipo, to_char(momento at time zone 'America/Guayaquil', 'YYYY-MM-DD HH24:MI:SS'), origen "
        "from marcaciones where turno_id = %s order by tipo", (tid,))


def test_la_marcacion_original_sobrevive_a_la_correccion(base_b):
    tid = insertar_turno(base_b, entrada="2026-09-09 09:13:29")
    with base_b.cursor("turnero_app", sello=("Prueba Uno", "colaborador")) as cur:
        cur.execute("update turnos set ts_salida = '2026-09-10 00:03:58', estado = 'Completo' where id = %s", (tid,))
    # El caso de septiembre: el turno se reescribe entero. Desde V012 se
    # aplica en el acto, y la marcación original tiene que seguir ahí.
    with base_b.cursor("turnero_app") as cur:
        cur.execute("select estado from solicitar_correccion(%s, '2026-09-09 00:12', '2026-09-09 09:12', null, %s, 'admin_a')",
                    (tid, MOTIVO))
        assert cur.fetchone()[0] == "aplicado"

    assert _marcaciones(base_b, tid) == [
        ("entrada", "2026-09-09 09:13:29", "colaborador"),
        ("salida", "2026-09-10 00:03:58", "colaborador"),
    ]
    vigente = base_b.uno(
        "select to_char(ts_entrada, 'HH24:MI'), to_char(ts_salida, 'HH24:MI'), horas_trabajadas "
        "from turnos where id = %s", (tid,), usuario="turnero_app")
    assert vigente[:2] == ("00:12", "09:12")


def test_un_registro_manual_queda_atribuido_al_admin(base_b):
    tid = insertar_turno(base_b, entrada="2026-09-10 08:00:00", salida="2026-09-10 17:00:00",
                         origen="admin", autor="admin_a", motivo="Olvidó marcar entrada y salida")
    assert [(t, o) for t, _, o in _marcaciones(base_b, tid)] == [("entrada", "admin"), ("salida", "admin")]


def test_las_horas_se_guardan_como_instante_de_ecuador(base_b):
    # La aplicación escribe la hora local sin zona.
    tid = insertar_turno(base_b, entrada="2026-09-10 22:00:00", salida="2026-09-11 06:30:00")
    # Leída en UTC, como en el Table Editor de Supabase, es la misma hora.
    utc = base_b.uno("set time zone 'UTC'; select to_char(ts_entrada, 'YYYY-MM-DD HH24:MI'), fecha_turno::text "
                     "from turnos where id = %s", (tid,))
    assert utc == ("2026-09-11 03:00", "2026-09-10")
    assert float(base_b.uno("select horas_trabajadas from turnos where id = %s", (tid,))[0]) == 8.5


def test_una_escritura_en_utc_no_cambia_la_fecha_de_turno(base_b):
    # Alguien edita desde una sesión en UTC con una hora absoluta.
    with base_b.cursor("propietario", sello=("", "fuera_de_la_app")) as cur:
        cur.execute("set local time zone 'UTC'")
        cur.execute("insert into turnos (nombre, fecha_turno, ts_entrada, estado) "
                    "values ('Prueba Uno', '2026-01-01', '2026-09-11 02:00:00+00', 'Abierto') returning fecha_turno::text")
        # 02:00 UTC es 21:00 del día anterior en Ecuador.
        assert cur.fetchone()[0] == "2026-09-10"
