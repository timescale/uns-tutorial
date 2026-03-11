# UNS Governance Demo

Companion repository for **"Ask Your Factory Floor Anything: Structuring Industrial Data for AI Agents"**.

Every governance concept from the tutorial is runnable here. Each edge-case script shows the same scenario twice — once without schema governance, once with — so you can see the difference in a single terminal session.

---

## What You'll Learn

### The Problem: Ungoverned UNS Data

A Unified Namespace solves the connectivity problem — all devices publish, all consumers subscribe. But without schema governance:

- ❌ Timezone-naive timestamps silently land in the wrong time buckets
- ❌ MQTT QoS 1 retries inflate alarm counts and event totals
- ❌ Missing fields create invisible gaps in downstream queries
- ❌ Non-standard event labels make cross-site queries miss entire lines

### The Solution: Schema Governance as the Foundation

Intentional schema design — enforced hierarchies, validated enums, `TIMESTAMPTZ` columns, `NOT NULL` constraints, and unique indexes — prevents these failures at ingestion time and makes every reading trustworthy. This same governed foundation is what enables AI agents, digital twins, and cross-domain analytics.


## Architecture

```
┌─────────────┐    ISA-95 topics      ┌───────────┐    MQTT      ┌────────────┐
│  Publisher  │ ──────────────────>   │ Mosquitto │ ──────────>  │  ingest.py │
│ (publisher  │   acme_mfg/{site}/    │  Broker   │              │  (governed)│
│  .py)       │   {area}/{line}/{cell}└───────────┘              └─────┬──────┘
└─────────────┘                                                        │ validated
                                                                       │ COPY batches
                                                                       ▼
                                                               ┌──────────────┐
                                                               │  TimescaleDB │
                                                               │  (uns_demo)  │
                                                               │              │
                                                               │ equipment_   │
                                                               │ context      │
                                                               │ sensor_      │
                                                               │ readings     │
                                                               │ equipment_   │
                                                               │ events       │
                                                               │ work_orders  │
                                                               │ dead_letter  │
                                                               └──────────────┘
```

Edge-case scripts inject data **directly** into TimescaleDB (no MQTT round-trip) for deterministic, self-contained output. Only `ingest.py` and `wildcard-demo.py` require a live MQTT broker.


## Quick Start

### Prerequisites

- Docker and Docker Compose
- Python 3.12+ with `uv` installed

### Setup

1. **Start the infrastructure** (TimescaleDB + Mosquitto):

```bash
docker compose up -d
```

2. **Install Python dependencies**:

```bash
uv sync
```

3. **Verify the database is ready**:

```bash
docker compose logs timescaledb | grep "database system is ready"
```

---

## Running the Experiments

### Edge-Case Suite (no MQTT required)

Run all four edge cases in sequence with a single command:

```bash
bash run-all.sh
```

Or run each experiment individually:

```bash
uv run src/edge-cases/timezone-drift.py
uv run src/edge-cases/duplicate-events.py
uv run src/edge-cases/missing-fields.py
uv run src/edge-cases/non-standard-enums.py
```

**Expected output (run-all.sh summary):**

```
======================================================================
UNS GOVERNANCE DEMO — SUMMARY
======================================================================

  Edge Case                       Without Governance             With Governance
  -----------------------------------------------------------------------
  1. Timezone Drift               Silent wrong time buckets      Rejected at ingestion
  2. Duplicate Events             130 rows, inflated count       100 rows, accurate count
  3. Missing Fields               Silent NULLs in results        Rejected + dead_letter
  4. Non-Standard Enums           Invisible to queries           Rejected + suggested fix

  Results: 4 passed, 0 failed
```

---

### MQTT Wildcard Demo (requires broker)

Shows how ISA-95 topic hierarchy enables role-based data access through MQTT wildcards:

```bash
uv run src/wildcard-demo.py
```

**Expected output:**

```
======================================================================
WILDCARD SUBSCRIPTION DEMO
======================================================================

Publisher:  200 messages across the full ISA-95 hierarchy
            (24 devices: 2 sites × 2 areas × 2 lines × 3 sensors)

Published 200 messages across full UNS hierarchy

----------------------------------------------------------------------
Subscriber                Pattern                                        Received
----------------------------------------------------------------------
  Plant Manager           acme_mfg/detroit/#                              104  (expected ~104)
  Assembly Supervisor     acme_mfg/+/assembly/#                            102  (expected ~102)
  Quality Analyst         acme_mfg/munich/+/+/plc_pressure_01              32  (expected ~32)
----------------------------------------------------------------------
  Total published:  200
```

