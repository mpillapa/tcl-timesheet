"""Las dos herramientas operativas de migracion/: el reporte y la sincronización."""

import os
import subprocess
import sys

from conftest import RAIZ, insertar_turno


def _correr(script, base, *args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               MIGRACION_DB_URL=base.servidor.url(base.nombre, "propietario"))
    return subprocess.run([sys.executable, str(RAIZ / "migracion" / script), *args],
                          capture_output=True, text=True, encoding="utf-8", env=env)


def test_el_reporte_encuentra_lo_que_las_reglas_nuevas_no_admitirian(base_b):
    # Datos como los del histórico, cargados saltando las validaciones, que es
    # como llegaron antes de V004.
    with base_b.cursor("propietario") as cur:
        cur.execute("alter table turnos disable trigger tg_turnos_30_validar")
        cur.execute("""
            insert into turnos (nombre, fecha_turno, ts_entrada, ts_salida, horas_trabajadas, estado) values
              ('Prueba Uno', '2026-09-01', '2026-09-01 08:00', '2026-09-02 21:53', 37.88, 'Completo'),
              ('Prueba Dos', '2026-09-03', '2026-09-03 08:00', '2026-09-03 17:00', 9, 'Completo'),
              ('Prueba Dos', '2026-09-03', '2026-09-03 16:00', '2026-09-03 20:00', 4, 'Completo')
        """)
        cur.execute("alter table turnos enable trigger tg_turnos_30_validar")
    insertar_turno(base_b, nombre="Persona Sin Padron", entrada="2026-09-05 08:00:00")

    res = _correr("validar_datos_existentes.py", base_b)
    assert res.returncode == 0, res.stdout + res.stderr
    salida = res.stdout
    assert "[  1] Turnos más largos que la duración máxima" in salida
    assert "[  1] Turnos de trabajo que se cruzan entre sí" in salida
    # Las tres personas de la prueba llegaron sin padrón: ninguna se sincronizó.
    assert "[  3] Personas creadas automáticamente" in salida
    assert "Persona Sin Padron" in salida


def test_el_reporte_no_escribe(base_b):
    antes = base_b.uno("select count(*) from turnos_auditoria")[0]
    _correr("validar_datos_existentes.py", base_b)
    assert base_b.uno("select count(*) from turnos_auditoria")[0] == antes


def test_la_sincronizacion_usa_el_padron_sin_guardar_el_pin(base_b, tmp_path):
    insertar_turno(base_b, nombre="Prueba Uno", entrada="2026-09-05 08:00:00")
    insertar_turno(base_b, nombre="Ex Empleado", entrada="2026-09-06 08:00:00")
    secrets = tmp_path / "secrets.toml"
    secrets.write_text(
        '[empleados]\n'
        '"1234" = {nombre = "Prueba Uno", area = "IMPORT"}\n'
        '"5678" = {nombre = "Prueba Nueva", area = "BODEGA"}\n',
        encoding="utf-8",
    )

    vista = _correr("sincronizar_empleados.py", base_b, "--secrets", str(secrets))
    assert vista.returncode == 0, vista.stdout + vista.stderr
    assert "No se aplicó nada" in vista.stdout
    assert base_b.uno("select count(*) from empleados where origen = 'padron'")[0] == 0

    res = _correr("sincronizar_empleados.py", base_b, "--secrets", str(secrets), "--aplicar")
    assert res.returncode == 0, res.stdout + res.stderr
    assert dict(base_b.todos("select nombre, area || ' ' || origen || ' ' || activo from empleados")) == {
        "Prueba Uno": "IMPORT padron true",
        "Prueba Nueva": "BODEGA padron true",
        "Ex Empleado": "BODEGA automatico false",
    }
    # El PIN no llegó a ninguna parte de la base.
    assert base_b.uno("select count(*) from empleados where nombre like '%1234%' or area like '%1234%'")[0] == 0
