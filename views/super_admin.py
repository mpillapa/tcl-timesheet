import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime, date, time, timedelta
from core.auth import logout, confiar_equipo_ui
from core.data import (
    leer_registros,
    leer_historico,
    leer_horas_esperadas,
    append_registro,
    append_registros_batch,
    actualizar_por_entrada,
    eliminar_por_entrada,
    archivar_historico,
    calcular_horas,
    calcular_horas_efectivas,
    calcular_horas_extra,
    buscar_turno_abierto_idx,
)
from core.employees import AREAS, EMPLEADOS_POR_AREA, AREA_DE
from core.config import UMBRAL_HORAS_EXTRA, UMBRAL_OLVIDO_H, TS_FMT, MIN_JUSTIF_CHARS, HORAS_BASE_TURNO
from core.marcado import guardar_salida, barrer_turnos_olvidados
from core.time_utils import now_ecuador, today_ecuador, parse_timestamp_flexible, parse_fecha_flexible
from core.ui_utils import bloquear_doble_click, set_flash, mostrar_flash


# Paleta de marca compartida con el marcador (única fuente: core/ui_theme).
from core.ui_theme import (
    BRAND_NAVY, BRAND_NAVY_MID, BRAND_NAVY_SOFT,
    BRAND_RED, BRAND_RED_SOFT, BRAND_VAC, BRAND_CUOTA,
    BRAND_BG_SOFT, BRAND_TEXT, BRAND_MUTED,
    BRAND_CATEGORICAL, BRAND_EVENTO,
)

# Tipos de eventualidad y motivos (compartidos por el módulo y la clasificación).
EVENTO_FALTA = "Justificación de falta"
EVENTO_PERMISO = "Solicitud de permiso"
EVENTO_VACACIONES = "Vacaciones"
TIPOS_EVENTO = [EVENTO_FALTA, EVENTO_PERMISO, EVENTO_VACACIONES]

SUBCATEGORIAS_EVENTO = [
    "Capacitación",
    "Comisión",
    "Salud",
    "Calamidad Doméstica",
    "Permiso Personal",
    "Maternidad",
    "Atención fuera de oficina",
]


