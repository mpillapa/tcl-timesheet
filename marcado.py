"""Lógica de negocio: marcar entrada/salida, justificación de horas extra."""

from datetime import datetime

import pandas as pd
import streamlit as st

from config import TS_FMT, UMBRAL_OLVIDO_H, UMBRAL_HORAS_EXTRA, MIN_JUSTIF_CHARS
from data import (
    leer_registros,
    escribir_registros,
    calcular_horas,
    buscar_turno_abierto_idx,
)
from employees import AREA_DE


def guardar_salida(df, idx, ts_salida, horas, observacion):
    """Actualiza una fila con los datos de salida. Usado por marcado y correcciones."""
    df.loc[idx, "Timestamp Salida"] = ts_salida.strftime(TS_FMT)
    df.loc[idx, "Horas Trabajadas"] = horas
    df.loc[idx, "Estado"] = "Completo"
    df.loc[idx, "Observaciones"] = observacion
    escribir_registros(df)


def marcar_entrada(nombre: str) -> None:
    df = leer_registros()
    idx_abierto = buscar_turno_abierto_idx(df, nombre)

    if idx_abierto is not None:
        ts_prev = pd.to_datetime(df.loc[idx_abierto, "Timestamp Entrada"])
        horas_abiertas = (datetime.now() - ts_prev).total_seconds() / 3600
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

    ahora = datetime.now()
    nueva = pd.DataFrame([{
        "Nombre": nombre,
        "Area": AREA_DE.get(nombre, ""),
        "Fecha de Turno": ahora.strftime("%Y-%m-%d"),
        "Timestamp Entrada": ahora.strftime(TS_FMT),
        "Timestamp Salida": "",
        "Horas Trabajadas": "",
        "Estado": "Abierto",
        "Observaciones": "",
    }])
    escribir_registros(pd.concat([df, nueva], ignore_index=True))
    st.success(f"✅ Entrada registrada para **{nombre}** a las {ahora.strftime('%H:%M:%S')}")


def marcar_salida(nombre: str) -> None:
    df = leer_registros()
    idx = buscar_turno_abierto_idx(df, nombre)

    if idx is None:
        st.error(
            f"❌ No se encontró un turno abierto para **{nombre}**. "
            "Si olvidaste marcar entrada, contacta a tu supervisor."
        )
        return

    ahora = datetime.now()
    ts_entrada = pd.to_datetime(df.loc[idx, "Timestamp Entrada"])
    horas = calcular_horas(ts_entrada, ahora)

    # Si excede el umbral, diferir guardado y pedir justificación en otro render.
    if horas > UMBRAL_HORAS_EXTRA:
        st.session_state["salida_pendiente"] = {
            "nombre": nombre,
            "ts_entrada_str": df.loc[idx, "Timestamp Entrada"],
            "ts_salida_str": ahora.strftime(TS_FMT),
            "horas": horas,
        }
        return

    guardar_salida(df, idx, ahora, horas, "")
    st.success(
        f"✅ Salida registrada para **{nombre}**. "
        f"Entrada: {ts_entrada.strftime('%Y-%m-%d %H:%M')} → "
        f"Salida: {ahora.strftime('%Y-%m-%d %H:%M')} = **{horas} h**"
    )


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

        df = leer_registros()
        mask = (df["Nombre"] == pend["nombre"]) & (df["Timestamp Entrada"] == pend["ts_entrada_str"])
        idxs = df.index[mask].tolist()
        if not idxs:
            st.error("No se encontró el turno a cerrar. Recarga la página.")
            return

        idx = idxs[0]
        ts_salida = datetime.strptime(pend["ts_salida_str"], TS_FMT)
        obs = f"Horas extra justificadas: {justif.strip()}"
        guardar_salida(df, idx, ts_salida, pend["horas"], obs)

        del st.session_state["salida_pendiente"]
        st.success(f"✅ Salida registrada con justificación. Horas: **{pend['horas']}**")
        st.rerun()
