-- =============================================================================
-- Functional Namespace — Hourly KPI View
-- Tutorial: Ask Your Factory Floor Anything — Section III → IV Bridge
-- =============================================================================
--
-- The hourly_line_kpis materialized view (defined in schema.sql) IS the
-- functional namespace: it transforms raw edge data into KPI-level summaries
-- without leaving the database. The view layer bridges raw ingestion (Section
-- III) and business-level AI/twin queries (Section IV).
--
-- Step 1: Refresh the view to incorporate the latest seed data.
-- Step 2: Query the KPI output.
--
-- In production, replace the materialized view with a TimescaleDB continuous
-- aggregate for automatic, incremental refresh on every new chunk:
--
--   CREATE MATERIALIZED VIEW hourly_line_kpis
--   WITH (timescaledb.continuous) AS
--   < same SELECT body as in schema.sql >
--   WITH NO DATA;
--
--   SELECT add_continuous_aggregate_policy('hourly_line_kpis',
--     start_offset => INTERVAL '3 hours',
--     end_offset   => INTERVAL '1 hour',
--     schedule_interval => INTERVAL '1 hour'
--   );
-- =============================================================================

-- Refresh the view (populate from current seed data)
REFRESH MATERIALIZED VIEW hourly_line_kpis;

-- Query: hourly KPIs per line, most recent first
SELECT
    hour,
    site,
    area,
    line,
    avg_temp,
    avg_pressure,
    avg_vibration,
    alarm_count,
    state_change_count
FROM hourly_line_kpis
ORDER BY hour DESC, site, area, line
LIMIT 20;