def _clasificar_obs(obs) -> str:
    """Clasifica un turno por su observación: 'vacaciones', 'evento'
    (falta/permiso) o 'trabajo' (turno normal)."""
    o = str(obs or "").strip().lower()
    if o.startswith(EVENTO_VACACIONES.lower()):
        return "vacaciones"
    if o.startswith(EVENTO_FALTA.lower()) or o.startswith(EVENTO_PERMISO.lower()):
        return "evento"
    return "trabajo"


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
    icon_html = (
        f'<div class="kpi-icon" style="background:{icon_bg};color:{icon_color};">{icon}</div>'
        if icon else ""
    )
    return (
        f'<div class="kpi-card{accent_cls}">'
        f'{icon_html}'
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


# Áreas de las unidades de carga (operaciones). Erick (jefe de operaciones) ve
# solo estas; Paul ve todas MENOS estas; Fabian y Manuel ven todo.
AREAS_CARGA = {"CARGA NAL UIO", "CARGA NAL GYE", "CARGA NAL SCY", "CARGA INT GYE"}

AREAS_POR_ADMIN = {
    "dbuestan":  {"IMPORT"},
    "pmena":     set(AREAS) - AREAS_CARGA,   # todo menos las áreas de carga de Erick
    "ecamposano": set(AREAS_CARGA),          # solo las áreas de carga
    "gproanio":  {"BODEGA"},
    "fherrera":  None,                        # acceso total
    "mpillapa":  None,                        # acceso total
    # Solo lectura
    "ereyes":    {"BODEGA"},
    "pmaldonado": {"BODEGA", "DOCUMENTAL", "SUPERVISORES", "CALIDAD"},
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
    d["Horas Efectivas"] = _parse_num_series(d.get("Horas Efectivas", pd.Series(index=d.index, dtype=object)))
    mask_falta_extra = d["Horas Trabajadas"].notna() & d["Horas Extra"].isna()
    if mask_falta_extra.any():
        d.loc[mask_falta_extra, "Horas Extra"] = d.loc[mask_falta_extra, "Horas Trabajadas"].apply(calcular_horas_extra)
    mask_falta_efect = d["Horas Trabajadas"].notna() & d["Horas Efectivas"].isna()
    if mask_falta_efect.any():
        d.loc[mask_falta_efect, "Horas Efectivas"] = d.loc[mask_falta_efect, "Horas Trabajadas"].apply(calcular_horas_efectivas)
    return d

def _iso_week_options(df: pd.DataFrame) -> dict:
    """Devuelve {label: (lunes, domingo)} para cada semana ISO presente en el df."""
    fechas = df["Fecha de Turno"].dropna()
    if fechas.empty:
        return {}
    pares = sorted(
        {(d.isocalendar()[0], d.isocalendar()[1]) for d in fechas},
        reverse=True,
    )
    opciones = {}
    for year, week in pares:
        lunes = date.fromisocalendar(year, week, 1)
        domingo = lunes + timedelta(days=6)
        label = f"Sem. {week:02d} · {year}  ({lunes.day} {lunes.strftime('%b')} – {domingo.day} {domingo.strftime('%b')})"
        opciones[label] = (lunes, domingo)
    return opciones


_SEM_TODAS = "— Todas las semanas —"
_MES_TODOS = "— Todos los meses —"

_MESES_ES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}


def _month_options(df: pd.DataFrame) -> dict:
    """Devuelve {label: (primer_dia, ultimo_dia)} para cada mes presente en el df."""
    import calendar
    fechas = df["Fecha de Turno"].dropna()
    if fechas.empty:
        return {}
    pares = sorted(
        {(d.year, d.month) for d in fechas},
        reverse=True,
    )
    opciones = {}
    for year, month in pares:
        ultimo_dia = calendar.monthrange(year, month)[1]
        primero = date(year, month, 1)
        ultimo = date(year, month, ultimo_dia)
        label = f"{_MESES_ES[month]} {year}"
        opciones[label] = (primero, ultimo)
    return opciones


def _clear_quick_filters():
    st.session_state["filtro_semana_iso"] = _SEM_TODAS
    st.session_state["filtro_mes"] = _MES_TODOS


def _build_filter_chips_html(
    rango, fmin, fmax,
    areas_sel, areas_disponibles,
    emp_sel, empleados_disp,
    est_sel, estados,
    semana_key: str,
    mes_key: str,
    total: int, filtrados: int,
) -> str:
    chips = []

    if semana_key and semana_key != _SEM_TODAS:
        chips.append(f'<span class="fchip fchip-date">{semana_key.strip()}</span>')
    elif mes_key and mes_key != _MES_TODOS:
        chips.append(f'<span class="fchip fchip-date">{mes_key}</span>')
    elif isinstance(rango, tuple) and len(rango) == 2 and (rango[0] != fmin or rango[1] != fmax):
        chips.append(
            f'<span class="fchip fchip-date">{rango[0].strftime("%d/%m/%y")} – {rango[1].strftime("%d/%m/%y")}</span>'
        )
    if bool(areas_sel) and set(areas_sel) != set(areas_disponibles):
        for a in areas_sel:
            chips.append(f'<span class="fchip fchip-area">{a}</span>')
    if bool(emp_sel) and set(emp_sel) != set(empleados_disp):
        shown = emp_sel[:4]
        rest = len(emp_sel) - len(shown)
        for e in shown:
            chips.append(f'<span class="fchip fchip-emp">{e}</span>')
        if rest > 0:
            chips.append(f'<span class="fchip fchip-emp">+{rest} más</span>')
    if bool(est_sel) and set(est_sel) != set(estados):
        for s in est_sel:
            chips.append(f'<span class="fchip fchip-est">{s}</span>')

    count_html = (
        f'<span class="fchip fchip-none">{filtrados} / {total} registros</span>'
    )

    if not chips:
        return (
            '<div class="filter-bar">'
            + f'<span class="fchip fchip-none">Sin filtros activos</span>'
            + count_html
            + "</div>"
        )
    return '<div class="filter-bar">' + "".join(chips) + count_html + "</div>"


def _filtros_inline(df: pd.DataFrame, areas_permitidas=None, df_base_prev=None):
    """Filtros en el sidebar + chips HTML en el cuerpo con las selecciones activas.

    Devuelve (df_filtrado, df_periodo_anterior, df_todos_los_meses).
    `df_base_prev` es el universo completo (típicamente activos + histórico):
    sobre él se calculan el período anterior (deltas de KPIs) y la vista de
    todos los meses (comparativo mensual, que ignora el filtro de fechas).
    Si no se pasa, se usa `df`."""
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

    semanas_iso  = _iso_week_options(df)
    meses        = _month_options(df)
    st.session_state["_semanas_iso_map"] = semanas_iso
    st.session_state["_meses_map"] = meses
    opciones_sem = [_SEM_TODAS] + list(semanas_iso.keys())
    opciones_mes = [_MES_TODOS] + list(meses.keys())

    # Filtros en el sidebar: siempre visibles sin empujar el contenido, y los
    # desplegables funcionan bien (dentro de st.popover a veces no despliegan
    # la lista — por eso se abandonó ese contenedor).
    with st.sidebar:
        st.markdown("## Filtros")
        st.selectbox("Mes", opciones_mes, key="filtro_mes")
        st.selectbox("Semana ISO", opciones_sem, key="filtro_semana_iso")
        st.date_input(
            "Rango de fechas",
            value=(fmin, fmax),
            min_value=fmin,
            max_value=fmax,
            key="filtro_rango",
            on_change=_clear_quick_filters,
        )
        st.multiselect("Área",     areas_disponibles, default=areas_disponibles, key="filtro_area")
        st.multiselect("Empleado", empleados_disp,    default=empleados_disp,    key="filtro_emp")
        st.multiselect("Estado",   estados,           default=estados,           key="filtro_est")
        if st.button("Restablecer filtros", use_container_width=True):
            for k in ("filtro_mes", "filtro_semana_iso", "filtro_rango", "filtro_area", "filtro_emp", "filtro_est"):
                st.session_state.pop(k, None)
            st.rerun()

    # Releer tras renderizar el popover
    semana_key   = st.session_state.get("filtro_semana_iso", _SEM_TODAS)
    mes_key      = st.session_state.get("filtro_mes", _MES_TODOS)
    rango_widget = st.session_state.get("filtro_rango", (fmin, fmax))
    areas_sel    = st.session_state.get("filtro_area", areas_disponibles)
    emp_sel      = st.session_state.get("filtro_emp",  empleados_disp)
    est_sel      = st.session_state.get("filtro_est",  estados)

    # Prioridad de fechas: semana ISO > mes > rango manual
    # No se intenta escribir en el widget date_input desde callbacks (no funciona de forma fiable)
    if semana_key != _SEM_TODAS and semana_key in semanas_iso:
        rango = semanas_iso[semana_key]
    elif mes_key != _MES_TODOS and mes_key in meses:
        rango = meses[mes_key]
    elif isinstance(rango_widget, tuple) and len(rango_widget) == 2 and all(rango_widget):
        rango = rango_widget
    else:
        rango = (fmin, fmax)

    mask = pd.Series(True, index=df.index)
    if isinstance(rango, tuple) and len(rango) == 2 and all(rango):
        mask &= df["Fecha de Turno"].between(rango[0], rango[1])
    areas_filtro = areas_sel if areas_sel else areas_disponibles
    emp_filtro   = emp_sel   if emp_sel   else empleados_disp
    est_filtro   = est_sel   if est_sel   else estados
    mask &= df["Area"].isin(areas_filtro)
    mask &= df["Nombre"].isin(emp_filtro)
    mask &= df["Estado"].isin(est_filtro)

    resultado = df[mask].copy()
    total = len(df)
    filtrados = len(resultado)

    st.markdown(
        _build_filter_chips_html(
            rango, fmin, fmax,
            areas_sel, areas_disponibles,
            emp_sel, empleados_disp,
            est_sel, estados,
            semana_key,
            mes_key,
            total, filtrados,
        ),
        unsafe_allow_html=True,
    )

    # Período anterior comparable: misma duración inmediatamente antes del rango
    # activo, con los mismos filtros de área/empleado/estado. Alimenta los deltas
    # de los KPIs. Solo aplica cuando hay un filtro de fechas activo (con el
    # rango completo no hay "anterior" que tenga sentido).
    base_prev = df_base_prev if df_base_prev is not None else df
    df_prev = base_prev.iloc[0:0]
    hay_filtro_fechas = (
        isinstance(rango, tuple) and len(rango) == 2 and all(rango)
        and (rango[0] != fmin or rango[1] != fmax)
    )
    if hay_filtro_fechas:
        duracion = rango[1] - rango[0]
        prev_fin = rango[0] - timedelta(days=1)
        prev_ini = prev_fin - duracion
        mask_prev = base_prev["Fecha de Turno"].between(prev_ini, prev_fin)
        mask_prev &= base_prev["Area"].isin(areas_filtro)
        mask_prev &= base_prev["Nombre"].isin(emp_filtro)
        mask_prev &= base_prev["Estado"].isin(est_filtro)
        df_prev = base_prev[mask_prev].copy()

    # Universo de todos los meses SIN filtro de fechas (para el comparativo
    # mensual). El filtro de empleado solo se aplica si el usuario lo redujo
    # activamente: empleados_disp sale de los datos activos, y aplicarlo
    # siempre borraría de los meses históricos a quienes ya no marcan hoy.
    mask_todo = base_prev["Area"].isin(areas_filtro)
    if bool(emp_sel) and set(emp_sel) != set(empleados_disp):
        mask_todo &= base_prev["Nombre"].isin(emp_sel)
    mask_todo &= base_prev["Estado"].isin(est_filtro)
    df_todo_meses = base_prev[mask_todo].copy()

    return resultado, df_prev, df_todo_meses

def _render_dashboard(df: pd.DataFrame, df_prev: pd.DataFrame = None) -> None:
    if df.empty:
        st.info("Sin registros en el rango filtrado.")
        return

    completos = df[df["Estado"] == "Completo"]
    abiertos = df[df["Estado"] == "Abierto"]
    revision = df[df["Estado"] == "Revision"]
    total_horas = float(completos["Horas Efectivas"].sum(skipna=True))
    funcionarios_activos = int(completos["Nombre"].nunique())
    promedio_turno = total_horas / len(completos) if len(completos) else 0.0

    # Comparación contra el período anterior de igual duración (mismos filtros
    # de área/empleado/estado). Sin período anterior, los deltas no se muestran.
    delta_horas_txt = ""
    delta_turnos_txt = ""
    if df_prev is not None and not df_prev.empty:
        prev_comp = df_prev[df_prev["Estado"] == "Completo"]
        prev_horas = float(prev_comp["Horas Efectivas"].sum(skipna=True))
        if prev_horas > 0:
            d_h = (total_horas - prev_horas) / prev_horas * 100
            delta_horas_txt = f" · <b>{d_h:+.1f}%</b> vs período anterior"
        if len(prev_comp) > 0:
            d_t = (len(completos) - len(prev_comp)) / len(prev_comp) * 100
            delta_turnos_txt = f" · <b>{d_t:+.1f}%</b> vs período anterior"

    # Cuota del período — misma lógica que el gráfico de progreso
    df_esp = leer_horas_esperadas()
    cuota_periodo = 0.0
    if not df_esp.empty and not completos.empty:
        meses_filtro = (
            completos.assign(
                Año=completos["Fecha de Turno"].apply(lambda d: int(d.year) if pd.notna(d) else None),
                Mes=completos["Fecha de Turno"].apply(lambda d: int(d.month) if pd.notna(d) else None),
            )
            .dropna(subset=["Año", "Mes"])[["Año", "Mes"]]
            .drop_duplicates()
            .astype(int)
        )
        cuota_periodo = float(df_esp.merge(meses_filtro, on=["Año", "Mes"])["Horas"].sum())

    if cuota_periodo > 0:
        # Horas que cada empleado acumuló por encima de la cuota del período
        agg_emp_kpi = completos.groupby("Nombre", dropna=True)["Horas Efectivas"].sum()
        horas_sobre_cuota = float((agg_emp_kpi - cuota_periodo).clip(lower=0).sum())
        kpi_extra_label = "Horas sobre cuota del período"
        kpi_extra_sub = f"cuota: <b>{cuota_periodo:.0f} h</b>/persona · <b>{pct_sobre:.1f}%</b> del total" if (pct_sobre := (horas_sobre_cuota / total_horas * 100) if total_horas > 0 else 0.0) >= 0 else ""
    else:
        horas_sobre_cuota = float(completos["Horas Extra"].sum(skipna=True))
        kpi_extra_label = f"Horas extra (>{HORAS_BASE_TURNO:.0f}h/turno)"
        kpi_extra_sub = f"<b>{(horas_sobre_cuota / total_horas * 100) if total_horas > 0 else 0.0:.1f}%</b> del total" if total_horas > 0 else ""

    cards_html = (
        '<div class="kpi-grid">'
        + _kpi_card(
            "", "#E6E9F4", BRAND_NAVY,
            "Total horas", f"{total_horas:,.1f}", "h",
            f"Prom. <b>{promedio_turno:.2f}</b> h/turno{delta_horas_txt}",
        )
        + _kpi_card(
            "", "#E6E9F4", BRAND_NAVY,
            "Funcionarios", f"{funcionarios_activos}", "",
            "con al menos un turno completo",
        )
        + _kpi_card(
            "", "#E1F3E7", "#1F9254",
            "Turnos completos", f"{len(completos):,}", "",
            f"con entrada y salida{delta_turnos_txt}",
        )
        + _kpi_card(
            "", "#FEF3E2", "#C97A0A",
            "Turnos abiertos", f"{len(abiertos):,}", "",
            "pendientes de cerrar salida",
        )
        + _kpi_card(
            "", "#FDE7E9", BRAND_RED,
            "En revisión", f"{len(revision):,}", "",
            f"turnos >{UMBRAL_OLVIDO_H}h enviados a super admin",
        )
        + _kpi_card(
            "", "#FDE7E9", BRAND_RED,
            kpi_extra_label,
            f"{max(0, horas_sobre_cuota):,.1f}", "h",
            kpi_extra_sub,
            accent=True,
        )
        + "</div>"
    )
    st.markdown(cards_html, unsafe_allow_html=True)

    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)

    if completos.empty:
        st.caption("Sin turnos completos en el filtro.")
        return

    # Vista agregada por área: los jefes piensan primero en áreas y luego en
    # personas. Solo tiene sentido cuando el filtro abarca más de una.
    areas_en_filtro = completos["Area"].dropna()
    areas_en_filtro = areas_en_filtro[areas_en_filtro != ""]
    if areas_en_filtro.nunique() > 1:
        _section_title("Horas por área")
        agg_area = (
            completos[completos["Area"].fillna("") != ""]
            .groupby("Area", dropna=True)
            .agg(**{
                "Horas Efectivas": ("Horas Efectivas", "sum"),
                "Horas Extra": ("Horas Extra", "sum"),
                "Funcionarios": ("Nombre", "nunique"),
                "Turnos": ("Nombre", "size"),
            })
            .reset_index()
            .sort_values("Horas Efectivas", ascending=False)
        )
        barras_area = agg_area.melt(
            id_vars=["Area", "Funcionarios", "Turnos"],
            value_vars=["Horas Efectivas", "Horas Extra"],
            var_name="Tipo",
            value_name="Horas",
        )
        chart_area = (
            alt.Chart(barras_area)
            .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
            .encode(
                y=alt.Y("Area:N", sort=agg_area["Area"].tolist(), title=None),
                x=alt.X("Horas:Q", title="Horas"),
                yOffset=alt.YOffset("Tipo:N"),
                color=alt.Color(
                    "Tipo:N",
                    title=None,
                    scale=alt.Scale(
                        domain=["Horas Efectivas", "Horas Extra"],
                        range=[BRAND_NAVY, BRAND_RED],
                    ),
                    legend=alt.Legend(orient="top", symbolType="square"),
                ),
                tooltip=[
                    alt.Tooltip("Area:N", title="Área"),
                    alt.Tooltip("Tipo:N", title="Tipo"),
                    alt.Tooltip("Horas:Q", title="Horas", format=".1f"),
                    alt.Tooltip("Funcionarios:Q", title="Funcionarios", format="d"),
                    alt.Tooltip("Turnos:Q", title="Turnos completos", format="d"),
                ],
            )
            .properties(height=max(160, 52 * len(agg_area)))
        )
        st.altair_chart(chart_area, use_container_width=True)

    _section_title("Tendencia diaria por funcionario")
    st.caption(
        "Se muestran los funcionarios con más horas en el período filtrado. "
        "Haz clic en un nombre de la leyenda para resaltar su línea (clic fuera para restaurar); "
        "arrastra o usa la rueda del mouse sobre el gráfico para hacer zoom en las fechas."
    )

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

    # Clic en la leyenda resalta un funcionario y atenúa el resto (soluciona el
    # "spaghetti" de muchas líneas cruzadas); zoom/arrastre solo en fechas.
    sel_func = alt.selection_point(fields=["Nombre"], bind="legend")
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
            opacity=alt.condition(sel_func, alt.value(1.0), alt.value(0.12)),
            tooltip=[
                alt.Tooltip("Fecha de Turno:T", title="Fecha", format="%d %b %Y"),
                alt.Tooltip("Nombre:N", title="Funcionario"),
                alt.Tooltip("Horas Trabajadas:Q", title="Horas", format=".2f"),
            ],
        )
        .add_params(sel_func)
        .properties(height=360)
        .interactive(bind_y=False)
    )
    st.altair_chart(chart_lineas, use_container_width=True)

    _section_title("Mapa de calor: horas por día y funcionario")
    st.caption(
        "Cada celda es un día trabajado; a mayor intensidad, más horas. Los tonos "
        f"rojos superan la jornada base de {HORAS_BASE_TURNO:.0f} h: una fila con "
        "rojos repetidos concentra horas extra de forma recurrente."
    )
    heat_df = (
        completos.dropna(subset=["Fecha de Turno"])
        .groupby(["Fecha de Turno", "Nombre"], dropna=True)["Horas Trabajadas"]
        .sum()
        .reset_index()
    )
    orden_heat = (
        heat_df.groupby("Nombre")["Horas Trabajadas"]
        .sum().sort_values(ascending=False).index.tolist()
    )
    chart_heat = (
        alt.Chart(heat_df)
        .mark_rect(cornerRadius=2, stroke="#FFFFFF", strokeWidth=1)
        .encode(
            x=alt.X(
                "yearmonthdate(Fecha de Turno):O",
                title="Fecha",
                axis=alt.Axis(format="%d %b", labelAngle=-45),
            ),
            y=alt.Y("Nombre:N", sort=orden_heat, title=None),
            color=alt.Color(
                "Horas Trabajadas:Q",
                title="Horas",
                scale=alt.Scale(
                    domain=[0, HORAS_BASE_TURNO, 14],
                    range=["#EEF2FB", BRAND_NAVY, BRAND_RED],
                    clamp=True,
                ),
            ),
            tooltip=[
                alt.Tooltip("Fecha de Turno:T", title="Fecha", format="%d %b %Y"),
                alt.Tooltip("Nombre:N", title="Funcionario"),
                alt.Tooltip("Horas Trabajadas:Q", title="Horas", format=".2f"),
            ],
        )
        .properties(height=max(280, min(34 * len(orden_heat), 1700)))
    )
    st.altair_chart(chart_heat, use_container_width=True)

    _section_title("Evolución de horas extra")
    base_extra = completos.dropna(subset=["Fecha de Turno"]).copy()
    fechas_dt = pd.to_datetime(base_extra["Fecha de Turno"])
    base_extra["_Semana"] = (fechas_dt - pd.to_timedelta(fechas_dt.dt.weekday, unit="D")).dt.date
    base_extra["_DiaN"] = fechas_dt.dt.weekday

    col_sem, col_dow = st.columns([3, 2])
    with col_sem:
        st.caption("Total de horas extra por semana (cada punto es la semana que inicia ese lunes).")
        serie_sem = base_extra.groupby("_Semana")["Horas Extra"].sum().reset_index()
        if len(serie_sem) < 2:
            st.info("Amplía el filtro a más de una semana para ver la tendencia.")
        else:
            base_c = alt.Chart(serie_sem).encode(
                x=alt.X("_Semana:T", title="Semana", axis=alt.Axis(format="%d %b")),
                y=alt.Y("Horas Extra:Q", title="Horas extra"),
                tooltip=[
                    alt.Tooltip("_Semana:T", title="Semana del", format="%d %b %Y"),
                    alt.Tooltip("Horas Extra:Q", title="Horas extra", format=".1f"),
                ],
            )
            chart_sem = alt.layer(
                base_c.mark_area(opacity=0.15, color=BRAND_RED),
                base_c.mark_line(
                    color=BRAND_RED,
                    strokeWidth=2.5,
                    interpolate="monotone",
                    point=alt.OverlayMarkDef(size=55, filled=True, color=BRAND_RED,
                                             stroke="white", strokeWidth=1),
                ),
            ).properties(height=300)
            st.altair_chart(chart_sem, use_container_width=True)
    with col_dow:
        st.caption("Promedio de horas extra por turno según el día de la semana.")
        dias_lbl = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        agg_dow = (
            base_extra.groupby("_DiaN")
            .agg(**{"Total": ("Horas Extra", "sum"), "Turnos": ("Horas Extra", "size")})
            .reset_index()
        )
        agg_dow["Promedio"] = (agg_dow["Total"] / agg_dow["Turnos"]).round(2)
        agg_dow["Día"] = agg_dow["_DiaN"].map(dict(enumerate(dias_lbl)))
        chart_dow = (
            alt.Chart(agg_dow)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, color=BRAND_NAVY)
            .encode(
                x=alt.X("Día:N", sort=dias_lbl, title=None, axis=alt.Axis(labelAngle=0)),
                y=alt.Y("Promedio:Q", title="Horas extra prom. por turno"),
                tooltip=[
                    alt.Tooltip("Día:N", title="Día"),
                    alt.Tooltip("Promedio:Q", title="Prom. por turno", format=".2f"),
                    alt.Tooltip("Total:Q", title="Total horas extra", format=".1f"),
                    alt.Tooltip("Turnos:Q", title="Turnos", format="d"),
                ],
            )
            .properties(height=300)
        )
        st.altair_chart(chart_dow, use_container_width=True)

    _section_title("Progreso de horas por funcionario vs cuota del período")
    st.caption(
        "La barra crece con cada turno: trabajo efectivo en azul, vacaciones en turquesa y "
        "faltas/permisos en morado. Cuando el total supera la cuota del período, el exceso aparece en rojo."
    )

    # Clasificar horas efectivas por funcionario: trabajo / vacaciones / eventos.
    comp_tipo = completos.assign(_Clase=completos["Observaciones"].apply(_clasificar_obs))
    piv = (
        comp_tipo.groupby(["Nombre", "_Clase"], dropna=True)["Horas Efectivas"]
        .sum()
        .unstack(fill_value=0.0)
    )
    for col in ("trabajo", "vacaciones", "evento"):
        if col not in piv.columns:
            piv[col] = 0.0
    piv = piv.rename(columns={"trabajo": "H_Trab", "vacaciones": "H_Vac", "evento": "H_Evt"})
    agg_emp = piv.reset_index()
    agg_emp["Total Efectivas"] = agg_emp["H_Trab"] + agg_emp["H_Vac"] + agg_emp["H_Evt"]
    # Sin tope de funcionarios: los equipos superan los 10-12 y deben verse
    # completos. El filtro de área/empleado acota cuando hace falta.
    agg_emp = agg_emp.sort_values("Total Efectivas", ascending=False)

    # Conteo de turnos por clase, por funcionario.
    conteo = comp_tipo.groupby(["Nombre", "_Clase"]).size().unstack(fill_value=0)
    for col in ("trabajo", "vacaciones", "evento"):
        if col not in conteo.columns:
            conteo[col] = 0
    conteo = conteo.rename(columns={
        "trabajo": "Turnos Trabajados", "vacaciones": "Dias Vacaciones", "evento": "Dias Eventos",
    })
    agg_emp = agg_emp.merge(
        conteo[["Turnos Trabajados", "Dias Vacaciones", "Dias Eventos"]].reset_index(),
        on="Nombre", how="left",
    )
    for c in ("Turnos Trabajados", "Dias Vacaciones", "Dias Eventos"):
        agg_emp[c] = agg_emp[c].fillna(0).astype(int)

    # cuota_periodo y df_esp ya calculados al inicio de la función.
    # La cuota se llena en orden vacaciones → eventos → trabajo; el excedente del
    # total se marca como "Sobre cuota".
    if cuota_periodo > 0:
        agg_emp["Vacaciones"] = agg_emp["H_Vac"].clip(upper=cuota_periodo)
        agg_emp["Faltas/Permisos"] = agg_emp["H_Evt"].clip(
            upper=(cuota_periodo - agg_emp["H_Vac"]).clip(lower=0)
        )
        agg_emp["Trabajo"] = agg_emp["H_Trab"].clip(
            upper=(cuota_periodo - agg_emp["H_Vac"] - agg_emp["H_Evt"]).clip(lower=0)
        )
        agg_emp["Sobre cuota"] = (agg_emp["Total Efectivas"] - cuota_periodo).clip(lower=0)
    else:
        agg_emp["Vacaciones"] = agg_emp["H_Vac"]
        agg_emp["Faltas/Permisos"] = agg_emp["H_Evt"]
        agg_emp["Trabajo"] = agg_emp["H_Trab"]
        agg_emp["Sobre cuota"] = 0.0

    orden_nombres = agg_emp["Nombre"].tolist()
    # Más alto que el resto de gráficos: con equipos grandes los segmentos
    # apilados y las etiquetas necesitan espacio vertical para leerse.
    _ALTO_PROGRESO = 480
    barras = agg_emp.melt(
        id_vars=["Nombre", "Turnos Trabajados", "Dias Vacaciones", "Dias Eventos"],
        value_vars=["Trabajo", "Vacaciones", "Faltas/Permisos", "Sobre cuota"],
        var_name="Segmento",
        value_name="Horas",
    )
    _orden_seg = {"Trabajo": 0, "Vacaciones": 1, "Faltas/Permisos": 2, "Sobre cuota": 3}
    barras["_orden"] = barras["Segmento"].map(_orden_seg)

    chart_barras = (
        alt.Chart(barras)
        .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            x=alt.X(
                "Nombre:N",
                sort=orden_nombres,
                title="Funcionario",
                axis=alt.Axis(labelAngle=-35, labelLimit=120),
            ),
            y=alt.Y("Horas:Q", stack=True, title="Horas acumuladas"),
            color=alt.Color(
                "Segmento:N",
                title=None,
                scale=alt.Scale(
                    domain=["Trabajo", "Vacaciones", "Faltas/Permisos", "Sobre cuota"],
                    range=[BRAND_NAVY, BRAND_VAC, BRAND_EVENTO, BRAND_RED],
                ),
                legend=alt.Legend(orient="top", symbolType="square"),
            ),
            order=alt.Order("_orden:Q", sort="ascending"),
            tooltip=[
                alt.Tooltip("Nombre:N", title="Funcionario"),
                alt.Tooltip("Segmento:N", title="Tramo"),
                alt.Tooltip("Horas:Q", title="Horas", format=".1f"),
                alt.Tooltip("Turnos Trabajados:Q", title="Turnos trabajados", format="d"),
                alt.Tooltip("Dias Vacaciones:Q", title="Días de vacaciones", format="d"),
                alt.Tooltip("Dias Eventos:Q", title="Días falta/permiso", format="d"),
            ],
        )
        .properties(height=_ALTO_PROGRESO)
    )

    # Etiqueta con el total de horas efectivas encima de cada barra.
    texto_total = (
        alt.Chart(agg_emp)
        .mark_text(align="center", baseline="bottom", dy=-4, fontWeight="bold",
                   fontSize=11, color=BRAND_TEXT)
        .encode(
            x=alt.X("Nombre:N", sort=orden_nombres),
            y=alt.Y("Total Efectivas:Q"),
            text=alt.Text("Total Efectivas:Q", format=".0f"),
        )
    )

    capas = [chart_barras, texto_total]

    if cuota_periodo > 0:
        regla_cuota = (
            alt.Chart(pd.DataFrame({"cuota": [cuota_periodo]}))
            .mark_rule(strokeDash=[6, 3], color=BRAND_CUOTA, strokeWidth=2.5)
            .encode(y=alt.Y("cuota:Q", title=""))
        )
        etiqueta_cuota = (
            alt.Chart(pd.DataFrame({
                "Nombre": [orden_nombres[0]],
                "cuota": [cuota_periodo],
                "label": [f"Cuota: {cuota_periodo:.0f} h"],
            }))
            .mark_text(align="left", baseline="bottom", dx=-24, dy=-4,
                       fontWeight="bold", fontSize=12, color=BRAND_CUOTA)
            .encode(
                x=alt.X("Nombre:N", sort=orden_nombres),
                y=alt.Y("cuota:Q"),
                text="label:N",
            )
        )
        capas += [regla_cuota, etiqueta_cuota]
        chart_final = alt.layer(*capas).properties(height=_ALTO_PROGRESO)
        st.altair_chart(chart_final, use_container_width=True)
    else:
        chart_final = alt.layer(*capas).properties(height=_ALTO_PROGRESO)
        st.altair_chart(chart_final, use_container_width=True)
        st.caption("Sin cuota definida en 'Horas Esperadas' para los meses del período filtrado.")


