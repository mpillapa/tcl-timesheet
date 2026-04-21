import streamlit as st
import pandas as pd
from datetime import datetime, date, time
from auth import logout
from data import leer_registros, escribir_registros, calcular_horas, buscar_turno_abierto_idx
from employees import AREAS, EMPLEADOS_POR_AREA, AREA_DE
from config import UMBRAL_HORAS_EXTRA, TS_FMT, MIN_JUSTIF_CHARS
from marcado import guardar_salida

def _preparar_df_dashboard(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte columnas a tipos aptos para análisis/gráficos."""
    d = df.copy()
    d["Fecha de Turno"] = pd.to_datetime(d["Fecha de Turno"], errors="coerce").dt.date
    d["Timestamp Entrada"] = pd.to_datetime(d["Timestamp Entrada"], errors="coerce")
    d["Timestamp Salida"] = pd.to_datetime(d["Timestamp Salida"], errors="coerce")
    d["Horas Trabajadas"] = pd.to_numeric(d["Horas Trabajadas"], errors="coerce")
    return d

def _sidebar_filtros(df: pd.DataFrame) -> pd.DataFrame:
    """Muestra filtros en la sidebar y devuelve el DataFrame filtrado."""       
    st.sidebar.header("🔍 Filtros")

    fechas_validas = df["Fecha de Turno"].dropna()
    if not fechas_validas.empty:
        fmin = fechas_validas.min()
        fmax = fechas_validas.max()
    else:
        fmin = fmax = date.today()

    rango = st.sidebar.date_input(
        "Rango de fechas de turno",
        value=(fmin, fmax),
        min_value=fmin,
        max_value=fmax,
        key="filtro_rango",
    )

    areas_sel = st.sidebar.multiselect("Áreas", AREAS, default=AREAS, key="filtro_area")

    empleados_disp = sorted(df["Nombre"].dropna().unique().tolist())
    emp_sel = st.sidebar.multiselect("Empleados", empleados_disp, default=empleados_disp, key="filtro_emp")

    estados = ["Completo", "Abierto"]
    est_sel = st.sidebar.multiselect("Estado", estados, default=estados, key="filtro_est")

    mask = pd.Series(True, index=df.index)
    if isinstance(rango, tuple) and len(rango) == 2 and all(rango):
        f_ini, f_fin = rango
        mask &= df["Fecha de Turno"].between(f_ini, f_fin)
    mask &= df["Area"].isin(areas_sel)
    mask &= df["Nombre"].isin(emp_sel)
    mask &= df["Estado"].isin(est_sel)

    return df[mask].copy()

def _render_dashboard(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("Sin registros en el rango filtrado.")
        return

    completos = df[df["Estado"] == "Completo"]
    abiertos = df[df["Estado"] == "Abierto"]
    total_horas = float(completos["Horas Trabajadas"].sum(skipna=True))
    horas_extra_df = completos[completos["Horas Trabajadas"] > UMBRAL_HORAS_EXTRA]
    horas_extra = float(horas_extra_df["Horas Trabajadas"].sum(skipna=True) - UMBRAL_HORAS_EXTRA * len(horas_extra_df))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total horas", f"{total_horas:.1f} h")
    c2.metric("Turnos completos", len(completos))
    c3.metric("Turnos abiertos", len(abiertos))
    c4.metric("Horas extra (>9.5h)", f"{max(0, horas_extra):.1f} h")

    st.divider()

    st.subheader("Horas por área")
    if not completos.empty:
        por_area = completos.groupby("Area", dropna=True)["Horas Trabajadas"].sum().reset_index()
        por_area = por_area.sort_values("Horas Trabajadas", ascending=False)    
        st.bar_chart(por_area, x="Area", y="Horas Trabajadas", use_container_width=True)
    else:
        st.caption("Sin turnos completos en el filtro.")

    st.subheader("Top 10 empleados por horas")
    if not completos.empty:
        por_emp = completos.groupby("Nombre", dropna=True)["Horas Trabajadas"].sum().reset_index()
        por_emp = por_emp.sort_values("Horas Trabajadas", ascending=False).head(10)
        st.bar_chart(por_emp, x="Nombre", y="Horas Trabajadas", use_container_width=True)
    else:
        st.caption("Sin datos.")

    st.subheader("Horas trabajadas por día")
    if not completos.empty:
        por_dia = completos.groupby("Fecha de Turno", dropna=True)["Horas Trabajadas"].sum().reset_index()
        por_dia = por_dia.sort_values("Fecha de Turno")
        st.line_chart(por_dia, x="Fecha de Turno", y="Horas Trabajadas", use_container_width=True)
    else:
        st.caption("Sin datos.")

def _render_tabla(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("Sin registros en el rango filtrado.")
        return

    st.caption(f"Mostrando **{len(df)}** registros.")
    st.dataframe(df, use_container_width=True, hide_index=True)

    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 Descargar CSV",
        data=csv,
        file_name=f"registros_{date.today().isoformat()}.csv",
        mime="text/csv",
        use_container_width=True,
    )

def _render_correcciones() -> None:
    """Flujo para cerrar turnos abiertos o crear registros históricos. Solo super admin."""
    st.caption(
        "Úsalo cuando un empleado olvidó marcar entrada, salida o ambas. "    
        "Toda corrección queda registrada en 'Observaciones' con el prefijo 'Registro manual:' para auditoría."
    )

    ca_corr, ce_corr = st.columns(2)
    with ca_corr:
        area_corr = st.selectbox("Área", AREAS, key="area_corr")
    with ce_corr:
        emp_corr = st.selectbox("Empleado a corregir", EMPLEADOS_POR_AREA[area_corr], key="emp_corr")

    modo = st.radio(
        "¿Qué quieres hacer?",
        [
            "Cerrar un turno abierto (olvido de SALIDA)",
            "Crear registro histórico completo (olvido de ENTRADA y/o SALIDA)",
        ],
        key="modo_corr",
    )

    df_actual = leer_registros()
    ahora_min = datetime.now().replace(second=0, microsecond=0).time()

    if modo.startswith("Cerrar"):
        df_abiertos = df_actual[
            (df_actual["Nombre"] == emp_corr) & (df_actual["Estado"] == "Abierto")
        ]
        if df_abiertos.empty:
            st.info(f"{emp_corr} no tiene turnos abiertos.")
        else:
            opciones = {
                f"Entrada {row['Timestamp Entrada']} (turno {row['Fecha de Turno']})": idx
                for idx, row in df_abiertos.iterrows()
            }
            elegido = st.selectbox("Turno abierto a cerrar", list(opciones.keys()), key="turno_sel")
            idx_obj = opciones[elegido]

            c1, c2 = st.columns(2)
            with c1:
                f_sal = st.date_input("Fecha de salida", value=date.today(), key="f_sal_close")
            with c2:
                h_sal = st.time_input("Hora de salida", value=ahora_min, key="h_sal_close")

            st.markdown("**Observación** (el prefijo *Registro manual:* se añade automáticamente)")
            cp, cd = st.columns([1, 3])
            with cp:
                st.text_input("Prefijo", value="Registro manual:", disabled=True,
                              key="pref_close", label_visibility="collapsed")   
            with cd:
                obs_det = st.text_input("Detalle", key="obs_close_det",
                                        placeholder="Describe el motivo del cierre manual...",
                                        label_visibility="collapsed")

            if st.button("💾 Cerrar turno", key="btn_close"):
                ts_sal = datetime.combine(f_sal, h_sal)
                ts_ent = pd.to_datetime(df_actual.loc[idx_obj, "Timestamp Entrada"])
                det = obs_det.strip()
                if ts_sal <= ts_ent:
                    st.error("La salida debe ser posterior a la entrada.")      
                elif not det:
                    st.error("⚠️ Ingresa un detalle válido en la observación (no puede quedar vacío).")
                else:
                    horas = calcular_horas(ts_ent, ts_sal)
                    if horas > UMBRAL_HORAS_EXTRA and len(det) < MIN_JUSTIF_CHARS:
                        st.error(
                            f"Las {horas} h exceden {UMBRAL_HORAS_EXTRA} h. "   
                            f"El detalle debe tener al menos {MIN_JUSTIF_CHARS} caracteres."
                        )
                    else:
                        guardar_salida(df_actual, idx_obj, ts_sal, horas, f"Registro manual: {det}")
                        st.success(f"✅ Turno cerrado. Horas trabajadas: {horas}")
                        st.rerun()
    else:
        st.caption("Ambas marcas se ingresan manualmente. Úsalo solo para turnos ya pasados.")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Entrada**")
            f_ent = st.date_input("Fecha", value=date.today(), key="f_ent_m")   
            h_ent = st.time_input("Hora", value=time(7, 0), key="h_ent_m")      
        with c2:
            st.markdown("**Salida**")
            f_sal = st.date_input("Fecha", value=date.today(), key="f_sal_m")   
            h_sal = st.time_input("Hora", value=time(17, 0), key="h_sal_m")     

        st.markdown("**Observación** (el prefijo *Registro manual:* se añade automáticamente)")
        cp2, cd2 = st.columns([1, 3])
        with cp2:
            st.text_input("Prefijo", value="Registro manual:", disabled=True,   
                          key="pref_m", label_visibility="collapsed")
        with cd2:
            obs_det = st.text_input("Detalle", key="obs_m_det",
                                    placeholder="Describe por qué se ingresa manualmente...",
                                    label_visibility="collapsed")

        if st.button("💾 Crear registro", key="btn_m"):
            ts_in = datetime.combine(f_ent, h_ent)
            ts_out = datetime.combine(f_sal, h_sal)
            det = obs_det.strip()

            if ts_out <= ts_in:
                st.error("La salida debe ser posterior a la entrada.")
            elif not det:
                st.error("⚠️ Ingresa un detalle válido en la observación (no puede quedar vacío).")
            elif buscar_turno_abierto_idx(df_actual, emp_corr) is not None:     
                st.error(
                    f"{emp_corr} tiene un turno abierto. Ciérralo primero en el modo anterior."
                )
            else:
                horas = calcular_horas(ts_in, ts_out)
                if horas > UMBRAL_HORAS_EXTRA and len(det) < MIN_JUSTIF_CHARS:  
                    st.error(
                        f"Las {horas} h exceden {UMBRAL_HORAS_EXTRA} h. "       
                        f"El detalle debe tener al menos {MIN_JUSTIF_CHARS} caracteres."
                    )
                else:
                    nueva = pd.DataFrame([{
                        "Nombre": emp_corr,
                        "Area": AREA_DE.get(emp_corr, ""),
                        "Fecha de Turno": ts_in.strftime("%Y-%m-%d"),
                        "Timestamp Entrada": ts_in.strftime(TS_FMT),
                        "Timestamp Salida": ts_out.strftime(TS_FMT),
                        "Horas Trabajadas": horas,
                        "Estado": "Completo",
                        "Observaciones": f"Registro manual: {det}",
                    }])
                    escribir_registros(pd.concat([df_actual, nueva], ignore_index=True))
                    st.success(f"✅ Registro creado. Horas trabajadas: {horas}")
                    st.rerun()

def vista_super_admin() -> None:
    usuario = st.session_state["usuario"]
    admin_rol = st.session_state.get("admin_rol", "")

    st.title("⏱️ Marcador de Horas — Panel Administrativo")

    ch1, ch2 = st.columns([3, 1])
    with ch1:
        st.markdown(f"### 👔 {usuario}  \n💼 **{admin_rol}**")
    with ch2:
        if st.button("Cerrar sesión", use_container_width=True):
            logout()
            st.rerun()

    st.divider()

    df_raw = leer_registros()
    df_dash = _preparar_df_dashboard(df_raw)
    df_filt = _sidebar_filtros(df_dash)

    tab_dash, tab_tabla, tab_corr = st.tabs(["📊 Dashboard", "📋 Tabla", "🛠️ Correcciones"])
    with tab_dash:
        _render_dashboard(df_filt)
    with tab_tabla:
        _render_tabla(df_filt)
    with tab_corr:
        _render_correcciones()