---

### Governed Ingestion Demo (live MQTT)

Runs a publisher and two ingest handlers simultaneously in a single terminal.
The publisher sends 240 messages — 192 valid and 48 intentionally bad — so you
can see governance rejections happen in real time:

```bash
uv run src/demo-ingest.py
```

**Expected output:**

```
  Publishing 240 messages through two ingest handlers simultaneously.

  Message mix:
    Valid      192   registered devices · UTC timestamps · all fields present
    Invalid     48   three failure types × 16 messages each:
                  • Unregistered device  — ghost device not in equipment_context
                  • Missing field        — 'value' dropped (firmware bug)
                  • Naive timestamp      — no timezone offset (CET misconfiguration)

  Both handlers connected. Publishing messages...

----------------------------------------------------------------------
                                           Without          With
                                        Governance    Governance
----------------------------------------------------------------------
  Rows written to DB                           240           192
  Rows rejected                                  0            48

  dead_letter breakdown:
    unregistered device                          —            16
    missing field                                —            16
    naive timestamp                              —            16
----------------------------------------------------------------------
```

`src/ingest.py` and `src/publisher.py` are the underlying reference implementations
if you want to explore the pieces separately.

---

### SQL Queries

Run queries against the seed data:

```bash
# Export the DSN once (psql doesn't read .env automatically)
export TIMESCALE_DSN=postgresql://postgres:postgres@localhost:5432/uns_demo

# Full UNS path + metadata + values for a time range
psql $TIMESCALE_DSN -f queries/uns-metadata-lookup.sql

# Hourly KPI view (the "functional namespace")
psql $TIMESCALE_DSN -f queries/functional-namespace.sql

# Cross-domain join: sensor data + events + business context
psql $TIMESCALE_DSN -f queries/cross-domain-join.sql

# Governance health checks (5 queries)
psql $TIMESCALE_DSN -f queries/governance-audit.sql
```

**`uns-metadata-lookup.sql`** — full UNS path with hierarchy columns, filtered to a time window:

```
                      uns_path                       | enterprise |  site   |   area    |  line   |       cell       | metric_type |           ts           | value
-----------------------------------------------------+------------+---------+-----------+---------+------------------+-------------+------------------------+-------
 acme_mfg/detroit/assembly/line_01/plc_temp_01       | acme_mfg   | detroit | assembly  | line_01 | plc_temp_01      | temperature | 2026-02-18 18:00:00+00 |  74.9
 acme_mfg/detroit/assembly/line_01/plc_temp_01       | acme_mfg   | detroit | assembly  | line_01 | plc_temp_01      | temperature | 2026-02-18 17:00:00+00 |  75.1
 acme_mfg/detroit/assembly/line_01/plc_pressure_01   | acme_mfg   | detroit | assembly  | line_01 | plc_pressure_01  | pressure    | 2026-02-18 17:00:00+00 | 100.3
 acme_mfg/detroit/assembly/line_01/plc_vibration_01  | acme_mfg   | detroit | assembly  | line_01 | plc_vibration_01 | vibration   | 2026-02-18 17:00:00+00 |  0.58
 acme_mfg/detroit/assembly/line_01/plc_pressure_01   | acme_mfg   | detroit | assembly  | line_01 | plc_pressure_01  | pressure    | 2026-02-18 16:00:00+00 | 100.7
 acme_mfg/detroit/assembly/line_01/plc_vibration_01  | acme_mfg   | detroit | assembly  | line_01 | plc_vibration_01 | vibration   | 2026-02-18 16:00:00+00 |  0.72
 acme_mfg/detroit/assembly/line_01/plc_temp_01       | acme_mfg   | detroit | assembly  | line_01 | plc_temp_01      | temperature | 2026-02-18 16:00:00+00 |  76.3
 acme_mfg/detroit/assembly/line_01/plc_temp_01       | acme_mfg   | detroit | assembly  | line_01 | plc_temp_01      | temperature | 2026-02-18 15:38:00+00 | 72.98
 ...
(23 rows)
```

**`functional-namespace.sql`** — hourly KPIs per line (seed data only; running `demo-ingest.py` first adds a row for the current hour across all 8 lines):

```
REFRESH MATERIALIZED VIEW
          hour          |  site   |   area    |  line   | avg_temp | avg_pressure | avg_vibration | alarm_count | state_change_count
------------------------+---------+-----------+---------+----------+--------------+---------------+-------------+--------------------
 2026-02-18 18:00:00+00 | detroit | assembly  | line_01 |    74.90 |              |               |           0 |                  0
 2026-02-18 17:00:00+00 | detroit | assembly  | line_01 |    75.10 |       100.30 |          0.58 |           0 |                  1
 2026-02-18 16:00:00+00 | detroit | assembly  | line_01 |    76.30 |       100.70 |          0.72 |           0 |                  0
 2026-02-18 15:00:00+00 | detroit | assembly  | line_01 |    72.95 |       101.20 |          0.69 |           2 |                  0
 2026-02-18 14:00:00+00 | detroit | assembly  | line_01 |    72.36 |        99.80 |          0.44 |           0 |                  0
 2026-02-18 11:00:00+00 | detroit | packaging | line_01 |    69.00 |              |               |           0 |                  0
 2026-02-18 11:00:00+00 | munich  | assembly  | line_01 |    71.80 |              |               |           1 |                  1
 2026-02-18 10:00:00+00 | detroit | packaging | line_01 |    68.20 |        95.30 |          0.28 |           0 |                  0
 2026-02-18 10:00:00+00 | detroit | packaging | line_02 |    68.50 |              |               |           0 |                  0
 2026-02-18 10:00:00+00 | munich  | assembly  | line_01 |    71.00 |        98.10 |               |           0 |                  0
 2026-02-18 10:00:00+00 | munich  | assembly  | line_02 |    71.40 |              |               |           0 |                  0
 ...
(20 rows)
```

**`cross-domain-join.sql`** — sensor metrics + event counts scoped to work order `WO-2026-003`:

```
  order_id   |       product        |   status    |    scheduled_start     |     scheduled_end      |                      uns_path                      |   area   | metric_type | avg_value | reading_count | alarm_count | maintenance_hold_count
-------------+----------------------+-------------+------------------------+------------------------+----------------------------------------------------+----------+-------------+-----------+---------------+-------------+------------------------
 WO-2026-003 | Transmission Housing | in_progress | 2026-02-18 14:00:00+00 | 2026-02-18 22:00:00+00 | acme_mfg/detroit/assembly/line_01/plc_pressure_01  | assembly | pressure    |    100.50 |             4 |           0 |                      0
 WO-2026-003 | Transmission Housing | in_progress | 2026-02-18 14:00:00+00 | 2026-02-18 22:00:00+00 | acme_mfg/detroit/assembly/line_01/plc_temp_01      | assembly | temperature |     72.76 |           106 |           0 |                      0
 WO-2026-003 | Transmission Housing | in_progress | 2026-02-18 14:00:00+00 | 2026-02-18 22:00:00+00 | acme_mfg/detroit/assembly/line_01/plc_vibration_01 | assembly | vibration   |      0.61 |            72 |           2 |                      1
(3 rows)
```

**`governance-audit.sql`** — runs 5 health checks (orphaned devices, non-taxonomy events, future timestamps, silent gaps, dead_letter summary). Queries 1–4 return no rows on a fresh DB. **Query 5 (dead_letter summary) is stateful** — it accumulates across all runs. Each `rejection_count` below reflects running all four edge-case scripts plus `demo-ingest.py` (the counts shown are from running the full suite). Naive timestamp rejections each get their own row because the timestamp is embedded in the reason string.

