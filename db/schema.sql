-- =============================================================================
-- UNS Governance Demo — Schema
-- Tutorial: Ask Your Factory Floor Anything
-- =============================================================================
--
-- Tables (dependency order):
--   1. equipment_context   ISA-95 hierarchy with generated uns_path column
--   2. event_taxonomy      Reference table for validated event types
--   3. sensor_readings     Hypertable for time-series sensor data
--   4. equipment_events    Hypertable for state-change and alarm events
--   5. work_orders         Business context (enables cross-domain joins)
--   6. dead_letter         Rejected payloads that failed governance checks
--   7. hourly_line_kpis    Materialized view — the "functional namespace"
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. equipment_context
--    Encodes the ISA-95 hierarchy. Every sensor and device in the UNS must
--    have a matching row here before it can publish governed data.
-- -----------------------------------------------------------------------------
CREATE TABLE equipment_context (
    enterprise      TEXT NOT NULL,
    site            TEXT NOT NULL,
    area            TEXT NOT NULL,
    line            TEXT NOT NULL,
    cell            TEXT NOT NULL,

    -- Generated column: the canonical UNS topic path for this device.
    -- Stored so it can be indexed and used directly in JOINs.
    -- sensor_readings.device_id stores this exact string.
    uns_path        TEXT GENERATED ALWAYS AS (
                        enterprise || '/' || site || '/' || area || '/' || line || '/' || cell
                    ) STORED,

    -- Tracks which schema version this device's topics use.
    -- Ingestion strips version prefixes (e.g. v1/acme_mfg/...) before lookup.
    schema_version  TEXT NOT NULL DEFAULT 'v1',

    description     TEXT,

    PRIMARY KEY (enterprise, site, area, line, cell)
);

-- Unique index on uns_path enables O(1) JOIN from sensor_readings.device_id
CREATE UNIQUE INDEX idx_equipment_context_uns_path ON equipment_context (uns_path);

-- Prefix-scan index: lets governance-audit.sql query "all detroit devices" efficiently
CREATE INDEX idx_equipment_context_site ON equipment_context (site);


-- -----------------------------------------------------------------------------
-- 2. event_taxonomy
--    Reference table for validated event types. Foreign key in equipment_events
--    ensures only known event types can be stored — any new site commissioning
--    non-standard labels will be caught at ingestion time.
-- -----------------------------------------------------------------------------
CREATE TABLE event_taxonomy (
    event_type  TEXT PRIMARY KEY,
    description TEXT
);


-- -----------------------------------------------------------------------------
-- 3. sensor_readings  (hypertable)
--    Narrow schema: one row per (timestamp, device, metric).
--    device_id stores the full uns_path string, e.g.:
--      acme_mfg/detroit/assembly/line_01/plc_temp_01
-- -----------------------------------------------------------------------------
CREATE TABLE sensor_readings (
    ts          TIMESTAMPTZ     NOT NULL,
    device_id   TEXT            NOT NULL,
    metric_type TEXT            NOT NULL,
    value       DOUBLE PRECISION NOT NULL
);

SELECT create_hypertable('sensor_readings', 'ts', chunk_time_interval => INTERVAL '1 day');

-- Deduplication: same pattern as the MQTT batching tutorial.
-- ON CONFLICT (ts, device_id, metric_type) DO NOTHING at insert time.
CREATE UNIQUE INDEX idx_sensor_readings_dedup
    ON sensor_readings (ts, device_id, metric_type);


-- -----------------------------------------------------------------------------
-- 4. equipment_events  (hypertable)
--    State-change and alarm events. Foreign key to event_taxonomy enforces
--    the standardised vocabulary — Edge Case 4 in the tutorial.
-- -----------------------------------------------------------------------------
CREATE TABLE equipment_events (
    ts          TIMESTAMPTZ NOT NULL,
    device_id   TEXT        NOT NULL,
    event_type  TEXT        NOT NULL REFERENCES event_taxonomy (event_type),
    description TEXT
);

SELECT create_hypertable('equipment_events', 'ts', chunk_time_interval => INTERVAL '1 day');

