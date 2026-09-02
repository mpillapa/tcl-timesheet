"""Reconstruye el antes y el después de cada corrección, usando el historial de
versiones del Google Sheet espejo.

Por qué funciona
----------------
La base no guarda el valor anterior de un turno corregido (ver
auditoria/README.md). El Sheet espejo sí deja rastro, porque Drive conserva una
revisión del archivo por cada tanda de escrituras, y la API de Drive permite
exportar cualquier revisión pasada a CSV.

El tag que la app escribe en Observaciones es el marcador de la corrección, así
que la revisión "después" es la primera en la que ese tag aparece y la "antes"
es la inmediatamente anterior. Se localiza por búsqueda binaria sobre las
revisiones de esa fecha, lo que da el valor real de antes y de después sin
adivinar la hora.

Comparar contra el cierre del día anterior, que fue el primer intento, no sirve:
en los turnos nocturnos la salida se marca a la mañana siguiente, el mismo día
en que se corrige, de modo que a esa hora el turno todavía estaba abierto y sin
horas.

Cómo empareja las filas
-----------------------
La clave natural de la app es Nombre más Timestamp Entrada, pero la corrección
puede mover justamente el timestamp, de modo que aquí se empareja por Nombre más
Fecha de Turno. Se comprobó que entre los turnos corregidos no hay ninguna
persona con dos turnos la misma fecha, así que la clave es única para este uso.
Si en el futuro apareciera un caso repetido, el script lo marca en vez de
adivinar.

Busca en las hojas Registros e Historico de cada revisión, porque un turno
corregido en el mes activo pasa a Historico cuando se cierra el mes.

Cobertura, y es una limitación importante
-----------------------------------------
El historial de Drive de este archivo arranca alrededor del 2026-07-09 y la
última escritura del espejo es del 2026-08-26. Fuera de esa ventana no hay nada
que comparar, y el borde inicial se mueve entre consultas porque Google
consolida y purga el historial por su cuenta. El script informa siempre de los
tres tramos con los números del momento.

Que el espejo llevara una semana sin escribir es un hallazgo aparte, no un
supuesto de este script: los secretos de Streamlit Cloud son independientes del
secrets.toml local y `core.data._espejo` se traga los fallos del respaldo a
propósito, para que nunca bloqueen una marcación.

Uso
---
    .venv\\Scripts\\python.exe auditoria\\comparar_con_sheets.py gproanio
    .venv\\Scripts\\python.exe auditoria\\comparar_con_sheets.py gproanio --csv
    .venv\\Scripts\\python.exe auditoria\\comparar_con_sheets.py gproanio --limite 10

Las revisiones descargadas quedan en auditoria/salida/revisiones/ como caché, de
modo que una segunda corrida no vuelve a bajarlas. Esa carpeta está ignorada por
git porque contiene datos de personas. No imprime credenciales.
"""

import csv
import re
import sys
import time
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import sqlalchemy as sa
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.normalizacion import _normalizar_cmp, _normalizar_texto  # noqa: E402
from core.time_utils import ECUADOR_OFFSET, parse_fecha_flexible  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
RUTA_SECRETS = RAIZ / ".streamlit" / "secrets.toml"
DIR_SALIDA = Path(__file__).resolve().parent / "salida"
DIR_CACHE = DIR_SALIDA / "revisiones"

HOJAS = ("Registros", "Historico")
ESPERA_CONEXION_SEG = 15

# El endpoint de exportación de Sheets limita por ráfaga. Con una pausa entre
# descargas y espera creciente al 429 la corrida termina sin quedarse a medias.
PAUSA_ENTRE_DESCARGAS_SEG = 2.0
ESPERAS_REINTENTO = (5, 15, 45, 90)

_SA_KEYS = {
    "type", "project_id", "private_key_id", "private_key",
    "client_email", "client_id", "auth_uri", "token_uri",
    "auth_provider_x509_cert_url", "client_x509_cert_url", "universe_domain",
}


# --- Secretos ---------------------------------------------------------------

def leer_secrets() -> dict:
    return tomllib.load(open(RUTA_SECRETS, "rb"))


def url_postgres(cfg: dict) -> str:
    return str(cfg["connections"]["supabase"]["url"]).strip()


