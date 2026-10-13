"""El ejecutor de migraciones: orden, idempotencia y protección de lo aplicado."""


from conftest import RAIZ, aplicar_migraciones, crear_base_vacia


def test_las_plantillas_tienen_todas_las_versiones(base_a, base_b):
    a = {v for (v,) in base_a.todos("select version from schema_migraciones")}
    b = {v for (v,) in base_b.todos("select version from schema_migraciones")}
    todas = {p.name[:4] for p in (RAIZ / "migracion" / "versiones").glob("V*.sql")}
    assert a == {v for v in todas if v < "V100"}
    assert b == todas


def test_volver_a_correr_no_aplica_nada(servidor, base_b):
    res = aplicar_migraciones(servidor, base_b.nombre, "B")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "No hay migraciones pendientes" in res.stdout


def test_detecta_una_migracion_editada_despues_de_aplicarse(servidor, base_a):
    base_a.uno("update schema_migraciones set checksum = 'otro' where version = 'V003' returning 1")
    res = aplicar_migraciones(servidor, base_a.nombre, "B")
    assert res.returncode == 2
    assert "cambió después de aplicarse" in res.stdout
    # Y no aplicó la fase B.
    assert base_a.uno("select count(*) from schema_migraciones where version >= 'V100'")[0] == 0


def test_se_niega_a_tocar_supabase_sin_permiso_explicito(servidor, monkeypatch):
    import subprocess, sys, os
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               MIGRACION_DB_URL="postgresql://u:p@aws-0-us-east-1.pooler.supabase.com:5432/postgres")
    res = subprocess.run([sys.executable, str(RAIZ / "migracion" / "aplicar_migraciones.py"), "--plan"],
                         capture_output=True, text=True, encoding="utf-8", env=env)
    assert res.returncode != 0
    assert "--produccion" in (res.stdout + res.stderr)


# Un esquema como el de producción antes de esta versión: los tres archivos
# sueltos, con datos. La fase A tiene que aplicarse encima sin perder nada.
ESQUEMA_ANTERIOR = [
    RAIZ / "migracion" / "schema.sql",
    RAIZ / "migracion" / "auditoria_schema.sql",
]

DATOS_ANTERIORES = """
insert into turnos (nombre, area, fecha_turno, ts_entrada, ts_salida, horas_trabajadas,
                    horas_efectivas, horas_extra, estado, observaciones)
values
 ('Prueba Uno', 'BODEGA', '2026-09-01', '2026-09-01 08:00:13', '2026-09-01 19:30:40', 11.51, 10.51, 2.51,
  'Completo', 'Horas extra justificadas: cierre de vuelo de prueba'),
 ('Prueba Dos', 'IMPORT', '2026-09-02', '2026-09-02 08:00:00', '2026-09-02 17:00:00', 9, 8, 0,
  'Completo', 'Solicitud de permiso - Permiso Personal (registrado por admin_prueba)'),
 ('Prueba Dos', 'IMPORT', '2026-09-03', '2026-09-03 08:00:00', '2026-09-03 17:00:00', 9, 8, 0,
  'Completo', 'Vacaciones (registrado por admin_prueba)'),
 ('Prueba Tres', 'BODEGA', '2026-09-04', '2026-09-04 22:00:00', null, null, null, null, 'Abierto', '');

-- Una corrección hecha con la versión anterior: queda en la bitácora.
select set_config('app.usuario', 'admin_prueba', false), set_config('app.origen', 'admin', false);
update turnos set ts_salida = '2026-09-01 17:30:00', horas_trabajadas = 9.5, horas_efectivas = 8.5,
                  horas_extra = 0.5
where nombre = 'Prueba Uno';
"""


def test_fase_a_sobre_una_base_como_la_de_produccion(servidor):
    nombre = "anterior_" + __import__("uuid").uuid4().hex[:6]
    crear_base_vacia(servidor, nombre)
    with servidor.cursor(nombre, "propietario") as cur:
        for ruta in ESQUEMA_ANTERIOR:
            cur.execute(ruta.read_text(encoding="utf-8"))
        cur.execute(DATOS_ANTERIORES)

    res = aplicar_migraciones(servidor, nombre, "A")
    assert res.returncode == 0, res.stdout + res.stderr

    with servidor.cursor(nombre, "propietario") as cur:
        cur.execute("select count(*) from turnos")
        assert cur.fetchone()[0] == 4

        # Cada turno quedó atado a una persona.
        cur.execute("select count(*) from turnos where empleado_id is null")
        assert cur.fetchone()[0] == 0
        cur.execute("select count(*), count(*) filter (where origen = 'historico') from empleados")
        assert cur.fetchone() == (3, 3)

        # Las eventualidades se reconocieron por la observación.
        cur.execute("select evento from turnos where nombre = 'Prueba Dos' order by fecha_turno")
        assert [r[0] for r in cur.fetchall()] == ["Solicitud de permiso", "Vacaciones"]
        cur.execute("select tipo, subtipo, registrado_por, origen from ausencias order by desde")
        assert cur.fetchall() == [
            ("Solicitud de permiso", "Permiso Personal", "admin_prueba", "migrado"),
            ("Vacaciones", "", "admin_prueba", "migrado"),
        ]

        # La marcación reconstruida rescata la salida anterior a la corrección,
        # desde la bitácora, y no la corregida.
        cur.execute("""
            select tipo, (momento at time zone 'America/Guayaquil')::text, origen
            from marcaciones m join turnos t on t.id = m.turno_id
            where t.nombre = 'Prueba Uno' order by tipo
        """)
        assert cur.fetchall() == [
            ("entrada", "2026-09-01 08:00:13", "reconstruido"),
            ("salida", "2026-09-01 19:30:40", "reconstruido"),
        ]
        # Las eventualidades no generan marcaciones; el turno abierto solo entrada.
        cur.execute("select count(*) from marcaciones")
        assert cur.fetchone()[0] == 3

        # La clasificación de eventualidades dejó rastro en la bitácora, sellado.
        cur.execute("select usuario, origen from turnos_auditoria where motivo like 'V006%' limit 1")
        assert cur.fetchone() == ("migracion V006", "sistema")

    with servidor.cursor() as cur:
        cur.execute(f"drop database {nombre} with (force)")
