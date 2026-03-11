-- =============================================================================
-- UNS Governance Demo — Seed Data
-- Tutorial: Ask Your Factory Floor Anything
-- =============================================================================
--
-- Inserts a realistic ISA-95 equipment hierarchy across two sites (detroit,
-- munich) and a clean baseline of sensor readings, events, and work orders.
-- Each edge-case script contrasts its bad data against this clean baseline.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. equipment_context — ISA-95 hierarchy (24 rows)
--    acme_mfg / {detroit,munich} / {assembly,packaging} / {line_01,line_02}
--                                / {plc_temp_01, plc_pressure_01, plc_vibration_01}
-- -----------------------------------------------------------------------------
INSERT INTO equipment_context (enterprise, site, area, line, cell, description) VALUES

-- Detroit / Assembly
('acme_mfg', 'detroit', 'assembly', 'line_01', 'plc_temp_01',      'Temperature sensor — Assembly Line 1, Detroit'),
('acme_mfg', 'detroit', 'assembly', 'line_01', 'plc_pressure_01',  'Pressure sensor — Assembly Line 1, Detroit'),
('acme_mfg', 'detroit', 'assembly', 'line_01', 'plc_vibration_01', 'Vibration sensor — Assembly Line 1, Detroit'),
('acme_mfg', 'detroit', 'assembly', 'line_02', 'plc_temp_01',      'Temperature sensor — Assembly Line 2, Detroit'),
('acme_mfg', 'detroit', 'assembly', 'line_02', 'plc_pressure_01',  'Pressure sensor — Assembly Line 2, Detroit'),
('acme_mfg', 'detroit', 'assembly', 'line_02', 'plc_vibration_01', 'Vibration sensor — Assembly Line 2, Detroit'),

-- Detroit / Packaging
('acme_mfg', 'detroit', 'packaging', 'line_01', 'plc_temp_01',      'Temperature sensor — Packaging Line 1, Detroit'),
('acme_mfg', 'detroit', 'packaging', 'line_01', 'plc_pressure_01',  'Pressure sensor — Packaging Line 1, Detroit'),
('acme_mfg', 'detroit', 'packaging', 'line_01', 'plc_vibration_01', 'Vibration sensor — Packaging Line 1, Detroit'),
('acme_mfg', 'detroit', 'packaging', 'line_02', 'plc_temp_01',      'Temperature sensor — Packaging Line 2, Detroit'),
('acme_mfg', 'detroit', 'packaging', 'line_02', 'plc_pressure_01',  'Pressure sensor — Packaging Line 2, Detroit'),
('acme_mfg', 'detroit', 'packaging', 'line_02', 'plc_vibration_01', 'Vibration sensor — Packaging Line 2, Detroit'),

-- Munich / Assembly
('acme_mfg', 'munich', 'assembly', 'line_01', 'plc_temp_01',      'Temperature sensor — Assembly Line 1, Munich'),
('acme_mfg', 'munich', 'assembly', 'line_01', 'plc_pressure_01',  'Pressure sensor — Assembly Line 1, Munich'),
('acme_mfg', 'munich', 'assembly', 'line_01', 'plc_vibration_01', 'Vibration sensor — Assembly Line 1, Munich'),
('acme_mfg', 'munich', 'assembly', 'line_02', 'plc_temp_01',      'Temperature sensor — Assembly Line 2, Munich'),
('acme_mfg', 'munich', 'assembly', 'line_02', 'plc_pressure_01',  'Pressure sensor — Assembly Line 2, Munich'),
('acme_mfg', 'munich', 'assembly', 'line_02', 'plc_vibration_01', 'Vibration sensor — Assembly Line 2, Munich'),