def id_spreadsheet(cfg: dict) -> str:
    ref = str(cfg["connections"]["gsheets"]["spreadsheet"])
    m = re.search(r"/d/([a-zA-Z0-9-_]+)", ref)
    return m.group(1) if m else ref


def sesion_drive(cfg: dict) -> AuthorizedSession:
    """Sesión autenticada con la misma cuenta de servicio del espejo. Necesita
    el scope drive para leer revisiones; gspread ya lo usa para escribir."""
    gs = cfg["connections"]["gsheets"]
    sa_info = {k: v for k, v in gs.items() if k in _SA_KEYS}
    creds = Credentials.from_service_account_info(
        sa_info, scopes=["https://www.googleapis.com/auth/drive"]
    )
    return AuthorizedSession(creds)


# --- Acceso a Drive ---------------------------------------------------------

def listar_revisiones(sesion, file_id: str) -> list:
    """[(rev_id, datetime UTC)] ordenado de la más antigua a la más nueva."""
    revs, token = [], None
    while True:
        params = {"pageSize": 1000,
                  "fields": "revisions(id,modifiedTime),nextPageToken"}
        if token:
            params["pageToken"] = token
        r = sesion.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}/revisions",
            params=params, timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        for v in data.get("revisions", []):
            revs.append((v["id"],
                         datetime.fromisoformat(v["modifiedTime"].replace("Z", "+00:00"))))
        token = data.get("nextPageToken")
        if not token:
            break
    revs.sort(key=lambda x: x[1])
    return revs


def gids_de_hojas(sesion, file_id: str) -> dict:
    r = sesion.get(f"https://sheets.googleapis.com/v4/spreadsheets/{file_id}",
                   params={"fields": "sheets(properties(sheetId,title))"}, timeout=60)
    r.raise_for_status()
    return {s["properties"]["title"]: s["properties"]["sheetId"]
            for s in r.json()["sheets"]}


def _exportar_con_reintento(sesion, url: str, params: dict) -> bytes:
    """El endpoint de exportación de Sheets no es una API formal y responde 429
    con facilidad. Se espacian las peticiones y se reintenta con espera
    creciente, porque el alternativo es una corrida a medias."""
    for intento, espera in enumerate(ESPERAS_REINTENTO, 1):
        r = sesion.get(url, params=params, timeout=120)
        if r.status_code == 200:
            return r.content
        if r.status_code not in (429, 500, 502, 503, 504):
            r.raise_for_status()
        print(f"      {r.status_code} de Google, reintento {intento} en {espera}s",
              flush=True)
        time.sleep(espera)
    r = sesion.get(url, params=params, timeout=120)
    r.raise_for_status()
    return r.content


