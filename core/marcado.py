"""Lógica de negocio: marcar entrada/salida, justificación de horas extra."""

from datetime import datetime
import time

import streamlit as st

from core.config import TS_FMT, UMBRAL_OLVIDO_H, UMBRAL_HORAS_EXTRA, MIN_JUSTIF_CHARS, MIN_MINUTOS_TURNO
from core.data import (
    leer_registros,
    append_registro,
    actualizar_por_entrada,
    actualizar_varios_por_entrada,
    calcular_horas,
    calcular_horas_efectivas,
    calcular_horas_extra,
    buscar_turno_abierto_idx,
    ReglaDeNegocio,
    ORIGEN_COLABORADOR,
    ORIGEN_SISTEMA,
)
from core.employees import AREA_DE
from core.time_utils import now_ecuador, parse_timestamp_flexible
from core.ui_utils import bloquear_doble_click


AUTO_LOGOUT_SECONDS = 3
ESTADO_REVISION = "Revision"
_DEBOUNCE_ENTRADA_SECS = 8  # ventana para ignorar doble-click en Marcar Entrada


def _fmt_duracion(horas: float) -> str:
    """Convierte horas decimales a 'Xh Ym' para mostrar al usuario."""
    total_min = round(horas * 60)
    h, m = divmod(total_min, 60)
    return f"{h}h {m:02d}min"


def _crear_nueva_entrada(nombre: str, ahora: datetime) -> bool:
    """True si la entrada quedó registrada. Un rechazo de la base, por ejemplo
    porque el horario se cruza con otro turno, se muestra y devuelve False."""
    try:
        return _insertar_entrada(nombre, ahora)
    except ReglaDeNegocio as e:
        st.error(f"No se pudo registrar la entrada: {e}")
        return False


def _insertar_entrada(nombre: str, ahora: datetime) -> bool:
    return append_registro({
        "Nombre": nombre,
        "Area": AREA_DE.get(nombre, ""),
        "Fecha de Turno": ahora.strftime("%Y-%m-%d"),
        "Timestamp Entrada": ahora.strftime(TS_FMT),
        "Timestamp Salida": "",
        "Horas Trabajadas": "",
        "Horas Efectivas": "",
        "Horas Extra": "",
        "Estado": "Abierto",
        "Observaciones": "",
    }, autor=nombre, origen=ORIGEN_COLABORADOR)


def programar_cierre_sesion() -> None:
    """Activa cierre automático de sesión tras una marcación exitosa."""
    st.session_state["auto_logout_started_at"] = time.time()


def guardar_salida(nombre: str, ts_entrada_str: str, ts_salida, horas, observacion,
                   *, autor: str = None, origen: str = None, motivo: str = "") -> bool:
    """Cierra el turno (Nombre, Timestamp Entrada). False si ya no existe.

    Por defecto se atribuye al propio colaborador, que es el caso del quiosco.
    El panel de admin pasa su usuario y el motivo cuando cierra un turno en
    revisión, para que la bitácora distinga un cierre propio de uno hecho por
    supervisión. Un rechazo de la base levanta ReglaDeNegocio."""
    cambios = {
        "Timestamp Salida": ts_salida.strftime(TS_FMT),
        "Horas Trabajadas": horas,
        "Horas Efectivas": calcular_horas_efectivas(horas),
        "Horas Extra": calcular_horas_extra(horas),
        "Estado": "Completo",
        "Observaciones": observacion,
    }
    return actualizar_por_entrada(
        nombre, ts_entrada_str, cambios,
        autor=autor if autor is not None else nombre,
        origen=origen if origen is not None else ORIGEN_COLABORADOR,
        motivo=motivo,
    )


