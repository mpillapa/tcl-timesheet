"""Paleta de marca Transoceánica y CSS compartido entre vistas.

Única fuente de los colores corporativos: views/super_admin.py los importa
para su CSS y tema Altair, y el marcador (colaborador + login) usa aquí su
estilo de quiosco. El tema de widgets nativos vive en .streamlit/config.toml.
"""

import streamlit as st

BRAND_NAVY = "#1E2D78"
BRAND_NAVY_MID = "#3A4BA0"
BRAND_NAVY_SOFT = "#8A96C9"
BRAND_RED = "#D8202F"
BRAND_RED_SOFT = "#F5C4C8"
BRAND_VAC = "#2F9E8F"  # turquesa para horas de vacaciones
BRAND_CUOTA = "#C77D00"  # ámbar/dorado para la línea de cuota (meta)
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

BRAND_EVENTO = "#7A5CA6"  # morado para faltas/permisos en el gráfico


def inject_kiosk_css() -> None:
    """Estilo de quiosco para el marcador y las pantallas de login: tarjetas
    de estado, reloj y botón de acción grande (pensado para celular/tablet y
    PCs compartidas). El botón grande aplica solo a los 'primary'; los
    secundarios (cerrar sesión, admin) quedan de tamaño normal."""
    st.markdown(
        f"""
        <style>
            /* padding-top >= altura del header fijo de Streamlit (~3.75rem);
               con menos, la tarjeta del usuario queda cortada debajo de él. */
            .block-container {{
                max-width: 640px !important;
                padding-top: 4.6rem;
            }}
            h1, h2, h3 {{ color: {BRAND_NAVY}; }}

            .kiosk-header {{
                display:flex; align-items:center; gap:14px;
                padding: 14px 20px; border-radius: 14px;
                background: linear-gradient(90deg, {BRAND_NAVY} 0%, {BRAND_NAVY_MID} 100%);
                color: #FFFFFF;
                box-shadow: 0 4px 14px rgba(30,45,120,0.18);
                margin-bottom: 6px;
            }}
            .kiosk-header .avatar {{
                width:46px; height:46px; border-radius:50%; flex: 0 0 46px;
                background: rgba(255,255,255,0.18);
                display:flex; align-items:center; justify-content:center;
                font-size:1.25rem; font-weight:700;
            }}
            .kiosk-header .uname {{ font-size:1.1rem; font-weight:700; line-height:1.2; }}
            .kiosk-header .uarea {{
                background: rgba(255,255,255,0.18);
                padding: 2px 12px; border-radius: 999px;
                font-size: 0.78rem; display:inline-block; margin-top:3px;
            }}

            .kiosk-clock {{
                text-align:center; margin: 10px 0 2px;
            }}
            .kiosk-clock .hora {{
                font-size: 3.2rem; font-weight: 800; color: {BRAND_NAVY};
                line-height: 1.05; letter-spacing: 0.02em;
            }}
            .kiosk-clock .fecha {{
                color: {BRAND_MUTED}; font-size: 0.95rem; margin-top: 2px;
            }}

            .kiosk-status {{
                border-radius: 14px; padding: 16px 20px; margin: 14px 0 10px;
                border: 1px solid #E6E9F4;
            }}
            .kiosk-status .titulo {{
                font-size: 0.78rem; font-weight: 700; text-transform: uppercase;
                letter-spacing: 0.06em; margin-bottom: 4px;
            }}
            .kiosk-status .detalle {{ font-size: 1.05rem; }}
            .kiosk-status .detalle b {{ font-size: 1.15rem; }}
            .kiosk-status.abierto {{
                background: #EBF0FF; border-color: #C5CBDF;
            }}
            .kiosk-status.abierto .titulo {{ color: {BRAND_NAVY}; }}
            .kiosk-status.libre {{
                background: {BRAND_BG_SOFT};
            }}
            .kiosk-status.libre .titulo {{ color: {BRAND_MUTED}; }}
            .kiosk-status.exito {{
                background: #E1F3E7; border-color: #BCE3C9;
            }}
            .kiosk-status.exito .titulo {{ color: #1F9254; }}

            /* Botón de acción principal, tamaño quiosco (ambas variantes de
               testid según versión de Streamlit) */
            button[data-testid="stBaseButton-primary"],
            button[data-testid="baseButton-primary"] {{
                height: 84px !important;
                font-size: 1.35rem !important;
                font-weight: 700 !important;
                border-radius: 16px !important;
            }}

        </style>
        """,
        unsafe_allow_html=True,
    )


def inject_login_css() -> None:
    """Estilo del login de colaborador: PIN grande, centrado y con espaciado
    (además del estilo de quiosco base)."""
    inject_kiosk_css()
    st.markdown(
        """
        <style>
            [data-testid="stTextInput"] input[type="password"] {
                font-size: 1.5rem !important;
                text-align: center;
                letter-spacing: 0.35em;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )
