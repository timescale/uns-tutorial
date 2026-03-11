-- =============================================================================
-- UNS Metadata Lookup
-- Tutorial: Ask Your Factory Floor Anything — Section II
-- =============================================================================
--
-- Joins sensor_readings with equipment_context to return the full UNS path
-- plus ISA-95 hierarchy metadata alongside the measured values.
--
-- This is the canonical read pattern for governed UNS data: the device_id
-- in sensor_readings stores the full uns_path string, so the JOIN is a
-- direct equality on the generated column — no string construction needed.
--
-- Adjust the WHERE clause for your site or time range of interest.
-- =============================================================================

SELECT
    ec.uns_path,            -- e.g. acme_mfg/detroit/assembly/line_01/plc_temp_01
    ec.enterprise,
    ec.site,
    ec.area,
    ec.line,
    ec.cell,
    sr.metric_type,
    sr.ts,
    sr.value
FROM sensor_readings sr
JOIN equipment_context ec ON ec.uns_path = sr.device_id
WHERE sr.ts BETWEEN '2026-02-18 00:00:00+00' AND '2026-02-18 23:59:59+00'
  AND ec.site = 'detroit'
ORDER BY sr.ts DESC;
