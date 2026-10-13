# Retención y acceso a los datos de auditoría

Propuesta técnica para que Recursos Humanos y Legal decidan. Nada de lo que
sigue está decidido salvo lo que se indica como ya implementado.

## Qué datos se guardan

Desde las migraciones de [migracion/versiones/](../migracion/versiones/), la
base guarda, además de los turnos:

| Tabla | Contenido | Identifica a la persona |
|---|---|---|
| `marcaciones` | Cada entrada y salida marcada en el reloj | Sí, por nombre |
| `turnos_auditoria` | Cada cambio sobre un turno, con la fila completa antes y después | Sí, por nombre, y al administrador que cambió |
| `ajustes_turno` | Cada corrección y anulación, con motivo y revisión | Sí, al colaborador y a los dos administradores |
| `empleados` | Nombre, área y si está activo | Sí. No guarda el PIN ni la cédula |
| `ausencias` | Permisos, faltas justificadas y vacaciones, con su tipo | Sí. El subtipo puede ser "Salud" o "Maternidad" |

Son datos laborales de personas identificadas. Algunos subtipos de ausencia
pueden revelar información de salud. La Ley Orgánica de Protección de Datos
Personales del Ecuador aplica a su tratamiento; qué exige en concreto para este
caso es una pregunta para Legal, no algo que se resuelva en el código.

## Lo que ya está implementado

- Ninguna de estas tablas es accesible por la API REST de Supabase: RLS activo,
  sin políticas para los roles públicos y sin permisos.
- La aplicación conecta con un rol propio que solo puede leer la bitácora, las
  marcaciones y los ajustes, nunca modificarlos.
- La bitácora, las marcaciones y el registro de cierres de mes son de solo
  inserción, también para el dueño de las tablas. Desactivar esa protección es
  un cambio de esquema, que Postgres registra en su log.
- En el panel, la comparación entre lo marcado y lo vigente y la bitácora solo
  las ve quien tiene `ver_auditoria = true` en secrets, y siempre dentro de sus
  áreas.
- Los reportes que se generan desde `auditoria/` van a `auditoria/salida/`,
  fuera de git.

## Lo que falta decidir

**Cuánto tiempo se conserva.** Hoy, indefinidamente. Una opción razonable es
alinearlo con el plazo de conservación de los registros de nómina y de las
posibles reclamaciones laborales, y anonimizar después: reemplazar el nombre
por el id de `empleados` y borrar el texto libre de los motivos. Como las tablas
son de solo inserción, una purga programada necesitaría una función de
mantenimiento propia, con su propio registro.

**Quién accede.** Hoy: quien tenga `ver_auditoria` en el panel, y quien tenga
la contraseña de `postgres` o acceso al proyecto de Supabase, que lo ve todo.
Conviene acotar esto último: el proyecto de Supabase es compartido con otra
aplicación y sus administradores ven también estas tablas.

**Qué se le responde a un colaborador que pide sus datos.** Las consultas para
reunir todo lo de una persona existen: sus turnos, sus marcaciones, los ajustes
sobre sus turnos y los cambios de la bitácora. Falta el procedimiento: quién
recibe la solicitud, en qué plazo y en qué formato se entrega.

**Las ausencias por salud o maternidad.** Si Legal las considera datos
sensibles, puede convenir guardar solo "Permiso" en el turno y dejar el subtipo
en `ausencias` con acceso más restringido.

**Los respaldos manuales.** El procedimiento de despliegue recomienda un
`pg_dump` antes de migrar. Ese archivo contiene todo lo anterior y queda en el
equipo de quien lo genera. Hay que definir dónde se guarda y cuándo se borra.
