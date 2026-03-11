-- =============================================================================
-- Governance Audit Queries
-- Tutorial: Ask Your Factory Floor Anything
-- =============================================================================
--
-- Five cross-site consistency checks that surface governance violations.
-- Run these periodically (or on-demand) to verify the governed UNS is healthy.
-- The dead_letter table is the primary triage tool: start there, then use
-- these queries to understand the broader pattern.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. Orphaned devices
--    Devices publishing to sensor_readings with no matching equipment_context
--    entry. These bypassed the governed ingestion path entirely.
-- ---------------------------------------------------------------------------
SELECT DISTINCT
    sr.device_id,
    count(sr.ts)  AS reading_count,
    min(sr.ts)    AS first_seen,
    max(sr.ts)    AS last_seen
FROM sensor_readings sr
LEFT JOIN equipment_context ec ON ec.uns_path = sr.device_id
WHERE ec.uns_path IS NULL
GROUP BY sr.device_id
ORDER BY reading_count DESC;


-- ---------------------------------------------------------------------------
-- 2. Non-taxonomy event types
--    Event types in equipment_events not present in event_taxonomy.
--    Should be empty if the FK constraint is active. If rows appear here,
--    the constraint was bypassed (e.g. direct SQL insert, migration).
-- ---------------------------------------------------------------------------
SELECT DISTINCT
    ee.event_type,
    count(*)  AS event_count
FROM equipment_events ee
LEFT JOIN event_taxonomy et ON et.event_type = ee.event_type
WHERE et.event_type IS NULL
GROUP BY ee.event_type
ORDER BY event_count DESC;


-- ---------------------------------------------------------------------------
-- 3. Future timestamps
--    Readings with ts more than 5 minutes in the future (likely clock skew
--    on the publishing device or a misconfigured timezone).
-- ---------------------------------------------------------------------------
SELECT
    device_id,
    ts,
    metric_type,
    value,
    ts - now()  AS drift
FROM sensor_readings
WHERE ts > now() + INTERVAL '5 minutes'
ORDER BY drift DESC
LIMIT 20;


-- ---------------------------------------------------------------------------
-- 4. Silent device gaps
--    Devices in equipment_context that have not published in the last 24 hours.
--    Useful for detecting decommissioned, offline, or misconfigured devices.
--    Adjust the interval to match your expected publish frequency.
-- ---------------------------------------------------------------------------
SELECT
    ec.uns_path,
    ec.site,
    ec.area,
    ec.line,
    max(sr.ts)          AS last_reading,
    now() - max(sr.ts)  AS silence_duration
FROM equipment_context ec
LEFT JOIN sensor_readings sr ON sr.device_id = ec.uns_path
GROUP BY ec.uns_path, ec.site, ec.area, ec.line
HAVING max(sr.ts) < now() - INTERVAL '24 hours'
    OR max(sr.ts) IS NULL   -- never published
ORDER BY silence_duration DESC NULLS FIRST;


-- ---------------------------------------------------------------------------
-- 5. Dead letter summary
--    Groups rejected payloads by reason to identify systemic issues.
--    A spike in one reason category (e.g. "missing timezone") pinpoints
--    the site or device class causing the problem.
-- ---------------------------------------------------------------------------
SELECT
    reason,
    count(*)                AS rejection_count,
    min(received_at)        AS first_seen,
    max(received_at)        AS last_seen,
    count(DISTINCT topic)   AS distinct_devices
FROM dead_letter
GROUP BY reason
ORDER BY rejection_count DESC;
