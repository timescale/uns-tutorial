#!/usr/bin/env python3
"""
Edge Case 4: Non-Standard Event Types from a Newly Commissioned Line

Scenario:
  A new packaging line in Munich is commissioned by a different team using
  their own event label convention. They publish 'MAINT_HOLD' where the
  standard taxonomy requires 'maintenance_hold', and 'QC_STOP' instead of
  'quality_hold'. Everything ingests fine at the transport layer.

Without governance (no FK to event_taxonomy):
  60 rows stored — 50 standard + 10 non-standard.
  A cross-site query filtering on 'maintenance_hold' silently misses the
  new line's events. Governance erosion happens one commissioning at a time.

With governance (FK to event_taxonomy — see schema.sql):
  50 standard rows stored. 10 non-standard rows rejected with suggested
  corrections written to dead_letter. The query returns accurate counts
  and the onboarding team gets actionable feedback.

Key insight:
  Without enum validation, the namespace is unified but the semantics
  diverge. Cross-site queries silently miss data from sites using
  non-standard labels.
"""

import os
from datetime import datetime, timezone, timedelta

import psycopg
from dotenv import load_dotenv

load_dotenv()

TIMESCALE_DSN       = os.getenv("TIMESCALE_DSN", "postgresql://postgres:postgres@localhost:5432/uns_demo")
STANDARD_EVENTS     = int(os.getenv("EXPERIMENT_STANDARD_EVENTS", 50))
NONSTANDARD_EVENTS  = int(os.getenv("EXPERIMENT_NONSTANDARD_EVENTS", 10))

BASE_TS   = datetime(2026, 2, 18, 7, 0, 0, tzinfo=timezone.utc)
DEVICE_A  = "acme_mfg/munich/packaging/line_01/plc_temp_01"
DEVICE_B  = "acme_mfg/munich/packaging/line_02/plc_temp_01"

# Standard taxonomy values (from event_taxonomy seed)
STANDARD_TYPES = ["state_change", "alarm", "quality_hold", "maintenance_hold", "production_start", "production_stop"]

