"""Formato del rastro que las correcciones dejan en `Observaciones`.

Vive en su propio módulo porque es una regla de negocio, no una decisión de
presentación: define cómo se lee y se escribe el historial visible de un turno,
y la usan tanto el panel de admin como los scripts de auditoría.

El formato es:

    [Corrección YYYY-MM-DD por <usuario>]: <motivo> | [Orig]: <observación previa>

La observación previa es lo que había antes de la corrección, que normalmente
es la justificación que escribió el colaborador al marcar una salida por encima
de UMBRAL_HORAS_EXTRA.

Sobre las correcciones repetidas
-------------------------------
Hasta el 2026-09-02 una segunda corrección envolvía el tag de la primera, y el
campo quedaba así:

    [Corr B]: m2 | [Orig]: [Corr A]: m1 | [Orig]: Horas extra justificadas: X

Eso tenía dos consecuencias malas: el campo crecía sin límite, y una consulta
que leyera el tag más externo atribuía a B una corrección que también hizo A.
Se detectaron 13 turnos con tags anidados.

Ahora se preserva solo la observación del colaborador y el tag anterior se
descarta del texto, porque el historial completo con valores antes y después
vive en `turnos_auditoria` (ver migracion/auditoria_schema.sql), que es una
fuente mejor que un campo de texto.

Las filas históricas no se reescriben: ese texto anidado es la única traza que
existe de esas correcciones anteriores a la bitácora. Para leerlas está
`tags_de_correccion`, que devuelve todas y no solo la externa.
"""

import re

_MARCA_ORIG = "[Orig]:"

# Tolera el acento de "Corrección", que viaja distinto según de dónde salió la
# fila (Sheet migrado o app), y captura fecha y usuario.
_RE_TAG = re.compile(r"\[Correcci\w*n\s+(\d{4}-\d{2}-\d{2})\s+por\s+([^\]]+)\]",
                     re.IGNORECASE)


def tag_correccion(fecha_str: str, usuario: str) -> str:
    """El tag que encabeza una observación corregida."""
    return f"[Corrección {fecha_str} por {usuario}]"


def observacion_del_colaborador(observaciones: str) -> str:
    """Devuelve la observación original del colaborador, sin los tags de
    correcciones anteriores.

    Casos que cubre:
      'Horas extra justificadas: X'                      -> tal cual
      '[Corr A]: m | [Orig]: Horas extra ...: X'         -> 'Horas extra ...: X'
      '[Corr B]: m2 | [Orig]: [Corr A]: m1 | [Orig]: X'  -> 'X'  (el más interno)
      '[Corr A]: m'                                       -> ''  (no había previa)
      ''                                                  -> ''
    """
    texto = (observaciones or "").strip()
    if not texto:
        return ""
    if _MARCA_ORIG in texto:
        # El último [Orig]: es el más interno, es decir la observación real.
        return texto.rsplit(_MARCA_ORIG, 1)[1].strip()
    if _RE_TAG.match(texto):
        return ""
    return texto


def tags_de_correccion(observaciones: str) -> list:
    """Todas las correcciones registradas en el texto, de la más reciente a la
    más antigua: [(fecha, usuario), ...]. Devuelve más de una en las filas
    históricas con tags anidados."""
    return [(f, u.strip()) for f, u in _RE_TAG.findall(observaciones or "")]


def fue_corregido_por(observaciones: str, usuario: str) -> bool:
    """True si `usuario` aparece como autor de cualquiera de las correcciones,
    no solo de la más externa."""
    objetivo = (usuario or "").strip().casefold()
    return any(u.casefold() == objetivo for _, u in tags_de_correccion(observaciones))