```
                                                           reason                                                            | rejection_count |          first_seen           |           last_seen           | distinct_devices 
------------------------------------------------------------------------------------------------------------------------------+-----------------+-------------------------------+-------------------------------+------------------
 Missing required field: 'value'                                                                                              |              32 | 2026-02-19 13:16:07.851171+00 | 2026-02-19 13:20:29.475454+00 |                8
 Unknown device 'acme_mfg/ghost/factory/line_01/plc_temp_01' — not registered in equipment_context                            |              32 | 2026-02-19 13:16:07.790334+00 | 2026-02-19 13:20:29.411968+00 |                1
 event_type 'MAINT_HOLD' is not in event_taxonomy. Did you mean 'maintenance_hold'?                                           |              15 | 2026-02-19 12:35:55.98123+00  | 2026-02-19 12:55:46.633819+00 |                2
 event_type 'QC_STOP' is not in event_taxonomy. Did you mean 'quality_hold'?                                                  |              15 | 2026-02-19 12:35:56.016547+00 | 2026-02-19 12:55:46.669051+00 |                2
 Missing required field: 'metric_type' (device: acme_mfg/munich/packaging/line_01/plc_temp_01, ts: 2026-02-18T12:56:00+00:00) |               3 | 2026-02-19 12:35:55.378971+00 | 2026-02-19 12:55:45.986205+00 |                1
 Missing required field: 'metric_type' (device: acme_mfg/munich/packaging/line_01/plc_temp_01, ts: 2026-02-18T12:57:00+00:00) |               3 | 2026-02-19 12:35:55.385718+00 | 2026-02-19 12:55:45.993181+00 |                1
 Missing required field: 'metric_type' (device: acme_mfg/munich/packaging/line_01/plc_temp_01, ts: 2026-02-18T12:58:00+00:00) |               3 | 2026-02-19 12:35:55.392721+00 | 2026-02-19 12:55:46.000115+00 |                1
 Missing required field: 'metric_type' (device: acme_mfg/munich/packaging/line_01/plc_temp_01, ts: 2026-02-18T12:52:00+00:00) |               3 | 2026-02-19 12:35:55.351214+00 | 2026-02-19 12:55:45.957977+00 |                1
 Missing required field: 'metric_type' (device: acme_mfg/munich/packaging/line_01/plc_temp_01, ts: 2026-02-18T12:50:00+00:00) |               3 | 2026-02-19 12:35:55.336222+00 | 2026-02-19 12:55:45.943194+00 |                1
 Missing required field: 'metric_type' (device: acme_mfg/munich/packaging/line_01/plc_temp_01, ts: 2026-02-18T12:51:00+00:00) |               3 | 2026-02-19 12:35:55.344143+00 | 2026-02-19 12:55:45.950906+00 |                1
 Missing required field: 'metric_type' (device: acme_mfg/munich/packaging/line_01/plc_temp_01, ts: 2026-02-18T12:59:00+00:00) |               3 | 2026-02-19 12:35:55.3996+00   | 2026-02-19 12:55:46.006807+00 |                1
 Missing required field: 'metric_type' (device: acme_mfg/munich/packaging/line_01/plc_temp_01, ts: 2026-02-18T12:53:00+00:00) |               3 | 2026-02-19 12:35:55.358164+00 | 2026-02-19 12:55:45.965049+00 |                1
 Missing required field: 'metric_type' (device: acme_mfg/munich/packaging/line_01/plc_temp_01, ts: 2026-02-18T12:54:00+00:00) |               3 | 2026-02-19 12:35:55.364993+00 | 2026-02-19 12:55:45.971884+00 |                1
 Missing required field: 'metric_type' (device: acme_mfg/munich/packaging/line_01/plc_temp_01, ts: 2026-02-18T12:55:00+00:00) |               3 | 2026-02-19 12:35:55.371894+00 | 2026-02-19 12:55:45.978968+00 |                1
 Timestamp '2026-02-19T09:16:07.181962' has no timezone offset — must be UTC (e.g. 2026-02-19T14:00:00+00:00)                 |               1 | 2026-02-19 13:16:10.340328+00 | 2026-02-19 13:16:10.340328+00 |                1
 Timestamp '2026-02-19T09:16:07.182051' has no timezone offset — must be UTC (e.g. 2026-02-19T14:00:00+00:00)                 |               1 | 2026-02-19 13:16:10.529983+00 | 2026-02-19 13:16:10.529983+00 |                1
 Timestamp '2026-02-19T09:16:07.182923' has no timezone offset — must be UTC (e.g. 2026-02-19T14:00:00+00:00)                 |               1 | 2026-02-19 13:16:10.725236+00 | 2026-02-19 13:16:10.725236+00 |                1
 Timestamp '2026-02-19T09:20:25.939459' has no timezone offset — must be UTC (e.g. 2026-02-19T14:00:00+00:00)                 |               1 | 2026-02-19 13:20:26.669786+00 | 2026-02-19 13:20:26.669786+00 |                1
 Timestamp '2026-02-19T09:20:25.939501' has no timezone offset — must be UTC (e.g. 2026-02-19T14:00:00+00:00)                 |               1 | 2026-02-19 13:20:26.8566+00   | 2026-02-19 13:20:26.8566+00   |                1
 Timestamp '2026-02-19T09:20:25.939546' has no timezone offset — must be UTC (e.g. 2026-02-19T14:00:00+00:00)                 |               1 | 2026-02-19 13:20:27.057634+00 | 2026-02-19 13:20:27.057634+00 |                1
 Timestamp '2026-02-19T09:20:25.939581' has no timezone offset — must be UTC (e.g. 2026-02-19T14:00:00+00:00)                 |               1 | 2026-02-19 13:20:27.248298+00 | 2026-02-19 13:20:27.248298+00 |                1
```