def _render_comparativo_horas(df: pd.DataFrame, df_todo_meses: pd.DataFrame = None) -> None:
    completos = df[df["Estado"] == "Completo"]
    df_esperadas = leer_horas_esperadas()

    if df_esperadas.empty:
        st.info("No hay horas esperadas cargadas (tabla horas_esperadas en la base).")
        return

    # ── Gráfico mensual (total equipo) ──────────────────────────────────────
    # Usa TODOS los meses (activos + histórico) con solo los filtros de área/
    # empleado/estado: el filtro de fechas NO afecta esta sección, para que la
    # comparación mensual siempre esté completa.
    base_meses = df_todo_meses if df_todo_meses is not None else df
    completos_todo = base_meses[base_meses["Estado"] == "Completo"]

    comp_mes_todo = completos_todo.copy()
    comp_mes_todo["Año"] = comp_mes_todo["Fecha de Turno"].apply(lambda d: d.year if pd.notna(d) else None)
    comp_mes_todo["Mes"] = comp_mes_todo["Fecha de Turno"].apply(lambda d: d.month if pd.notna(d) else None)
    comp_mes_todo = comp_mes_todo.dropna(subset=["Año", "Mes"])
    comp_mes_todo["Año"] = comp_mes_todo["Año"].astype(int)
    comp_mes_todo["Mes"] = comp_mes_todo["Mes"].astype(int)

    _section_title("Horas esperadas vs efectivas por mes (total equipo)")
    st.caption(
        "Comparativo histórico completo: **ignora el filtro de fechas** (solo aplican "
        "área y empleado). Las esperadas se escalan por los funcionarios activos de "
        "cada mes; la columna roja aparece cuando las efectivas superan las esperadas."
    )

    activos_por_mes = (
        comp_mes_todo.groupby(["Año", "Mes"])["Nombre"]
        .nunique()
        .reset_index()
        .rename(columns={"Nombre": "N_Activos"})
    )
    actual_mes = (
        comp_mes_todo.groupby(["Año", "Mes"])["Horas Efectivas"]
        .sum()
        .reset_index()
        .rename(columns={"Horas Efectivas": "Reales"})
    )

    comp_chart = (
        df_esperadas
        .merge(actual_mes, on=["Año", "Mes"], how="left")
        .merge(activos_por_mes, on=["Año", "Mes"], how="left")
    )
    comp_chart["Reales"] = comp_chart["Reales"].fillna(0)
    comp_chart["N_Activos"] = comp_chart["N_Activos"].fillna(0).astype(int)
    comp_chart["Esperadas"] = comp_chart["Horas"] * comp_chart["N_Activos"]
    comp_chart = comp_chart.drop(columns=["Horas"]).sort_values(["Año", "Mes"])
    comp_chart["Periodo"] = comp_chart.apply(
        lambda r: f"{_MESES_ES[int(r['Mes'])][:3]} {int(r['Año'])}", axis=1
    )
    comp_chart["% Cumplimiento"] = (
        comp_chart["Reales"] / comp_chart["Esperadas"].replace(0, pd.NA) * 100
    ).fillna(0).round(1)
    # Exceso = horas extra a nivel de equipo: lo efectivo por encima de lo esperado.
    comp_chart["Exceso"] = (comp_chart["Reales"] - comp_chart["Esperadas"]).clip(lower=0)

    comp_chart_visible = comp_chart[comp_chart["N_Activos"] > 0]

    if comp_chart_visible.empty:
        st.info("No hay meses con datos para comparar con horas esperadas.")
    else:
        # ── Insights del comparativo (mismo estilo que los KPI del dashboard) ──
        hoy = today_ecuador()
        cc = comp_chart_visible.reset_index(drop=True)
        es_cerrado = (cc["Año"] < hoy.year) | ((cc["Año"] == hoy.year) & (cc["Mes"] < hoy.month))
        cerrados = cc[es_cerrado]
        en_curso = cc[(cc["Año"] == hoy.year) & (cc["Mes"] == hoy.month)]

        cards = []
        if len(cerrados) >= 1:
            ult = cerrados.iloc[-1]
            if len(cerrados) >= 2:
                prev = cerrados.iloc[-2]
                dif = float(ult["Exceso"] - prev["Exceso"])
                pct_txt = f" ({dif / prev['Exceso'] * 100:+.1f}%)" if prev["Exceso"] > 0 else ""
                if dif < 0:
                    sub = f"<b>{-dif:,.0f} h reducidas{pct_txt}</b> vs {prev['Periodo']}"
                    bg, fg = "#E1F3E7", "#1F9254"
                elif dif > 0:
                    sub = f"<b>{dif:,.0f} h más{pct_txt}</b> vs {prev['Periodo']}"
                    bg, fg = "#FDE7E9", BRAND_RED
                else:
                    sub = f"sin variación vs {prev['Periodo']}"
                    bg, fg = "#E6E9F4", BRAND_NAVY
            else:
                sub = "sin mes anterior para comparar"
                bg, fg = "#E6E9F4", BRAND_NAVY
            cards.append(_kpi_card(
                "", bg, fg,
                f"Horas extra {ult['Periodo']} (último mes cerrado)",
                f"{ult['Exceso']:,.0f}", "h", sub,
            ))

        exceso_anio = float(cc.loc[cc["Año"] == hoy.year, "Exceso"].sum())
        n_meses_anio = int((cc["Año"] == hoy.year).sum())
        cards.append(_kpi_card(
            "", "#FDE7E9", BRAND_RED,
            f"Exceso acumulado {hoy.year}",
            f"{exceso_anio:,.0f}", "h",
            f"sobre lo esperado, en <b>{n_meses_anio}</b> mes(es) con datos",
        ))

        if (cc["Exceso"] > 0).any():
            peor = cc.loc[cc["Exceso"].idxmax()]
            cards.append(_kpi_card(
                "", "#FEF3E2", "#C97A0A",
                "Mes con mayor exceso",
                f"{peor['Exceso']:,.0f}", "h",
                f"<b>{peor['Periodo']}</b> · cumplimiento {peor['% Cumplimiento']:.0f}%",
            ))

        if not en_curso.empty:
            mc = en_curso.iloc[0]
            cards.append(_kpi_card(
                "", "#E6E9F4", BRAND_NAVY,
                f"Avance {mc['Periodo']} (en curso)",
                f"{mc['% Cumplimiento']:.0f}", "%",
                f"<b>{mc['Reales']:,.0f} h</b> de {mc['Esperadas']:,.0f} h esperadas del mes",
            ))

        st.markdown('<div class="kpi-grid">' + "".join(cards) + "</div>", unsafe_allow_html=True)
        st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)

        # ── Barras: Esperadas / Efectivas / Horas extra (solo si hay exceso) ──
        orden_periodos = comp_chart_visible["Periodo"].tolist()
        barras_comp = (
            comp_chart_visible
            .rename(columns={"Reales": "Efectivas", "Exceso": "Horas extra"})
            .melt(
                id_vars=["Periodo", "% Cumplimiento", "N_Activos"],
                value_vars=["Esperadas", "Efectivas", "Horas extra"],
                var_name="Tipo",
                value_name="H",
            )
        )
        # La columna de horas extra solo se dibuja en los meses donde existe.
        barras_comp = barras_comp[(barras_comp["Tipo"] != "Horas extra") | (barras_comp["H"] > 0)]
        chart_comp = (
            alt.Chart(barras_comp)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(
                x=alt.X("Periodo:N", sort=orden_periodos, title="Mes", axis=alt.Axis(labelAngle=0)),
                y=alt.Y("H:Q", title="Horas"),
                color=alt.Color(
                    "Tipo:N",
                    title=None,
                    scale=alt.Scale(
                        domain=["Esperadas", "Efectivas", "Horas extra"],
                        range=[BRAND_NAVY_SOFT, BRAND_NAVY, BRAND_RED],
                    ),
                    legend=alt.Legend(orient="top", symbolType="square"),
                ),
                xOffset=alt.XOffset("Tipo:N", scale=alt.Scale(domain=["Esperadas", "Efectivas", "Horas extra"])),
                tooltip=[
                    alt.Tooltip("Periodo:N", title="Mes"),
                    alt.Tooltip("Tipo:N", title="Tipo"),
                    alt.Tooltip("H:Q", title="Horas", format=".1f"),
                    alt.Tooltip("N_Activos:Q", title="Funcionarios activos", format="d"),
                    alt.Tooltip("% Cumplimiento:Q", title="% Cumpl.", format=".1f"),
                ],
            )
        )
        texto_comp = (
            alt.Chart(barras_comp)
            .mark_text(baseline="bottom", dy=-4, fontSize=10, fontWeight="bold", color=BRAND_TEXT)
            .encode(
                x=alt.X("Periodo:N", sort=orden_periodos),
                xOffset=alt.XOffset("Tipo:N", scale=alt.Scale(domain=["Esperadas", "Efectivas", "Horas extra"])),
                y=alt.Y("H:Q"),
                text=alt.Text("H:Q", format=".0f"),
                detail=alt.Detail("Tipo:N"),
            )
        )
        st.altair_chart((chart_comp + texto_comp).properties(height=380), use_container_width=True)

    # El detalle por funcionario (abajo) sí respeta todos los filtros, incluido fechas.
    if completos.empty:
        st.info("Sin turnos completos en el rango filtrado para el detalle por funcionario.")
        return

    comp_mes = completos.copy()
    comp_mes["Año"] = comp_mes["Fecha de Turno"].apply(lambda d: d.year if pd.notna(d) else None)
    comp_mes["Mes"] = comp_mes["Fecha de Turno"].apply(lambda d: d.month if pd.notna(d) else None)
    comp_mes = comp_mes.dropna(subset=["Año", "Mes"])
    comp_mes["Año"] = comp_mes["Año"].astype(int)
    comp_mes["Mes"] = comp_mes["Mes"].astype(int)

    # ── Detalle por funcionario ──────────────────────────────────────────────
    _section_title("Horas efectivas vs esperadas por funcionario")
    st.caption(
        "Horas esperadas por persona = suma de horas del calendario "
        "para cada mes en que el funcionario registró al menos un turno completo."
    )

    emp_mes = (
        comp_mes.groupby(["Nombre", "Año", "Mes"])["Horas Efectivas"]
        .sum()
        .reset_index()
    )
    emp_mes = emp_mes.merge(df_esperadas, on=["Año", "Mes"], how="left")
    emp_mes["Horas esp/mes"] = emp_mes["Horas"].fillna(0)

    emp_agg = (
        emp_mes.groupby("Nombre")
        .agg(**{
            "Horas Efectivas": ("Horas Efectivas", "sum"),
            "Horas Esperadas": ("Horas esp/mes", "sum"),
        })
        .reset_index()
    )
    emp_agg["% Cumplimiento"] = (
        emp_agg["Horas Efectivas"] / emp_agg["Horas Esperadas"].replace(0, pd.NA) * 100
    ).fillna(0).round(1)

    # Turnos esperados = horas esperadas / 8 (horas efectivas por turno base)
    emp_agg["Turnos Esperados"] = (emp_agg["Horas Esperadas"] / 8.0).round().astype(int)

    # Cantidad de turnos = nº de turnos completos del funcionario en el rango
    turnos_por_emp = (
        comp_mes.groupby("Nombre").size().reset_index(name="Cantidad Turnos")
    )
    emp_agg = emp_agg.merge(turnos_por_emp, on="Nombre", how="left")
    emp_agg["Cantidad Turnos"] = emp_agg["Cantidad Turnos"].fillna(0).astype(int)

    emp_agg = emp_agg.sort_values("Horas Efectivas", ascending=False)

    barras_emp = emp_agg.melt(
        id_vars=["Nombre", "% Cumplimiento"],
        value_vars=["Horas Esperadas", "Horas Efectivas"],
        var_name="Tipo",
        value_name="H",
    )
    chart_emp = (
        alt.Chart(barras_emp)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X(
                "Nombre:N",
                sort=emp_agg["Nombre"].tolist(),
                title="Funcionario",
                axis=alt.Axis(labelAngle=-35, labelLimit=120),
            ),
            y=alt.Y("H:Q", title="Horas"),
            color=alt.Color(
                "Tipo:N",
                title=None,
                scale=alt.Scale(
                    domain=["Horas Esperadas", "Horas Efectivas"],
                    range=[BRAND_NAVY_SOFT, BRAND_NAVY],
                ),
                legend=alt.Legend(orient="top", symbolType="square"),
            ),
            xOffset=alt.XOffset("Tipo:N"),
            tooltip=[
                alt.Tooltip("Nombre:N", title="Funcionario"),
                alt.Tooltip("Tipo:N", title="Tipo"),
                alt.Tooltip("H:Q", title="Horas", format=".1f"),
                alt.Tooltip("% Cumplimiento:Q", title="% Cumpl.", format=".1f"),
            ],
        )
    )
    texto_emp = (
        alt.Chart(barras_emp)
        .mark_text(baseline="bottom", dy=-4, fontSize=9, fontWeight="bold", color=BRAND_TEXT)
        .encode(
            x=alt.X("Nombre:N", sort=emp_agg["Nombre"].tolist()),
            xOffset=alt.XOffset("Tipo:N"),
            y=alt.Y("H:Q"),
            text=alt.Text("H:Q", format=".0f"),
            detail=alt.Detail("Tipo:N"),
        )
    )
    st.altair_chart((chart_emp + texto_emp).properties(height=360), use_container_width=True)

    tabla_emp = emp_agg[[
        "Nombre", "Horas Efectivas", "Horas Esperadas",
        "Turnos Esperados", "Cantidad Turnos", "% Cumplimiento",
    ]].copy()
    tabla_emp = tabla_emp.sort_values("% Cumplimiento")
    max_pct = float(tabla_emp["% Cumplimiento"].max()) if len(tabla_emp) else 100.0
    st.dataframe(
        tabla_emp,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Nombre":           st.column_config.TextColumn("Funcionario", width="medium"),
            "Horas Efectivas":  st.column_config.NumberColumn("Horas efectivas", format="%.1f h"),
            "Horas Esperadas":  st.column_config.NumberColumn("Horas esperadas", format="%.1f h"),
            "Turnos Esperados": st.column_config.NumberColumn("Turnos esperados", format="%d"),
            "Cantidad Turnos":  st.column_config.NumberColumn("Turnos", format="%d"),
            "% Cumplimiento":   st.column_config.ProgressColumn(
                "% Cumplimiento",
                format="%.1f%%",
                min_value=0.0,
                max_value=max(100.0, max_pct),
            ),
        },
    )
    st.download_button(
        "Descargar comparativo (CSV)",
        data=tabla_emp.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"comparativo_{today_ecuador().isoformat()}.csv",
        mime="text/csv",
        use_container_width=True,
    )


