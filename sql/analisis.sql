-- criterios tiene una fila con el corte del reporte y los umbrales operativos.
CREATE TEMP TABLE viajes_auditados AS
WITH eventos_corte AS (
    SELECT r.* FROM recepciones r, criterios c WHERE r.registrado_en <= c.corte
), firmas AS (
    -- Un reenvío idéntico cuenta una vez. Dos firmas distintas requieren revisión.
    SELECT viaje_id, destino, llegada, inicio_descarga, fin_descarga, peso_tmh,
           MIN(registrado_en) AS registrado_en, COUNT(*) AS repeticiones
    FROM eventos_corte
    GROUP BY viaje_id, destino, llegada, inicio_descarga, fin_descarga, peso_tmh
), por_viaje AS (
    SELECT viaje_id, COUNT(*) AS firmas, SUM(repeticiones) AS eventos
    FROM firmas GROUP BY viaje_id
), cruces AS (
    SELECT v.*, DATE(v.salida, '-7 hours') AS dia_operativo,
           CASE WHEN TIME(v.salida) >= '07:00:00' AND TIME(v.salida) < '19:00:00'
                THEN 'Día' ELSE 'Noche' END AS turno,
           COALESCE(n.eventos, 0) AS eventos_recepcion,
           COALESCE(n.firmas, 0) AS firmas_recepcion,
           f.destino AS destino_recibido, f.llegada, f.inicio_descarga, f.fin_descarga,
           f.registrado_en, f.peso_tmh,
           despacho.cu_pct AS cu_despacho_pct,
           despacho.disponible_en AS fecha_ley_despacho,
           ref.cu_pct AS cu_referencia_pct,
           ref.disponible_en AS fecha_ley_referencia,
           c.corte_alta, c.corte_acopio, c.peso_tolerancia_pct
    FROM viajes v CROSS JOIN criterios c
    LEFT JOIN por_viaje n ON n.viaje_id = v.viaje_id
    LEFT JOIN firmas f ON f.viaje_id = v.viaje_id AND n.firmas = 1
    LEFT JOIN leyes despacho ON despacho.bloque = v.bloque
        AND despacho.disponible_en = (
            SELECT MAX(g.disponible_en) FROM leyes g
            WHERE g.bloque = v.bloque AND g.aprobado = 1 AND g.disponible_en <= v.salida
        )
    LEFT JOIN leyes ref ON ref.bloque = v.bloque
        AND ref.disponible_en = (
            SELECT MAX(g.disponible_en) FROM leyes g
            WHERE g.bloque = v.bloque AND g.aprobado = 1 AND g.disponible_en <= c.corte
        )
    WHERE v.salida <= c.corte
), qa AS (
    SELECT *,
        CASE
          WHEN firmas_recepcion = 0 THEN 'sin_recepcion_al_corte'
          WHEN firmas_recepcion > 1 THEN 'recepcion_conflictiva'
          WHEN NOT (salida < llegada AND llegada <= inicio_descarga
                    AND inicio_descarga < fin_descarga AND fin_descarga <= registrado_en)
               THEN 'secuencia_invalida'
          WHEN peso_tmh <= 0 OR ABS(peso_tmh - carga_tmh) * 100.0 / carga_tmh > peso_tolerancia_pct
               THEN 'peso_en_revision'
          ELSE 'validado'
        END AS estado_qaqc
    FROM cruces
), reglas AS (
    SELECT *,
       CASE WHEN cu_despacho_pct >= corte_alta THEN 'CHANCADO'
            WHEN cu_despacho_pct >= corte_acopio THEN 'ACOPIO'
            WHEN cu_despacho_pct IS NOT NULL THEN 'BOTADERO' END AS ruta_regla_despacho,
       CASE WHEN cu_referencia_pct >= corte_alta THEN 'CHANCADO'
            WHEN cu_referencia_pct >= corte_acopio THEN 'ACOPIO'
            WHEN cu_referencia_pct IS NOT NULL THEN 'BOTADERO' END AS ruta_regla_referencia
    FROM qa
)
SELECT *,
    CASE WHEN estado_qaqc = 'validado' THEN (STRFTIME('%s', inicio_descarga) - STRFTIME('%s', llegada)) / 60.0 END AS espera_min,
    CASE WHEN estado_qaqc = 'validado' THEN (STRFTIME('%s', llegada) - STRFTIME('%s', salida)) / 60.0 END AS traslado_min,
    CASE WHEN estado_qaqc = 'validado' THEN (destino_recibido <> destino_plan) END AS cambio_destino,
    CASE WHEN estado_qaqc <> 'validado' THEN 'sin_validar'
         WHEN ruta_regla_referencia IS NULL THEN 'sin_ley_referencia'
         WHEN ruta_regla_referencia = 'CHANCADO' AND destino_recibido = 'BOTADERO' THEN 'mineral_a_botadero'
         WHEN ruta_regla_referencia <> 'CHANCADO' AND destino_recibido = 'CHANCADO' THEN 'bajo_corte_a_chancado'
         WHEN ruta_regla_referencia = 'ACOPIO' AND destino_recibido = 'BOTADERO' THEN 'baja_ley_a_botadero'
         WHEN ruta_regla_referencia <> destino_recibido THEN 'otra_diferencia'
         ELSE 'coincide'
    END AS alerta_ruta,
    CASE WHEN estado_qaqc = 'validado' AND ruta_regla_referencia IS NOT NULL
              AND ruta_regla_referencia <> destino_recibido
         THEN CASE WHEN ruta_regla_despacho IS NULL THEN 'sin_ley_al_despacho'
                   WHEN ruta_regla_despacho = destino_recibido THEN 'surge_con_referencia_posterior'
                   ELSE 'visible_con_ley_al_despacho' END
    END AS visibilidad_alerta
