"""Quiosco de marcación del colaborador.

Expone una sola acción a la vez según el estado real del turno, para eliminar
de raíz las marcaciones equivocadas: con ambos botones visibles, el error se
descubría después del clic.
"""

import time

import streamlit as st

from core.auth import logout
from core.config import UMBRAL_OLVIDO_H
from core.data import leer_registros, buscar_turno_abierto_idx
from core.marcado import (
    AUTO_LOGOUT_SECONDS,
    marcar_entrada,
    marcar_salida,
    render_formulario_justificacion,
)
from core.time_utils import now_ecuador, parse_timestamp_flexible
from core.ui_theme import inject_kiosk_css

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _fmt_hm(horas: float) -> str:
    total_min = int(round(horas * 60))
    h, m = divmod(total_min, 60)
    return f"{h}h {m:02d}min"


@st.dialog("Turno enviado a revisión")
def _dialogo_revision(info: dict) -> None:
    st.warning(
        f"El turno abierto desde {info['entrada']} lleva "
        f"{info['horas']:.1f} h (más de {UMBRAL_OLVIDO_H} h)."
    )
    st.write(
        "Por superar las horas permitidas se envió a **revisión de tu supervisor** "
        "y **no se registró la salida**. "
        "Puedes marcar tu **entrada** normalmente para iniciar tu jornada de hoy."
    )
    if st.button("Entendido", type="primary", use_container_width=True):
        st.session_state.pop("aviso_revision", None)
        st.rerun()


def _procesar_auto_logout() -> None:
    started_at = st.session_state.get("auto_logout_started_at")
    if started_at is None:
        return

    elapsed = time.time() - float(started_at)
    remaining = AUTO_LOGOUT_SECONDS - elapsed
    if remaining <= 0:
        logout()
        st.rerun()

    st.progress(
        min(1.0, elapsed / AUTO_LOGOUT_SECONDS),
        text=f"Cerrando sesión automáticamente en {int(remaining) + 1} s…",
    )
    time.sleep(1)
    st.rerun()


def _render_header(usuario: str, area: str) -> None:
    iniciales = "".join(p[0] for p in str(usuario).split()[:2]).upper() or "?"
    ch1, ch2 = st.columns([4, 1])
    with ch1:
        st.markdown(
            f"""
            <div class="kiosk-header">
                <div class="avatar">{iniciales}</div>
                <div>
                    <div class="uname">{usuario}</div>
                    <span class="uarea">{area}</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with ch2:
        st.write("")
        if st.button("Salir", use_container_width=True, help="Cerrar sesión"):
            logout()
            st.rerun()


def _render_reloj() -> None:
    ahora = now_ecuador()
    fecha = f"{_DIAS[ahora.weekday()]} {ahora.day} de {_MESES[ahora.month - 1]} de {ahora.year}"
    st.markdown(
        f"""
        <div class="kiosk-clock">
            <div class="hora">{ahora.strftime("%H:%M")}</div>
            <div class="fecha">{fecha} · hora de Ecuador</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def vista_colaborador() -> None:
    inject_kiosk_css()
    usuario = st.session_state["usuario"]
    area_usuario = st.session_state["area"]

    _render_header(usuario, area_usuario)
    _render_reloj()

    if "aviso_revision" in st.session_state:
        _dialogo_revision(st.session_state["aviso_revision"])

    # Sin botones, para no mostrar estado desactualizado ni permitir dobles.
    if st.session_state.get("auto_logout_started_at"):
        st.markdown(
            """
            <div class="kiosk-status exito">
                <div class="titulo">Marcación registrada</div>
                <div class="detalle">Tu registro quedó guardado correctamente.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        _procesar_auto_logout()
        return

    # Justificación de horas extra pendiente: es la única acción posible.
    if "salida_pendiente" in st.session_state:
        render_formulario_justificacion()
        _procesar_auto_logout()
        return

    df = leer_registros()
    idx_abierto = buscar_turno_abierto_idx(df, usuario)

    if idx_abierto is None:
        st.markdown(
            """
            <div class="kiosk-status libre">
                <div class="titulo">Sin turno activo</div>
                <div class="detalle">No tienes ninguna entrada abierta. Marca tu entrada para iniciar el turno.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("Marcar Entrada", use_container_width=True, type="primary"):
            marcar_entrada(usuario)
    else:
        ts_str = str(df.loc[idx_abierto, "Timestamp Entrada"])
        ts_entrada = parse_timestamp_flexible(ts_str)
        if ts_entrada is not None:
            horas_abiertas = (now_ecuador() - ts_entrada).total_seconds() / 3600
            detalle = (
                f"Entrada: <b>{ts_entrada.strftime('%H:%M')}</b> del "
                f"{ts_entrada.strftime('%d/%m/%Y')} — llevas <b>{_fmt_hm(horas_abiertas)}</b>."
            )
            if horas_abiertas > UMBRAL_OLVIDO_H:
                detalle += (
                    f"<br>Este turno supera las {UMBRAL_OLVIDO_H} h: al marcar salida "
                    "pasará a revisión de tu supervisor."
                )
        else:
            detalle = "Tienes un turno abierto. Marca tu salida para cerrarlo."
        st.markdown(
            f"""
            <div class="kiosk-status abierto">
                <div class="titulo">Turno abierto</div>
                <div class="detalle">{detalle}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("Marcar Salida", use_container_width=True, type="primary"):
            marcar_salida(usuario)

    # Si la salida excedió el umbral, marcar_salida acaba de setear
    # salida_pendiente y el formulario debe salir en este mismo render.
    render_formulario_justificacion()

    _procesar_auto_logout()