> Counts reflect the full session: `demo-ingest.py` ran twice (32 = 16 × 2), edge cases ran three times each (15 = 5 × 3), and each unique rejection reason gets its own row.

---

## Repository Structure

```
uns-tutorial/
├── db/
│   ├── schema.sql          ISA-95 tables, generated uns_path column, hypertables
│   └── seed.sql            Equipment hierarchy, baseline readings, work orders
├── src/
│   ├── demo-ingest.py      Single-terminal governance demo: valid + invalid mix
│   ├── publisher.py        Reference publisher: ISA-95 topics, all 24 devices
│   ├── ingest.py           Reference ingestion: validates, batches, dead-letters
│   ├── wildcard-demo.py    Role-based MQTT wildcard subscriptions
│   └── edge-cases/
│       ├── timezone-drift.py       Edge Case 1
│       ├── duplicate-events.py     Edge Case 2
│       ├── missing-fields.py       Edge Case 3
│       └── non-standard-enums.py   Edge Case 4
├── queries/
│   ├── uns-metadata-lookup.sql     Section II — hierarchy + values query
│   ├── functional-namespace.sql    Materialized KPI view (functional namespace)
│   ├── cross-domain-join.sql       Section IV — AI agent query example
│   └── governance-audit.sql        Cross-site consistency checks
├── .env.example
├── docker-compose.yml
├── pyproject.toml
└── run-all.sh
```

---

## Equipment Hierarchy (Seed Data)

| Level | Count | Examples |
|---|---|---|
| Enterprise | 1 | `acme_mfg` |
| Site | 2 | `detroit`, `munich` |
| Area | 2 per site | `assembly`, `packaging` |
| Line | 2 per area | `line_01`, `line_02` |
| Cell | 3 per line | `plc_temp_01`, `plc_pressure_01`, `plc_vibration_01` |

UNS topic paths follow ISA-95:
```
acme_mfg/detroit/assembly/line_01/plc_temp_01
acme_mfg/munich/packaging/line_02/plc_vibration_01
```

The `uns_path` column in `equipment_context` is a **PostgreSQL generated column** — always derived from the five hierarchy fields, indexable, and consistent across every query.

---

## Schema Design Highlights

The `uns_path` generated column is the key design decision:

```sql
CREATE TABLE equipment_context (
    enterprise  TEXT NOT NULL,
    site        TEXT NOT NULL,
    area        TEXT NOT NULL,
    line        TEXT NOT NULL,
    cell        TEXT NOT NULL,

    uns_path    TEXT GENERATED ALWAYS AS (
                    enterprise || '/' || site || '/' || area || '/' || line || '/' || cell
                ) STORED,
    ...
    PRIMARY KEY (enterprise, site, area, line, cell)
);

CREATE UNIQUE INDEX idx_equipment_context_uns_path ON equipment_context (uns_path);
```

This makes `sensor_readings.device_id` semantically exact (it stores the full UNS path), and every JOIN is a direct equality:

```sql
JOIN equipment_context ec ON ec.uns_path = sr.device_id
```

---

## Configuration

Copy `.env.example` to `.env` and adjust as needed:

```bash
cp .env.example .env
```

Key settings:

```bash
TIMESCALE_DSN=postgresql://postgres:postgres@localhost:5432/uns_demo
MQTT_BROKER_HOST=localhost
MQTT_BROKER_PORT=1883
FLUSH_INTERVAL_SEC=5.0        # ingest.py batch flush interval
```

---

## Cleanup

Stop and remove containers:

```bash
docker compose down -v
```