FROM reglas;

CREATE TEMP VIEW kpi_turno AS
SELECT dia_operativo, turno, COUNT(*) AS viajes,
       COUNT(*) FILTER (WHERE estado_qaqc = 'validado') AS viajes_validados,
       COUNT(*) FILTER (WHERE estado_qaqc = 'sin_recepcion_al_corte') AS sin_recepcion,
       COUNT(*) FILTER (WHERE estado_qaqc NOT IN ('validado','sin_recepcion_al_corte')) AS otros_qaqc,
       SUM(carga_tmh) AS tmh_cargadas,
       COALESCE(SUM(peso_tmh) FILTER (WHERE estado_qaqc = 'validado'),0) AS tmh_validadas,
       COUNT(*) FILTER (WHERE cambio_destino = 1) AS cambios_respecto_plan,
       COUNT(*) FILTER (WHERE estado_qaqc = 'validado' AND cu_referencia_pct IS NOT NULL) AS viajes_evaluables_ruta
FROM viajes_auditados
GROUP BY dia_operativo, turno;

CREATE TEMP VIEW rutas_zona AS
SELECT zona, turno,
       COUNT(*) AS viajes_evaluables,
       SUM(peso_tmh) AS tmh_evaluables,
       COALESCE(SUM(peso_tmh) FILTER (WHERE alerta_ruta = 'mineral_a_botadero'), 0) AS mineral_a_botadero_tmh,
       COALESCE(SUM(peso_tmh) FILTER (WHERE alerta_ruta = 'bajo_corte_a_chancado'), 0) AS bajo_corte_a_chancado_tmh,
       COALESCE(SUM(peso_tmh) FILTER (WHERE alerta_ruta = 'baja_ley_a_botadero'), 0) AS baja_ley_a_botadero_tmh,
       COUNT(*) FILTER (WHERE alerta_ruta <> 'coincide') AS viajes_alerta,
       100.0 * COALESCE(SUM(peso_tmh) FILTER (WHERE alerta_ruta <> 'coincide'),0) / SUM(peso_tmh) AS pct_tmh_alerta
FROM viajes_auditados
WHERE estado_qaqc = 'validado' AND cu_referencia_pct IS NOT NULL
GROUP BY zona, turno;

CREATE TEMP VIEW qa_orfanas AS
SELECT r.* FROM recepciones r CROSS JOIN criterios c
WHERE r.registrado_en <= c.corte
AND NOT EXISTS (SELECT 1 FROM viajes v WHERE v.viaje_id = r.viaje_id);