_ESTADO_ROW_BG = {
    "Completo": "#F2FFF6",
    "Abierto":  "#FFFDF0",
    "Revision": "#FFF5F5",
}

def _dec_a_hhmm(v) -> str:
    """Convierte horas decimales a formato HH:MM. Ej: 8.5 → '8:30'."""
    if pd.isna(v) or v == 0:
        return "—"
    hh = int(v)
    mm = round((v - hh) * 60)
    if mm == 60:
        hh += 1
        mm = 0
    return f"{hh}:{mm:02d}"


def _style_tabla(df: pd.DataFrame):
    """Aplica colores de fondo por fila según Estado y formatos de columna."""
    def _row_bg(row):
        bg = _ESTADO_ROW_BG.get(row.get("Estado", ""), "")
        return [f"background-color: {bg}" if bg else ""] * len(row)

    return df.style.apply(_row_bg, axis=1).format(
        {
            "Horas Trabajadas": _dec_a_hhmm,
            "Horas Efectivas":  _dec_a_hhmm,
            "Horas Extra":      _dec_a_hhmm,
        },
        na_rep="—",
    )


def _render_tabla(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("Sin registros en el rango filtrado.")
        return

    df = df.copy()
    for col in ("Horas Trabajadas", "Horas Efectivas", "Horas Extra"):
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
            "Horas Efectivas":   st.column_config.TextColumn("Horas efectivas",  width="small"),
            "Horas Extra":       st.column_config.TextColumn("Horas extra",      width="small"),
            "Estado":            st.column_config.TextColumn("Estado",           width="small"),
            "Observaciones":     st.column_config.TextColumn("Observaciones",    width="large"),
        },
    )

    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "Descargar CSV",
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


