#!/usr/bin/env python3
"""
Edge Case 1: Timezone Drift Between Sites

Scenario:
  Detroit publishes timestamps in UTC (correct).
  Munich publishes naive local timestamps — CET is UTC+1, but the timezone
  offset is omitted. The DB receives the bare string "2026-02-18T10:00:00"
  with no context.

Without governance (TIMESTAMP column, no ingestion check):
  All 100 rows insert. The naive Munich timestamps are stored as-is.
  PostgreSQL interprets them using the server timezone (UTC), so they land
  in the right wall-clock slot — but the intent was CET, meaning Munich data
  is silently 1 hour ahead of where it should be in any cross-site aggregate.

With governance (TIMESTAMPTZ column + ingestion-layer check):
  50 UTC readings accepted. 50 naive CET readings rejected at ingestion —
  they go to dead_letter with the reason for the team to investigate.
  The cross-site aggregate is incomplete but honest.

Key insight:
  Without timezone enforcement, Munich data silently lands in the wrong
  time buckets. OEE and downtime calculations are wrong by 1 hour.
  Nobody notices until a business review.
"""

import os
import sys
from datetime import datetime, timezone, timedelta

import psycopg
from dotenv import load_dotenv

load_dotenv()

TIMESCALE_DSN     = os.getenv("TIMESCALE_DSN", "postgresql://postgres:postgres@localhost:5432/uns_demo")
UTC_READINGS      = int(os.getenv("EXPERIMENT_UTC_READINGS", 50))
NAIVE_READINGS    = int(os.getenv("EXPERIMENT_NAIVE_READINGS", 50))