CREATE UNIQUE INDEX idx_equipment_events_dedup
    ON equipment_events (ts, device_id, event_type);


-- -----------------------------------------------------------------------------
-- 5. work_orders
--    Business context. Enables cross-domain joins that answer questions like
--    "What happened on Line 01 during work order WO-2026-003?" — the kind of
--    query an AI agent would generate from plain English.
-- -----------------------------------------------------------------------------
CREATE TABLE work_orders (
    order_id        TEXT PRIMARY KEY,
    product         TEXT        NOT NULL,
    target_quantity INTEGER,
    status          TEXT        NOT NULL CHECK (status IN ('planned', 'in_progress', 'complete')),
    site            TEXT        NOT NULL,
    assigned_line   TEXT        NOT NULL,
    scheduled_start TIMESTAMPTZ NOT NULL,
    scheduled_end   TIMESTAMPTZ NOT NULL
);


-- -----------------------------------------------------------------------------
-- 6. dead_letter
--    Payloads rejected by governance checks at ingestion time.
--    Keeps bad data visible for investigation rather than silently dropping it.
--    Queried by governance-audit.sql.
-- -----------------------------------------------------------------------------
CREATE TABLE dead_letter (
    id          BIGSERIAL   PRIMARY KEY,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    topic       TEXT,
    payload     JSONB,
    reason      TEXT        NOT NULL
);

CREATE INDEX idx_dead_letter_received_at ON dead_letter (received_at DESC);
CREATE INDEX idx_dead_letter_reason      ON dead_letter (reason);


-- -----------------------------------------------------------------------------
-- 7. hourly_line_kpis  (materialized view — the "functional namespace")
--    Transforms raw edge data into KPI-level summaries without leaving the DB.
--    This view IS the functional namespace layer: it bridges raw ingestion
--    (sensor_readings, equipment_events) and business-level queries (Section IV).
--
--    Refresh manually:  REFRESH MATERIALIZED VIEW hourly_line_kpis;
--    See queries/functional-namespace.sql for the refresh + sample query.
--
--    Production upgrade path: replace with a TimescaleDB continuous aggregate
--    for automatic, incremental refresh:
--      CREATE MATERIALIZED VIEW hourly_line_kpis
--      WITH (timescaledb.continuous) AS ...
-- -----------------------------------------------------------------------------
CREATE MATERIALIZED VIEW hourly_line_kpis AS
WITH hourly_sensors AS (
    SELECT
        date_trunc('hour', sr.ts)                                                           AS hour,
        ec.site,
        ec.area,
        ec.line,
        round(avg(sr.value) FILTER (WHERE sr.metric_type = 'temperature')::numeric, 2)     AS avg_temp,
        round(avg(sr.value) FILTER (WHERE sr.metric_type = 'pressure')::numeric, 2)        AS avg_pressure,
        round(avg(sr.value) FILTER (WHERE sr.metric_type = 'vibration')::numeric, 2)       AS avg_vibration
    FROM sensor_readings sr
    JOIN equipment_context ec ON ec.uns_path = sr.device_id
    GROUP BY 1, 2, 3, 4
),
hourly_events AS (
    SELECT
        date_trunc('hour', ee.ts)                                           AS hour,
        ec.site,
        ec.area,
        ec.line,
        count(*) FILTER (WHERE ee.event_type = 'alarm')                     AS alarm_count,
        count(*) FILTER (WHERE ee.event_type = 'state_change')              AS state_change_count
    FROM equipment_events ee
    JOIN equipment_context ec ON ec.uns_path = ee.device_id
    GROUP BY 1, 2, 3, 4
)
SELECT
    hs.hour,
    hs.site,
    hs.area,
    hs.line,
    hs.avg_temp,
    hs.avg_pressure,
    hs.avg_vibration,
    coalesce(he.alarm_count, 0)         AS alarm_count,
    coalesce(he.state_change_count, 0)  AS state_change_count
FROM hourly_sensors hs
LEFT JOIN hourly_events he USING (hour, site, area, line)
WITH NO DATA;

CREATE UNIQUE INDEX idx_hourly_line_kpis ON hourly_line_kpis (hour, site, area, line);
