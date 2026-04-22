"""Lógica de negocio: marcar entrada/salida, justificación de horas extra."""

from datetime import datetime
import time

import pandas as pd
import streamlit as st

from config import TS_FMT, UMBRAL_OLVIDO_H, UMBRAL_HORAS_EXTRA, MIN_JUSTIF_CHARS
from data import (
    leer_registros,
    append_registro,
    actualizar_por_entrada,
    calcular_horas,
    calcular_horas_extra,
    buscar_turno_abierto_idx,
)
from employees import AREA_DE
from time_utils import now_ecuador


AUTO_LOGOUT_SECONDS = 5


def programar_cierre_sesion() -> None:
    """Activa cierre automático de sesión tras una marcación exitosa."""
    st.session_state["auto_logout_started_at"] = time.time()


def guardar_salida(nombre: str, ts_entrada_str: str, ts_salida, horas, observacion) -> bool:
    """Cierra un turno localizándolo por (Nombre, Timestamp Entrada) sobre una
    lectura fresca. Devuelve False si la fila ya no existe."""
    cambios = {
        "Timestamp Salida": ts_salida.strftime(TS_FMT),
        "Horas Trabajadas": horas,
        "Horas Extra": calcular_horas_extra(horas),
        "Estado": "Completo",
        "Observaciones": observacion,
    }
    return actualizar_por_entrada(nombre, ts_entrada_str, cambios)


def marcar_entrada(nombre: str) -> None:
    df = leer_registros()
    idx_abierto = buscar_turno_abierto_idx(df, nombre)

    if idx_abierto is not None:
        ts_prev = datetime.strptime(str(df.loc[idx_abierto, "Timestamp Entrada"]), TS_FMT)
        horas_abiertas = (now_ecuador() - ts_prev).total_seconds() / 3600
        if horas_abiertas > UMBRAL_OLVIDO_H:
            st.error(
                f"⚠️ Parece que **{nombre}** olvidó marcar salida del turno iniciado el "
                f"{ts_prev.strftime('%Y-%m-%d %H:%M')} "
                f"({horas_abiertas:.1f} h abiertas). Contacta a tu supervisor para cerrarlo."
            )
        else:
            st.warning(
                f"⚠️ {nombre} ya tiene un turno abierto desde "
                f"{ts_prev.strftime('%Y-%m-%d %H:%M')}. Marca la salida primero."
            )
        return

    ahora = now_ecuador()
    append_registro({
        "Nombre": nombre,
        "Area": AREA_DE.get(nombre, ""),
        "Fecha de Turno": ahora.strftime("%Y-%m-%d"),
        "Timestamp Entrada": ahora.strftime(TS_FMT),
        "Timestamp Salida": "",
        "Horas Trabajadas": "",
        "Horas Extra": "",
        "Estado": "Abierto",
        "Observaciones": "",
    })
    st.success(f"✅ Entrada registrada para **{nombre}** a las {ahora.strftime('%H:%M:%S')}")
    programar_cierre_sesion()


def marcar_salida(nombre: str) -> None:
    df = leer_registros()
    idx = buscar_turno_abierto_idx(df, nombre)

    if idx is None:
        st.error(
            f"❌ No se encontró un turno abierto para **{nombre}**. "
            "Si olvidaste marcar entrada, contacta a tu supervisor."
        )
        return

    ahora = now_ecuador()
    ts_entrada_str = str(df.loc[idx, "Timestamp Entrada"])
    ts_entrada = datetime.strptime(ts_entrada_str, TS_FMT)
    horas = calcular_horas(ts_entrada, ahora)

    # Si excede el umbral, diferir guardado y pedir justificación en otro render.
    if horas > UMBRAL_HORAS_EXTRA:
        st.session_state["salida_pendiente"] = {
            "nombre": nombre,
            "ts_entrada_str": ts_entrada_str,
            "ts_salida_str": ahora.strftime(TS_FMT),
            "horas": horas,
        }
        return

    if not guardar_salida(nombre, ts_entrada_str, ahora, horas, ""):
        st.error("El turno ya no existe (pudo haber sido modificado por un administrador). Refresca la página.")
        return
    st.success(
        f"✅ Salida registrada para **{nombre}**. "
        f"Entrada: {ts_entrada.strftime('%Y-%m-%d %H:%M')} → "
        f"Salida: {ahora.strftime('%Y-%m-%d %H:%M')} = **{horas} h**"
    )
    programar_cierre_sesion()


def render_formulario_justificacion() -> None:
    """Formulario que aparece cuando una salida excede UMBRAL_HORAS_EXTRA."""
    if "salida_pendiente" not in st.session_state:
        return

    pend = st.session_state["salida_pendiente"]
    st.warning(
        f"⏱️ **{pend['nombre']}** trabajó **{pend['horas']} h** "
        f"(excede {UMBRAL_HORAS_EXTRA} h). "
        "Debes ingresar una justificación válida para guardar la salida."
    )
    justif = st.text_area(
        f"Justificación obligatoria (mínimo {MIN_JUSTIF_CHARS} caracteres)",
        key="justif_horas_extra",
        height=100,
        placeholder="Ej: Cierre urgente de inventario solicitado por el supervisor Juan Gómez...",
    )

    c1, c2 = st.columns(2)
    with c1:
        confirmar = st.button("💾 Confirmar salida", type="primary", use_container_width=True)
    with c2:
        cancelar = st.button("✖️ Cancelar", use_container_width=True)

    if cancelar:
        del st.session_state["salida_pendiente"]
        st.rerun()

    if confirmar:
        if len(justif.strip()) < MIN_JUSTIF_CHARS:
            st.error(
                f"La justificación debe tener al menos {MIN_JUSTIF_CHARS} caracteres. "
                "Describe el motivo concreto del exceso."
            )
            return

        ts_salida = datetime.strptime(pend["ts_salida_str"], TS_FMT)
        obs = f"Horas extra justificadas: {justif.strip()}"
        if not guardar_salida(pend["nombre"], pend["ts_entrada_str"], ts_salida, pend["horas"], obs):
            st.error("No se encontró el turno a cerrar. Recarga la página.")
            return

        del st.session_state["salida_pendiente"]
        st.success(f"✅ Salida registrada con justificación. Horas: **{pend['horas']}**")
        programar_cierre_sesion()
        st.rerun()