@st.dialog("Confirmar registro manual")
def _dialogo_confirmar_correccion() -> None:
    payload = st.session_state.get("_corr_pendiente")
    if not payload:
        st.rerun()
        return

    modo = payload["modo"]
    emp = payload["emp"]

    def _fila(label: str, valor: str, color: str = BRAND_NAVY) -> str:
        return (
            f"<div style='display:flex;justify-content:space-between;align-items:center;"
            f"padding:7px 0;border-bottom:1px solid #eef0f6;'>"
            f"<span style='color:{BRAND_MUTED};font-size:.85rem;'>{label}</span>"
            f"<span style='font-weight:600;color:{color};font-size:.95rem;'>{valor}</span>"
            f"</div>"
        )

    filas_html = [
        _fila("Empleado", emp),
        _fila("Área", payload.get("area", "")),
    ]

    if modo == "cierre":
        ts_ent: datetime = payload["ts_ent"]
        ts_sal: datetime = payload["ts_sal"]
        horas: float = payload["horas"]
        filas_html += [
            _fila("Acción", "Cierre de turno — registro de SALIDA"),
            _fila("Entrada registrada", ts_ent.strftime("%d/%m/%Y  %H:%M")),
            _fila("Salida a registrar", ts_sal.strftime("%d/%m/%Y  %H:%M"), BRAND_RED),
            _fila("Duración", f"{horas:.2f} h"),
            _fila("Observación", f"Registro manual: {payload['obs']}"),
        ]

    elif modo == "entrada":
        ts_in: datetime = payload["ts_in"]
        filas_html += [
            _fila("Acción", "Registro de ENTRADA manual — turno quedará abierto"),
            _fila("Fecha / Hora entrada", ts_in.strftime("%d/%m/%Y  %H:%M"), BRAND_RED),
            _fila("Observación", "Registro manual: entrada registrada por administrador"),
        ]

    else:
        ts_in = payload["ts_in"]
        ts_out = payload["ts_out"]
        horas = payload["horas"]
        hef = calcular_horas_efectivas(horas)
        hex_ = calcular_horas_extra(horas)
        filas_html += [
            _fila("Acción", "Registro histórico completo — ENTRADA y SALIDA"),
            _fila("Entrada", ts_in.strftime("%d/%m/%Y  %H:%M")),
            _fila("Salida", ts_out.strftime("%d/%m/%Y  %H:%M"), BRAND_RED),
            _fila("Horas trabajadas", f"{horas:.2f} h"),
            _fila("Horas efectivas", f"{hef:.2f} h"),
            _fila("Horas extra", f"{hex_:.2f} h"),
            _fila("Observación", f"Registro manual: {payload['obs']}"),
        ]

    st.markdown(
        f"<div style='background:{BRAND_BG_SOFT};border-radius:10px;padding:4px 14px 8px;margin-bottom:12px;'>"
        + "".join(filas_html)
        + "</div>",
        unsafe_allow_html=True,
    )

    st.divider()
    bc, bx = st.columns(2)
    with bc:
        if st.button("Confirmar", type="primary", use_container_width=True, key="dlg_confirm"):
            if bloquear_doble_click("corr_confirm"):
                st.rerun()
                return
            if modo == "cierre":
                if not guardar_salida(
                    emp, payload["ts_entrada_str"], payload["ts_sal"],
                    payload["horas"], f"Registro manual: {payload['obs']}"
                ):
                    st.error("El turno ya no existe. Recarga la página.")
                    return
                flash_msg = f"Turno cerrado para {emp} · {payload['horas']:.2f} h trabajadas"
            elif modo == "entrada":
                ts_in = payload["ts_in"]
                append_registro({
                    "Nombre": emp,
                    "Area": AREA_DE.get(emp, ""),
                    "Fecha de Turno": ts_in.strftime("%Y-%m-%d"),
                    "Timestamp Entrada": ts_in.strftime(TS_FMT),
                    "Timestamp Salida": "",
                    "Horas Trabajadas": "",
                    "Horas Efectivas": "",
                    "Horas Extra": "",
                    "Estado": "Abierto",
                    "Observaciones": "Registro manual: entrada registrada por administrador",
                })
                flash_msg = f"Entrada manual registrada para {emp} · {ts_in.strftime('%d/%m %H:%M')}"
            else:
                ts_in, ts_out = payload["ts_in"], payload["ts_out"]
                horas = payload["horas"]
                append_registro({
                    "Nombre": emp,
                    "Area": AREA_DE.get(emp, ""),
                    "Fecha de Turno": ts_in.strftime("%Y-%m-%d"),
                    "Timestamp Entrada": ts_in.strftime(TS_FMT),
                    "Timestamp Salida": ts_out.strftime(TS_FMT),
                    "Horas Trabajadas": horas,
                    "Horas Efectivas": calcular_horas_efectivas(horas),
                    "Horas Extra": calcular_horas_extra(horas),
                    "Estado": "Completo",
                    "Observaciones": f"Registro manual: {payload['obs']}",
                })
                flash_msg = f"Registro histórico creado para {emp} · {horas:.2f} h trabajadas"
            set_flash(flash_msg)
            st.session_state["_corr_pendiente"] = None
            st.session_state["_corr_rev"] = st.session_state.get("_corr_rev", 0) + 1
            st.rerun()
    with bx:
        if st.button("Cancelar", use_container_width=True, key="dlg_cancel"):
            st.session_state["_corr_pendiente"] = None
            st.rerun()


def _render_correcciones(areas_permitidas=None, df_filt=None) -> None:
    """Flujo para cerrar turnos abiertos, crear registros históricos o editar
    turnos cerrados. Solo super admin. `df_filt` son los registros ya filtrados
    del dashboard, usados por la opción de edición para respetar los filtros."""
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
        df_pend_reset = df_pendientes.reset_index(drop=True)
        if filas and filas[0] < len(df_pend_reset):
            fila_sel = df_pend_reset.iloc[filas[0]]
            new_area = fila_sel["Area"]
            new_emp = fila_sel["Nombre"]
            rev_now = st.session_state.get("_corr_rev", 0)
            if new_area in areas_corr:
                st.session_state[f"area_corr_{rev_now}"] = new_area
            lista_emp_prefill = EMPLEADOS_POR_AREA.get(new_area, [])
            if new_emp in lista_emp_prefill:
                st.session_state[f"emp_corr_{rev_now}"] = new_emp

    st.divider()
    st.markdown("#### Formulario de corrección")

    # rev cambia después de cada guardado exitoso, forzando reset de todos los widgets
    rev = st.session_state.get("_corr_rev", 0)

    opciones_area = [None] + list(areas_corr)
    ca_corr, ce_corr = st.columns(2)
    with ca_corr:
        area_corr = st.selectbox(
            "Área",
            opciones_area,
            format_func=lambda x: "— Selecciona un área —" if x is None else x,
            key=f"area_corr_{rev}",
        )
    with ce_corr:
        lista_empleados = [None] + list(EMPLEADOS_POR_AREA.get(area_corr, [])) if area_corr else [None]
        emp_corr = st.selectbox(
            "Empleado a corregir",
            lista_empleados,
            format_func=lambda x: "— Selecciona un empleado —" if x is None else x,
            key=f"emp_corr_{rev}",
        )

    if emp_corr is None:
        st.info("Selecciona un área y un empleado para continuar.")
        return

    modo = st.radio(
        "¿Qué quieres hacer?",
        [
            "Cerrar un turno abierto (olvido de SALIDA)",
            "Registrar entrada manual (sin salida aún)",
            "Crear registro histórico completo (olvido de ENTRADA y/o SALIDA)",
            "Editar un turno ya cerrado",
            "Eliminar un turno (borrado definitivo)",
        ],
        key="modo_corr",
    )

    if modo.startswith("Cerrar"):
        emp_norm = str(emp_corr).strip()
        _nombre = df_actual["Nombre"].fillna("").astype(str).str.strip()
        _estado = df_actual["Estado"].fillna("").astype(str).str.strip()
        mask_revision = (_nombre == emp_norm) & (_estado == "Revision")
        df_abiertos = df_actual[mask_revision]
        if df_abiertos.empty:
            st.info(f"{emp_corr} no tiene turnos en revisión pendientes de cierre.")
        else:
            opciones = {
                f"Entrada {row['Timestamp Entrada']} (turno {row['Fecha de Turno']})": idx
                for idx, row in df_abiertos.iterrows()
            }
            elegido = st.selectbox("Turno en revisión a cerrar", list(opciones.keys()), key=f"turno_sel_{rev}")
            idx_obj = opciones[elegido]

            ts_ent = pd.to_datetime(df_actual.loc[idx_obj, "Timestamp Entrada"])
            f_ent_date = ts_ent.date()
            f_sal_max = f_ent_date + timedelta(days=2)

            c1, c2 = st.columns(2)
            with c1:
                f_sal = st.date_input(
                    "Fecha de salida",
                    value=None,
                    min_value=f_ent_date,
                    max_value=f_sal_max,
                    key=f"f_sal_close_{rev}",
                )
            with c2:
                h_sal = _time_input("Hora de salida", time(0, 0), f"h_sal_close_{rev}")

            st.markdown("**Observación** (el prefijo *Registro manual:* se añade automáticamente)")
            cp, cd = st.columns([1, 3])
            with cp:
                st.text_input("Prefijo", value="Registro manual:", disabled=True,
                              key=f"pref_close_{rev}", label_visibility="collapsed")
            with cd:
                obs_det = st.text_input("Detalle", key=f"obs_close_det_{rev}",
                                        placeholder="Describe el motivo del cierre manual...",
                                        label_visibility="collapsed")

            confirm_largo = st.checkbox(
                "Confirmo el cierre de un turno superior a 15 h",
                key=f"confirm_turno_largo_{rev}",
            )

            if st.button("Cerrar turno", key=f"btn_close_{rev}"):
                det = obs_det.strip()
                if f_sal is None:
                    st.error("Selecciona una fecha de salida.")
                elif not det:
                    st.error("Ingresa un detalle válido en la observación.")
                else:
                    ts_sal = datetime.combine(f_sal, h_sal)
                    if ts_sal <= ts_ent:
                        st.error("La salida debe ser posterior a la entrada.")
                    else:
                        horas = calcular_horas(ts_ent, ts_sal)
                        if horas > 15 and not confirm_largo:
                            st.warning(
                                f"El turno tiene **{horas:.2f} h**. "
                                "Marca la casilla de confirmación antes de cerrar."
                            )
                        elif horas > UMBRAL_HORAS_EXTRA and len(det) < MIN_JUSTIF_CHARS:
                            st.error(
                                f"Las {horas} h exceden {UMBRAL_HORAS_EXTRA} h. "
                                f"El detalle debe tener al menos {MIN_JUSTIF_CHARS} caracteres."
                            )
                        else:
                            st.session_state["_corr_pendiente"] = {
                                "modo": "cierre",
                                "emp": emp_corr,
                                "area": AREA_DE.get(emp_corr, ""),
                                "ts_ent": ts_ent.to_pydatetime(),
                                "ts_sal": ts_sal,
                                "horas": horas,
                                "obs": det,
                                "ts_entrada_str": str(df_actual.loc[idx_obj, "Timestamp Entrada"]),
                            }

    elif modo.startswith("Registrar entrada"):
        st.caption("Crea un turno abierto con una hora de entrada pasada. El empleado marcará la salida normalmente.")
        if buscar_turno_abierto_idx(df_actual, emp_corr) is not None:
            st.warning(f"{emp_corr} ya tiene un turno abierto. Ciérralo primero antes de registrar una nueva entrada.")
        else:
            c1, c2 = st.columns(2)
            with c1:
                f_ent = st.date_input("Fecha de entrada", value=None, key=f"f_ent_manual_{rev}")
            with c2:
                h_ent = _time_input("Hora de entrada", time(0, 0), f"h_ent_manual_{rev}")

            if st.button("Registrar entrada", key=f"btn_ent_manual_{rev}"):
                if f_ent is None:
                    st.error("Selecciona una fecha de entrada.")
                else:
                    ts_in = datetime.combine(f_ent, h_ent)
                    if ts_in > now_ecuador():
                        st.error("La hora de entrada no puede ser futura.")
                    else:
                        st.session_state["_corr_pendiente"] = {
                            "modo": "entrada",
                            "emp": emp_corr,
                            "area": AREA_DE.get(emp_corr, ""),
                            "ts_in": ts_in,
                        }

    elif modo.startswith("Crear"):
        st.caption("Ambas marcas se ingresan manualmente. Úsalo solo para turnos ya pasados.")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Entrada**")
            f_ent = st.date_input("Fecha", value=None, key=f"f_ent_m_{rev}")
            h_ent = _time_input("Hora", time(0, 0), f"h_ent_m_{rev}")
        with c2:
            st.markdown("**Salida**")
            f_sal = st.date_input("Fecha", value=None, key=f"f_sal_m_{rev}")
            h_sal = _time_input("Hora", time(0, 0), f"h_sal_m_{rev}")

        st.markdown("**Observación** (el prefijo *Registro manual:* se añade automáticamente)")
        cp2, cd2 = st.columns([1, 3])
        with cp2:
            st.text_input("Prefijo", value="Registro manual:", disabled=True,
                          key=f"pref_m_{rev}", label_visibility="collapsed")
        with cd2:
            obs_det = st.text_input("Detalle", key=f"obs_m_det_{rev}",
                                    placeholder="Describe por qué se ingresa manualmente...",
                                    label_visibility="collapsed")

        if st.button("Crear registro", key=f"btn_m_{rev}"):
            det = obs_det.strip()
            if f_ent is None or f_sal is None:
                st.error("Selecciona fecha de entrada y salida.")
            elif not det:
                st.error("Ingresa un detalle válido en la observación.")
            else:
                ts_in = datetime.combine(f_ent, h_ent)
                ts_out = datetime.combine(f_sal, h_sal)
                if ts_out <= ts_in:
                    st.error("La salida debe ser posterior a la entrada.")
                else:
                    horas = calcular_horas(ts_in, ts_out)
                    if horas > UMBRAL_HORAS_EXTRA and len(det) < MIN_JUSTIF_CHARS:
                        st.error(
                            f"Las {horas} h exceden {UMBRAL_HORAS_EXTRA} h. "
                            f"El detalle debe tener al menos {MIN_JUSTIF_CHARS} caracteres."
                        )
                    else:
                        st.session_state["_corr_pendiente"] = {
                            "modo": "completo",
                            "emp": emp_corr,
                            "area": AREA_DE.get(emp_corr, ""),
                            "ts_in": ts_in,
                            "ts_out": ts_out,
                            "horas": horas,
                            "obs": det,
                        }

    elif modo.startswith("Editar"):
        _flujo_editar_turno(emp_corr, df_filt)

    elif modo.startswith("Eliminar"):
        _flujo_eliminar_turno(emp_corr, df_filt)

    if st.session_state.get("_corr_pendiente"):
        _dialogo_confirmar_correccion()

