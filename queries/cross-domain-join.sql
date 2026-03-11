-- =============================================================================
-- Cross-Domain Join: Sensor + Events + Business Context
-- Tutorial: Ask Your Factory Floor Anything — Section IV
-- =============================================================================
--
-- "What happened on Line 01 during work order WO-2026-003?"
--
-- This is the query an AI agent would generate from that plain-English
-- question. It only works reliably because the schema is governed:
--   • Consistent device_id = uns_path (no ad-hoc naming)
--   • Validated event_type (only known types in equipment_events)
--   • Enforced TIMESTAMPTZ (ts comparisons are unambiguous)
--   • ISA-95 hierarchy in equipment_context (site + line join works cleanly)
--
-- Without governance, each of those assumptions can silently break:
--   • device_id mismatches → empty result
--   • non-standard event_type labels → alarm_count undercounts
--   • naive timestamps → wrong time window matched
--   • missing hierarchy → JOIN returns NULL site/area
--
-- Change the order_id to explore other work orders from seed.sql.
-- =============================================================================

WITH wo AS (
    SELECT order_id, product, status, site, assigned_line,
           scheduled_start, scheduled_end
    FROM work_orders
    WHERE order_id = 'WO-2026-003'
),
line_devices AS (
    SELECT ec.uns_path, ec.area
    FROM equipment_context ec
    JOIN wo ON ec.site = wo.site AND ec.line = wo.assigned_line
),
sensor_agg AS (
    SELECT
        sr.device_id,
        sr.metric_type,
        round(avg(sr.value)::numeric, 2) AS avg_value,
        count(*)                         AS reading_count
    FROM sensor_readings sr
    JOIN line_devices ld ON sr.device_id = ld.uns_path
    WHERE sr.ts BETWEEN (SELECT scheduled_start FROM wo)
                    AND (SELECT scheduled_end   FROM wo)
    GROUP BY sr.device_id, sr.metric_type
),
event_agg AS (
    SELECT
        ee.device_id,
        count(*) FILTER (WHERE ee.event_type = 'alarm')            AS alarm_count,
        count(*) FILTER (WHERE ee.event_type = 'maintenance_hold') AS maintenance_hold_count
    FROM equipment_events ee
    JOIN line_devices ld ON ee.device_id = ld.uns_path
    WHERE ee.ts BETWEEN (SELECT scheduled_start FROM wo)
                    AND (SELECT scheduled_end   FROM wo)
    GROUP BY ee.device_id
)
SELECT
    wo.order_id,
    wo.product,
    wo.status,
    wo.scheduled_start,
    wo.scheduled_end,
    ld.uns_path,
    ld.area,
    sa.metric_type,
    sa.avg_value,
    sa.reading_count,
    coalesce(ea.alarm_count,            0) AS alarm_count,
    coalesce(ea.maintenance_hold_count, 0) AS maintenance_hold_count
FROM wo
CROSS JOIN line_devices ld
JOIN sensor_agg sa
    ON  sa.device_id = ld.uns_path
LEFT JOIN event_agg ea
    ON  ea.device_id = ld.uns_path
ORDER BY
    ld.uns_path, sa.metric_type;
