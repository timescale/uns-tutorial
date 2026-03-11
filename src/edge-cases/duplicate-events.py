#!/usr/bin/env python3
"""
Edge Case 2: Duplicate Events from MQTT Retry Logic

Scenario:
  MQTT QoS 1 guarantees at-least-once delivery. When a network hiccup causes
  the broker to redeliver a message, the same state-change event arrives twice.
  Multiplied across all devices and all lines, alarm counts and production event
  totals silently inflate.

Without governance (no unique index on equipment_events):
  130 rows stored (100 unique + 30 duplicates).
  Alarm count for line_01 is inflated — includes phantom alarms from retries.

With governance (unique index + ON CONFLICT DO NOTHING):
  100 rows stored. Duplicates are silently discarded.
  Alarm count is accurate.

Key insight:
  Duplicate events inflate alarm counts, production totals, and audit trails.
  ON CONFLICT DO NOTHING makes every insert idempotent with zero
  application-side logic.

Refactored from: mqtt-sql-timescale-repo/src/experiments/duplicate-handling.py
"""

import os
from datetime import datetime, timezone, timedelta

import psycopg
from dotenv import load_dotenv

load_dotenv()

TIMESCALE_DSN     = os.getenv("TIMESCALE_DSN", "postgresql://postgres:postgres@localhost:5432/uns_demo")
UNIQUE_EVENTS     = int(os.getenv("EXPERIMENT_UNIQUE_EVENTS", 100))
DUPLICATE_EVENTS  = int(os.getenv("EXPERIMENT_DUPLICATE_EVENTS", 30))

BASE_TS      = datetime(2026, 2, 20, 8, 0, 0, tzinfo=timezone.utc)  # date with no seed data
DEVICE_LINE1 = "acme_mfg/detroit/assembly/line_01/plc_vibration_01"
DEVICE_LINE2 = "acme_mfg/detroit/assembly/line_02/plc_vibration_01"
ALARM_EVENTS = 14  # Expected alarms in the honest dataset

# Scopes count/alarm queries to this test's rows only (seed data lives on 2026-02-18)
_END_TS = BASE_TS + timedelta(minutes=(100 - 1) * 5)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
def setup(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS test_events_without CASCADE")
        cur.execute("""
            CREATE TABLE test_events_without (
                ts          TIMESTAMPTZ NOT NULL,
                device_id   TEXT NOT NULL,
                event_type  TEXT NOT NULL,
                description TEXT
            )
        """)
        # No unique index — duplicates will pile up
    conn.commit()
    print("✓ Test tables created")


def cleanup(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS test_events_without CASCADE")
    conn.commit()


# ---------------------------------------------------------------------------
# Generate event dataset
# ---------------------------------------------------------------------------
def generate_events() -> list[tuple]:
    """
    Generate 100 unique events: a mix of alarms, state_changes, and
    production events across two devices. ALARM_EVENTS of them are 'alarm'.
    """
    events = []
    event_cycle = (
        ["alarm"] * ALARM_EVENTS +
        ["state_change"] * 30 +
        ["production_start"] * 28 +
        ["production_stop"] * (UNIQUE_EVENTS - ALARM_EVENTS - 30 - 28)
    )
    devices = [DEVICE_LINE1, DEVICE_LINE2]

    for i in range(UNIQUE_EVENTS):
        ts = BASE_TS + timedelta(minutes=i * 5)
        device = devices[i % len(devices)]
        event_type = event_cycle[i % len(event_cycle)]
        events.append((ts, device, event_type, f"Event {i+1}"))

    return events


# ---------------------------------------------------------------------------
# Injection
# ---------------------------------------------------------------------------
def inject_without_governance(conn: psycopg.Connection, events: list[tuple], duplicates: list[tuple]) -> int:
    """Insert events + duplicates into the ungoverned table."""
    with conn.cursor() as cur:
        for row in events + duplicates:
            cur.execute(
                "INSERT INTO test_events_without (ts, device_id, event_type, description) "
                "VALUES (%s, %s, %s, %s)",
                row,
            )
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM test_events_without")
        return cur.fetchone()[0]


def inject_with_governance(conn: psycopg.Connection, events: list[tuple], duplicates: list[tuple]) -> int:
    """
    Insert events + duplicates into the governed equipment_events table.
    ON CONFLICT DO NOTHING silently drops duplicates.
    """
    for row in events + duplicates:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO equipment_events (ts, device_id, event_type, description) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (ts, device_id, event_type) DO NOTHING",
                    row,
                )
            conn.commit()
        except psycopg.errors.ForeignKeyViolation:
            conn.rollback()
            # event_type not in taxonomy — not the scenario we're testing here

    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM equipment_events "
            "WHERE device_id IN (%s, %s) AND ts BETWEEN %s AND %s",
            (DEVICE_LINE1, DEVICE_LINE2, BASE_TS, _END_TS),
        )
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def query_alarm_count_without(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM test_events_without WHERE event_type = 'alarm'",
        )
        return cur.fetchone()[0]