@st.dialog("Confirmar corrección de turno", width="large")
def _dialogo_confirmar_edicion() -> None:
    payload = st.session_state.get("_edit_pendiente")
    if not payload:
        st.rerun()
        return

    def _fila_cmp(label: str, antes: str, despues: str, cambiado: bool = False) -> str:
        color_d = BRAND_RED if cambiado else BRAND_TEXT
        return (
            f"<tr>"
            f"<td style='padding:7px 10px;color:{BRAND_MUTED};font-size:.85rem;"
            f"border-bottom:1px solid #eef0f6;white-space:nowrap;'>{label}</td>"
            f"<td style='padding:7px 10px;border-bottom:1px solid #eef0f6;'>{antes}</td>"
            f"<td style='padding:7px 10px;border-bottom:1px solid #eef0f6;"
            f"font-weight:600;color:{color_d};'>{despues}</td>"
            f"</tr>"
        )

    ts_ent_orig = payload["ts_ent_orig"]
    ts_sal_orig = payload["ts_sal_orig"]
    ts_ent_nueva = payload["ts_ent_nueva"]
    ts_sal_nueva = payload["ts_sal_nueva"]
    h_orig = payload["horas_orig"]
    h_nueva = payload["horas_nuevas"]

    def _ts_str(ts):
        return ts.strftime("%d/%m/%Y  %H:%M") if ts else "—"

    def _cambiado(orig, nueva):
        if orig is None:
            return True
        return orig.replace(second=0, microsecond=0) != nueva.replace(second=0, microsecond=0)

    filas = (
        _fila_cmp("Funcionario", payload["nombre"], payload["nombre"])
        + _fila_cmp("Entrada", _ts_str(ts_ent_orig), _ts_str(ts_ent_nueva), _cambiado(ts_ent_orig, ts_ent_nueva))
        + _fila_cmp("Salida", _ts_str(ts_sal_orig), _ts_str(ts_sal_nueva), _cambiado(ts_sal_orig, ts_sal_nueva))
        + _fila_cmp(
            "Horas trabajadas",
            _dec_a_hhmm(h_orig),
            _dec_a_hhmm(h_nueva),
            abs(h_nueva - h_orig) > 0.01,
        )
    )

    st.markdown(
        f"<div style='background:{BRAND_BG_SOFT};border-radius:10px;padding:8px 4px;margin-bottom:12px;overflow:auto;'>"
        f"<table style='width:100%;border-collapse:collapse;'>"
        f"<thead><tr>"
        f"<th style='padding:6px 10px;text-align:left;color:{BRAND_MUTED};font-size:.78rem;"
        f"border-bottom:2px solid #d0d5e8;'>Campo</th>"
        f"<th style='padding:6px 10px;text-align:left;color:{BRAND_MUTED};font-size:.78rem;"
        f"border-bottom:2px solid #d0d5e8;'>Antes</th>"
        f"<th style='padding:6px 10px;text-align:left;color:{BRAND_MUTED};font-size:.78rem;"
        f"border-bottom:2px solid #d0d5e8;'>Después</th>"
        f"</tr></thead>"
        f"<tbody>{filas}</tbody>"
        f"</table></div>",
        unsafe_allow_html=True,
    )

    obs_nueva = payload["obs_nueva"]
    obs_orig = payload["obs_orig"]
    if obs_nueva or obs_orig:
        st.divider()
        if obs_nueva:
            st.markdown(f"**Observación del admin:** {obs_nueva}")
        if obs_orig:
            st.caption(f"Observación original preservada: {obs_orig}")

    bc, bx = st.columns(2)
    with bc:
        if st.button("Confirmar corrección", type="primary", use_container_width=True, key="edit_dlg_confirm"):
            if bloquear_doble_click("edit_confirm"):
                st.rerun()
                return
            ahora = now_ecuador()
            admin_tag = f"[Corrección {ahora.strftime('%Y-%m-%d')} por {payload['admin_user']}]"
            obs_final = f"{admin_tag}: {obs_nueva}" if obs_nueva else admin_tag
            if obs_orig:
                obs_final += f" | [Orig]: {obs_orig}"

            h = payload["horas_nuevas"]
            cambios = {
                "Timestamp Entrada": ts_ent_nueva.strftime(TS_FMT),
                "Timestamp Salida": ts_sal_nueva.strftime(TS_FMT),
                "Horas Trabajadas": h,
                "Horas Efectivas": calcular_horas_efectivas(h),
                "Horas Extra": calcular_horas_extra(h),
                "Observaciones": obs_final,
            }
            ok = actualizar_por_entrada(payload["nombre"], payload["ts_entrada_str_orig"], cambios)
            if ok:
                set_flash(f"Turno de {payload['nombre']} corregido · {_dec_a_hhmm(h)} trabajadas")
                st.session_state["_edit_pendiente"] = None
                st.session_state["_edit_rev"] = st.session_state.get("_edit_rev", 0) + 1
                st.rerun()
            else:
                st.error("No se encontró el turno en la hoja. Puede haber sido modificado. Recarga la página.")
    with bx:
        if st.button("Cancelar", use_container_width=True, key="edit_dlg_cancel"):
            st.session_state["_edit_pendiente"] = None
            st.rerun()


def _flujo_editar_turno(emp_corr, df: pd.DataFrame) -> None:
    """Edición de un turno ya cerrado del empleado seleccionado.

    Recibe el empleado elegido en el formulario de correcciones y el DataFrame
    YA FILTRADO del dashboard, así la tabla respeta los filtros activos (fechas,
    área, estado). No duplica la selección de área/empleado."""
    admin_user = st.session_state.get("admin_user", "")
    rev = st.session_state.get("_edit_rev", 0)

    st.caption(
        "Corrige un turno ya cerrado cuando el horario o las horas registradas no "
        "corresponden. Solo se muestran turnos normales dentro de los filtros activos "
        "(las eventualidades —vacaciones, faltas, permisos— no se editan aquí); "
        "la observación original del funcionario se conserva."
    )

    if df is None or df.empty:
        st.info("No hay registros en los filtros activos.")
        return

    nombre_col = df["Nombre"].fillna("").astype(str).str.strip()
    estado_col = df["Estado"].fillna("").astype(str).str.strip().str.casefold()
    clase = df["Observaciones"].apply(_clasificar_obs)
    mask = (nombre_col == str(emp_corr).strip()) & (estado_col == "completo") & (clase == "trabajo")
    cerrados = df[mask].copy().sort_values("Timestamp Entrada").reset_index(drop=True)

    if cerrados.empty:
        st.info(f"{emp_corr} no tiene turnos normales cerrados dentro de los filtros activos.")
        return

    st.caption(f"{len(cerrados)} turno(s) cerrado(s) en los filtros activos. Selecciona una fila para editarla.")

    cols_disp = ["Fecha de Turno", "Timestamp Entrada", "Timestamp Salida",
                 "Horas Trabajadas", "Horas Efectivas", "Horas Extra", "Observaciones"]
    cerrados_disp = cerrados[cols_disp].copy()
    for col in ["Timestamp Entrada", "Timestamp Salida"]:
        cerrados_disp[col] = cerrados_disp[col].apply(
            lambda v: pd.Timestamp(v).strftime("%Y-%m-%d %H:%M") if pd.notna(v) else "—"
        )
    for col in ["Horas Trabajadas", "Horas Efectivas", "Horas Extra"]:
        cerrados_disp[col] = pd.to_numeric(cerrados_disp[col], errors="coerce").apply(_dec_a_hhmm)

    event = st.dataframe(
        cerrados_disp,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=f"edit_tabla_{rev}",
    )

    sel_rows = event.selection.rows
    if not sel_rows:
        return

    turno = cerrados.iloc[sel_rows[0]]

    ts_ent_val = turno["Timestamp Entrada"]
    ts_sal_val = turno["Timestamp Salida"]
    ts_ent_orig = pd.Timestamp(ts_ent_val).to_pydatetime() if pd.notna(ts_ent_val) else None
    ts_sal_orig = pd.Timestamp(ts_sal_val).to_pydatetime() if pd.notna(ts_sal_val) else None
    if ts_ent_orig is None:
        st.error("Este turno no tiene una hora de entrada válida y no puede editarse aquí.")
        return
    ts_entrada_str_orig = ts_ent_orig.strftime(TS_FMT)

    obs_orig = str(turno.get("Observaciones", "") or "").strip()
    if obs_orig.lower() == "nan":
        obs_orig = ""

    h_orig_num = pd.to_numeric(turno["Horas Trabajadas"], errors="coerce")
    h_orig = float(h_orig_num) if pd.notna(h_orig_num) else 0.0

    st.divider()
    _section_title("Corregir turno seleccionado")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"<span style='color:{BRAND_MUTED};font-size:.9rem;font-weight:600;'>Entrada</span>",
                    unsafe_allow_html=True)
        f_ent = st.date_input("Fecha entrada",
                               value=ts_ent_orig.date() if ts_ent_orig else None,
                               key=f"edit_f_ent_{rev}", label_visibility="collapsed")
        h_ent = _time_input("Hora entrada",
                             ts_ent_orig.time() if ts_ent_orig else time(0, 0),
                             f"edit_h_ent_{rev}")
    with c2:
        st.markdown(f"<span style='color:{BRAND_MUTED};font-size:.9rem;font-weight:600;'>Salida</span>",
                    unsafe_allow_html=True)
        f_sal = st.date_input("Fecha salida",
                               value=ts_sal_orig.date() if ts_sal_orig else None,
                               key=f"edit_f_sal_{rev}", label_visibility="collapsed")
        h_sal = _time_input("Hora salida",
                             ts_sal_orig.time() if ts_sal_orig else time(0, 0),
                             f"edit_h_sal_{rev}")

    obs_nueva = st.text_area(
        "Motivo de la corrección (observación del administrador)",
        key=f"edit_obs_{rev}",
        placeholder="Ej: Horario corregido por discrepancia confirmada con el supervisor...",
    )

    if st.button("Revisar cambios", type="primary", use_container_width=True, key=f"edit_btn_{rev}"):
        if f_ent is None or f_sal is None:
            st.error("Completa ambas fechas.")
        else:
            ts_ent_nueva = datetime.combine(f_ent, h_ent)
            ts_sal_nueva = datetime.combine(f_sal, h_sal)
            if ts_sal_nueva <= ts_ent_nueva:
                st.error("La salida debe ser posterior a la entrada.")
            else:
                horas_nuevas = calcular_horas(ts_ent_nueva, ts_sal_nueva)
                st.session_state["_edit_pendiente"] = {
                    "nombre": emp_corr,
                    "ts_entrada_str_orig": ts_entrada_str_orig,
                    "ts_ent_orig": ts_ent_orig,
                    "ts_sal_orig": ts_sal_orig,
                    "horas_orig": h_orig,
                    "obs_orig": obs_orig,
                    "ts_ent_nueva": ts_ent_nueva,
                    "ts_sal_nueva": ts_sal_nueva,
                    "horas_nuevas": horas_nuevas,
                    "obs_nueva": obs_nueva.strip(),
                    "admin_user": admin_user,
                }

    if st.session_state.get("_edit_pendiente"):
        _dialogo_confirmar_edicion()