def marcar_entrada(nombre: str) -> None:
    debounce_key = f"_debounce_entrada_{nombre}"
    ahora = now_ecuador()
    ultima = st.session_state.get(debounce_key)
    if ultima and (ahora - ultima).total_seconds() < _DEBOUNCE_ENTRADA_SECS:
        # Si el primer rerun se interrumpió antes de programar_cierre_sesion(),
        # arrancar el countdown desde aquí.
        if not st.session_state.get("auto_logout_started_at"):
            programar_cierre_sesion()
        return
    # Antes de cualquier I/O, para que el segundo rerun vea el flag.
    st.session_state[debounce_key] = ahora

    df = leer_registros()
    idx_abierto = buscar_turno_abierto_idx(df, nombre)

    if idx_abierto is not None:
        ts_prev_str = str(df.loc[idx_abierto, "Timestamp Entrada"])
        ts_prev = parse_timestamp_flexible(ts_prev_str)
        if ts_prev is None:
            st.error("No se pudo interpretar la hora de entrada del turno abierto. Contacta a tu supervisor.")
            return
        horas_abiertas = (now_ecuador() - ts_prev).total_seconds() / 3600
        if horas_abiertas > UMBRAL_OLVIDO_H:
            obs_prev = str(df.loc[idx_abierto, "Observaciones"] or "").strip()
            if obs_prev.lower() == "nan":
                obs_prev = ""
            tag_revision = (
                f"Pendiente revision supervisor: turno > {UMBRAL_OLVIDO_H} h "
                f"sin salida (inicio {ts_prev.strftime('%Y-%m-%d %H:%M')})."
            )
            obs_nueva = f"{obs_prev} | {tag_revision}" if obs_prev else tag_revision

            try:
                actualizado = actualizar_por_entrada(
                    nombre,
                    ts_prev_str,
                    {
                        "Estado": ESTADO_REVISION,
                        "Observaciones": obs_nueva,
                    },
                    autor=nombre,
                    origen=ORIGEN_COLABORADOR,
                )
            except ReglaDeNegocio as e:
                st.error(f"No se pudo enviar el turno anterior a revisión: {e}")
                return

            if not actualizado:
                st.error(
                    f"Se detectó un turno abierto mayor a {UMBRAL_OLVIDO_H}h, pero no se pudo "
                    "enviarlo a revisión. Recarga la página e intenta nuevamente."
                )
                return

            if not _crear_nueva_entrada(nombre, ahora):
                return
            st.warning(
                f"El turno anterior de **{nombre}** ("
                f"{horas_abiertas:.1f} h abiertas) fue enviado a revisión de super admin. "
                f"Se registró una nueva entrada a las {ahora.strftime('%H:%M:%S')}."
            )
            programar_cierre_sesion()
        else:
            st.warning(
                f"{nombre} ya tiene un turno abierto desde "
                f"{ts_prev.strftime('%Y-%m-%d %H:%M')}. Marca la salida primero."
            )
        return

    if not _crear_nueva_entrada(nombre, ahora):
        return
    st.success(f"Entrada registrada para **{nombre}** a las {ahora.strftime('%H:%M:%S')}")
    programar_cierre_sesion()


def marcar_salida(nombre: str) -> None:
    if bloquear_doble_click(f"salida_{nombre}"):
        return
    df = leer_registros()
    idx = buscar_turno_abierto_idx(df, nombre)

    if idx is None:
        st.error(
            f"No se encontró un turno abierto para **{nombre}**. "
            "Si olvidaste marcar entrada, contacta a tu supervisor."
        )
        from core.data import _normalizar_cmp
        nombre_norm = _normalizar_cmp(nombre)
        filas_persona = df[df["Nombre"].fillna("").map(_normalizar_cmp) == nombre_norm]
        with st.expander("Diagnóstico (para el administrador)"):
            st.write(f"**Nombre buscado (normalizado):** `{nombre_norm}`")
            if filas_persona.empty:
                st.write("No se encontró ninguna fila con ese nombre. Nombres únicos en la hoja:")
                nombres_hoja = df["Nombre"].dropna().unique().tolist()
                for n in nombres_hoja:
                    st.code(f"{repr(n)}  →  norm: {repr(_normalizar_cmp(n))}")
            else:
                st.write(f"Filas encontradas para ese nombre ({len(filas_persona)}):")
                st.dataframe(filas_persona[["Nombre", "Fecha de Turno", "Timestamp Entrada", "Estado"]].reset_index(drop=True))
        return

    ahora = now_ecuador()
    ts_entrada_str = str(df.loc[idx, "Timestamp Entrada"])
    ts_entrada = parse_timestamp_flexible(ts_entrada_str)
    if ts_entrada is None:
        st.error("No se pudo interpretar la hora de entrada del turno. Contacta a tu supervisor.")
        return

    minutos_transcurridos = (ahora - ts_entrada).total_seconds() / 60
    if minutos_transcurridos < MIN_MINUTOS_TURNO:
        st.warning(
            f"Debes esperar al menos {MIN_MINUTOS_TURNO} minutos desde la entrada para marcar salida. "
            f"Entrada registrada a las {ts_entrada.strftime('%H:%M:%S')}."
        )
        return

    horas = calcular_horas(ts_entrada, ahora)

    # Pasado UMBRAL_OLVIDO_H la duración no es confiable (20 h, por ejemplo), así
    # que va a revisión sin registrar la salida.
    horas_abiertas = (ahora - ts_entrada).total_seconds() / 3600
    if horas_abiertas > UMBRAL_OLVIDO_H:
        obs_prev = str(df.loc[idx, "Observaciones"] or "").strip()
        if obs_prev.lower() == "nan":
            obs_prev = ""
        tag_revision = (
            f"Pendiente revision supervisor: turno > {UMBRAL_OLVIDO_H} h "
            f"(entrada {ts_entrada.strftime('%Y-%m-%d %H:%M')}, "
            f"salida intentada {ahora.strftime('%Y-%m-%d %H:%M')})."
        )
        obs_nueva = f"{obs_prev} | {tag_revision}" if obs_prev else tag_revision
        try:
            enviado = actualizar_por_entrada(
                nombre, ts_entrada_str, {"Estado": ESTADO_REVISION, "Observaciones": obs_nueva},
                autor=nombre, origen=ORIGEN_COLABORADOR,
            )
        except ReglaDeNegocio as e:
            st.error(f"No se pudo enviar el turno a revisión: {e}")
            return
        if not enviado:
            st.error("El turno ya no existe o no se pudo enviar a revisión. Refresca la página.")
            return
        # No se cierra sesión, a diferencia de una salida normal: el empleado
        # puede marcar su entrada para iniciar la jornada de hoy.
        st.session_state["aviso_revision"] = {
            "nombre": nombre,
            "horas": horas_abiertas,
            "entrada": ts_entrada.strftime("%Y-%m-%d %H:%M"),
        }
        return

    # Si excede el umbral, diferir guardado y pedir justificación en otro render.
    if horas > UMBRAL_HORAS_EXTRA:
        st.session_state["salida_pendiente"] = {
            "nombre": nombre,
            "ts_entrada_str": ts_entrada_str,
            "ts_salida_str": ahora.strftime(TS_FMT),
            "horas": horas,
        }
        return

    try:
        guardada = guardar_salida(nombre, ts_entrada_str, ahora, horas, "")
    except ReglaDeNegocio as e:
        st.error(f"No se pudo registrar la salida: {e}")
        return
    if not guardada:
        st.error("El turno ya no existe (pudo haber sido modificado por un administrador). Refresca la página.")
        return
    st.success(
        f"Salida registrada para **{nombre}**. "
        f"Entrada: {ts_entrada.strftime('%H:%M')} → "
        f"Salida: {ahora.strftime('%H:%M')} — "
        f"**{_fmt_duracion(horas)} trabajadas**"
    )
    programar_cierre_sesion()


