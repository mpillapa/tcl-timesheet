"""
Carga del padrón de empleados desde secrets.toml.

Estructura esperada en secrets:

    [empleados]
    "3399" = {nombre = "Jaramillo Napoleon", area = "SUPERVISORES"}
    "7404" = {nombre = "Yanza Cristina", area = "BODEGA"}
    ...

Exporta:
    PIN_A_EMPLEADO   — dict {pin: nombre}
    EMPLEADOS_POR_AREA — dict {area: [nombres]}
    AREA_DE          — dict {nombre: area}
    AREAS            — lista de áreas
"""

import streamlit as st


def _cargar():
    data = dict(st.secrets.get("empleados", {}))
    pin_a_emp = {}
    emp_por_area = {}
    area_de = {}

    for pin, info in data.items():
        info_d = dict(info) if hasattr(info, "keys") else {}
        nombre = str(info_d.get("nombre", "")).strip()
        area = str(info_d.get("area", "")).strip()
        if not nombre or not area:
            continue
        pin_a_emp[str(pin)] = nombre
        area_de[nombre] = area
        emp_por_area.setdefault(area, []).append(nombre)

    for a in emp_por_area:
        emp_por_area[a].sort()

    return pin_a_emp, emp_por_area, area_de, list(emp_por_area.keys())


PIN_A_EMPLEADO, EMPLEADOS_POR_AREA, AREA_DE, AREAS = _cargar()
