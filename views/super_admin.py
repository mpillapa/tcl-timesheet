import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime, date, time
from core.auth import logout
from core.data import (
    leer_registros,
    append_registro,
    calcular_horas,
    calcular_horas_extra,
    buscar_turno_abierto_idx,
)
from core.employees import AREAS, EMPLEADOS_POR_AREA, AREA_DE
from core.config import UMBRAL_HORAS_EXTRA, TS_FMT, MIN_JUSTIF_CHARS, HORAS_BASE_TURNO
from core.marcado import guardar_salida
from core.time_utils import now_ecuador, today_ecuador


BRAND_NAVY = "#1E2D78"
BRAND_NAVY_MID = "#3A4BA0"
BRAND_NAVY_SOFT = "#8A96C9"
BRAND_RED = "#D8202F"
BRAND_RED_SOFT = "#F5C4C8"
BRAND_BG_SOFT = "#F4F6FC"
BRAND_TEXT = "#1B1F3B"
BRAND_MUTED = "#6B7280"

BRAND_CATEGORICAL = [
    BRAND_NAVY,
    BRAND_RED,
    BRAND_NAVY_MID,
    "#2F9E8F",
    "#E08E2B",
    BRAND_NAVY_SOFT,
    "#7A5CA6",
    "#4B5B8F",
]


def _inject_brand_css() -> None:
    st.markdown(
        f"""
        <style>
            .block-container {{
                max-width: 1400px !important;
                padding-top: 1.2rem;
                padding-left: 2.5rem;
                padding-right: 2.5rem;
            }}
            h1, h2, h3 {{ color: {BRAND_NAVY}; }}
            h1 {{ font-size: 1.75rem !important; margin-bottom: 0.6rem !important; }}
            h3 {{ font-size: 1.1rem !important; }}

            .stTabs [data-baseweb="tab-list"] {{
                gap: 6px;
                border-bottom: 2px solid {BRAND_BG_SOFT};
                margin-bottom: 1rem;
            }}
            .stTabs [data-baseweb="tab"] {{
                background: {BRAND_BG_SOFT};
                border-radius: 10px 10px 0 0;
                padding: 10px 20px;
                color: {BRAND_NAVY};
                font-weight: 600;
            }}
            .stTabs [aria-selected="true"] {{
                background: {BRAND_NAVY} !important;
                color: #FFFFFF !important;
            }}

            .brand-header {{
                display:flex; align-items:center; justify-content:space-between;
                gap:14px; padding: 14px 20px; border-radius: 14px;
                background: linear-gradient(90deg, {BRAND_NAVY} 0%, {BRAND_NAVY_MID} 100%);
                color: #FFFFFF;
                box-shadow: 0 4px 14px rgba(30,45,120,0.18);
            }}
            .brand-header .user-block {{
                display:flex; align-items:center; gap:14px;
            }}
            .brand-header .avatar {{
                width:44px; height:44px; border-radius:50%;
                background: rgba(255,255,255,0.18);
                display:flex; align-items:center; justify-content:center;
                font-size:1.3rem;
            }}
            .brand-header .uname {{
                font-size:1.05rem; font-weight:700; line-height:1.2;
            }}
            .brand-header .role {{
                background: rgba(255,255,255,0.18);
                padding: 3px 12px; border-radius: 999px;
                font-size: 0.8rem; font-weight:500;
                display:inline-block; margin-top:3px;
            }}

            .kpi-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
                gap: 16px;
                margin: 8px 0 10px;
            }}
            .kpi-card {{
                background: #FFFFFF;
                border: 1px solid #E6E9F4;
                border-radius: 14px;
                padding: 18px 20px;
                display: flex;
                gap: 14px;
                align-items: flex-start;
                box-shadow: 0 2px 6px rgba(30,45,120,0.05);
                transition: transform .18s ease, box-shadow .18s ease;
            }}
            .kpi-card:hover {{
                transform: translateY(-2px);
                box-shadow: 0 8px 20px rgba(30,45,120,0.10);
            }}
            .kpi-icon {{
                width: 46px; height: 46px; border-radius: 12px;
                display: flex; align-items: center; justify-content: center;
                font-size: 1.35rem; flex: 0 0 46px;
            }}
            .kpi-body {{ flex: 1; min-width: 0; }}
            .kpi-label {{
                color: {BRAND_MUTED}; font-size: .75rem; font-weight: 600;
                text-transform: uppercase; letter-spacing: .06em;
            }}
            .kpi-value {{
                color: {BRAND_NAVY}; font-size: 1.85rem; font-weight: 800;
                line-height: 1.15; margin-top: 2px;
            }}
            .kpi-value .unit {{
                font-size: .95rem; font-weight: 600; color: {BRAND_MUTED}; margin-left: 4px;
            }}
            .kpi-sub {{
                color: {BRAND_MUTED}; font-size: .78rem; margin-top: 6px;
            }}
            .kpi-sub b {{ color: {BRAND_NAVY}; }}
            .kpi-card.accent {{ border-top: 3px solid {BRAND_RED}; }}

            .section-title {{
                display:flex; align-items:center; gap:10px;
                margin: 18px 0 10px;
            }}
            .section-title .dot {{
                width: 6px; height: 22px; background: {BRAND_NAVY};
                border-radius: 3px;
            }}
            .section-title h3 {{ margin: 0 !important; }}

            .chart-card {{
                background: #FFFFFF;
                border: 1px solid #E6E9F4;
                border-radius: 14px;
                padding: 18px 20px;
                box-shadow: 0 2px 6px rgba(30,45,120,0.04);
                margin-bottom: 14px;
            }}

            div[data-testid="stCaptionContainer"] {{ color: {BRAND_MUTED}; }}

            [data-testid="stSidebar"] {{ background: {BRAND_BG_SOFT}; }}
            [data-testid="stSidebar"] h2 {{ color: {BRAND_NAVY}; }}

            /* ── Filter chips ── */
            .filter-bar {{
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                gap: 6px;
                padding: 8px 0 6px;
            }}
            .fchip {{
                display: inline-flex;
                align-items: center;
                gap: 5px;
                padding: 4px 12px;
                border-radius: 999px;
                font-size: 0.78rem;
                font-weight: 600;
                border: 1px solid transparent;
                white-space: nowrap;
                letter-spacing: 0.01em;
            }}
            .fchip-date  {{ background:#EBF0FF; color:{BRAND_NAVY};  border-color:#C5CBDF; }}
            .fchip-area  {{ background:{BRAND_NAVY}; color:#FFFFFF;   border-color:{BRAND_NAVY}; }}
            .fchip-emp   {{ background:#E8F4F8; color:#0D6E8A;        border-color:#A0CEDE; }}
            .fchip-est   {{ background:#FEF3E2; color:#C97A0A;        border-color:#F5CC7A; }}
            .fchip-none  {{ background:#F4F6FC; color:{BRAND_MUTED};  border-color:#E6E9F4; font-weight:400; font-style:italic; }}

            /* Estilo mejorado del popover */
            [data-testid="stPopover"] > button {{
                background: {BRAND_NAVY} !important;
                color: #FFFFFF !important;
                border: none !important;
                border-radius: 8px !important;
                font-weight: 600 !important;
            }}
            [data-testid="stPopover"] > button:hover {{
                background: {BRAND_NAVY_MID} !important;
                color: #FFFFFF !important;
            }}

        </style>
        """,
        unsafe_allow_html=True,
    )


