"""Pestaña "Ajustes y auditoría" del panel de administración.

Tres secciones, cada una visible según un permiso en secrets
([super_admins.<usuario>]):

    puede_aprobar      aprobar o rechazar ajustes de otros administradores
    gestiona_periodos  cerrar y reabrir meses de nómina
    ver_auditoria      ver la comparación entre lo marcado y lo vigente, y la
                       bitácora de cambios de supervisión

Quién tiene cada permiso lo deciden los superadmins responsables del turnero,
no la aplicación. Ver migracion/README.md.

Las reglas no viven aquí. Que quien solicita un ajuste no pueda aprobarlo, que
un mes en curso no se pueda cerrar o que reabrir exija motivo lo impone la base
(migracion/versiones/V007 y V009). Esta pantalla solo las presenta y muestra el
mensaje de la base cuando rechaza algo.
"""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from core.config import MIN_JUSTIF_CHARS, SCOPE_POR_PADRON_VIGENTE
from core.data import (
    ReglaDeNegocio,
    aprobar_ajuste,
    cerrar_periodo,
    leer_ajustes,
    leer_bitacora_supervision,
    leer_diferencias_marcacion,
    leer_periodos,
    reabrir_periodo,
    rechazar_ajuste,
)
from core.employees import AREA_DE
from core.time_utils import today_ecuador
from core.ui_utils import bloquear_doble_click, set_flash

# Nombres de las clases que devuelve clase_de_ajuste() (V011).
CLASES_DE_AJUSTE = {
    "anulacion": "Anulación",
    "solo_comentario": "Solo comentario",
    "menor": "Ajuste menor (10 min o menos)",
    "suma": "Suma tiempo",
    "recorte": "Recorte (más de 10 min)",
}


def _hhmm(horas: float) -> str:
    """Horas decimales a 'H:MM', como el resto del panel."""
    minutos = int(round(abs(horas) * 60))
    return f"{'-' if horas < 0 else ''}{minutos // 60}:{minutos % 60:02d}"


_MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def permiso(admin_user: str, clave: str) -> bool:
    """Lee un permiso booleano del administrador en secrets."""
    try:
        return bool(st.secrets["super_admins"].get(admin_user, {}).get(clave, False))
    except Exception:
        return False


def _en_alcance(df: pd.DataFrame, areas_permitidas) -> pd.DataFrame:
    """Mismo alcance que el resto del panel: el área vigente del padrón si la
    persona está en él, o la del turno si ya no. Ver core/config.py."""
    if df.empty or areas_permitidas is None:
        return df
    if SCOPE_POR_PADRON_VIGENTE:
        area = df["nombre"].map(AREA_DE).fillna(df["area"])
    else:
        area = df["area"]
    return df[area.isin(areas_permitidas)]


def _nombre_mes(anio: int, mes: int) -> str:
    return f"{_MESES[int(mes)]} {int(anio)}"


# ---------------------------------------------------------------------------
# Ajustes pendientes
# ---------------------------------------------------------------------------
def contar_pendientes_para(admin_user: str, areas_permitidas) -> int:
    """Ajustes que este administrador puede resolver: de su alcance y que no
    pidió él. Para el aviso de la cabecera del panel."""
    try:
        df = _en_alcance(leer_ajustes("pendiente"), areas_permitidas)
    except Exception:
        return 0
    if df.empty:
        return 0
    return int((df["solicitado_por"].str.lower() != admin_user.lower()).sum())


def render_ajustes_pendientes(admin_user: str, areas_permitidas) -> None:
    st.markdown("#### Ajustes esperando aprobación")
    st.caption(
        "Una corrección que quita más de 10 minutos, o la anulación de un turno de "
        "trabajo, espera a que otro administrador la revise. Quien la solicita no "
        "puede aprobarla."
    )
    df = _en_alcance(leer_ajustes("pendiente"), areas_permitidas)
    if df.empty:
        st.success("No hay ajustes esperando aprobación.")
        return

    puede = permiso(admin_user, "puede_aprobar")
    propios = df[df["solicitado_por"].str.lower() == admin_user.lower()]
    ajenos = df[df["solicitado_por"].str.lower() != admin_user.lower()]

    if not propios.empty:
        st.info(f"Tienes {len(propios)} solicitud(es) tuya(s) esperando a otro administrador.")

    if ajenos.empty:
        return
    if not puede:
        st.info(f"Hay {len(ajenos)} ajuste(s) de otros administradores esperando. "
                "No tienes permiso para aprobarlos.")
        return

    for _, a in ajenos.iterrows():
        _tarjeta_ajuste(a, admin_user)