-- Munich / Packaging
('acme_mfg', 'munich', 'packaging', 'line_01', 'plc_temp_01',      'Temperature sensor — Packaging Line 1, Munich'),
('acme_mfg', 'munich', 'packaging', 'line_01', 'plc_pressure_01',  'Pressure sensor — Packaging Line 1, Munich'),
('acme_mfg', 'munich', 'packaging', 'line_01', 'plc_vibration_01', 'Vibration sensor — Packaging Line 1, Munich'),
('acme_mfg', 'munich', 'packaging', 'line_02', 'plc_temp_01',      'Temperature sensor — Packaging Line 2, Munich'),
('acme_mfg', 'munich', 'packaging', 'line_02', 'plc_pressure_01',  'Pressure sensor — Packaging Line 2, Munich'),
('acme_mfg', 'munich', 'packaging', 'line_02', 'plc_vibration_01', 'Vibration sensor — Packaging Line 2, Munich');


-- -----------------------------------------------------------------------------
-- 2. event_taxonomy — validated event vocabulary
-- -----------------------------------------------------------------------------
INSERT INTO event_taxonomy (event_type, description) VALUES
('state_change',      'Equipment state changed (e.g. running → stopped)'),
('alarm',             'Alarm condition triggered'),
('quality_hold',      'Production hold due to quality issue'),
('maintenance_hold',  'Production hold for scheduled or unscheduled maintenance'),
('production_start',  'Work order or production run started'),
('production_stop',   'Work order or production run stopped');


-- -----------------------------------------------------------------------------
-- 3. sensor_readings — clean baseline (~60 rows, all with TIMESTAMPTZ)
--    Readings span 2026-02-18, covering the work order windows below.
--    WO-2026-003 window (detroit/line_01, 14:00–22:00) has dense coverage
--    so the cross-domain-join.sql query returns meaningful results.
-- -----------------------------------------------------------------------------
INSERT INTO sensor_readings (ts, device_id, metric_type, value) VALUES

-- Detroit / Assembly / Line 01 — morning (WO-2026-001 window)
('2026-02-18 06:15:00+00', 'acme_mfg/detroit/assembly/line_01/plc_temp_01',      'temperature', 71.4),
('2026-02-18 07:00:00+00', 'acme_mfg/detroit/assembly/line_01/plc_temp_01',      'temperature', 72.1),
('2026-02-18 08:00:00+00', 'acme_mfg/detroit/assembly/line_01/plc_temp_01',      'temperature', 73.0),
('2026-02-18 09:00:00+00', 'acme_mfg/detroit/assembly/line_01/plc_temp_01',      'temperature', 72.8),
('2026-02-18 06:15:00+00', 'acme_mfg/detroit/assembly/line_01/plc_pressure_01',  'pressure',    98.2),
('2026-02-18 07:00:00+00', 'acme_mfg/detroit/assembly/line_01/plc_pressure_01',  'pressure',    99.1),
('2026-02-18 08:00:00+00', 'acme_mfg/detroit/assembly/line_01/plc_pressure_01',  'pressure',    99.5),
('2026-02-18 06:15:00+00', 'acme_mfg/detroit/assembly/line_01/plc_vibration_01', 'vibration',   0.42),
('2026-02-18 07:00:00+00', 'acme_mfg/detroit/assembly/line_01/plc_vibration_01', 'vibration',   0.38),
('2026-02-18 08:00:00+00', 'acme_mfg/detroit/assembly/line_01/plc_vibration_01', 'vibration',   0.51),

