-- FASE B. Aplicar solo después de desplegar la versión de la aplicación que
-- usa ajustes y envía el motivo. Con la versión anterior en producción, esta
-- migración hace fallar toda corrección y todo registro manual.
--
-- Enciende las dos reglas que la fase A dejó preparadas pero apagadas, y quita
-- el índice único viejo que la versión anterior necesitaba.


update reglas_negocio
set valor = 1, actualizado_en = now()
where clave in ('exigir_ajuste_para_corregir', 'exigir_motivo_admin')
  and valor <> 1;

-- El índice viejo cubre también los turnos anulados, e impide rehacer un turno
-- anulado con la misma hora de entrada. Su reemplazo parcial existe desde V002.
drop index if exists ux_turnos_nombre_entrada;