def _tarjeta_ajuste(a, admin_user: str) -> None:
    aid = int(a["id"])
    if a["tipo"] == "anulacion":
        titulo = f"Anular el turno de {a['nombre']} del {a['fecha_turno']}"
        detalle = f"Turno de {a['entrada_antes']} a {a['salida_antes'] or 'sin salida'}"
    else:
        titulo = f"Corregir el turno de {a['nombre']} del {a['fecha_turno']}"
        detalle = (f"De {a['entrada_antes']} → {a['salida_antes']} "
                   f"a {a['entrada_propuesta']} → {a['salida_propuesta']}")
    with st.container(border=True):
        st.markdown(f"**{titulo}**")
        st.write(detalle)
        c1, c2, c3 = st.columns(3)
        c1.metric("Tiempo que se quita", _hhmm(-float(a["delta_horas"])),
                  help=f"De eso, {-float(a['delta_extra']):.2f} h son horas extra.")
        c2.metric("Solicitado por", a["solicitado_por"])
        c3.metric("Solicitado el", pd.Timestamp(a["solicitado_en"]).strftime("%d/%m %H:%M"))
        st.markdown(f"Motivo: _{a['motivo']}_")

        comentario = st.text_input("Comentario de la revisión", key=f"aj_com_{aid}",
                                    placeholder="Obligatorio para rechazar")
        b1, b2 = st.columns(2)
        if b1.button("Aprobar", key=f"aj_ok_{aid}", type="primary", use_container_width=True):
            if not bloquear_doble_click(f"aj_ok_{aid}"):
                _resolver(lambda: aprobar_ajuste(aid, usuario=admin_user, comentario=comentario.strip()),
                          f"Ajuste aprobado: {a['nombre']}, {a['fecha_turno']}")
        if b2.button("Rechazar", key=f"aj_no_{aid}", use_container_width=True):
            if len(comentario.strip()) < MIN_JUSTIF_CHARS:
                st.error(f"Para rechazar, explica el motivo con al menos {MIN_JUSTIF_CHARS} caracteres.")
            elif not bloquear_doble_click(f"aj_no_{aid}"):
                _resolver(lambda: rechazar_ajuste(aid, usuario=admin_user, comentario=comentario.strip()),
                          f"Ajuste rechazado: {a['nombre']}, {a['fecha_turno']}")


def _resolver(accion, mensaje: str) -> None:
    try:
        accion()
    except ReglaDeNegocio as e:
        st.error(str(e))
        return
    set_flash(mensaje)
    st.rerun()


# ---------------------------------------------------------------------------
# Periodos de nómina
# ---------------------------------------------------------------------------
def render_periodos(admin_user: str) -> None:
    st.markdown("#### Periodos de nómina")
    st.caption(
        "Un mes cerrado no admite crear, corregir ni anular turnos. Archivar, que "
        "hace el panel solo a fin de mes, no es cerrar: solo separa el histórico. "
        "Reabrir un mes queda registrado con quién y por qué."
    )
    df = leer_periodos()
    if df.empty:
        st.info("Todavía no hay turnos registrados.")
        return

    vista = df.copy()
    vista["Mes"] = [_nombre_mes(a, m) for a, m in zip(vista["anio"], vista["mes"])]
    vista["Estado"] = vista["estado"].map({"abierto": "Abierto", "cerrado": "Cerrado"})
    vista["Último cambio"] = [
        f"{p} · {pd.Timestamp(e).strftime('%d/%m/%Y %H:%M')}" if pd.notna(e) and p else "—"
        for p, e in zip(vista["cambiado_por"], vista["cambiado_en"])
    ]
    vista["Reaperturas"] = vista["reaperturas"].astype(int)
    st.dataframe(vista[["Mes", "Estado", "Último cambio", "Reaperturas"]],
                 hide_index=True, use_container_width=True)

    if not permiso(admin_user, "gestiona_periodos"):
        return

    hoy = today_ecuador()
    opciones = {_nombre_mes(a, m): (int(a), int(m), e) for a, m, e in zip(df["anio"], df["mes"], df["estado"])}
    elegido = st.selectbox("Mes", list(opciones.keys()), key="per_mes")
    anio, mes, estado = opciones[elegido]
    motivo = st.text_input("Motivo", key="per_motivo",
                           placeholder="Obligatorio para reabrir. Ej.: corrección reportada por el jefe de área")

    if estado == "cerrado":
        if st.button(f"Reabrir {elegido}", key="per_reabrir"):
            if len(motivo.strip()) < MIN_JUSTIF_CHARS:
                st.error(f"Reabrir exige un motivo de al menos {MIN_JUSTIF_CHARS} caracteres.")
            elif not bloquear_doble_click("per_reabrir"):
                _resolver(lambda: reabrir_periodo(anio, mes, usuario=admin_user, motivo=motivo.strip()),
                          f"{elegido} reabierto. La reapertura quedó registrada.")
    else:
        termino = date(anio + (mes == 12), mes % 12 + 1, 1) <= hoy
        if not termino:
            st.caption("El mes en curso no se puede cerrar.")
        elif st.button(f"Cerrar {elegido}", key="per_cerrar", type="primary"):
            if not bloquear_doble_click("per_cerrar"):
                _resolver(lambda: cerrar_periodo(anio, mes, usuario=admin_user, motivo=motivo.strip()),
                          f"{elegido} cerrado.")