-- Detroit / Assembly / Line 01 — afternoon (WO-2026-003 window: 14:00–22:00)
('2026-02-18 14:10:00+00', 'acme_mfg/detroit/assembly/line_01/plc_temp_01',      'temperature', 74.2),
('2026-02-18 15:00:00+00', 'acme_mfg/detroit/assembly/line_01/plc_temp_01',      'temperature', 75.8),
('2026-02-18 16:00:00+00', 'acme_mfg/detroit/assembly/line_01/plc_temp_01',      'temperature', 76.3),
('2026-02-18 17:00:00+00', 'acme_mfg/detroit/assembly/line_01/plc_temp_01',      'temperature', 75.1),
('2026-02-18 18:00:00+00', 'acme_mfg/detroit/assembly/line_01/plc_temp_01',      'temperature', 74.9),
('2026-02-18 14:10:00+00', 'acme_mfg/detroit/assembly/line_01/plc_pressure_01',  'pressure',    99.8),
('2026-02-18 15:00:00+00', 'acme_mfg/detroit/assembly/line_01/plc_pressure_01',  'pressure',   101.2),
('2026-02-18 16:00:00+00', 'acme_mfg/detroit/assembly/line_01/plc_pressure_01',  'pressure',   100.7),
('2026-02-18 17:00:00+00', 'acme_mfg/detroit/assembly/line_01/plc_pressure_01',  'pressure',   100.3),
('2026-02-18 14:10:00+00', 'acme_mfg/detroit/assembly/line_01/plc_vibration_01', 'vibration',   0.44),
('2026-02-18 15:00:00+00', 'acme_mfg/detroit/assembly/line_01/plc_vibration_01', 'vibration',   0.69),
('2026-02-18 16:00:00+00', 'acme_mfg/detroit/assembly/line_01/plc_vibration_01', 'vibration',   0.72),
('2026-02-18 17:00:00+00', 'acme_mfg/detroit/assembly/line_01/plc_vibration_01', 'vibration',   0.58),

-- Detroit / Assembly / Line 02 — morning (WO-2026-002 window)
('2026-02-18 06:20:00+00', 'acme_mfg/detroit/assembly/line_02/plc_temp_01',      'temperature', 70.9),
('2026-02-18 07:30:00+00', 'acme_mfg/detroit/assembly/line_02/plc_temp_01',      'temperature', 71.5),
('2026-02-18 09:30:00+00', 'acme_mfg/detroit/assembly/line_02/plc_temp_01',      'temperature', 72.3),
('2026-02-18 06:20:00+00', 'acme_mfg/detroit/assembly/line_02/plc_pressure_01',  'pressure',    97.5),
('2026-02-18 07:30:00+00', 'acme_mfg/detroit/assembly/line_02/plc_pressure_01',  'pressure',    98.0),
('2026-02-18 06:20:00+00', 'acme_mfg/detroit/assembly/line_02/plc_vibration_01', 'vibration',   0.35),
('2026-02-18 07:30:00+00', 'acme_mfg/detroit/assembly/line_02/plc_vibration_01', 'vibration',   0.40),

-- Detroit / Packaging / Line 01
('2026-02-18 10:00:00+00', 'acme_mfg/detroit/packaging/line_01/plc_temp_01',      'temperature', 68.2),
('2026-02-18 11:00:00+00', 'acme_mfg/detroit/packaging/line_01/plc_temp_01',      'temperature', 69.0),
('2026-02-18 10:00:00+00', 'acme_mfg/detroit/packaging/line_01/plc_pressure_01',  'pressure',    95.3),
('2026-02-18 10:00:00+00', 'acme_mfg/detroit/packaging/line_01/plc_vibration_01', 'vibration',   0.28),

-- Detroit / Packaging / Line 02
('2026-02-18 09:00:00+00', 'acme_mfg/detroit/packaging/line_02/plc_temp_01',      'temperature', 67.8),
('2026-02-18 10:00:00+00', 'acme_mfg/detroit/packaging/line_02/plc_temp_01',      'temperature', 68.5),
('2026-02-18 09:00:00+00', 'acme_mfg/detroit/packaging/line_02/plc_pressure_01',  'pressure',    94.9),
('2026-02-18 09:00:00+00', 'acme_mfg/detroit/packaging/line_02/plc_vibration_01', 'vibration',   0.31),

