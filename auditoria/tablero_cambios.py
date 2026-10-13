"""Genera el tablero de cambios de horas: un HTML que se abre en el navegador.

Para responder, turno por turno, qué horas cambiaron, a quién y quién lo hizo.
Es la versión navegable de analisis_mensual.py, pensada para explorar una
sospecha concreta sobre una persona o un administrador en vez de leer un total.

Tres piezas, separadas a propósito:

    consultas_cambios.py     acceso a datos y la regla que decide qué costó
                             horas extra
    plantilla_tablero.html   la interfaz, sin datos dentro
    tablero_cambios.py       este archivo: une las dos y escribe el resultado

El HTML sale autocontenido, con los datos incrustados, de modo que se abre con
doble clic y no necesita servidor ni conexión. Va a auditoria/salida/, carpeta
ignorada por git porque lleva nombres de personas y señalamientos sobre quién
les tocó las horas. Si hay que compartirlo, es una decisión consciente de quien
lo genera, no algo que ocurra por estar el archivo en el repositorio.

Uso
---
    .venv\\Scripts\\python.exe auditoria\\tablero_cambios.py
    .venv\\Scripts\\python.exe auditoria\\tablero_cambios.py --desde 2026-09-01
    .venv\\Scripts\\python.exe auditoria\\tablero_cambios.py --json

--desde acota el contexto de turnos que sirve de denominador; los cambios se
traen siempre completos, porque la bitácora entera son unos pocos cientos de
filas. --json escribe solo los datos, para revisarlos sin abrir el navegador.

No imprime la cadena de conexión ni la contraseña.
"""

import json
import sys
import webbrowser
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from bitacora import motor, tabla_existe, DIR_SALIDA
from consultas_cambios import (HORAS_BASE_TURNO, UMBRAL_MARCA_MOVIDA_MIN,
                               UMBRAL_RUIDO_H, clasificar, leer_todo,
                               minutos_movidos, texto_hora_local, tiene_motivo)

AQUI = Path(__file__).resolve().parent
RUTA_PLANTILLA = AQUI / "plantilla_tablero.html"
MARCADOR = "/*DATOS*/"

# Desde dónde traer el contexto de turnos. La bitácora arranca el 2026-09-02, de
# modo que no tiene sentido pedir meses anteriores: no hay cambios que mostrar
# contra ellos.
DESDE_POR_DEFECTO = "2026-09-01"


def serializar(valor):
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    raise TypeError(f"No se puede serializar {type(valor)}")


