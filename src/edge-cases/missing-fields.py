#!/usr/bin/env python3
"""
Edge Case 3: Missing Fields That Break Downstream Queries

Scenario:
  A PLC firmware update on the Munich packaging line drops the metric_type
  field from payloads. The device still publishes at normal frequency, so
  nothing looks wrong at the network level. Ingestion succeeds, and the
  readings quietly accumulate — but every query that filters on metric_type
  silently excludes them.

Without governance (no NOT NULL on metric_type):
  60 rows stored. avg(value) query runs but is based on only 38 rows —
  the 10 rows with NULL metric_type are silently excluded. The result
  looks correct but reflects incomplete data.

With governance (NOT NULL constraint + dead_letter):
  50 rows stored. 10 rejected (NOT NULL violation) and written to
  dead_letter. avg(value) query uses the complete 38-row dataset —
  same number, but now you know the dataset is complete and the gap
  is visible for investigation.

Key insight:
  Without NOT NULL constraints, incomplete payloads insert successfully
  but create silent gaps. Downstream queries return correct-looking
  results from incomplete data. The gap surfaces days later in reports.

Refactored from: mqtt-sql-timescale-repo/src/experiments/error-recovery.py
"""

import os
from datetime import datetime, timezone, timedelta

import psycopg
from dotenv import load_dotenv

load_dotenv()

TIMESCALE_DSN         = os.getenv("TIMESCALE_DSN", "postgresql://postgres:postgres@localhost:5432/uns_demo")
COMPLETE_READINGS     = int(os.getenv("EXPERIMENT_COMPLETE_READINGS", 50))
INCOMPLETE_READINGS   = int(os.getenv("EXPERIMENT_INCOMPLETE_READINGS", 10))

BASE_TS  = datetime(2026, 2, 18, 12, 0, 0, tzinfo=timezone.utc)
DEVICE   = "acme_mfg/munich/packaging/line_01/plc_temp_01"
EXPECTED_TEMP_ROWS = 38   # rows where metric_type = 'temperature' in the complete set


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
def setup(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS test_missing_without CASCADE")
        cur.execute("""
            CREATE TABLE test_missing_without (
                ts          TIMESTAMPTZ      NOT NULL,
                device_id   TEXT             NOT NULL,
                metric_type TEXT,                       -- ← nullable, no governance
                value       DOUBLE PRECISION NOT NULL
            )
        """)
    conn.commit()
    print("✓ Test tables created")


def cleanup(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS test_missing_without CASCADE")
    conn.commit()


# ---------------------------------------------------------------------------
# Data generation
# ---------------------------------------------------------------------------
def generate_complete_readings() -> list[tuple]:
    """Generate COMPLETE_READINGS rows with metric_type populated."""
    rows = []
    metrics = ["temperature"] * EXPECTED_TEMP_ROWS + ["pressure"] * (COMPLETE_READINGS - EXPECTED_TEMP_ROWS)
    for i in range(COMPLETE_READINGS):
        ts = BASE_TS + timedelta(minutes=i)
        rows.append((ts, DEVICE, metrics[i], 67.0 + i * 0.05))
    return rows


def generate_incomplete_readings() -> list[tuple]:
    """Generate INCOMPLETE_READINGS rows with metric_type = None (firmware dropped it)."""
    rows = []
    for i in range(INCOMPLETE_READINGS):
        ts = BASE_TS + timedelta(minutes=COMPLETE_READINGS + i)
        rows.append((ts, DEVICE, None, 68.5 + i * 0.03))
    return rows


# ---------------------------------------------------------------------------
# Injection
# ---------------------------------------------------------------------------
def inject_without_governance(conn: psycopg.Connection,
                               complete: list[tuple],
                               incomplete: list[tuple]) -> int:
    """Insert all rows into the ungoverned table (metric_type nullable)."""
    with conn.cursor() as cur:
        for row in complete + incomplete:
            cur.execute(
                "INSERT INTO test_missing_without (ts, device_id, metric_type, value) "
                "VALUES (%s, %s, %s, %s)",
                row,
            )
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM test_missing_without")
        return cur.fetchone()[0]


def inject_with_governance(conn: psycopg.Connection,
                            complete: list[tuple],
                            incomplete: list[tuple]) -> tuple[int, int]:
    """
    Attempt to insert all rows into the governed sensor_readings table.
    Rows with NULL metric_type violate the NOT NULL constraint and are
    caught and written to dead_letter instead.
    """
    accepted = 0
    rejected = 0

    for row in complete + incomplete:
        ts, device_id, metric_type, value = row
        if metric_type is None:
            # Governance check: NOT NULL violation caught before DB round-trip
            reason = f"Missing required field: 'metric_type' (device: {device_id}, ts: {ts.isoformat()})"
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO dead_letter (topic, payload, reason) VALUES (%s, %s, %s)",
                    (
                        device_id,
                        f'{{"ts": "{ts.isoformat()}", "value": {value}, "metric_type": null}}',
                        reason,
                    ),
                )
            conn.commit()
            rejected += 1
        else:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sensor_readings (ts, device_id, metric_type, value) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (ts, device_id, metric_type) DO NOTHING",
                    row,
                )
            conn.commit()
            accepted += 1

    return accepted, rejected


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def query_avg_without(conn: psycopg.Connection) -> tuple[float | None, int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT avg(value), count(*) FROM test_missing_without "
            "WHERE device_id = %s AND metric_type = 'temperature'",
            (DEVICE,),
        )
        avg_val, row_count = cur.fetchone()
    return avg_val, row_count