# ---------------------------------------------------------------------------
# Auditoría
# ---------------------------------------------------------------------------
def render_auditoria(areas_permitidas) -> None:
    st.markdown("#### Lo que se marcó contra lo que quedó")
    st.caption(
        "Turnos cuyo horario vigente no coincide con lo que se marcó en el reloj. "
        "La marcación se guarda aparte y no se puede editar, así que la diferencia "
        "es exactamente lo que se corrigió. Las marcaciones anteriores a la "
        "migración son reconstruidas: para turnos corregidos antes del 2 de "
        "septiembre de 2026 pueden reflejar ya el valor corregido."
    )
    desde = st.date_input("Desde la fecha de turno", value=today_ecuador() - timedelta(days=60),
                          key="aud_desde")

    dif = _en_alcance(leer_diferencias_marcacion(desde), areas_permitidas)
    if dif.empty:
        st.success("Ningún turno del periodo difiere de su marcación.")
    else:
        quitada = dif["extra_quitada"].astype(float).clip(lower=0).sum()
        c1, c2 = st.columns(2)
        c1.metric("Turnos con horario cambiado", len(dif))
        c2.metric("Horas extra quitadas", f"{quitada:.2f} h")
        st.dataframe(
            dif.rename(columns={
                "nombre": "Funcionario", "area": "Área", "fecha_turno": "Turno",
                "marco_entrada": "Marcó entrada", "marco_salida": "Marcó salida",
                "vigente_entrada": "Quedó entrada", "vigente_salida": "Quedó salida",
                "horas_marcadas": "Horas marcadas", "horas_vigentes": "Horas vigentes",
                "extra_quitada": "Extra quitada", "origen_marcacion": "Marcó",
            }),
            hide_index=True, use_container_width=True,
        )

    st.markdown("#### Ajustes solicitados")
    aj = _en_alcance(leer_ajustes("", desde), areas_permitidas)
    if aj.empty:
        st.caption("Sin ajustes en el periodo.")
    else:
        st.dataframe(
            aj.assign(clase=aj["clase"].map(CLASES_DE_AJUSTE).fillna(aj["clase"]))[
                ["nombre", "fecha_turno", "clase", "estado", "motivo", "solicitado_por",
                 "revisado_por", "delta_horas", "delta_extra", "comentario_revision"]].rename(columns={
                "nombre": "Funcionario", "fecha_turno": "Turno", "clase": "Cambio", "estado": "Estado",
                "motivo": "Motivo", "solicitado_por": "Solicitó", "revisado_por": "Revisó",
                "delta_horas": "Cambio en horas", "delta_extra": "Cambio en horas extra",
                "comentario_revision": "Comentario",
            }),
            hide_index=True, use_container_width=True,
        )

    st.markdown("#### Bitácora de cambios de supervisión")
    st.caption("Incluye las ediciones hechas directamente en la base, sin pasar por la aplicación.")
    bit = leer_bitacora_supervision(desde)
    if bit.empty:
        st.caption("Sin cambios en el periodo.")
        return
    if areas_permitidas is not None:
        bit = bit.assign(area=bit["nombre"].map(AREA_DE).fillna(""))
        bit = bit[bit["area"].isin(areas_permitidas)]
    fuera = int((bit["origen"] == "fuera_de_la_app").sum())
    if fuera:
        st.warning(f"{fuera} cambio(s) se hicieron por fuera de la aplicación, sin autor registrado.")
    st.dataframe(bit, hide_index=True, use_container_width=True)