def query_alarm_count_with(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM equipment_events "
            "WHERE device_id IN (%s, %s) AND event_type = 'alarm' "
            "AND ts BETWEEN %s AND %s",
            (DEVICE_LINE1, DEVICE_LINE2, BASE_TS, _END_TS),
        )
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    total_sent = UNIQUE_EVENTS + DUPLICATE_EVENTS

    print("=" * 70)
    print("EDGE CASE 2: DUPLICATE EVENTS FROM RETRY LOGIC")
    print("=" * 70)
    print(f"""
Setup:
  Unique events:    {UNIQUE_EVENTS}
  Duplicates sent:  {DUPLICATE_EVENTS}  (simulating MQTT QoS 1 redelivery)
  Total messages:   {total_sent}
  Expected alarms:  {ALARM_EVENTS}  (accurate count)
  Two tables:
    - test_events_without  (no unique constraint)
    - equipment_events     (unique index + ON CONFLICT DO NOTHING)
""")

    conn = psycopg.connect(TIMESCALE_DSN)
    setup(conn)

    try:
        events = generate_events()
        # Select the first DUPLICATE_EVENTS events to replay as duplicates
        duplicates = events[:DUPLICATE_EVENTS]

        print(f"Sending {UNIQUE_EVENTS} unique events + {DUPLICATE_EVENTS} duplicates (QoS 1 retry simulation)...")
        print()

        # ── Without governance ────────────────────────────────────────────────
        print("-" * 70)
        print("WITHOUT GOVERNANCE  (no unique index)")
        print("-" * 70)
        rows_without = inject_without_governance(conn, events, duplicates)
        alarms_without = query_alarm_count_without(conn)
        print(f"  Rows stored:          {rows_without}  (includes {DUPLICATE_EVENTS} duplicates)")
        print(f"  Alarm count:          {alarms_without}  ← inflated (actual: {ALARM_EVENTS})")

        # ── With governance ───────────────────────────────────────────────────
        print()
        print("-" * 70)
        print("WITH GOVERNANCE  (unique index on (ts, device_id, event_type) + ON CONFLICT DO NOTHING)")
        print("-" * 70)
        rows_with = inject_with_governance(conn, events, duplicates)
        alarms_with = query_alarm_count_with(conn)
        print(f"  Rows stored:          {rows_with}  ({DUPLICATE_EVENTS} duplicates silently discarded)")
        print(f"  Alarm count:          {alarms_with}  ← accurate")

        # ── Key insight ───────────────────────────────────────────────────────
        print()
        print("=" * 70)
        print("KEY INSIGHT")
        print("=" * 70)
        print(f"""
  WITHOUT governance: {rows_without} rows stored, alarm count inflated by
  {alarms_without - ALARM_EVENTS} phantom alarms from QoS 1 redelivery.

  WITH governance: {rows_with} rows stored. ON CONFLICT DO NOTHING makes every
  insert idempotent — MQTT retries cannot corrupt your event log.
  Alarm count: {alarms_with} ✓
""")
        print("✓ Experiment complete!\n")

    finally:
        cleanup(conn)
        conn.close()


if __name__ == "__main__":
    main()