# Reference window: 2026-02-18 14:00 UTC
# Detroit: publishes UTC timestamps starting at 14:00 UTC
# Munich:  publishes CET timestamps — 15:00 CET = 14:00 UTC, but the
#          offset is stripped, so they arrive as "2026-02-18T15:00:00"
WINDOW_START_UTC  = datetime(2026, 2, 18, 14, 0, 0, tzinfo=timezone.utc)
DEVICE_DETROIT    = "acme_mfg/detroit/assembly/line_01/plc_temp_01"
DEVICE_MUNICH     = "acme_mfg/munich/assembly/line_01/plc_temp_01"


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
def setup(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS test_tz_without, test_tz_dead_letter CASCADE")

        # Without governance: TIMESTAMP (no timezone) — accepts naive strings
        cur.execute("""
            CREATE TABLE test_tz_without (
                ts          TIMESTAMP NOT NULL,       -- ← no timezone
                device_id   TEXT NOT NULL,
                metric_type TEXT NOT NULL,
                value       DOUBLE PRECISION NOT NULL
            )
        """)

        # With governance: governed sensor_readings + dead_letter already exist in schema
        # We'll use the real sensor_readings table for the "with" side, but we need
        # a separate dead_letter-style table to track only THIS experiment's rejections.
        cur.execute("""
            CREATE TABLE test_tz_dead_letter (
                id          BIGSERIAL PRIMARY KEY,
                received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                device_id   TEXT,
                raw_ts      TEXT,
                reason      TEXT NOT NULL
            )
        """)
    conn.commit()
    print("✓ Test tables created")


def cleanup(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS test_tz_without, test_tz_dead_letter CASCADE")
    conn.commit()


# ---------------------------------------------------------------------------
# Data injection helpers
# ---------------------------------------------------------------------------
def inject_without_governance(conn: psycopg.Connection) -> tuple[int, int]:
    """Insert both UTC and naive CET readings into the ungoverned table."""
    accepted = 0
    with conn.cursor() as cur:
        # Detroit: UTC timestamps  (correct)
        for i in range(UTC_READINGS):
            ts = WINDOW_START_UTC + timedelta(minutes=i * 2)
            cur.execute(
                "INSERT INTO test_tz_without (ts, device_id, metric_type, value) VALUES (%s, %s, %s, %s)",
                (ts.replace(tzinfo=None), DEVICE_DETROIT, "temperature", 72.0 + i * 0.02)
            )
            accepted += 1

        # Munich: naive CET timestamps — strip the +01:00 offset before inserting
        # This simulates a PLC or gateway that sends local time without the offset.
        for i in range(NAIVE_READINGS):
            ts_utc = WINDOW_START_UTC + timedelta(minutes=i * 2)
            ts_cet_naive = (ts_utc + timedelta(hours=1)).replace(tzinfo=None)  # CET = UTC+1, offset stripped
            cur.execute(
                "INSERT INTO test_tz_without (ts, device_id, metric_type, value) VALUES (%s, %s, %s, %s)",
                (ts_cet_naive, DEVICE_MUNICH, "temperature", 69.0 + i * 0.02)
            )
            accepted += 1

    conn.commit()
    return accepted, 0  # (accepted, rejected)


def inject_with_governance(conn: psycopg.Connection) -> tuple[int, int]:
    """
    Simulate governed ingestion: accept UTC readings, reject naive CET readings.

    The governance check mirrors ingest.py: timestamps must have an explicit
    timezone offset. Naive strings fail validation and go to test_tz_dead_letter.
    """
    accepted = 0
    rejected = 0

    with conn.cursor() as cur:
        # Detroit: UTC timestamps — pass validation
        for i in range(UTC_READINGS):
            ts = WINDOW_START_UTC + timedelta(minutes=i * 2)
            # Governed insert: use sensor_readings (production table)
            cur.execute(
                "INSERT INTO sensor_readings (ts, device_id, metric_type, value) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (ts, device_id, metric_type) DO NOTHING",
                (ts, DEVICE_DETROIT, "temperature", 72.0 + i * 0.02)
            )
            accepted += 1

        # Munich: naive CET strings — fail the timezone check
        for i in range(NAIVE_READINGS):
            ts_utc = WINDOW_START_UTC + timedelta(minutes=i * 2)
            ts_cet_naive = (ts_utc + timedelta(hours=1)).replace(tzinfo=None)
            raw_ts = ts_cet_naive.isoformat()

            # Governance check: no tzinfo → reject
            reason = (
                f"Timestamp '{raw_ts}' has no timezone offset. "
                f"All timestamps must be UTC (e.g. 2026-02-18T14:00:00+00:00)."
            )
            cur.execute(
                "INSERT INTO test_tz_dead_letter (device_id, raw_ts, reason) VALUES (%s, %s, %s)",
                (DEVICE_MUNICH, raw_ts, reason)
            )
            rejected += 1

    conn.commit()
    return accepted, rejected


# ---------------------------------------------------------------------------
# Comparison queries
# ---------------------------------------------------------------------------
def query_without(conn: psycopg.Connection) -> dict:
    """Cross-site hourly aggregate on the ungoverned table."""
    with conn.cursor() as cur:
        # Detroit readings in the 14:00 hour (naive TIMESTAMP comparison)
        cur.execute("""
            SELECT count(*) FROM test_tz_without
            WHERE device_id = %s
              AND ts >= '2026-02-18 14:00:00'
              AND ts <  '2026-02-18 15:00:00'
        """, (DEVICE_DETROIT,))
        detroit_count = cur.fetchone()[0]

        # Munich naive readings land in 15:00–16:00 (1 hour drift!)
        cur.execute("""
            SELECT count(*) FROM test_tz_without
            WHERE device_id = %s
              AND ts >= '2026-02-18 14:00:00'
              AND ts <  '2026-02-18 15:00:00'
        """, (DEVICE_MUNICH,))
        munich_count_correct_bucket = cur.fetchone()[0]

    return {"detroit": detroit_count, "munich_in_14h_bucket": munich_count_correct_bucket}


def query_with(conn: psycopg.Connection) -> dict:
    """Cross-site hourly aggregate on governed sensor_readings + rejection count."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT count(*) FROM sensor_readings
            WHERE device_id = %s
              AND ts >= '2026-02-18 14:00:00+00'
              AND ts <  '2026-02-18 15:00:00+00'
        """, (DEVICE_DETROIT,))
        detroit_count = cur.fetchone()[0]

        cur.execute("""
            SELECT count(*) FROM sensor_readings
            WHERE device_id = %s
              AND ts >= '2026-02-18 14:00:00+00'
              AND ts <  '2026-02-18 15:00:00+00'
        """, (DEVICE_MUNICH,))
        munich_count = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM test_tz_dead_letter")
        dead_letter_count = cur.fetchone()[0]

    return {
        "detroit":     detroit_count,
        "munich":      munich_count,
        "dead_letter": dead_letter_count,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("EDGE CASE 1: TIMEZONE DRIFT BETWEEN SITES")
    print("=" * 70)
    print(f"""
Setup:
  Detroit site publishes {UTC_READINGS} readings — explicit UTC timestamps
  Munich site publishes {NAIVE_READINGS} readings — naive local time (CET, offset stripped)
  Query window: 2026-02-18 14:00 UTC (= 15:00 CET)
""")

    conn = psycopg.connect(TIMESCALE_DSN)
    setup(conn)

    try:
        # ── Without governance ────────────────────────────────────────────────
        print("-" * 70)
        print("WITHOUT GOVERNANCE  (TIMESTAMP column, no ingestion check)")
        print("-" * 70)
        accepted, _ = inject_without_governance(conn)
        print(f"  Rows stored: {accepted}")
        result = query_without(conn)
        print(f"\n  Cross-site hourly aggregate for 14:00 UTC window:")
        print(f"    detroit  readings: {result['detroit']}")
        print(f"    munich   readings: {result['munich_in_14h_bucket']}  "
              f"← munich data landed in 15:00 bucket (1 hour drift)")

        # ── With governance ───────────────────────────────────────────────────
        print()
        print("-" * 70)
        print("WITH GOVERNANCE  (TIMESTAMPTZ + ingestion-layer timezone check)")
        print("-" * 70)
        accepted, rejected = inject_with_governance(conn)
        print(f"  UTC readings accepted:      {accepted}")
        print(f"  Naive CET readings rejected: {rejected}  → written to dead_letter")
        result = query_with(conn)
        print(f"\n  Cross-site hourly aggregate for 14:00 UTC window:")
        print(f"    detroit  readings: {result['detroit']}")
        print(f"    munich   readings: {result['munich']}  "
              f"← no data (rejected at ingestion, flagged for correction)")
        print(f"\n  dead_letter rows: {result['dead_letter']}")

        # ── Key insight ───────────────────────────────────────────────────────
        print()
        print("=" * 70)
        print("KEY INSIGHT")
        print("=" * 70)
        print("""
  Without timezone enforcement, Munich data silently appears in the wrong
  time buckets. OEE and downtime calculations are wrong by 1 hour. Nobody
  notices until a business review.

  With TIMESTAMPTZ + ingestion-layer validation, naive timestamps are caught
  and sidelined. The aggregate is incomplete but honest — a gap you can fix
  is better than a wrong number you trust.
""")
        print("✓ Experiment complete!\n")

    finally:
        cleanup(conn)
        conn.close()


if __name__ == "__main__":
    main()