-- Munich / Assembly / Line 01 (WO-2026-004 window: 08:00–16:00)
-- Note: All Munich readings use explicit UTC offset — governed ingestion enforces this
('2026-02-18 08:05:00+00', 'acme_mfg/munich/assembly/line_01/plc_temp_01',      'temperature', 69.5),
('2026-02-18 09:00:00+00', 'acme_mfg/munich/assembly/line_01/plc_temp_01',      'temperature', 70.2),
('2026-02-18 10:00:00+00', 'acme_mfg/munich/assembly/line_01/plc_temp_01',      'temperature', 71.0),
('2026-02-18 11:00:00+00', 'acme_mfg/munich/assembly/line_01/plc_temp_01',      'temperature', 71.8),
('2026-02-18 08:05:00+00', 'acme_mfg/munich/assembly/line_01/plc_pressure_01',  'pressure',    96.8),
('2026-02-18 09:00:00+00', 'acme_mfg/munich/assembly/line_01/plc_pressure_01',  'pressure',    97.5),
('2026-02-18 10:00:00+00', 'acme_mfg/munich/assembly/line_01/plc_pressure_01',  'pressure',    98.1),
('2026-02-18 08:05:00+00', 'acme_mfg/munich/assembly/line_01/plc_vibration_01', 'vibration',   0.33),
('2026-02-18 09:00:00+00', 'acme_mfg/munich/assembly/line_01/plc_vibration_01', 'vibration',   0.36),

-- Munich / Assembly / Line 02
('2026-02-18 08:30:00+00', 'acme_mfg/munich/assembly/line_02/plc_temp_01',      'temperature', 70.1),
('2026-02-18 10:30:00+00', 'acme_mfg/munich/assembly/line_02/plc_temp_01',      'temperature', 71.4),
('2026-02-18 08:30:00+00', 'acme_mfg/munich/assembly/line_02/plc_pressure_01',  'pressure',    97.2),
('2026-02-18 08:30:00+00', 'acme_mfg/munich/assembly/line_02/plc_vibration_01', 'vibration',   0.37),

-- Munich / Packaging
('2026-02-18 07:45:00+00', 'acme_mfg/munich/packaging/line_01/plc_temp_01',      'temperature', 66.3),
('2026-02-18 07:45:00+00', 'acme_mfg/munich/packaging/line_01/plc_pressure_01',  'pressure',    93.4),
('2026-02-18 07:45:00+00', 'acme_mfg/munich/packaging/line_01/plc_vibration_01', 'vibration',   0.25),
('2026-02-18 07:45:00+00', 'acme_mfg/munich/packaging/line_02/plc_temp_01',      'temperature', 65.9),
('2026-02-18 07:45:00+00', 'acme_mfg/munich/packaging/line_02/plc_pressure_01',  'pressure',    92.8),
('2026-02-18 07:45:00+00', 'acme_mfg/munich/packaging/line_02/plc_vibration_01', 'vibration',   0.22);


-- -----------------------------------------------------------------------------
-- 4. equipment_events — clean baseline (~20 events, both sites)
--    Includes events in the WO-2026-003 window so cross-domain-join.sql
--    returns non-zero alarm counts.
-- -----------------------------------------------------------------------------
INSERT INTO equipment_events (ts, device_id, event_type, description) VALUES

-- Detroit / Assembly / Line 01 — morning
('2026-02-18 06:00:00+00', 'acme_mfg/detroit/assembly/line_01/plc_temp_01', 'production_start', 'WO-2026-001 started'),
('2026-02-18 09:45:00+00', 'acme_mfg/detroit/assembly/line_01/plc_vibration_01', 'alarm', 'Vibration spike — bearing check'),
('2026-02-18 10:30:00+00', 'acme_mfg/detroit/assembly/line_01/plc_vibration_01', 'state_change', 'Alarm cleared, running'),
('2026-02-18 14:00:00+00', 'acme_mfg/detroit/assembly/line_01/plc_temp_01', 'production_stop',  'WO-2026-001 complete'),

-- Detroit / Assembly / Line 01 — afternoon (WO-2026-003 window: 14:00–22:00)
('2026-02-18 14:05:00+00', 'acme_mfg/detroit/assembly/line_01/plc_temp_01',      'production_start', 'WO-2026-003 started'),
('2026-02-18 15:30:00+00', 'acme_mfg/detroit/assembly/line_01/plc_vibration_01', 'alarm',            'Vibration above threshold'),
('2026-02-18 15:45:00+00', 'acme_mfg/detroit/assembly/line_01/plc_vibration_01', 'alarm',            'Vibration threshold sustained'),
('2026-02-18 16:10:00+00', 'acme_mfg/detroit/assembly/line_01/plc_vibration_01', 'maintenance_hold', 'Scheduled bearing inspection'),
('2026-02-18 17:00:00+00', 'acme_mfg/detroit/assembly/line_01/plc_vibration_01', 'state_change',     'Maintenance complete, resuming'),