@st.dialog("Eliminar turno", width="large")
def _dialogo_confirmar_borrado() -> None:
    payload = st.session_state.get("_del_pendiente")
    if not payload:
        st.rerun()
        return

    st.warning("Esta acción elimina el turno de forma definitiva. No se puede deshacer.")

    def _fila(label: str, valor: str, color: str = BRAND_NAVY) -> str:
        return (
            f"<div style='display:flex;justify-content:space-between;align-items:center;"
            f"padding:7px 0;border-bottom:1px solid #eef0f6;'>"
            f"<span style='color:{BRAND_MUTED};font-size:.85rem;'>{label}</span>"
            f"<span style='font-weight:600;color:{color};font-size:.95rem;'>{valor}</span>"
            f"</div>"
        )

    st.markdown(
        f"<div style='background:{BRAND_BG_SOFT};border-radius:10px;padding:4px 14px 8px;margin-bottom:12px;'>"
        + _fila("Empleado", payload["nombre"])
        + _fila("Fecha de turno", payload["fecha"])
        + _fila("Entrada", payload["entrada"])
        + _fila("Salida", payload["salida"])
        + _fila("Estado", payload["estado"])
        + _fila("Observaciones", payload["obs"] or "—")
        + "</div>",
        unsafe_allow_html=True,
    )

    bc, bx = st.columns(2)
    with bc:
        if st.button("Eliminar definitivamente", type="primary", use_container_width=True, key="del_dlg_confirm"):
            if bloquear_doble_click("del_confirm"):
                st.rerun()
                return
            ok = eliminar_por_entrada(payload["nombre"], payload["ts_entrada_str"])
            if ok:
                set_flash(f"Turno de {payload['nombre']} ({payload['fecha']}) eliminado")
                st.session_state["_del_pendiente"] = None
                st.session_state["_del_rev"] = st.session_state.get("_del_rev", 0) + 1
                st.rerun()
            else:
                st.error("No se encontró el turno en la hoja. Puede haber sido modificado. Recarga la página.")
    with bx:
        if st.button("Cancelar", use_container_width=True, key="del_dlg_cancel"):
            st.session_state["_del_pendiente"] = None
            st.rerun()


def _flujo_eliminar_turno(emp_corr, df: pd.DataFrame) -> None:
    """Elimina un turno mal ingresado del empleado seleccionado. Muestra todos
    los turnos del empleado dentro de los filtros activos (cualquier estado,
    incluidas eventualidades) y permite borrar uno con confirmación."""
    rev = st.session_state.get("_del_rev", 0)

    st.caption(
        "Borra un turno mal ingresado. Se muestran todos los turnos del funcionario "
        "dentro de los filtros activos. El borrado es definitivo."
    )

    if df is None or df.empty:
        st.info("No hay registros en los filtros activos.")
        return

    nombre_col = df["Nombre"].fillna("").astype(str).str.strip()
    cerrados = df[nombre_col == str(emp_corr).strip()].copy().sort_values("Timestamp Entrada").reset_index(drop=True)

    if cerrados.empty:
        st.info(f"{emp_corr} no tiene turnos dentro de los filtros activos.")
        return

    st.caption(f"{len(cerrados)} turno(s) en los filtros activos. Selecciona la fila a eliminar.")

    cols_disp = ["Fecha de Turno", "Timestamp Entrada", "Timestamp Salida",
                 "Horas Trabajadas", "Estado", "Observaciones"]
    disp = cerrados[cols_disp].copy()
    for col in ["Timestamp Entrada", "Timestamp Salida"]:
        disp[col] = disp[col].apply(lambda v: pd.Timestamp(v).strftime("%Y-%m-%d %H:%M") if pd.notna(v) else "—")
    disp["Horas Trabajadas"] = pd.to_numeric(disp["Horas Trabajadas"], errors="coerce").apply(_dec_a_hhmm)

    event = st.dataframe(
        disp,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=f"del_tabla_{rev}",
    )

    sel_rows = event.selection.rows
    if not sel_rows:
        return

    turno = cerrados.iloc[sel_rows[0]]
    ts_ent_val = turno["Timestamp Entrada"]
    if pd.isna(ts_ent_val):
        st.error("Este turno no tiene hora de entrada válida y no puede eliminarse desde aquí.")
        return
    ts_ent_dt = pd.Timestamp(ts_ent_val).to_pydatetime()
    ts_sal_val = turno["Timestamp Salida"]
    fecha_turno = turno["Fecha de Turno"]
    obs = str(turno.get("Observaciones", "") or "").strip()
    if obs.lower() == "nan":
        obs = ""

    if st.button("Eliminar turno seleccionado", type="primary", use_container_width=True, key=f"del_btn_{rev}"):
        st.session_state["_del_pendiente"] = {
            "nombre": emp_corr,
            "ts_entrada_str": ts_ent_dt.strftime(TS_FMT),
            "fecha": str(fecha_turno),
            "entrada": ts_ent_dt.strftime("%Y-%m-%d %H:%M"),
            "salida": pd.Timestamp(ts_sal_val).strftime("%Y-%m-%d %H:%M") if pd.notna(ts_sal_val) else "—",
            "estado": str(turno.get("Estado", "")),
            "obs": obs,
        }

    if st.session_state.get("_del_pendiente"):
        _dialogo_confirmar_borrado()


# ── Módulo de Eventualidades ────────────────────────────────────────────────
EVT_HORA_ENTRADA = time(8, 0)
EVT_HORA_SALIDA = time(17, 0)

_DIAS_ES = {0: "Lun", 1: "Mar", 2: "Mié", 3: "Jue", 4: "Vie", 5: "Sáb", 6: "Dom"}


def _obs_evento(tipo: str, subcat, admin_user: str) -> str:
    """Construye la observación del evento. Vacaciones empieza con 'Vacaciones'
    para que el gráfico de progreso la siga reconociendo."""
    base = tipo if tipo == EVENTO_VACACIONES else f"{tipo} - {subcat}"
    return f"{base} (registrado por {admin_user})"


@st.dialog("Confirmar registro de eventualidad", width="large")
def _dialogo_confirmar_evento() -> None:
    payload = st.session_state.get("_evt_pendiente")
    if not payload:
        st.rerun()
        return

    dias_nuevos = [date.fromisoformat(s) for s in payload["dias_nuevos"]]
    dias_saltados = [date.fromisoformat(s) for s in payload["dias_saltados"]]
    n = len(dias_nuevos)
    h_ef_total = n * calcular_horas_efectivas(9.0)

    def _fila(label: str, valor: str, color: str = BRAND_NAVY) -> str:
        return (
            f"<div style='display:flex;justify-content:space-between;align-items:center;"
            f"padding:7px 0;border-bottom:1px solid #eef0f6;'>"
            f"<span style='color:{BRAND_MUTED};font-size:.85rem;'>{label}</span>"
            f"<span style='font-weight:600;color:{color};font-size:.95rem;'>{valor}</span>"
            f"</div>"
        )

    f_ini = date.fromisoformat(payload["f_ini"])
    f_fin = date.fromisoformat(payload["f_fin"])

    filas = (
        _fila("Empleado", payload["emp"])
        + _fila("Área", payload["area"])
        + _fila("Tipo de evento", payload["tipo"])
    )
    if payload.get("subcat"):
        filas += _fila("Motivo", payload["subcat"])
    filas += (
        _fila("Rango solicitado", f"{f_ini.strftime('%d/%m/%Y')} → {f_fin.strftime('%d/%m/%Y')}")
        + _fila("Turno por día", "08:00 → 17:00  ·  9 h (8 h efectivas, 1 h almuerzo)")
        + _fila("Días a registrar", f"{n} día(s) laborable(s)", BRAND_RED)
        + _fila("Total horas efectivas", f"{h_ef_total:.0f} h")
    )

    st.markdown(
        f"<div style='background:{BRAND_BG_SOFT};border-radius:10px;padding:4px 14px 8px;margin-bottom:12px;'>"
        + filas + "</div>",
        unsafe_allow_html=True,
    )

    chips = "".join(
        f"<span style='display:inline-block;background:#EBF0FF;color:{BRAND_NAVY};"
        f"padding:3px 10px;border-radius:8px;margin:3px;font-size:.82rem;font-weight:600;'>"
        f"{_DIAS_ES[d.weekday()]} {d.strftime('%d/%m')}</span>"
        for d in dias_nuevos
    )
    st.markdown("**Días que se registrarán:**")
    st.markdown(f"<div style='line-height:2;'>{chips}</div>", unsafe_allow_html=True)

    if dias_saltados:
        saltados_str = ", ".join(f"{_DIAS_ES[d.weekday()]} {d.strftime('%d/%m')}" for d in dias_saltados)
        st.caption(f"{len(dias_saltados)} día(s) se omiten porque ya tienen un turno registrado: {saltados_str}")

    st.divider()
    bc, bx = st.columns(2)
    with bc:
        if st.button("Confirmar registro", type="primary", use_container_width=True, key="evt_dlg_confirm"):
            if bloquear_doble_click("evt_confirm"):
                st.rerun()
                return
            emp = payload["emp"]
            area = payload["area"]
            obs = _obs_evento(payload["tipo"], payload.get("subcat"), payload["admin_user"])
            filas_reg = []
            for d in dias_nuevos:
                ts_in = datetime.combine(d, EVT_HORA_ENTRADA)
                ts_out = datetime.combine(d, EVT_HORA_SALIDA)
                horas = calcular_horas(ts_in, ts_out)
                filas_reg.append({
                    "Nombre": emp,
                    "Area": area,
                    "Fecha de Turno": d.strftime("%Y-%m-%d"),
                    "Timestamp Entrada": ts_in.strftime(TS_FMT),
                    "Timestamp Salida": ts_out.strftime(TS_FMT),
                    "Horas Trabajadas": horas,
                    "Horas Efectivas": calcular_horas_efectivas(horas),
                    "Horas Extra": calcular_horas_extra(horas),
                    "Estado": "Completo",
                    "Observaciones": obs,
                })
            n_ins = append_registros_batch(filas_reg)
            set_flash(f"{n_ins} día(s) registrados para {emp} — {payload['tipo']}")
            st.session_state["_evt_pendiente"] = None
            st.session_state["_evt_rev"] = st.session_state.get("_evt_rev", 0) + 1
            st.rerun()
    with bx:
        if st.button("Cancelar", use_container_width=True, key="evt_dlg_cancel"):
            st.session_state["_evt_pendiente"] = None
            st.rerun()


