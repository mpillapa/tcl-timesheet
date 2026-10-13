"""Lista los turnos existentes que las reglas nuevas no admitirían. Solo lectura.

Las validaciones de V004 se aplican a lo que se escribe desde la migración, no
al histórico: un turno viejo que las incumple sigue ahí hasta que alguien lo
toque, y en ese momento la base rechaza el cambio. Este reporte los muestra
antes, para revisarlos con calma y no encontrarlos en medio de una corrección.

Funciona antes y después de migrar. La transacción se abre en READ ONLY: el
script no puede escribir aunque quisiera.

Uso
---
    .venv\\Scripts\\python.exe migracion\\validar_datos_existentes.py --desde-secrets --produccion
    $env:MIGRACION_DB_URL = "postgresql://..."; .venv\\Scripts\\python.exe migracion\\validar_datos_existentes.py

No imprime la URL ni la contraseña. Muestra nombres de personas: no pegues la
salida en un canal abierto.
"""

import argparse
import sys

import psycopg2

from aplicar_migraciones import describir_destino, resolver_url

# El mismo valor provisional que V001. Si ya se migró, se lee de la tabla.
DURACION_MAXIMA_POR_DEFECTO = 24

# Turnos de trabajo: los que no son eventualidades. Antes de V006 la columna
# evento está vacía y se reconoce por la observación. El %% es un % escapado:
# las consultas van con parámetros y psycopg2 reserva el % para marcarlos.
ES_TRABAJO = """
    coalesce(nullif(evento, ''), '') = ''
    and translate(lower(observaciones), 'áéíóú', 'aeiou') not similar to
        '(vacaciones|justificacion de falta|solicitud de permiso)%%'
"""
VIGENTE = "not coalesce((to_jsonb(t) ->> 'anulado')::boolean, false)"

CONTROLES = [
    ("Turnos más largos que la duración máxima",
     "Se rechazaría cualquier cambio sobre ellos. Revisar si la salida es real.",
     f"""select nombre, fecha_turno, ts_entrada, ts_salida, horas_trabajadas
         from turnos t
         where {VIGENTE} and ts_salida is not null
           and extract(epoch from ts_salida - ts_entrada) / 3600 > %(maxima)s
         order by horas_trabajadas desc"""),

    ("Turnos de trabajo que se cruzan entre sí",
     "Dos turnos de la misma persona en el mismo horario. Uno sobra o tiene la hora mal.",
     f"""select a.nombre, a.fecha_turno, a.ts_entrada, a.ts_salida, b.ts_entrada, b.ts_salida
         from turnos a join turnos b
           on lower(a.nombre) = lower(b.nombre) and a.id < b.id
         where not coalesce((to_jsonb(a) ->> 'anulado')::boolean, false)
           and not coalesce((to_jsonb(b) ->> 'anulado')::boolean, false)
           and a.ts_entrada < coalesce(b.ts_salida, b.ts_entrada + interval '1 second')
           and b.ts_entrada < coalesce(a.ts_salida, a.ts_entrada + interval '1 second')
           and {ES_TRABAJO.replace('evento', 'a.evento').replace('observaciones', 'a.observaciones')}
           and {ES_TRABAJO.replace('evento', 'b.evento').replace('observaciones', 'b.observaciones')}
         order by a.fecha_turno"""),

    ("Fecha de turno distinta de la fecha de la entrada",
     "La base la recalcularía al primer cambio, y el turno se movería de día en los gráficos.",
     f"""select nombre, fecha_turno, ts_entrada
         from turnos t
         where {VIGENTE}
           and fecha_turno <> (ts_entrada at time zone 'America/Guayaquil')::date
           and pg_typeof(ts_entrada)::text = 'timestamp with time zone'
         union all
         select nombre, fecha_turno, ts_entrada
         from turnos t
         where {VIGENTE}
           and pg_typeof(ts_entrada)::text = 'timestamp without time zone'
           and fecha_turno <> ts_entrada::date
         order by 2"""),

    ("Turnos de trabajo con fecha futura",
     "Una eventualidad puede registrarse por adelantado; un turno trabajado no.",
     f"""select nombre, fecha_turno, ts_entrada, estado, left(observaciones, 50)
         from turnos t
         where {VIGENTE} and fecha_turno > (now() at time zone 'America/Guayaquil')::date
           and {ES_TRABAJO}
         order by fecha_turno"""),

    ("Turnos abiertos desde hace más de un día",
     "Deberían estar en revisión. El barrido del panel los envía al abrirlo.",
     f"""select nombre, fecha_turno, ts_entrada, estado
         from turnos t
         where {VIGENTE} and estado = 'Abierto' and ts_salida is null
           and ts_entrada < now() - interval '1 day'
         order by ts_entrada"""),
]

CONTROL_EMPLEADOS = (
    "Personas creadas automáticamente",
    "Un turno llegó con un nombre que no estaba en la tabla de empleados. Puede ser un "
    "ingreso nuevo o un nombre mal escrito. Correr sincronizar_empleados.py y revisar.",
    """select e.nombre, e.area, e.creado_en, count(t.id)
       from empleados e left join turnos t on t.empleado_id = e.id
       where e.origen = 'automatico'
       group by 1, 2, 3 order by 3 desc""",
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--desde-secrets", action="store_true")
    ap.add_argument("--produccion", action="store_true")
    ap.add_argument("--limite", type=int, default=20, help="Filas de detalle por control.")
    args = ap.parse_args()

    url = resolver_url(args)
    print(f"Destino: {describir_destino(url)} (solo lectura)\n")
    conn = psycopg2.connect(url, connect_timeout=15)
    conn.set_session(readonly=True)
    hallazgos = 0
    try:
        with conn.cursor() as cur:
            cur.execute("select to_regclass('public.reglas_negocio') is not null, "
                        "       to_regclass('public.empleados') is not null")
            con_reglas, con_empleados = cur.fetchone()
            maxima = DURACION_MAXIMA_POR_DEFECTO
            if con_reglas:
                cur.execute("select valor from reglas_negocio where clave = 'duracion_maxima_turno_h'")
                maxima = float(cur.fetchone()[0])

            controles = list(CONTROLES) + ([CONTROL_EMPLEADOS] if con_empleados else [])
            for titulo, explicacion, sql in controles:
                cur.execute(sql, {"maxima": maxima})
                filas = cur.fetchall()
                marca = "OK " if not filas else f"{len(filas):>3}"
                print(f"[{marca}] {titulo}")
                if filas:
                    hallazgos += len(filas)
                    print(f"      {explicacion}")
                    for f in filas[: args.limite]:
                        print("      " + " | ".join("" if v is None else str(v) for v in f))
                    if len(filas) > args.limite:
                        print(f"      ... y {len(filas) - args.limite} más")
                print()
    finally:
        conn.close()

    print(f"Total de hallazgos: {hallazgos}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