-- Detroit / Assembly / Line 02 — morning
('2026-02-18 06:00:00+00', 'acme_mfg/detroit/assembly/line_02/plc_temp_01', 'production_start', 'WO-2026-002 started'),
('2026-02-18 08:15:00+00', 'acme_mfg/detroit/assembly/line_02/plc_pressure_01', 'alarm', 'Pressure drop detected'),
('2026-02-18 08:45:00+00', 'acme_mfg/detroit/assembly/line_02/plc_pressure_01', 'state_change', 'Pressure stabilised'),
('2026-02-18 14:00:00+00', 'acme_mfg/detroit/assembly/line_02/plc_temp_01', 'production_stop', 'WO-2026-002 complete'),

-- Detroit / Packaging
('2026-02-18 09:00:00+00', 'acme_mfg/detroit/packaging/line_01/plc_temp_01', 'production_start', 'Packaging run started'),
('2026-02-18 11:30:00+00', 'acme_mfg/detroit/packaging/line_01/plc_temp_01', 'quality_hold',     'Label verification failure'),

-- Munich / Assembly / Line 01
('2026-02-18 08:00:00+00', 'acme_mfg/munich/assembly/line_01/plc_temp_01',      'production_start', 'WO-2026-004 started'),
('2026-02-18 11:15:00+00', 'acme_mfg/munich/assembly/line_01/plc_pressure_01',  'alarm',            'Hydraulic pressure warning'),
('2026-02-18 11:40:00+00', 'acme_mfg/munich/assembly/line_01/plc_pressure_01',  'state_change',     'Pressure restored'),
('2026-02-18 14:00:00+00', 'acme_mfg/munich/assembly/line_01/plc_temp_01',      'production_stop',  'WO-2026-004 complete'),

-- Munich / Packaging
('2026-02-18 07:30:00+00', 'acme_mfg/munich/packaging/line_01/plc_temp_01', 'production_start', 'Packaging shift started'),
('2026-02-18 12:00:00+00', 'acme_mfg/munich/packaging/line_01/plc_temp_01', 'state_change',     'Lunch break — line paused');


-- -----------------------------------------------------------------------------
-- 5. work_orders — business context (7 orders)
--    WO-2026-003 (detroit/line_01, 14:00–22:00) is the anchor for
--    cross-domain-join.sql; it has sensor readings and alarm events above.
-- -----------------------------------------------------------------------------
INSERT INTO work_orders (order_id, product, target_quantity, status, site, assigned_line, scheduled_start, scheduled_end) VALUES
('WO-2026-001', 'Engine Mount',           250, 'complete',     'detroit', 'line_01', '2026-02-18 06:00:00+00', '2026-02-18 14:00:00+00'),
('WO-2026-002', 'Brake Caliper',          180, 'complete',     'detroit', 'line_02', '2026-02-18 06:00:00+00', '2026-02-18 14:00:00+00'),
('WO-2026-003', 'Transmission Housing',   120, 'in_progress',  'detroit', 'line_01', '2026-02-18 14:00:00+00', '2026-02-18 22:00:00+00'),
('WO-2026-004', 'Gear Set',               300, 'complete',     'munich',  'line_01', '2026-02-18 08:00:00+00', '2026-02-18 16:00:00+00'),
('WO-2026-005', 'Bearing Housing',        200, 'planned',      'munich',  'line_02', '2026-02-19 06:00:00+00', '2026-02-19 14:00:00+00'),
('WO-2026-006', 'Cylinder Head',          400, 'complete',     'detroit', 'line_02', '2026-02-17 06:00:00+00', '2026-02-17 22:00:00+00'),
('WO-2026-007', 'Camshaft Assembly',      150, 'in_progress',  'munich',  'line_01', '2026-02-18 16:00:00+00', '2026-02-19 00:00:00+00');