def _section_title(text: str) -> None:
    st.markdown(
        f'<div class="section-title"><span class="dot"></span><h3>{text}</h3></div>',
        unsafe_allow_html=True,
    )


def _kpi_card(icon: str, icon_bg: str, icon_color: str, label: str,
              value: str, unit: str = "", sub: str = "", accent: bool = False) -> str:
    accent_cls = " accent" if accent else ""
    unit_html = f'<span class="unit">{unit}</span>' if unit else ""
    sub_html = f'<div class="kpi-sub">{sub}</div>' if sub else ""
    return (
        f'<div class="kpi-card{accent_cls}">'
        f'<div class="kpi-icon" style="background:{icon_bg};color:{icon_color};">{icon}</div>'
        f'<div class="kpi-body">'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value}{unit_html}</div>'
        f'{sub_html}'
        f'</div></div>'
    )


def _altair_brand_theme():
    return {
        "config": {
            "background": "#FFFFFF",
            "view": {"stroke": "transparent"},
            "axis": {
                "labelColor": BRAND_TEXT,
                "titleColor": BRAND_TEXT,
                "labelFontSize": 11,
                "titleFontSize": 12,
                "titleFontWeight": 600,
                "gridColor": "#E6E9F4",
                "domainColor": "#D0D5E3",
                "tickColor": "#D0D5E3",
            },
            "legend": {
                "labelColor": BRAND_TEXT,
                "titleColor": BRAND_TEXT,
                "labelFontSize": 11,
                "titleFontSize": 12,
                "titleFontWeight": 600,
            },
            "title": {
                "color": BRAND_NAVY,
                "fontSize": 14,
                "fontWeight": 700,
                "anchor": "start",
            },
            "range": {"category": BRAND_CATEGORICAL},
        }
    }