def barrer_turnos_olvidados(df) -> int:
    """Envía a revisión los turnos 'Abierto' con más de UMBRAL_OLVIDO_H horas.

    marcar_entrada y marcar_salida solo actúan cuando el empleado vuelve a
    marcar; este barrido no depende de él. Corre al abrir el panel de super
    admin. Recibe el df ya leído para no duplicar la lectura."""
    if df is None or df.empty:
        return 0
    ahora = now_ecuador()
    estado = df["Estado"].astype(str).str.strip().str.lower()
    abiertos = df[estado == "abierto"]
    cambios = []
    for _, fila in abiertos.iterrows():
        ts_ent = parse_timestamp_flexible(str(fila["Timestamp Entrada"]))
        if ts_ent is None:
            continue
        if (ahora - ts_ent).total_seconds() / 3600 <= UMBRAL_OLVIDO_H:
            continue
        obs_prev = str(fila.get("Observaciones") or "").strip()
        if obs_prev.lower() == "nan":
            obs_prev = ""
        tag_revision = (
            f"Pendiente revision supervisor: turno > {UMBRAL_OLVIDO_H} h "
            f"sin salida (inicio {ts_ent.strftime('%Y-%m-%d %H:%M')})."
        )
        obs_nueva = f"{obs_prev} | {tag_revision}" if obs_prev else tag_revision
        cambios.append((
            str(fila["Nombre"]),
            str(fila["Timestamp Entrada"]),
            {"Estado": ESTADO_REVISION, "Observaciones": obs_nueva},
        ))
    # Corre al abrir el panel: un rechazo no puede tumbarlo. Se avisa y se
    # reintenta en la siguiente sesión.
    try:
        return actualizar_varios_por_entrada(
            cambios, autor="barrido_olvidados", origen=ORIGEN_SISTEMA
        )
    except ReglaDeNegocio as e:
        st.warning(f"No se pudieron enviar a revisión los turnos olvidados: {e}")
        return 0


def render_formulario_justificacion() -> None:
    """Formulario que aparece cuando una salida excede UMBRAL_HORAS_EXTRA."""
    if "salida_pendiente" not in st.session_state:
        return

    pend = st.session_state["salida_pendiente"]
    st.warning(
        f"**{pend['nombre']}** trabajó **{_fmt_duracion(pend['horas'])}** "
        f"(excede el límite de {UMBRAL_HORAS_EXTRA} h). "
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
        confirmar = st.button("Confirmar salida", type="primary", use_container_width=True)
    with c2:
        cancelar = st.button("Cancelar", use_container_width=True)

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
        if bloquear_doble_click("confirmar_salida_justif"):
            st.rerun()
            return

        ts_salida = datetime.strptime(pend["ts_salida_str"], TS_FMT)
        obs = f"Horas extra justificadas: {justif.strip()}"
        try:
            guardada = guardar_salida(pend["nombre"], pend["ts_entrada_str"], ts_salida, pend["horas"], obs)
        except ReglaDeNegocio as e:
            st.error(f"No se pudo registrar la salida: {e}")
            return
        if not guardada:
            st.error("No se encontró el turno a cerrar. Recarga la página.")
            return

        del st.session_state["salida_pendiente"]
        st.success(f"Salida registrada con justificación. Tiempo trabajado: **{_fmt_duracion(pend['horas'])}**")
        programar_cierre_sesion()
        st.rerun()