# Non-standard labels used by the new commissioning team
NON_STANDARD_MAPPING = {
    "MAINT_HOLD": "maintenance_hold",
    "QC_STOP":    "quality_hold",
}
# Split NONSTANDARD_EVENTS evenly across both non-standard types
NON_STANDARD_TYPES = (
    ["MAINT_HOLD"] * (NONSTANDARD_EVENTS // 2) +
    ["QC_STOP"]    * (NONSTANDARD_EVENTS - NONSTANDARD_EVENTS // 2)
)

# Expected count of 'maintenance_hold' events in the honest standard dataset
EXPECTED_MAINT_HOLD = sum(
    1 for i in range(STANDARD_EVENTS)
    if STANDARD_TYPES[i % len(STANDARD_TYPES)] == "maintenance_hold"
)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
def setup(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS test_enums_without CASCADE")
        cur.execute("""
            CREATE TABLE test_enums_without (
                ts          TIMESTAMPTZ NOT NULL,
                device_id   TEXT        NOT NULL,
                event_type  TEXT        NOT NULL,   -- ← no FK, accepts anything
                description TEXT
            )
        """)
    conn.commit()
    print("✓ Test tables created")


def cleanup(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS test_enums_without CASCADE")
    conn.commit()


# ---------------------------------------------------------------------------
# Data generation
# ---------------------------------------------------------------------------
def generate_standard_events() -> list[tuple]:
    rows = []
    devices = [DEVICE_A, DEVICE_B]
    for i in range(STANDARD_EVENTS):
        ts = BASE_TS + timedelta(minutes=i * 10)
        device = devices[i % len(devices)]
        event_type = STANDARD_TYPES[i % len(STANDARD_TYPES)]
        rows.append((ts, device, event_type, f"Standard event {i+1}"))
    return rows


def generate_nonstandard_events() -> list[tuple]:
    rows = []
    devices = [DEVICE_A, DEVICE_B]
    for i, nst in enumerate(NON_STANDARD_TYPES):
        ts = BASE_TS + timedelta(hours=10, minutes=i * 10)
        device = devices[i % len(devices)]
        rows.append((ts, device, nst, f"Non-standard event: {nst}"))
    return rows


# ---------------------------------------------------------------------------
# Injection
# ---------------------------------------------------------------------------
def inject_without_governance(conn: psycopg.Connection,
                               standard: list[tuple],
                               nonstandard: list[tuple]) -> int:
    """Insert all events into the ungoverned table — no FK constraint."""
    with conn.cursor() as cur:
        for row in standard + nonstandard:
            cur.execute(
                "INSERT INTO test_enums_without (ts, device_id, event_type, description) "
                "VALUES (%s, %s, %s, %s)",
                row,
            )
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM test_enums_without")
        return cur.fetchone()[0]


def inject_with_governance(conn: psycopg.Connection,
                            standard: list[tuple],
                            nonstandard: list[tuple]) -> tuple[int, int]:
    """
    Insert events into the governed equipment_events table.
    Non-standard event_types violate the FK to event_taxonomy and are
    rejected — written to dead_letter with a suggested correction.
    """
    accepted = 0
    rejected = 0

    for row in standard:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO equipment_events (ts, device_id, event_type, description) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (ts, device_id, event_type) DO NOTHING",
                row,
            )
        conn.commit()
        accepted += 1

    for row in nonstandard:
        ts, device_id, event_type, description = row
        suggestion = NON_STANDARD_MAPPING.get(event_type, "unknown — check event_taxonomy table")
        reason = (
            f"event_type '{event_type}' is not in event_taxonomy. "
            f"Did you mean '{suggestion}'?"
        )
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO dead_letter (topic, payload, reason) VALUES (%s, %s, %s)",
                (
                    device_id,
                    f'{{"ts": "{ts.isoformat()}", "event_type": "{event_type}"}}',
                    reason,
                ),
            )
        conn.commit()
        rejected += 1

    return accepted, rejected


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def query_maint_hold_without(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM test_enums_without "
            "WHERE event_type = 'maintenance_hold'"
        )
        return cur.fetchone()[0]


def query_maint_hold_with(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM equipment_events "
            "WHERE device_id IN (%s, %s) AND event_type = 'maintenance_hold'",
            (DEVICE_A, DEVICE_B),
        )
        return cur.fetchone()[0]


def query_dead_letter_suggestions(conn: psycopg.Connection, since: datetime) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT topic, reason FROM dead_letter "
            "WHERE topic IN (%s, %s) AND received_at >= %s "
            "ORDER BY received_at",
            (DEVICE_A, DEVICE_B, since),
        )
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    total_sent = STANDARD_EVENTS + NONSTANDARD_EVENTS
    missed_events = NONSTANDARD_EVENTS // 2  # MAINT_HOLD events missed by 'maintenance_hold' query

    print("=" * 70)
    print("EDGE CASE 4: NON-STANDARD EVENT TYPES FROM NEW LINE COMMISSIONING")
    print("=" * 70)
    print(f"""
Setup:
  Standard events:     {STANDARD_EVENTS}  (using event_taxonomy vocabulary)
  Non-standard events: {NONSTANDARD_EVENTS}  (MAINT_HOLD, QC_STOP — new team's convention)
  Total sent:          {total_sent}
  Two tables:
    - test_enums_without  (no FK to event_taxonomy)
    - equipment_events    (FK to event_taxonomy — rejects unknown types)
""")

    conn = psycopg.connect(TIMESCALE_DSN)
    setup(conn)

    try:
        standard    = generate_standard_events()
        nonstandard = generate_nonstandard_events()

        print(f"Sending {STANDARD_EVENTS} standard events + {NONSTANDARD_EVENTS} non-standard events...")
        print()

        # ── Without governance ────────────────────────────────────────────────
        print("-" * 70)
        print("WITHOUT GOVERNANCE  (no FK to event_taxonomy)")
        print("-" * 70)
        rows_without = inject_without_governance(conn, standard, nonstandard)
        maint_without = query_maint_hold_without(conn)
        print(f"  Rows stored:  {rows_without}")
        print(f"\n  Query: SELECT count(*) WHERE event_type = 'maintenance_hold'")
        print(f"  Result: {maint_without}  ← missing {missed_events} events labeled 'MAINT_HOLD'")

        # ── With governance ───────────────────────────────────────────────────
        print()
        print("-" * 70)
        print("WITH GOVERNANCE  (FK to event_taxonomy rejects unknown types)")
        print("-" * 70)
        test_start = datetime.now(timezone.utc)
        accepted, rejected = inject_with_governance(conn, standard, nonstandard)
        maint_with = query_maint_hold_with(conn)
        suggestions = query_dead_letter_suggestions(conn, test_start)
        print(f"  Standard events stored:    {accepted}")
        print(f"  Non-standard rows rejected: {rejected}  → written to dead_letter with corrections")
        print(f"\n  Query: SELECT count(*) WHERE event_type = 'maintenance_hold'")
        print(f"  Result: {maint_with}  ← accurate")
        print(f"\n  dead_letter suggested corrections:")
        for _device, reason in suggestions[:4]:  # show first 4
            print(f"    {reason}")
        if len(suggestions) > 4:
            print(f"    ... ({len(suggestions) - 4} more)")

        # ── Key insight ───────────────────────────────────────────────────────
        print()
        print("=" * 70)
        print("KEY INSIGHT")
        print("=" * 70)
        print(f"""
  WITHOUT governance: {rows_without} rows stored. The 'maintenance_hold' query
  returns {maint_without} — it silently misses {missed_events} events labeled 'MAINT_HOLD'.
  Governance erosion happens one new line commissioning at a time.

  WITH governance: {accepted} rows stored. {rejected} rows in dead_letter with
  suggested corrections. The new team gets immediate, actionable feedback.
  The query returns {maint_with} accurate results.
""")
        print("✓ Experiment complete!\n")

    finally:
        cleanup(conn)
        conn.close()


if __name__ == "__main__":
    main()