try:
    alt.theme.register("transoceanica", enable=True)(_altair_brand_theme)
except AttributeError:
    alt.themes.register("transoceanica", _altair_brand_theme)
    alt.themes.enable("transoceanica")


AREAS_POR_ADMIN = {
    "dbuestan": {"IMPORT"},
    "pmena": {"DOCUMENTAL", "SUPERVISORES"},
    "gproanio": {"BODEGA"},
    "fherrera": None,
    "mpillapa": None,
}


def _get_areas_permitidas(admin_user: str):
    return AREAS_POR_ADMIN.get(admin_user, set())


def _aplicar_scope_admin(df: pd.DataFrame, admin_user: str) -> pd.DataFrame:
    """Restringe visualización por áreas según el admin autenticado."""
    permitidas = _get_areas_permitidas(admin_user)
    if permitidas is None:
        return df.copy()
    if df.empty:
        return df.copy()
    return df[df["Area"].isin(permitidas)].copy()

def _preparar_df_dashboard(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte columnas a tipos aptos para análisis/gráficos."""
    d = df.copy()

    def _parse_dt_series(series: pd.Series, only_date: bool = False) -> pd.Series:
        dt = pd.to_datetime(series, errors="coerce")
        mask = dt.isna()
        if mask.any():
            dt_alt = pd.to_datetime(series[mask], errors="coerce", dayfirst=True)
            dt.loc[mask] = dt_alt
        return dt.dt.date if only_date else dt

    def _parse_num_series(series: pd.Series) -> pd.Series:
        s = series.astype(str).str.strip()
        s = s.str.replace(" ", "", regex=False)
        s = s.str.replace(",", ".", regex=False)
        return pd.to_numeric(s, errors="coerce")

    d["Fecha de Turno"] = _parse_dt_series(d["Fecha de Turno"], only_date=True)
    d["Timestamp Entrada"] = _parse_dt_series(d["Timestamp Entrada"])
    d["Timestamp Salida"] = _parse_dt_series(d["Timestamp Salida"])

    d["Estado"] = d["Estado"].fillna("").astype(str).str.strip().str.casefold()
    d["Estado"] = d["Estado"].replace(
        {
            "completo": "Completo",
            "abierto": "Abierto",
            "revision": "Revision",
        }
    )

    d["Horas Trabajadas"] = _parse_num_series(d["Horas Trabajadas"])
    d["Horas Extra"] = _parse_num_series(d.get("Horas Extra", pd.Series(index=d.index, dtype=object)))
    mask_falta_extra = d["Horas Trabajadas"].notna() & d["Horas Extra"].isna()
    if mask_falta_extra.any():
        d.loc[mask_falta_extra, "Horas Extra"] = d.loc[mask_falta_extra, "Horas Trabajadas"].apply(calcular_horas_extra)
    return d

def _build_filter_chips_html(
    rango, fmin, fmax,
    areas_sel, areas_disponibles,
    emp_sel, empleados_disp,
    est_sel, estados,
    total: int, filtrados: int,
) -> str:
    chips = []

    if isinstance(rango, tuple) and len(rango) == 2 and (rango[0] != fmin or rango[1] != fmax):
        chips.append(
            f'<span class="fchip fchip-date">📅 {rango[0].strftime("%d/%m/%y")} – {rango[1].strftime("%d/%m/%y")}</span>'
        )
    if bool(areas_sel) and set(areas_sel) != set(areas_disponibles):
        for a in areas_sel:
            chips.append(f'<span class="fchip fchip-area">🏢 {a}</span>')
    if bool(emp_sel) and set(emp_sel) != set(empleados_disp):
        shown = emp_sel[:4]
        rest = len(emp_sel) - len(shown)
        for e in shown:
            chips.append(f'<span class="fchip fchip-emp">👤 {e}</span>')
        if rest > 0:
            chips.append(f'<span class="fchip fchip-emp">+{rest} más</span>')
    if bool(est_sel) and set(est_sel) != set(estados):
        for s in est_sel:
            chips.append(f'<span class="fchip fchip-est">📊 {s}</span>')

    count_html = (
        f'<span class="fchip fchip-none">📋 {filtrados} / {total} registros</span>'
    )

    if not chips:
        return (
            '<div class="filter-bar">'
            + f'<span class="fchip fchip-none">Sin filtros activos</span>'
            + count_html
            + "</div>"
        )
    return '<div class="filter-bar">' + "".join(chips) + count_html + "</div>"


def _filtros_inline(df: pd.DataFrame, areas_permitidas=None) -> pd.DataFrame:
    """Filtros via popover + chips HTML que muestran las selecciones activas."""
    fechas_validas = df["Fecha de Turno"].dropna()
    fmin = fechas_validas.min() if not fechas_validas.empty else today_ecuador()
    fmax = fechas_validas.max() if not fechas_validas.empty else today_ecuador()

    areas_disponibles = AREAS if areas_permitidas is None else [a for a in AREAS if a in areas_permitidas]
    estados = ["Completo", "Abierto", "Revision"]

    # Leer áreas activas primero para restringir el listado de empleados
    cur_areas = st.session_state.get("filtro_area", areas_disponibles)

    areas_activas  = cur_areas if cur_areas else areas_disponibles
    empleados_disp = sorted(
        df[df["Area"].isin(areas_activas)]["Nombre"].dropna().unique().tolist()
    )

    # Limpiar empleados fuera del área activa
    cur_emp_raw = st.session_state.get("filtro_emp", empleados_disp)
    cur_emp = [e for e in cur_emp_raw if e in empleados_disp]
    if cur_emp != cur_emp_raw:
        st.session_state["filtro_emp"] = cur_emp

    # Botones de control
    c_pop, c_reset = st.columns([1.5, 1])
    with c_pop:
        with st.popover("⚙️ Editar filtros", use_container_width=True):
            st.date_input(
                "📅 Rango de fechas",
                value=(fmin, fmax),
                min_value=fmin,
                max_value=fmax,
                key="filtro_rango",
            )
            st.multiselect("🏢 Área",     areas_disponibles, default=areas_disponibles, key="filtro_area")
            st.multiselect("👤 Empleado", empleados_disp,    default=empleados_disp,    key="filtro_emp")
            st.multiselect("📊 Estado",   estados,           default=estados,           key="filtro_est")

    with c_reset:
        if st.button("↺ Restablecer filtros", use_container_width=True):
            for k in ("filtro_rango", "filtro_area", "filtro_emp", "filtro_est"):
                st.session_state.pop(k, None)
            st.rerun()

    # Releer tras renderizar el popover
    rango     = st.session_state.get("filtro_rango", (fmin, fmax))
    areas_sel = st.session_state.get("filtro_area", areas_disponibles)
    emp_sel   = st.session_state.get("filtro_emp",  empleados_disp)
    est_sel   = st.session_state.get("filtro_est",  estados)

    # Aplicar filtros
    mask = pd.Series(True, index=df.index)
    if isinstance(rango, tuple) and len(rango) == 2 and all(rango):
        f_ini, f_fin = rango
        mask &= df["Fecha de Turno"].between(f_ini, f_fin)
    areas_filtro = areas_sel if areas_sel else areas_disponibles
    emp_filtro   = emp_sel   if emp_sel   else empleados_disp
    est_filtro   = est_sel   if est_sel   else estados
    mask &= df["Area"].isin(areas_filtro)
    mask &= df["Nombre"].isin(emp_filtro)
    mask &= df["Estado"].isin(est_filtro)

    resultado = df[mask].copy()
    total = len(df)
    filtrados = len(resultado)

    # Chips HTML con el resumen visual de filtros activos
    st.markdown(
        _build_filter_chips_html(
            rango, fmin, fmax,
            areas_sel, areas_disponibles,
            emp_sel, empleados_disp,
            est_sel, estados,
            total, filtrados,
        ),
        unsafe_allow_html=True,
    )

    return resultado

def _render_dashboard(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("Sin registros en el rango filtrado.")
        return

    completos = df[df["Estado"] == "Completo"]
    abiertos = df[df["Estado"] == "Abierto"]
    revision = df[df["Estado"] == "Revision"]
    total_horas = float(completos["Horas Trabajadas"].sum(skipna=True))
    horas_extra = float(completos["Horas Extra"].sum(skipna=True))
    funcionarios_activos = int(completos["Nombre"].nunique())
    promedio_turno = total_horas / len(completos) if len(completos) else 0.0
    pct_extra = (horas_extra / total_horas * 100) if total_horas > 0 else 0.0

    cards_html = (
        '<div class="kpi-grid">'
        + _kpi_card(
            "⏱️", "#E6E9F4", BRAND_NAVY,
            "Total horas", f"{total_horas:,.1f}", "h",
            f"Prom. <b>{promedio_turno:.2f}</b> h/turno",
        )
        + _kpi_card(
            "👥", "#E6E9F4", BRAND_NAVY,
            "Funcionarios", f"{funcionarios_activos}", "",
            "con al menos un turno completo",
        )
        + _kpi_card(
            "✅", "#E1F3E7", "#1F9254",
            "Turnos completos", f"{len(completos):,}", "",
            "con entrada y salida",
        )
        + _kpi_card(
            "🟠", "#FEF3E2", "#C97A0A",
            "Turnos abiertos", f"{len(abiertos):,}", "",
            "pendientes de cerrar salida",
        )
        + _kpi_card(
            "📝", "#FDE7E9", BRAND_RED,
            "En revisión", f"{len(revision):,}", "",
            "turnos >18h enviados a super admin",
        )
        + _kpi_card(
            "🔥", "#FDE7E9", BRAND_RED,
            f"Horas extra (>{HORAS_BASE_TURNO:.0f}h)",
            f"{max(0, horas_extra):,.1f}", "h",
            f"<b>{pct_extra:.1f}%</b> del total" if total_horas > 0 else "",
            accent=True,
        )
        + "</div>"
    )
    st.markdown(cards_html, unsafe_allow_html=True)

    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)

    if completos.empty:
        st.caption("Sin turnos completos en el filtro.")
        return

    _section_title("📈 Tendencia diaria por funcionario")
    st.caption("Se muestran los funcionarios con más horas en el período filtrado para facilitar la lectura.")

    top_n = st.slider("Funcionarios en el gráfico", min_value=3, max_value=20, value=8, step=1, key="top_n_func")

    total_por_func = (
        completos.groupby("Nombre", dropna=True)["Horas Trabajadas"]
        .sum()
        .sort_values(ascending=False)
    )
    top_funcionarios = total_por_func.head(top_n).index.tolist()

    lineas_df = (
        completos[completos["Nombre"].isin(top_funcionarios)]
        .groupby(["Fecha de Turno", "Nombre"], dropna=True)["Horas Trabajadas"]
        .sum()
        .reset_index()
        .sort_values(["Fecha de Turno", "Nombre"])
    )

    chart_lineas = (
        alt.Chart(lineas_df)
        .mark_line(
            point=alt.OverlayMarkDef(size=55, filled=True, stroke="white", strokeWidth=1),
            strokeWidth=2.5,
            interpolate="monotone",
        )
        .encode(
            x=alt.X("yearmonthdate(Fecha de Turno):T", title="Fecha", axis=alt.Axis(format="%d %b", labelAngle=0)),
            y=alt.Y("Horas Trabajadas:Q", title="Horas trabajadas"),
            color=alt.Color(
                "Nombre:N",
                title="Funcionario",
                scale=alt.Scale(range=BRAND_CATEGORICAL),
                legend=alt.Legend(orient="bottom", columns=4, symbolType="circle"),
            ),
            tooltip=[
                alt.Tooltip("Fecha de Turno:T", title="Fecha", format="%d %b %Y"),
                alt.Tooltip("Nombre:N", title="Funcionario"),
                alt.Tooltip("Horas Trabajadas:Q", title="Horas", format=".2f"),
            ],
        )
        .properties(height=360)
    )
    st.altair_chart(chart_lineas, use_container_width=True)

    c_left, c_right = st.columns(2, gap="large")

    with c_left:
        _section_title("📊 Horas y horas extra por funcionario")
        agg_emp = (
            completos.groupby("Nombre", dropna=True)[["Horas Trabajadas", "Horas Extra"]]
            .sum()
            .reset_index()
            .sort_values("Horas Trabajadas", ascending=False)
            .head(12)
        )

        barras = agg_emp.melt(
            id_vars=["Nombre"],
            value_vars=["Horas Trabajadas", "Horas Extra"],
            var_name="Indicador",
            value_name="Horas",
        )
        orden_nombres = agg_emp["Nombre"].tolist()
        chart_barras = (
            alt.Chart(barras)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(
                x=alt.X(
                    "Nombre:N",
                    sort=orden_nombres,
                    title="Funcionario",
                    axis=alt.Axis(labelAngle=-35, labelLimit=120),
                ),
                y=alt.Y("Horas:Q", title="Horas"),
                color=alt.Color(
                    "Indicador:N",
                    title=None,
                    scale=alt.Scale(
                        domain=["Horas Trabajadas", "Horas Extra"],
                        range=[BRAND_NAVY, BRAND_RED],
                    ),
                    legend=alt.Legend(orient="top", symbolType="square"),
                ),
                xOffset=alt.XOffset("Indicador:N"),
                tooltip=[
                    alt.Tooltip("Nombre:N", title="Funcionario"),
                    alt.Tooltip("Indicador:N", title="Indicador"),
                    alt.Tooltip("Horas:Q", title="Horas", format=".2f"),
                ],
            )
            .properties(height=340)
        )
        st.altair_chart(chart_barras, use_container_width=True)

    with c_right:
        _section_title("🗂️ Distribución de horas por área")
        por_area_dia = (
            completos.groupby(["Fecha de Turno", "Area"], dropna=True)["Horas Trabajadas"]
            .sum()
            .reset_index()
            .sort_values("Fecha de Turno")
        )
        chart_area = (
            alt.Chart(por_area_dia)
            .mark_area(opacity=0.78, interpolate="monotone", line={"strokeWidth": 1.5})
            .encode(
                x=alt.X("yearmonthdate(Fecha de Turno):T", title="Fecha", axis=alt.Axis(format="%d %b", labelAngle=0)),
                y=alt.Y("Horas Trabajadas:Q", stack="zero", title="Horas"),
                color=alt.Color(
                    "Area:N",
                    title="Área",
                    scale=alt.Scale(range=BRAND_CATEGORICAL),
                    legend=alt.Legend(orient="bottom", symbolType="square"),
                ),
                tooltip=[
                    alt.Tooltip("Fecha de Turno:T", title="Fecha", format="%d %b %Y"),
                    alt.Tooltip("Area:N", title="Área"),
                    alt.Tooltip("Horas Trabajadas:Q", title="Horas", format=".2f"),
                ],
            )
            .properties(height=340)
        )
        st.altair_chart(chart_area, use_container_width=True)

_ESTADO_ROW_BG = {
    "Completo": "#F2FFF6",
    "Abierto":  "#FFFDF0",
    "Revision": "#FFF5F5",
}

def _style_tabla(df: pd.DataFrame):
    """Aplica colores de fondo por fila según Estado y formatos de columna."""
    def _row_bg(row):
        bg = _ESTADO_ROW_BG.get(row.get("Estado", ""), "")
        return [f"background-color: {bg}" if bg else ""] * len(row)

    return df.style.apply(_row_bg, axis=1).format(
        {
            "Horas Trabajadas": lambda v: f"{v:.2f} h" if pd.notna(v) and v != 0 else "—",
            "Horas Extra":      lambda v: f"{v:.2f} h" if pd.notna(v) and v != 0 else "—",
        },
        na_rep="—",
    )


def _render_tabla(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("Sin registros en el rango filtrado.")
        return

    df = df.copy()
    for col in ("Horas Trabajadas", "Horas Extra"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).round(2)

    # Leyenda de colores por estado
    st.markdown(
        '<div style="display:flex;gap:14px;align-items:center;margin-bottom:8px;font-size:0.78rem;font-weight:600;">'
        '<span style="display:flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;border-radius:3px;background:#F2FFF6;border:1px solid #6FCF97;display:inline-block;"></span>Completo</span>'
        '<span style="display:flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;border-radius:3px;background:#FFFDF0;border:1px solid #F5CC7A;display:inline-block;"></span>Abierto</span>'
        '<span style="display:flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;border-radius:3px;background:#FFF5F5;border:1px solid #F5A0A6;display:inline-block;"></span>Revisión</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    st.dataframe(
        _style_tabla(df),
        use_container_width=True,
        hide_index=True,
        height=540,
        column_config={
            "Nombre":            st.column_config.TextColumn("Empleado",         width="medium"),
            "Area":              st.column_config.TextColumn("Área",             width="small"),
            "Fecha de Turno":    st.column_config.DateColumn("Fecha",            width="small",  format="DD/MM/YYYY"),
            "Timestamp Entrada": st.column_config.TextColumn("Entrada",          width="medium"),
            "Timestamp Salida":  st.column_config.TextColumn("Salida",           width="medium"),
            "Horas Trabajadas":  st.column_config.TextColumn("Horas trabajadas", width="small"),
            "Horas Extra":       st.column_config.TextColumn("Horas extra",      width="small"),
            "Estado":            st.column_config.TextColumn("Estado",           width="small"),
            "Observaciones":     st.column_config.TextColumn("Observaciones",    width="large"),
        },
    )

    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 Descargar CSV",
        data=csv,
        file_name=f"registros_{today_ecuador().isoformat()}.csv",
        mime="text/csv",
        use_container_width=True,
    )

def _time_input(label: str, default: time, key: str) -> time:
    """Entrada HH:MM como dos campos numéricos con el separador ':' visible entre ellos."""
    st.markdown(
        f"<p style='font-size:.875rem;font-weight:600;margin:0 0 4px;'>{label}</p>",
        unsafe_allow_html=True,
    )
    col_h, col_sep, col_m = st.columns([4, 1, 4])
    with col_h:
        h = st.number_input("HH", min_value=0, max_value=23, value=default.hour,
                            key=f"{key}_h", label_visibility="collapsed")
    with col_sep:
        st.markdown(
            "<div style='text-align:center;padding-top:6px;"
            "font-size:1.4rem;font-weight:800;color:#1E2D78;line-height:2.2;'>:</div>",
            unsafe_allow_html=True,
        )
    with col_m:
        m = st.number_input("MM", min_value=0, max_value=59, value=default.minute,
                            key=f"{key}_m", label_visibility="collapsed")
    return time(int(h), int(m))


def _render_correcciones(areas_permitidas=None) -> None:
    """Flujo para cerrar turnos abiertos o crear registros históricos. Solo super admin."""
    st.caption(
        "Úsalo cuando un empleado olvidó marcar entrada, salida o ambas. "
        "Toda corrección queda registrada en 'Observaciones' con el prefijo 'Registro manual:' para auditoría."
    )

    if areas_permitidas is None:
        areas_corr = AREAS
    else:
        areas_corr = [a for a in AREAS if a in areas_permitidas]

    if not areas_corr:
        st.warning("No tienes áreas habilitadas para realizar correcciones.")
        return

    df_actual = leer_registros()

    # --- Tabla de turnos pendientes ---
    st.markdown("#### Turnos pendientes de corrección")
    mask_area = df_actual["Area"].isin(areas_corr) if areas_corr else pd.Series(False, index=df_actual.index)
    mask_estado = df_actual["Estado"].fillna("").str.strip() == "Revision"
    df_pendientes = df_actual[mask_area & mask_estado][
        ["Nombre", "Area", "Fecha de Turno", "Timestamp Entrada", "Estado", "Observaciones"]
    ].copy()

    if df_pendientes.empty:
        st.success("No hay turnos pendientes de corrección.")
    else:
        st.warning(f"**{len(df_pendientes)} turno(s)** requieren corrección. Selecciona una fila para cargar el empleado automáticamente.")
        sel = st.dataframe(
            df_pendientes.reset_index(drop=True),
            use_container_width=True,
            selection_mode="single-row",
            on_select="rerun",
            key="tabla_pendientes",
            column_config={
                "Nombre": st.column_config.TextColumn("Empleado", width="medium"),
                "Area": st.column_config.TextColumn("Área", width="small"),
                "Fecha de Turno": st.column_config.TextColumn("Fecha Turno", width="small"),
                "Timestamp Entrada": st.column_config.TextColumn("Entrada", width="medium"),
                "Estado": st.column_config.TextColumn("Estado", width="small"),
                "Observaciones": st.column_config.TextColumn("Observaciones", width="large"),
            },
        )
        filas = sel.selection.rows if sel and hasattr(sel, "selection") else []
        if filas:
            fila_sel = df_pendientes.reset_index(drop=True).iloc[filas[0]]
            new_area = fila_sel["Area"]
            new_emp = fila_sel["Nombre"]
            if new_area in areas_corr:
                st.session_state["area_corr"] = new_area
            lista_emp_prefill = EMPLEADOS_POR_AREA.get(new_area, [])
            if new_emp in lista_emp_prefill:
                st.session_state["emp_corr"] = new_emp

    st.divider()
    st.markdown("#### Formulario de corrección")

    ca_corr, ce_corr = st.columns(2)
    with ca_corr:
        area_corr = st.selectbox("Área", areas_corr, key="area_corr")
    with ce_corr:
        lista_empleados = EMPLEADOS_POR_AREA.get(area_corr, []) if area_corr else []
        emp_corr = st.selectbox("Empleado a corregir", lista_empleados, key="emp_corr")

    modo = st.radio(
        "¿Qué quieres hacer?",
        [
            "Cerrar un turno abierto (olvido de SALIDA)",
            "Crear registro histórico completo (olvido de ENTRADA y/o SALIDA)",
        ],
        key="modo_corr",
    )

    ahora_min = now_ecuador().replace(second=0, microsecond=0).time()

    if modo.startswith("Cerrar"):
        emp_norm = str(emp_corr).strip()
        _nombre = df_actual["Nombre"].fillna("").astype(str).str.strip()
        _estado = df_actual["Estado"].fillna("").astype(str).str.strip()
        _obs = df_actual["Observaciones"].fillna("").astype(str).str.strip()
        mask_primary = (_nombre == emp_norm) & (_estado.isin(["Abierto", "Revision"]))
        mask_legacy = (_nombre == emp_norm) & (_obs == "Abierto") & (_estado == "")
        df_abiertos = df_actual[mask_primary | mask_legacy]
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
                f_sal = st.date_input("Fecha de salida", value=today_ecuador(), key="f_sal_close")
            with c2:
                h_sal = _time_input("Hora de salida", ahora_min, "h_sal_close")

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
                        ts_entrada_str = str(df_actual.loc[idx_obj, "Timestamp Entrada"])
                        if not guardar_salida(emp_corr, ts_entrada_str, ts_sal, horas, f"Registro manual: {det}"):
                            st.error("El turno ya no existe (puede haber sido modificado). Recarga la página.")
                        else:
                            st.toast(f"Turno cerrado · {horas:.2f} h trabajadas", icon="✅")
                            st.rerun()
    else:
        st.caption("Ambas marcas se ingresan manualmente. Úsalo solo para turnos ya pasados.")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Entrada**")
            f_ent = st.date_input("Fecha", value=today_ecuador(), key="f_ent_m")
            h_ent = _time_input("Hora", time(7, 0), "h_ent_m")
        with c2:
            st.markdown("**Salida**")
            f_sal = st.date_input("Fecha", value=today_ecuador(), key="f_sal_m")
            h_sal = _time_input("Hora", time(17, 0), "h_sal_m")

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
                    append_registro({
                        "Nombre": emp_corr,
                        "Area": AREA_DE.get(emp_corr, ""),
                        "Fecha de Turno": ts_in.strftime("%Y-%m-%d"),
                        "Timestamp Entrada": ts_in.strftime(TS_FMT),
                        "Timestamp Salida": ts_out.strftime(TS_FMT),
                        "Horas Trabajadas": horas,
                        "Horas Extra": calcular_horas_extra(horas),
                        "Estado": "Completo",
                        "Observaciones": f"Registro manual: {det}",
                    })
                    st.toast(f"Registro creado · {horas:.2f} h trabajadas", icon="✅")
                    st.rerun()

def vista_super_admin() -> None:
    usuario = st.session_state["usuario"]
    admin_rol = st.session_state.get("admin_rol", "")
    admin_user = st.session_state.get("admin_user", "")
    areas_permitidas = _get_areas_permitidas(admin_user)

    _inject_brand_css()

    st.title("⏱️ Marcador de Horas — Panel Administrativo")

    ch1, ch2 = st.columns([5, 1])
    with ch1:
        st.markdown(
            f"""
            <div class="brand-header">
                <div class="user-block">
                    <div class="avatar">👔</div>
                    <div>
                        <div class="uname">{usuario}</div>
                        <span class="role">💼 {admin_rol}</span>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with ch2:
        st.write("")
        if st.button("🚪 Cerrar sesión", use_container_width=True):
            logout()
            st.rerun()

    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)

    df_raw = leer_registros()
    df_scope = _aplicar_scope_admin(df_raw, admin_user)
    df_dash = _preparar_df_dashboard(df_scope)

    if areas_permitidas is None:
        st.caption("🔓 Acceso total a todas las áreas.")
    else:
        st.caption(f"🔐 Áreas habilitadas para este usuario: {', '.join(sorted(areas_permitidas))}")

    df_filt = _filtros_inline(df_dash, areas_permitidas=areas_permitidas)

    tab_dash, tab_tabla, tab_corr = st.tabs(["📊 Dashboard", "📋 Tabla", "🛠️ Correcciones"])
    with tab_dash:
        _render_dashboard(df_filt)
    with tab_tabla:
        _render_tabla(df_filt)
    with tab_corr:
        _render_correcciones(areas_permitidas=areas_permitidas)
