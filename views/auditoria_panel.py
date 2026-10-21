"""Pestaña "Modificaciones de turnos" del panel de administración.

La ve el administrador que tiene `ver_auditoria = true` en secrets
([super_admins.<usuario>]), cada uno dentro de sus áreas. Tres secciones:

    Modificaciones de turnos     cada corrección y anulación, con el horario de
                                 antes y el de después, quién, cuándo y por qué
    Lo que se marcó contra lo    turnos cuyo horario vigente difiere de lo
    que quedó                    marcado en el reloj
    Bitácora de cambios de       toda escritura de supervisión, incluidas las
    supervisión                  hechas por fuera de la aplicación

Desde el 2026-10-21 ninguna modificación espera aprobación ni la bloquea un mes
cerrado (migracion/versiones/V012). El control es posterior: esta pestaña
muestra lo que la base registró, que no se puede editar ni borrar.
"""

from datetime import timedelta

import pandas as pd
import streamlit as st

from core.config import SCOPE_POR_PADRON_VIGENTE
from core.data import leer_ajustes, leer_bitacora_supervision, leer_diferencias_marcacion
from core.employees import AREA_DE
from core.time_utils import today_ecuador

# Nombres de las clases que devuelve clase_de_ajuste() (V011).
CLASES_DE_AJUSTE = {
    "anulacion": "Anulación",
    "solo_comentario": "Solo comentario",
    "menor": "Ajuste menor (10 min o menos)",
    "suma": "Suma tiempo",
    "recorte": "Recorte (más de 10 min)",
}

ESTADOS_DE_AJUSTE = {
    "aplicado": "Aplicado",
    "pendiente": "Pendiente",
    "rechazado": "No aplicado",
}


def _hhmm(horas) -> str:
    """Horas decimales a '+H:MM' o '-H:MM', con el signo del cambio."""
    if pd.isna(horas):
        return ""
    horas = float(horas)
    minutos = int(round(abs(horas) * 60))
    signo = "-" if horas < 0 else "+" if minutos else ""
    return f"{signo}{minutos // 60}:{minutos % 60:02d}"


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


def _horario(entrada, salida) -> str:
    if not entrada:
        return ""
    return f"{entrada} → {salida or 'sin salida'}"


def render_auditoria(areas_permitidas) -> None:
    desde = st.date_input("Desde", value=today_ecuador() - timedelta(days=60), key="aud_desde",
                          help="Para las modificaciones y la bitácora, la fecha del cambio; "
                               "para lo marcado contra lo vigente, la fecha del turno.")
    _render_modificaciones(desde, areas_permitidas)
    st.divider()
    _render_diferencias(desde, areas_permitidas)
    st.divider()
    _render_bitacora(desde, areas_permitidas)


def _render_modificaciones(desde, areas_permitidas) -> None:
    st.markdown("#### Modificaciones de turnos")
    st.caption(
        "Cada corrección y anulación hecha desde el panel, con el horario de antes y "
        "el de después. Se aplican en el acto, sin aprobación; este registro no se "
        "puede editar ni borrar."
    )
    aj = _en_alcance(leer_ajustes("", desde), areas_permitidas)
    if aj.empty:
        st.caption("Sin modificaciones en el periodo.")
        return

    aplicados = aj[aj["estado"] == "aplicado"]
    delta = aplicados["delta_horas"].astype(float)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Modificaciones", len(aplicados))
    c2.metric("Anulaciones", int((aplicados["tipo"] == "anulacion").sum()))
    c3.metric("Tiempo quitado", _hhmm(delta.clip(upper=0).sum()) or "0:00")
    c4.metric("Tiempo sumado", _hhmm(delta.clip(lower=0).sum()) or "0:00")

    vista = pd.DataFrame({
        "Modificado el": pd.to_datetime(aj["solicitado_en"]).dt.strftime("%Y-%m-%d %H:%M"),
        "Funcionario": aj["nombre"],
        "Área": aj["area"],
        "Turno": aj["fecha_turno"],
        "Cambio": aj["clase"].map(CLASES_DE_AJUSTE).fillna(aj["clase"]),
        "Antes": [_horario(e, s) for e, s in zip(aj["entrada_antes"], aj["salida_antes"])],
        "Después": [("anulado" if t == "anulacion" else _horario(e, s))
                    for t, e, s in zip(aj["tipo"], aj["entrada_propuesta"], aj["salida_propuesta"])],
        "Horas": aj["delta_horas"].map(_hhmm),
        "Horas extra": aj["delta_extra"].map(_hhmm),
        "Motivo": aj["motivo"],
        "Hecho por": aj["solicitado_por"],
    })
    # Solo aparece si hay ajustes de cuando existía la aprobación que no
    # llegaron a aplicarse; lo normal es que todos estén aplicados.
    if (aj["estado"] != "aplicado").any():
        vista["Estado"] = aj["estado"].map(ESTADOS_DE_AJUSTE).fillna(aj["estado"])
        vista["Comentario"] = aj["comentario_revision"]
    st.dataframe(vista, hide_index=True, use_container_width=True)
    st.download_button("Descargar CSV", vista.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"modificaciones_turnos_desde_{desde}.csv", mime="text/csv",
                       key="aud_csv")


def _render_diferencias(desde, areas_permitidas) -> None:
    st.markdown("#### Lo que se marcó contra lo que quedó")
    st.caption(
        "Turnos cuyo horario vigente no coincide con lo que se marcó en el reloj. "
        "La marcación se guarda aparte y no se puede editar, así que la diferencia "
        "es exactamente lo que se corrigió. Las marcaciones anteriores a la "
        "migración son reconstruidas: para turnos corregidos antes del 2 de "
        "septiembre de 2026 pueden reflejar ya el valor corregido."
    )
    dif = _en_alcance(leer_diferencias_marcacion(desde), areas_permitidas)
    if dif.empty:
        st.success("Ningún turno del periodo difiere de su marcación.")
        return
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


def _render_bitacora(desde, areas_permitidas) -> None:
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