def _render_eventualidades() -> None:
    admin_user = st.session_state.get("admin_user", "")
    areas_admin = AREAS_POR_ADMIN.get(admin_user)
    rev = st.session_state.get("_evt_rev", 0)

    st.caption(
        "Registra faltas justificadas, permisos o vacaciones. Cada día del rango se crea "
        "como un turno de 08:00 a 17:00 (9 h trabajadas, 8 h efectivas) con el motivo en "
        "observaciones. Vacaciones solo toma días laborables (L-V); faltas y permisos toman "
        "todos los días. Los días que ya tengan un turno se omiten."
    )

    areas_evt = sorted(AREAS) if areas_admin is None else sorted(areas_admin)
    if not areas_evt:
        st.warning("No tienes áreas habilitadas para registrar eventualidades.")
        return

    tipo = st.selectbox("Tipo de evento", TIPOS_EVENTO, key=f"evt_tipo_{rev}")
    subcat = None
    if tipo != EVENTO_VACACIONES:
        subcat = st.selectbox("Motivo", SUBCATEGORIAS_EVENTO, key=f"evt_sub_{rev}")

    c1, c2 = st.columns([2, 3])
    with c1:
        area_sel = st.selectbox(
            "Área",
            [None] + areas_evt,
            format_func=lambda x: "— Selecciona un área —" if x is None else x,
            key=f"evt_area_{rev}",
        )
    with c2:
        lista_emp = [None] + sorted(EMPLEADOS_POR_AREA.get(area_sel, [])) if area_sel else [None]
        emp_sel = st.selectbox(
            "Empleado",
            lista_emp,
            format_func=lambda x: "— Selecciona un empleado —" if x is None else x,
            key=f"evt_emp_{rev}",
        )

    if area_sel is None or emp_sel is None:
        st.info("Selecciona un área y un empleado para registrar el evento.")
        return

    c3, c4 = st.columns(2)
    with c3:
        f_ini = st.date_input("Fecha de inicio", value=None, key=f"evt_ini_{rev}")
    with c4:
        f_fin = st.date_input("Fecha de fin", value=None, key=f"evt_fin_{rev}")

    if st.button("Revisar registro", type="primary", use_container_width=True, key=f"evt_btn_{rev}"):
        if f_ini is None or f_fin is None:
            st.error("Selecciona fecha de inicio y fin.")
        elif f_fin < f_ini:
            st.error("La fecha de fin debe ser igual o posterior a la de inicio.")
        else:
            # Vacaciones: solo días laborables (L-V). Faltas/permisos: todos los
            # días, porque hay funcionarios con turnos también en fin de semana.
            solo_laborables = (tipo == EVENTO_VACACIONES)
            dias = []
            d = f_ini
            while d <= f_fin:
                if not solo_laborables or d.weekday() < 5:
                    dias.append(d)
                d += timedelta(days=1)

            if not dias:
                st.warning("El rango seleccionado no contiene días laborables (lunes a viernes).")
            else:
                df_act = leer_registros()
                fechas_emp = set()
                if not df_act.empty:
                    emp_norm = str(emp_sel).strip()
                    mask_emp = df_act["Nombre"].fillna("").astype(str).str.strip() == emp_norm
                    for raw in df_act.loc[mask_emp, "Fecha de Turno"]:
                        f = parse_fecha_flexible(raw)
                        if f:
                            fechas_emp.add(f)
                dias_nuevos = [d for d in dias if d not in fechas_emp]
                dias_saltados = [d for d in dias if d in fechas_emp]
                if not dias_nuevos:
                    st.warning(
                        "Todos los días laborables del rango ya tienen un turno registrado "
                        f"para {emp_sel}. No hay nada que agregar."
                    )
                else:
                    st.session_state["_evt_pendiente"] = {
                        "tipo": tipo,
                        "subcat": subcat,
                        "emp": emp_sel,
                        "area": AREA_DE.get(emp_sel, ""),
                        "f_ini": f_ini.isoformat(),
                        "f_fin": f_fin.isoformat(),
                        "dias_nuevos": [d.isoformat() for d in dias_nuevos],
                        "dias_saltados": [d.isoformat() for d in dias_saltados],
                        "admin_user": admin_user,
                    }

    if st.session_state.get("_evt_pendiente"):
        _dialogo_confirmar_evento()


def _es_solo_lectura(admin_user: str) -> bool:
    """Devuelve True si el usuario tiene solo_lectura = true en secrets."""
    try:
        cfg = st.secrets["super_admins"].get(admin_user, {})
        return bool(cfg.get("solo_lectura", False))
    except Exception:
        return False


def vista_super_admin() -> None:
    usuario = st.session_state["usuario"]
    admin_rol = st.session_state.get("admin_rol", "")
    admin_user = st.session_state.get("admin_user", "")
    areas_permitidas = _get_areas_permitidas(admin_user)
    solo_lectura = _es_solo_lectura(admin_user)

    _inject_brand_css()

    st.title("Marcador de Horas — Panel Administrativo")

    iniciales = "".join(p[0] for p in str(usuario).split()[:2]).upper() or "?"
    ch1, ch2, ch3 = st.columns([4, 1, 1])
    with ch1:
        st.markdown(
            f"""
            <div class="brand-header">
                <div class="user-block">
                    <div class="avatar">{iniciales}</div>
                    <div>
                        <div class="uname">{usuario}</div>
                        <span class="role">{admin_rol}</span>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with ch2:
        st.write("")
        if st.button("Actualizar", use_container_width=True, help="Relee los datos de la base ahora mismo"):
            leer_registros.clear()
            leer_historico.clear()
            leer_horas_esperadas.clear()
            set_flash("Datos actualizados.")
            st.rerun()
    with ch3:
        st.write("")
        if st.button("Cerrar sesión", use_container_width=True):
            logout()
            st.rerun()

    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)

    # Barrido de turnos olvidados: una vez por sesión de panel, envía a revisión
    # los turnos abiertos con más de UMBRAL_OLVIDO_H h. Cubre el caso en que el
    # empleado nunca vuelve a marcar (marcar_entrada/salida no se disparan).
    if not st.session_state.get("_barrido_olvidados_hecho"):
        st.session_state["_barrido_olvidados_hecho"] = True
        _n_olv = barrer_turnos_olvidados(leer_registros())
        if _n_olv:
            set_flash(
                f"{_n_olv} turno(s) con más de {UMBRAL_OLVIDO_H} h sin salida "
                "enviado(s) a revisión."
            )

    # Archivado automático de meses cerrados: una vez por sesión. Solo procede si
    # lo pendiente es de un único mes (mantenimiento liviano); un backlog de
    # varios meses se deja para el archivado manual (primera limpieza).
    if not st.session_state.get("_archivado_auto_hecho"):
        st.session_state["_archivado_auto_hecho"] = True
        _res_arch = archivar_historico(solo_un_mes=True)
        if _res_arch["archivadas"]:
            set_flash(f"{_res_arch['archivadas']} registro(s) de meses cerrados archivados en 'Historico'.")
        elif _res_arch["bloqueado"]:
            st.session_state["_archivado_backlog"] = _res_arch["meses"]

    mostrar_flash()

    # Sidebar (parte superior): alcance de datos. Los filtros los agrega
    # _filtros_inline a continuación y las utilidades van al fondo.
    with st.sidebar:
        if areas_permitidas is None:
            st.caption("Acceso total a todas las áreas.")
        else:
            st.caption(f"Áreas habilitadas: {', '.join(sorted(areas_permitidas))}" +
                       ("  |  Solo lectura" if solo_lectura else ""))
        incluir_hist = st.checkbox(
            "Incluir histórico archivado",
            value=False,
            help="Incluye también los turnos archivados de meses cerrados.",
        )

    df_raw = leer_registros()
    if incluir_hist:
        df_raw = pd.concat([df_raw, leer_historico()], ignore_index=True)
    df_scope = _aplicar_scope_admin(df_raw, admin_user)
    df_dash = _preparar_df_dashboard(df_scope)

    # Los turnos en revisión requieren acción de un supervisor: que se vean
    # de entrada, sin tener que llegar hasta la pestaña de gestión.
    n_rev = int((df_dash["Estado"] == "Revision").sum())
    if n_rev:
        if solo_lectura:
            st.warning(f"Hay **{n_rev} turno(s) en revisión** pendientes de gestión por un administrador.")
        else:
            st.warning(
                f"Hay **{n_rev} turno(s) en revisión** esperando tu gestión — "
                "revísalos en la pestaña **Gestión de turnos**."
            )

    # Universo para el "período anterior" de los deltas: incluye el histórico
    # archivado (el mes previo casi siempre está ahí), sin alterar lo que ve
    # el usuario en las gráficas.
    if incluir_hist:
        df_base_prev = df_dash
    else:
        df_base_prev = pd.concat(
            [df_dash, _preparar_df_dashboard(_aplicar_scope_admin(leer_historico(), admin_user))],
            ignore_index=True,
        )
    df_filt, df_prev, df_todo_meses = _filtros_inline(
        df_dash, areas_permitidas=areas_permitidas, df_base_prev=df_base_prev
    )

    # Utilidades al fondo del sidebar, debajo de los filtros.
    with st.sidebar:
        st.divider()
        with st.expander("Acceso de este equipo"):
            confiar_equipo_ui()
        if admin_user == "mpillapa":
            with st.expander("Mantenimiento: archivar históricos"):
                st.caption(
                    "Marca como archivados los turnos 'Completo' de meses "
                    "anteriores al actual (solo cambia una marca en la base; "
                    "no borra nada). Los turnos abiertos o en revisión no se tocan."
                )
                _backlog = st.session_state.get("_archivado_backlog")
                if _backlog:
                    _meses_txt = ", ".join(f"{a}-{m:02d}" for a, m in _backlog)
                    st.warning(
                        f"Hay registros de varios meses pendientes de archivar ({_meses_txt}). "
                        "El archivado automático no los toca; usa el botón para la limpieza inicial."
                    )
                if st.button("Archivar meses cerrados ahora", type="primary"):
                    if bloquear_doble_click("archivar_hist"):
                        st.rerun()
                    else:
                        _res = archivar_historico(solo_un_mes=False)
                        st.session_state.pop("_archivado_backlog", None)
                        set_flash(f"{_res['archivadas']} registro(s) archivados.")
                        st.rerun()

    if solo_lectura:
        tab_dash, tab_comp, tab_tabla = st.tabs([
            "Dashboard",
            "Comparativo horas esperadas",
            "Tabla",
        ])
        with tab_dash:
            _render_dashboard(df_filt, df_prev)
        with tab_comp:
            _render_comparativo_horas(df_filt, df_todo_meses)
        with tab_tabla:
            _render_tabla(df_filt)
    else:
        tab_dash, tab_comp, tab_tabla, tab_gestion, tab_evt = st.tabs([
            "Dashboard",
            "Comparativo horas esperadas",
            "Tabla",
            "Gestión de turnos",
            "Eventualidades",
        ])
        with tab_dash:
            _render_dashboard(df_filt, df_prev)
        with tab_comp:
            _render_comparativo_horas(df_filt, df_todo_meses)
        with tab_tabla:
            _render_tabla(df_filt)
        with tab_gestion:
            _render_correcciones(areas_permitidas=areas_permitidas, df_filt=df_filt)
        with tab_evt:
            _render_eventualidades()
