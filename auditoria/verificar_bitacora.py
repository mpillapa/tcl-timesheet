"""Comprueba, contra la base real, que la bitácora de auditoría funciona.

El proyecto no tiene pruebas automatizadas, así que esto sigue el patrón de
diagnostico/probar_conexion.py: un script que se corre a mano y dice sí o no.

Qué comprueba
-------------
  1. La tabla y los dos triggers existen, y la tabla está cerrada a la API REST.
  2. Un UPDATE sellado con core.data._sellar_autor queda registrado con el
     usuario y el origen correctos.
  3. delta_horas se calcula solo y con el signo esperado.
  4. Un UPDATE sin sellar queda como origen = 'fuera_de_la_app'.
  5. Un cambio que solo toca `archivado` NO genera fila, para que el cierre de
     mes no ensucie la bitácora.
  6. Un DELETE queda registrado con la fila completa en `antes`.

Sobre los datos que escribe
---------------------------
Crea un turno de prueba con un nombre reservado, lo modifica, lo borra, y al
final borra también sus filas de bitácora. No toca ningún turno real y no deja
rastro. El nombre reservado empieza por 'ZZ PRUEBA' para que sea evidente en el
panel si una corrida se interrumpiera a medias.

Aun así escribe en la base productiva. Corre esto con criterio, no en medio de
un turno de bodega.

Uso
---
    .venv\\Scripts\\python.exe auditoria\\verificar_bitacora.py
    .venv\\Scripts\\python.exe auditoria\\verificar_bitacora.py --limpiar

Con --limpiar solo borra restos de una corrida anterior y sale.
"""

import sys
import tomllib
from datetime import datetime, timedelta
from pathlib import Path

import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.data import _sellar_autor, ORIGEN_ADMIN  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
RUTA_SECRETS = RAIZ / ".streamlit" / "secrets.toml"

NOMBRE_PRUEBA = "ZZ PRUEBA AUDITORIA"
AREA_PRUEBA = "ZZ PRUEBA"
ESPERA_CONEXION_SEG = 15

_resultados = []


def afirmar(descripcion: str, condicion: bool, detalle: str = "") -> None:
    _resultados.append((descripcion, condicion, detalle))
    marca = "OK   " if condicion else "FALLA"
    print(f"  {marca}  {descripcion}")
    if detalle and not condicion:
        print(f"         {detalle}")


def motor():
    cfg = tomllib.load(open(RUTA_SECRETS, "rb"))
    return sa.create_engine(
        str(cfg["connections"]["supabase"]["url"]).strip(),
        connect_args={"sslmode": "require", "connect_timeout": ESPERA_CONEXION_SEG},
    )


def limpiar(eng) -> int:
    """Borra el turno de prueba y sus filas de bitácora. Devuelve cuánto borró."""
    with eng.begin() as conn:
        n1 = conn.execute(sa.text("delete from turnos where nombre = :n"),
                          {"n": NOMBRE_PRUEBA}).rowcount
        n2 = conn.execute(sa.text("delete from turnos_auditoria where nombre = :n"),
                          {"n": NOMBRE_PRUEBA}).rowcount
    return n1 + n2


def bitacora_de_prueba(conn) -> list:
    return list(conn.execute(sa.text(
        "select accion, usuario, origen, horas_antes, horas_despues, delta_horas,"
        "       antes ->> 'estado' as estado_antes, despues is null as sin_despues"
        "  from turnos_auditoria where nombre = :n order by id"
    ), {"n": NOMBRE_PRUEBA}))


def paso_1_estructura(conn) -> bool:
    print("\n1. Estructura")
    existe = conn.execute(sa.text(
        "select to_regclass('public.turnos_auditoria') is not null")).scalar()
    afirmar("la tabla turnos_auditoria existe", bool(existe),
            "ejecuta migracion/auditoria_schema.sql en el SQL Editor de Supabase")
    if not existe:
        return False
    triggers = conn.execute(sa.text(
        "select count(*) from pg_trigger where tgrelid = 'turnos'::regclass"
        "  and tgname like 'tg_turnos_auditoria%'")).scalar()
    afirmar("los dos triggers están creados", triggers == 2, f"encontrados: {triggers}")
    rls = conn.execute(sa.text(
        "select relrowsecurity from pg_class where oid = 'turnos_auditoria'::regclass")).scalar()
    afirmar("RLS activo, la tabla no se expone por la API REST", bool(rls))
    politicas = conn.execute(sa.text(
        "select count(*) from pg_policy where polrelid = 'turnos_auditoria'::regclass")).scalar()
    afirmar("sin políticas de RLS, o sea cerrada del todo", politicas == 0)
    return True


