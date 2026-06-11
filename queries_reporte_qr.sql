-- ============================================================
-- QUERIES REPORTE QR BBVA - Cuota 25 Mayo 2026
-- Base: 300 solicitudes del Excel "CLIENTS SIN CAMPAÑA ESTE MES"
-- ============================================================

-- NOTA: Reemplazar la lista de IDs con los 300 del Excel.
-- Aqui se usa un placeholder (EXCEL_IDS) para legibilidad.
-- En ejecucion real, expandir con los 300 IDs separados por coma.


-- ============================================================
-- 1. % DE CLIENTES QUE PAGARON EL DIA 25
-- ============================================================

-- Total de clientes que pagaron el 25 de mayo
SELECT COUNT(DISTINCT c.solicitud_id) AS clientes_que_pagaron
FROM pago p
JOIN cronograma c ON c.id = p.cronograma_id
WHERE c.solicitud_id IN (EXCEL_IDS)
  AND DATE(p.fecha_pago) = '2026-05-25'
  AND p.deleted_at IS NULL;

-- Desglose por pasarela de pago
SELECT p.pasarela_pago, COUNT(*) AS cantidad
FROM pago p
JOIN cronograma c ON c.id = p.cronograma_id
WHERE c.solicitud_id IN (EXCEL_IDS)
  AND DATE(p.fecha_pago) = '2026-05-25'
  AND p.deleted_at IS NULL
GROUP BY p.pasarela_pago
ORDER BY cantidad DESC;


-- ============================================================
-- 2. % DE CLIENTES QUE USARON QR
-- ============================================================

-- Clientes que generaron al menos un QR
SELECT COUNT(DISTINCT q.solicitud_id) AS clientes_con_qr
FROM bbva_qr_payments q
WHERE q.solicitud_id IN (EXCEL_IDS);

-- Clientes que pagaron via QR (status = 'paid')
SELECT COUNT(DISTINCT q.solicitud_id) AS clientes_pagaron_qr
FROM bbva_qr_payments q
WHERE q.solicitud_id IN (EXCEL_IDS)
  AND q.status = 'paid';


-- ============================================================
-- 3. CANTIDAD DE QRs GENERADOS POR CLIENTE
-- ============================================================

SELECT q.solicitud_id,
       COUNT(*) AS qrs_generados
FROM bbva_qr_payments q
WHERE q.solicitud_id IN (EXCEL_IDS)
GROUP BY q.solicitud_id
ORDER BY qrs_generados DESC;


-- ============================================================
-- 4. STATUS DE QRs (tasa de error / no completados)
-- ============================================================

-- Resumen por status
SELECT q.status,
       COUNT(*) AS cantidad,
       ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM bbva_qr_payments WHERE solicitud_id IN (EXCEL_IDS)), 1) AS porcentaje
FROM bbva_qr_payments q
WHERE q.solicitud_id IN (EXCEL_IDS)
GROUP BY q.status;


-- ============================================================
-- 5. DETALLE COMPLETO DE QRs GENERADOS
-- ============================================================

SELECT q.solicitud_id,
       q.status,
       q.amount AS monto,
       q.created_at AS fecha_generacion,
       q.paid_at AS fecha_pago
FROM bbva_qr_payments q
WHERE q.solicitud_id IN (EXCEL_IDS)
ORDER BY q.created_at;