def descargar_hoja(sesion, file_id: str, rev_id: str, hoja: str, gid: int) -> pd.DataFrame:
    """Exporta una hoja de una revisión concreta a CSV, con caché en disco."""
    DIR_CACHE.mkdir(parents=True, exist_ok=True)
    destino = DIR_CACHE / f"rev{rev_id}_{hoja}.csv"
    if not destino.exists():
        contenido = _exportar_con_reintento(
            sesion,
            f"https://docs.google.com/spreadsheets/d/{file_id}/export",
            {"format": "csv", "gid": gid, "revision": rev_id},
        )
        destino.write_bytes(contenido)
        time.sleep(PAUSA_ENTRE_DESCARGAS_SEG)
    try:
        return pd.read_csv(destino, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


# --- Índice de filas por revisión -------------------------------------------
# Una revisión se indexa una sola vez y se guarda en memoria, porque la búsqueda
# binaria del apartado siguiente vuelve sobre las mismas revisiones.

_indices = {}


def _clave(nombre, fecha_turno) -> tuple:
    f = parse_fecha_flexible(fecha_turno)
    return (_normalizar_cmp(nombre), f.isoformat() if f else "")


def indice_hoja(sesion, file_id: str, rev_id: str, hoja: str, gids: dict) -> dict:
    """{(nombre_cmp, fecha_iso): fila} de una hoja de una revisión. El valor es
    None si la clave aparece más de una vez, para no adivinar."""
    memo = (rev_id, hoja)
    if memo in _indices:
        return _indices[memo]
    filas = {}
    if hoja in gids:
        df = descargar_hoja(sesion, file_id, rev_id, hoja, gids[hoja])
        if not df.empty and "Nombre" in df.columns:
            for fila in df.to_dict("records"):
                k = _clave(fila.get("Nombre"), fila.get("Fecha de Turno"))
                if not k[0] or not k[1]:
                    continue
                filas[k] = None if k in filas else fila
    _indices[memo] = filas
    return filas


def buscar_fila(sesion, file_id: str, rev_id: str, gids: dict, clave: tuple):
    """(fila, nota). Mira Registros primero y solo baja Historico si hace falta,
    que es la mitad de descargas contra un endpoint que limita por ráfaga."""
    ambigua = False
    for hoja in HOJAS:
        idx = indice_hoja(sesion, file_id, rev_id, hoja, gids)
        if clave in idx:
            if idx[clave] is None:
                ambigua = True
                continue
            return idx[clave], ""
    if ambigua:
        return None, "clave ambigua, dos turnos la misma fecha"
    return None, "el turno no estaba en esa versión"


# --- Localización exacta de la corrección -----------------------------------
# El tag que escribe la app en Observaciones es el marcador de la corrección, de
# modo que no hace falta adivinar por hora: la revisión "después" es la primera
# en la que ese tag aparece, y la "antes" es la inmediatamente anterior.
#
# Comparar contra el cierre del día anterior, que fue el primer intento, falla
# con los turnos nocturnos: a esa hora el turno todavía estaba abierto y sin
# horas, porque la salida se marca a la mañana siguiente, el mismo día en que se
# corrige.


def patron_tag(usuario: str):
    return re.compile(r"\[correcci.n[^\]]*por " + re.escape(usuario.casefold()) + r"\]")


def tiene_tag(fila, rx) -> bool:
    return bool(rx.search(_normalizar_cmp((fila or {}).get("Observaciones", ""))))


def indice_primera_con_tag(sesion, file_id: str, gids: dict, ventana: list,
                           clave: tuple, rx):
    """Posición dentro de `ventana` de la primera revisión que ya trae el tag, o
    None si ninguna lo trae. Búsqueda binaria: el tag, una vez escrito, no
    desaparece, así que la condición es monótona dentro de la ventana."""
    if not ventana:
        return None
    fila, _ = buscar_fila(sesion, file_id, ventana[-1][0], gids, clave)
    if not tiene_tag(fila, rx):
        return None
    lo, hi = 0, len(ventana) - 1
    while lo < hi:
        mitad = (lo + hi) // 2
        fila, _ = buscar_fila(sesion, file_id, ventana[mitad][0], gids, clave)
        if tiene_tag(fila, rx):
            hi = mitad
        else:
            lo = mitad + 1
    return lo



# --- Correcciones desde la base ---------------------------------------------

SQL_CORRECCIONES = """
select id, nombre, area, fecha_turno,
       to_char(ts_entrada, 'YYYY-MM-DD HH24:MI:SS') as ts_entrada,
       to_char(ts_salida,  'YYYY-MM-DD HH24:MI:SS') as ts_salida,
       horas_trabajadas, horas_extra, observaciones,
       substring(observaciones from '\\[Correcci[^0-9]*(\\d{4}-\\d{2}-\\d{2})')::date as fecha_correccion
from turnos
where observaciones ilike '%[Correcci%por ' || :usuario || ']%'
order by fecha_turno
"""


def leer_correcciones(cfg: dict, usuario: str) -> list:
    eng = sa.create_engine(
        url_postgres(cfg),
        connect_args={"sslmode": "require", "connect_timeout": ESPERA_CONEXION_SEG},
    )
    try:
        with eng.connect() as conn:
            res = conn.execute(sa.text(SQL_CORRECCIONES), {"usuario": usuario})
            return [dict(r._mapping) for r in res]
    finally:
        eng.dispose()


def motivo(observaciones: str) -> str:
    m = re.search(r"\]: ([^|]{0,70})", observaciones or "")
    return _normalizar_texto(m.group(1)) if m else ""


# --- Comparación ------------------------------------------------------------

def a_float(v):
    try:
        s = str(v).strip().replace(",", ".")
        return float(s) if s else None
    except (TypeError, ValueError):
        return None


def comparar(correcciones: list, sesion, file_id: str, gids: dict, revs: list,
             usuario: str, limite: int = 0) -> tuple:
    """Devuelve (resultados, fuera_de_rango). Para cada corrección localiza la
    primera revisión que ya trae el tag y compara contra la anterior."""
    if not revs:
        return [], correcciones

    rx = patron_tag(usuario)
    inicio_cobertura, fin_cobertura = revs[0][1], revs[-1][1]
    resultados, fuera, dentro = [], [], []

    for c in correcciones:
        d = c["fecha_correccion"]
        if d is None:
            c["_motivo_fuera"] = "sin fecha legible en el tag"
            fuera.append(c)
            continue
        # El tag solo trae la fecha. El día en Ecuador va de 05:00Z a 05:00Z, y
        # se deja un día de margen por si Google fecha la revisión más tarde.
        ini = datetime.combine(d, datetime.min.time(), ECUADOR_OFFSET).astimezone(timezone.utc)
        fin = ini + timedelta(days=2)
        if fin <= inicio_cobertura:
            c["_motivo_fuera"] = f"anterior al inicio del historial ({inicio_cobertura.date()})"
            fuera.append(c)
        elif ini > fin_cobertura:
            c["_motivo_fuera"] = f"posterior a la última escritura del espejo ({fin_cobertura.date()})"
            fuera.append(c)
        else:
            dentro.append((c, ini, fin))

    if limite:
        dentro = dentro[:limite]

    print(f"  correcciones a reconstruir: {len(dentro)}")
    for n, (c, ini, fin) in enumerate(dentro, 1):
        clave = _clave(c["nombre"], c["fecha_turno"])
        # Índices globales de la ventana, para poder retroceder una revisión
        # más allá de su borde cuando la corrección es la primera del día.
        pos = [i for i, r in enumerate(revs) if ini <= r[1] < fin]
        ventana = [revs[i] for i in pos]
        j = indice_primera_con_tag(sesion, file_id, gids, ventana, clave, rx)

        if j is None:
            nota = "no se encontró el tag en las revisiones de esa fecha"
            fila_antes = fila_despues = None
            rev_antes = rev_despues = None
        else:
            g = pos[j]
            rev_despues = revs[g]
            rev_antes = revs[g - 1] if g > 0 else None
            fila_despues, nota_d = buscar_fila(sesion, file_id, rev_despues[0], gids, clave)
            if rev_antes:
                fila_antes, nota_a = buscar_fila(sesion, file_id, rev_antes[0], gids, clave)
            else:
                fila_antes, nota_a = None, "no hay revisión anterior en el historial"
            nota = " / ".join(x for x in (nota_a, nota_d) if x)

        h_antes = a_float((fila_antes or {}).get("Horas Trabajadas"))
        h_despues = a_float((fila_despues or {}).get("Horas Trabajadas"))

        # Un turno sin horas en la revisión previa significa que la salida y la
        # corrección cayeron en la misma tanda de escrituras, y entonces el
        # Sheet no llegó a guardar un estado intermedio.
        if fila_antes is not None and h_antes is None:
            nota = "la salida y la corrección cayeron en la misma revisión"

        resultados.append({
            "id": c["id"],
            "nombre": c["nombre"],
            "fecha_turno": c["fecha_turno"],
            "fecha_correccion": c["fecha_correccion"],
            "rev_antes": rev_antes[0] if rev_antes else "",
            "rev_despues": rev_despues[0] if rev_despues else "",
            "momento_correccion_utc": (rev_despues[1].isoformat() if rev_despues else ""),
            "entrada_antes": (fila_antes or {}).get("Timestamp Entrada", ""),
            "entrada_despues": (fila_despues or {}).get("Timestamp Entrada", ""),
            "entrada_hoy": c["ts_entrada"] or "",
            "salida_antes": (fila_antes or {}).get("Timestamp Salida", ""),
            "salida_despues": (fila_despues or {}).get("Timestamp Salida", ""),
            "salida_hoy": c["ts_salida"] or "",
            "horas_antes": h_antes,
            "horas_despues": h_despues,
            "horas_hoy": a_float(c["horas_trabajadas"]),
            "delta": (round(h_despues - h_antes, 2)
                      if h_antes is not None and h_despues is not None else None),
            "nota": nota,
            "motivo": motivo(c["observaciones"]),
        })
        if n % 10 == 0 or n == len(dentro):
            print(f"    {n}/{len(dentro)}  revisiones en caché: {len(_indices)//2}",
                  flush=True)
    return resultados, fuera



# --- Presentación -----------------------------------------------------------

def imprimir(resultados: list, fuera: list, usuario: str) -> None:
    print(f"\n== Correcciones de {usuario} fuera de la cobertura del historial ==")
    if not fuera:
        print("  (ninguna)")
    else:
        agrupado = {}
        for c in fuera:
            agrupado.setdefault(c["_motivo_fuera"], []).append(c)
        for m, lista in sorted(agrupado.items()):
            print(f"  {len(lista):>4}  {m}")

    print(f"\n== Reconstruidas desde el Sheet: {len(resultados)} ==")
    con_delta = [r for r in resultados if r["delta"] is not None]
    cambiadas = [r for r in con_delta if abs(r["delta"]) >= 0.01]
    sin_dato = [r for r in resultados if r["delta"] is None]

    print(f"  con antes y después legibles : {len(con_delta)}")
    print(f"  con las horas cambiadas      : {len(cambiadas)}")
    print(f"  solo cambió la observación   : {len(con_delta) - len(cambiadas)}")
    print(f"  sin dato suficiente          : {len(sin_dato)}")
    if cambiadas:
        suma = round(sum(r["delta"] for r in cambiadas), 2)
        recortes = [r for r in cambiadas if r["delta"] < 0]
        aumentos = [r for r in cambiadas if r["delta"] > 0]
        print(f"\n  variación neta de horas      : {suma:+.2f} h")
        print(f"  turnos con horas a la baja   : {len(recortes)}  "
              f"({round(sum(r['delta'] for r in recortes), 2):+.2f} h)")
        print(f"  turnos con horas al alza     : {len(aumentos)}  "
              f"({round(sum(r['delta'] for r in aumentos), 2):+.2f} h)")

        print("\n  Detalle de los turnos con horas cambiadas:")
        cab = f"  {'funcionario':<19}{'turno':<12}{'corregido':<12}{'antes':>7}{'después':>9}{'delta':>8}   motivo"
        print(cab)
        print("  " + "-" * (len(cab) - 2))
        for r in sorted(cambiadas, key=lambda x: x["delta"]):
            print(f"  {str(r['nombre'])[:18]:<19}{str(r['fecha_turno']):<12}"
                  f"{str(r['fecha_correccion']):<12}{r['horas_antes']:>7.2f}"
                  f"{r['horas_despues']:>9.2f}{r['delta']:>+8.2f}   {r['motivo'][:40]}")

    if sin_dato:
        print("\n  Sin dato suficiente:")
        for r in sin_dato:
            print(f"  {str(r['nombre'])[:18]:<19}{str(r['fecha_turno']):<12}"
                  f"{str(r['fecha_correccion']):<12}  {r['nota']}")


def volcar_csv(resultados: list, usuario: str) -> Path:
    DIR_SALIDA.mkdir(exist_ok=True)
    destino = DIR_SALIDA / f"antes_despues_{usuario}.csv"
    if not resultados:
        return destino
    with open(destino, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(resultados[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(resultados)
    return destino


def main() -> None:
    args = sys.argv[1:]
    usuario = next((a for a in args if not a.startswith("--")), None)
    if not usuario:
        print(__doc__.split("Uso\n---")[1].strip())
        return
    quiere_csv = "--csv" in args
    limite = 0
    if "--limite" in args:
        limite = int(args[args.index("--limite") + 1])

    cfg = leer_secrets()
    file_id = id_spreadsheet(cfg)
    sesion = sesion_drive(cfg)

    print("Leyendo correcciones de la base...")
    correcciones = leer_correcciones(cfg, usuario)
    print(f"  {len(correcciones)} correcciones con el tag de {usuario}")

    print("Leyendo historial de versiones del Sheet...")
    revs = listar_revisiones(sesion, file_id)
    if revs:
        print(f"  {len(revs)} revisiones, de {revs[0][1].date()} a {revs[-1][1].date()}")
    gids = gids_de_hojas(sesion, file_id)

    resultados, fuera = comparar(correcciones, sesion, file_id, gids, revs, usuario, limite)
    imprimir(resultados, fuera, usuario)

    if quiere_csv:
        destino = volcar_csv(resultados, usuario)
        print(f"\nDetalle completo en: {destino}")


if __name__ == "__main__":
    main()