def paso_2_ciclo(eng) -> None:
    ts_ent = datetime(2000, 1, 1, 8, 0, 0)      # fecha imposible, no aparece en reportes
    ts_sal = ts_ent + timedelta(hours=12)

    print("\n2. Alta del turno de prueba")
    with eng.begin() as conn:
        conn.execute(sa.text(
            "insert into turnos (nombre, area, fecha_turno, ts_entrada, ts_salida,"
            "                    horas_trabajadas, horas_efectivas, horas_extra, estado)"
            " values (:n, :a, :f, :e, :s, 12.00, 11.00, 3.00, 'Completo')"
        ), {"n": NOMBRE_PRUEBA, "a": AREA_PRUEBA, "f": ts_ent.date(),
            "e": ts_ent, "s": ts_sal})
    with eng.connect() as conn:
        afirmar("el INSERT no genera fila de bitácora", len(bitacora_de_prueba(conn)) == 0)

    print("\n3. UPDATE sellado, como una corrección del panel")
    with eng.begin() as conn:
        _sellar_autor(conn, "usuario_de_prueba", ORIGEN_ADMIN)
        conn.execute(sa.text(
            "update turnos set horas_trabajadas = 9.00, horas_efectivas = 8.00,"
            "                  horas_extra = 0.00, observaciones = 'prueba de bitacora',"
            "                  actualizado_en = now()"
            " where nombre = :n"), {"n": NOMBRE_PRUEBA})
    with eng.connect() as conn:
        filas = bitacora_de_prueba(conn)
        afirmar("el UPDATE genera exactamente una fila", len(filas) == 1, f"{len(filas)} filas")
        if filas:
            f = filas[0]
            afirmar("la acción es UPDATE", f.accion == "UPDATE", f.accion)
            afirmar("guarda el usuario que sella", f.usuario == "usuario_de_prueba", f.usuario)
            afirmar("guarda origen = admin", f.origen == ORIGEN_ADMIN, f.origen)
            afirmar("horas_antes es el valor previo (12.00)", float(f.horas_antes) == 12.00,
                    str(f.horas_antes))
            afirmar("horas_despues es el nuevo (9.00)", float(f.horas_despues) == 9.00,
                    str(f.horas_despues))
            afirmar("delta_horas se calcula solo y es -3.00", float(f.delta_horas) == -3.00,
                    str(f.delta_horas))

    print("\n4. UPDATE sin sellar, como una edición directa en la base")
    with eng.begin() as conn:
        conn.execute(sa.text(
            "update turnos set observaciones = 'editado por fuera' where nombre = :n"),
            {"n": NOMBRE_PRUEBA})
    with eng.connect() as conn:
        filas = bitacora_de_prueba(conn)
        afirmar("queda registrado igual", len(filas) == 2, f"{len(filas)} filas")
        if len(filas) >= 2:
            afirmar("con origen = fuera_de_la_app",
                    filas[1].origen == "fuera_de_la_app", filas[1].origen)
            afirmar("y sin usuario", filas[1].usuario == "", repr(filas[1].usuario))

    print("\n5. Cambio de solo `archivado`, como el cierre de mes")
    with eng.begin() as conn:
        conn.execute(sa.text(
            "update turnos set archivado = true, actualizado_en = now() where nombre = :n"),
            {"n": NOMBRE_PRUEBA})
    with eng.connect() as conn:
        filas = bitacora_de_prueba(conn)
        afirmar("no genera fila, la bitácora sigue en 2", len(filas) == 2, f"{len(filas)} filas")

    print("\n6. DELETE")
    with eng.begin() as conn:
        _sellar_autor(conn, "usuario_de_prueba", ORIGEN_ADMIN)
        conn.execute(sa.text("delete from turnos where nombre = :n"), {"n": NOMBRE_PRUEBA})
    with eng.connect() as conn:
        filas = bitacora_de_prueba(conn)
        afirmar("el DELETE genera fila", len(filas) == 3, f"{len(filas)} filas")
        if len(filas) >= 3:
            f = filas[2]
            afirmar("con acción DELETE", f.accion == "DELETE", f.accion)
            afirmar("con el autor del borrado", f.usuario == "usuario_de_prueba", f.usuario)
            afirmar("`despues` viene vacío", bool(f.sin_despues))
            afirmar("`antes` conserva la fila completa", f.estado_antes == "Completo",
                    str(f.estado_antes))
        sigue = conn.execute(sa.text("select count(*) from turnos where nombre = :n"),
                             {"n": NOMBRE_PRUEBA}).scalar()
        afirmar("el turno ya no está en turnos", sigue == 0)
        afirmar("pero su historia sí sigue en la bitácora", len(filas) == 3)


def main() -> None:
    eng = motor()
    try:
        if "--limpiar" in sys.argv:
            print(f"Borradas {limpiar(eng)} filas de prueba.")
            return

        print("Verificación de la bitácora de auditoría")
        print(f"Turno de prueba: {NOMBRE_PRUEBA!r}. Se borra al terminar.")
        limpiar(eng)  # restos de una corrida anterior

        with eng.connect() as conn:
            if not paso_1_estructura(conn):
                return
        paso_2_ciclo(eng)
    finally:
        try:
            limpiar(eng)
            print("\nDatos de prueba borrados.")
        finally:
            eng.dispose()

    fallos = [d for d, ok, _ in _resultados if not ok]
    print(f"\n{len(_resultados) - len(fallos)} de {len(_resultados)} comprobaciones pasan.")
    if fallos:
        print("Fallan:")
        for d in fallos:
            print(f"  - {d}")
        sys.exit(1)
    print("La bitácora registra los cambios como se espera.")


if __name__ == "__main__":
    main()