def query_avg_with(conn: psycopg.Connection) -> tuple[float | None, int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT avg(value), count(*) FROM sensor_readings "
            "WHERE device_id = %s AND metric_type = 'temperature' "
            "AND ts >= '2026-02-18 12:00:00+00'",
            (DEVICE,),
        )
        avg_val, row_count = cur.fetchone()
    return avg_val, row_count


def query_dead_letter_count(conn: psycopg.Connection, since: datetime) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM dead_letter WHERE topic = %s AND received_at >= %s",
            (DEVICE, since),
        )
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    total_sent = COMPLETE_READINGS + INCOMPLETE_READINGS

    print("=" * 70)
    print("EDGE CASE 3: MISSING FIELDS THAT BREAK DOWNSTREAM QUERIES")
    print("=" * 70)
    print(f"""
Setup:
  Complete readings:    {COMPLETE_READINGS}  (metric_type populated)
  Incomplete readings:  {INCOMPLETE_READINGS}  (metric_type = NULL — firmware dropped the field)
  Total sent:           {total_sent}
  Device:               {DEVICE}
""")

    conn = psycopg.connect(TIMESCALE_DSN)
    setup(conn)

    try:
        complete   = generate_complete_readings()
        incomplete = generate_incomplete_readings()

        print(f"Sending {COMPLETE_READINGS} complete readings + {INCOMPLETE_READINGS} with missing metric_type...")
        print()

        # ── Without governance ────────────────────────────────────────────────
        print("-" * 70)
        print("WITHOUT GOVERNANCE  (metric_type column is nullable)")
        print("-" * 70)
        rows_stored = inject_without_governance(conn, complete, incomplete)
        avg_val, row_count = query_avg_without(conn)
        print(f"  Rows stored:  {rows_stored}")
        print(f"\n  Query: SELECT avg(value) WHERE metric_type = 'temperature'")
        print(f"  Result: {avg_val:.2f}°F  (based on {row_count} rows "
              f"— {INCOMPLETE_READINGS} NULL rows silently excluded)")

        # ── With governance ───────────────────────────────────────────────────
        print()
        print("-" * 70)
        print("WITH GOVERNANCE  (NOT NULL on metric_type + dead_letter)")
        print("-" * 70)
        test_start = datetime.now(timezone.utc)
        accepted, rejected = inject_with_governance(conn, complete, incomplete)
        dead_letter_count = query_dead_letter_count(conn, test_start)
        avg_val_gov, row_count_gov = query_avg_with(conn)
        print(f"  Complete readings stored:  {accepted}")
        print(f"  Incomplete rows rejected:  {rejected}  → written to dead_letter")
        print(f"  dead_letter rows:          {dead_letter_count}")
        print(f"\n  Query: SELECT avg(value) WHERE metric_type = 'temperature'")
        print(f"  Result: {avg_val_gov:.2f}°F  (based on {row_count_gov} rows "
              f"— complete dataset, no silent gaps)")

        # ── Key insight ───────────────────────────────────────────────────────
        print()
        print("=" * 70)
        print("KEY INSIGHT")
        print("=" * 70)
        print(f"""
  WITHOUT governance: {rows_stored} rows stored. The avg query returns the
  same number either way — but is based on incomplete data. The {INCOMPLETE_READINGS}
  missing readings are invisible. Nobody knows to investigate.

  WITH governance: {accepted} rows stored. {rejected} rows in dead_letter with
  the reason. The avg query runs on the complete dataset. The firmware issue
  surfaces immediately — a gap you know about is better than one you don't.
""")
        print("✓ Experiment complete!\n")

    finally:
        cleanup(conn)
        conn.close()


if __name__ == "__main__":
    main()