def preparar(crudo: dict) -> dict:
    """Clasifica cada turno intervenido y arma la estructura que lee la página.

    La clasificación se hace aquí y no en JavaScript para que la regla viva en
    un solo sitio y sea revisable por alguien que no lea el navegador. Ver
    consultas_cambios.clasificar.
    """
    turnos = []
    for fila in crudo["turnos"]:
        clase = clasificar(fila)
        turnos.append({
            "id": fila["turno_id"],
            "nombre": fila["nombre"],
            "area": fila["area"] or "",
            "fecha": fila["fecha_turno"],
            "mes": str(fila["fecha_turno"])[:7] if fila["fecha_turno"] else "",
            "admin": fila["usuarios"] or "(sin autor)",
            "cambiado": texto_hora_local(fila["ultimo_cambio"]),
            "intervenciones": fila["intervenciones"],
            "h_antes": fila["h_antes"],
            "h_despues": fila["h_despues"],
            "e_antes": fila["e_antes"],
            "e_despues": fila["e_despues"],
            "horas_hoy": fila["horas_hoy"],
            "extra_hoy": fila["extra_hoy"],
            "turnos_hoy": fila["turnos_hoy"],
            # Normalizados a hora de Ecuador sin zona: la bitácora mezcla los
            # dos formatos desde V102. Ver consultas_cambios.a_hora_local.
            "entrada_antes": texto_hora_local(fila["entrada_antes"]),
            "entrada_despues": texto_hora_local(fila["entrada_despues"]),
            "salida_antes": texto_hora_local(fila["salida_antes"]),
            "salida_despues": texto_hora_local(fila["salida_despues"]),
            "estado_antes": fila["estado_antes"],
            "estado_despues": fila["estado_despues"],
            "motivo": _motivo(fila["obs_despues"]),
            "con_motivo": tiene_motivo(fila["obs_despues"]),
            "mes_cerrado": bool(fila["mes_ya_cerrado"]),
            "entrada_movida": minutos_movidos(fila["entrada_antes"], fila["entrada_despues"]),
            "salida_movida": minutos_movidos(fila["salida_antes"], fila["salida_despues"]),
            **clase,
        })

    secuencia = {}
    for fila in crudo["secuencia"]:
        secuencia.setdefault(str(fila["turno_id"]), []).append({
            "momento": texto_hora_local(fila["momento"]),
            "usuario": fila["usuario"] or "(sin autor)",
            "origen": fila["origen"],
            "accion": fila["accion"],
            "h_antes": fila["horas_antes"],
            "h_despues": fila["horas_despues"],
            "salida_antes": texto_hora_local(fila["salida_antes"]),
            "salida_despues": texto_hora_local(fila["salida_despues"]),
            "estado_antes": fila["estado_antes"],
            "estado_despues": fila["estado_despues"],
        })

    return {
        "turnos": turnos,
        "secuencia": secuencia,
        "contexto": crudo["contexto"],
        "cobertura": crudo["cobertura"],
        "generado": datetime.now().isoformat(timespec="minutes"),
        "jornada_base": HORAS_BASE_TURNO,
        "umbral_ruido": UMBRAL_RUIDO_H,
        "umbral_marca_movida": UMBRAL_MARCA_MOVIDA_MIN,
    }


def _motivo(obs) -> str:
    """Saca el motivo que escribió el administrador, sin el resto del historial.

    La observación resultante encadena el tag de corrección y la observación
    previa del colaborador separadas por ` | `. Interesa solo el primer tramo.
    """
    if not obs:
        return ""
    primero = obs.split(" | ")[0]
    if "]" in primero:
        cola = primero.split("]", 1)[1].lstrip(": ").strip()
        return cola
    return primero.strip()


def main() -> None:
    args = sys.argv[1:]
    desde = args[args.index("--desde") + 1] if "--desde" in args else DESDE_POR_DEFECTO
    solo_json = "--json" in args

    eng = motor()
    try:
        with eng.connect() as conn:
            if not tabla_existe(conn):
                print("La tabla turnos_auditoria no existe todavía.\n"
                      "Ejecuta migracion/auditoria_schema.sql en el SQL Editor de Supabase.")
                return
            datos = preparar(leer_todo(conn, desde))
    finally:
        eng.dispose()

    DIR_SALIDA.mkdir(exist_ok=True)
    crudo = json.dumps(datos, default=serializar, ensure_ascii=False)
    # Una observación que contenga `</script>` cerraría la etiqueta antes de
    # tiempo y rompería la página. Escapar `<` lo evita sin cambiar el valor:
    # JavaScript lee < como el mismo carácter.
    crudo_html = crudo.replace("<", "\\u003c")

    if solo_json:
        destino = DIR_SALIDA / "tablero_cambios.json"
        destino.write_text(crudo, encoding="utf-8")
        print(f"Datos en: {destino}")
        return

    plantilla = RUTA_PLANTILLA.read_text(encoding="utf-8")
    if MARCADOR not in plantilla:
        raise SystemExit(f"La plantilla no tiene el marcador {MARCADOR}")

    destino = DIR_SALIDA / "tablero_cambios.html"
    destino.write_text(plantilla.replace(MARCADOR, crudo_html), encoding="utf-8")

    print(f"Tablero en: {destino}")
    print(f"{len(datos['turnos'])} turnos intervenidos, "
          f"{len(datos['secuencia'])} con historial completo.")
    webbrowser.open(destino.as_uri())


if __name__ == "__main__":
    main()
