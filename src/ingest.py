#!/usr/bin/env python3
"""
UNS Governance Ingestion — Happy-Path Reference Implementation

Subscribes to the ISA-95 MQTT topic hierarchy and writes governed sensor
readings to TimescaleDB. This is the reference implementation; the
edge-case scripts (src/edge-cases/) show what happens when governance breaks.

Governance checks applied before every insert:
  1. Topic path must parse into exactly 5 ISA-95 segments.
  2. Device must exist in equipment_context (validated at startup).
  3. Payload must contain ts, metric_type, and value.
  4. Timestamp must carry an explicit UTC offset (no naive datetimes).

Payloads that fail any check are written to the dead_letter table for
investigation rather than silently dropped.

Run alongside the publisher:
  uv run src/ingest.py &
  uv run src/publisher.py
"""

import json
import os
import signal
import threading
import time
from datetime import datetime, timezone, timedelta

import psycopg
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

BROKER_HOST      = os.getenv("MQTT_BROKER_HOST", "localhost")
BROKER_PORT      = int(os.getenv("MQTT_BROKER_PORT", 1883))
INGEST_TOPIC     = os.getenv("INGEST_TOPIC", "acme_mfg/#")
FLUSH_INTERVAL   = float(os.getenv("FLUSH_INTERVAL_SEC", 5.0))
TIMESCALE_DSN    = os.getenv("TIMESCALE_DSN", "postgresql://postgres:postgres@localhost:5432/uns_demo")

# Thread-safe buffer for batched writes
buffer: list[tuple] = []
buffer_lock = threading.Lock()
shutdown_flag = threading.Event()

# Metrics
accepted = 0
rejected = 0
batch_count = 0


# ---------------------------------------------------------------------------
# Startup: load known devices from equipment_context into a set for O(1) lookup.
# Avoids a DB round-trip per message.
# ---------------------------------------------------------------------------
def load_known_devices(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT uns_path FROM equipment_context")
        return {row[0] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Topic parsing
# ---------------------------------------------------------------------------
def strip_version_prefix(topic: str) -> str:
    """
    Strip an optional version prefix from a UNS topic.

    Handles both bare topics and version-prefixed topics:
      acme_mfg/detroit/...        → unchanged
      v1/acme_mfg/detroit/...     → acme_mfg/detroit/...
    """
    parts = topic.split("/")
    if parts and parts[0].startswith("v") and parts[0][1:].isdigit():
        return "/".join(parts[1:])
    return topic


def parse_topic(topic: str) -> dict:
    """
    Parse an ISA-95 topic path into its five hierarchy segments.

    Expected structure after version stripping:
      {enterprise}/{site}/{area}/{line}/{cell}

    Returns a dict with keys: enterprise, site, area, line, cell, uns_path.
    Raises ValueError if the path does not have exactly 5 segments.
    """
    clean = strip_version_prefix(topic)
    parts = clean.split("/")
    if len(parts) != 5:
        raise ValueError(
            f"Expected 5 path segments (enterprise/site/area/line/cell), "
            f"got {len(parts)} in topic '{topic}'"
        )
    enterprise, site, area, line, cell = parts
    return {
        "enterprise": enterprise,
        "site":       site,
        "area":       area,
        "line":       line,
        "cell":       cell,
        "uns_path":   clean,
    }


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------
def validate_payload(payload: dict) -> None:
    """
    Validate a sensor reading payload.

    Checks:
      - Required fields are present: ts, metric_type, value
      - ts carries an explicit timezone offset (no naive datetimes)

    Raises ValueError with a descriptive reason on failure.
    """
    for field in ("ts", "metric_type", "value"):
        if field not in payload:
            raise ValueError(f"Missing required field: '{field}'")

    # Parse timestamp and verify it has timezone info
    try:
        ts = datetime.fromisoformat(str(payload["ts"]))
    except (ValueError, TypeError):
        raise ValueError(f"Cannot parse timestamp: '{payload['ts']}'")

    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(
            f"Timestamp '{payload['ts']}' has no timezone offset. "
            f"All timestamps must be UTC (e.g. 2026-02-18T14:00:00+00:00)."
        )

    if not isinstance(payload["value"], (int, float)):
        raise ValueError(
            f"Field 'value' must be numeric, got {type(payload['value']).__name__}"
        )


# ---------------------------------------------------------------------------
# Dead-letter writer (direct insert, not batched — rejections are rare)
# ---------------------------------------------------------------------------
def dead_letter_write(conn: psycopg.Connection, topic: str, payload: dict | None, reason: str) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO dead_letter (topic, payload, reason) VALUES (%s, %s, %s)",
                (topic, json.dumps(payload) if payload is not None else None, reason),
            )
        conn.commit()
    except Exception as e:
        print(f"  [dead_letter] Failed to write rejection: {e}")


# ---------------------------------------------------------------------------
# Batch flush (COPY + staging → ON CONFLICT dedup)
# Reuses the same COPY pattern from the MQTT batching tutorial.
# ---------------------------------------------------------------------------
def flush_buffer(db_conn: psycopg.Connection) -> None:
    global batch_count
    if shutdown_flag.is_set():
        return

    with buffer_lock:
        if not buffer:
            if not shutdown_flag.is_set():
                threading.Timer(FLUSH_INTERVAL, lambda: flush_buffer(db_conn)).start()
            return
        batch = buffer[:]
        buffer.clear()

    start = time.time()
    try:
        with db_conn.cursor() as cur:
            cur.execute(
                "CREATE TEMP TABLE tmp_ingest "
                "(LIKE sensor_readings INCLUDING DEFAULTS) ON COMMIT DROP"
            )
            with cur.copy("COPY tmp_ingest (ts, device_id, metric_type, value) FROM STDIN") as copy:
                for row in batch:
                    copy.write_row(row)
            cur.execute("""
                INSERT INTO sensor_readings (ts, device_id, metric_type, value)
                SELECT ts, device_id, metric_type, value FROM tmp_ingest
                ON CONFLICT (ts, device_id, metric_type) DO NOTHING
            """)
        db_conn.commit()

        batch_count += 1
        latency_ms = (time.time() - start) * 1000
        print(f"  Batch {batch_count:3d} | {len(batch):4d} rows | {latency_ms:5.1f}ms | "
              f"accepted: {accepted:,}  rejected: {rejected:,}")

    except Exception as e:
        db_conn.rollback()
        print(f"  [flush] Error: {e}")

    if not shutdown_flag.is_set():
        threading.Timer(FLUSH_INTERVAL, lambda: flush_buffer(db_conn)).start()


# ---------------------------------------------------------------------------
# MQTT callbacks
# ---------------------------------------------------------------------------
def make_on_message(known_devices: set[str], db_conn: psycopg.Connection):
    def on_message(client, userdata, message, properties=None):
        global accepted, rejected
        topic = message.topic
        raw = None

        try:
            raw = json.loads(message.payload.decode())

            # 1. Parse and validate topic path
            parsed = parse_topic(topic)

            # 2. Check device is registered in equipment_context
            if parsed["uns_path"] not in known_devices:
                raise ValueError(
                    f"Unknown device '{parsed['uns_path']}' — "
                    f"not registered in equipment_context"
                )

            # 3. Validate payload fields and timestamp
            validate_payload(raw)

            # 4. Enqueue for batched write
            ts = datetime.fromisoformat(str(raw["ts"]))
            with buffer_lock:
                buffer.append((ts, parsed["uns_path"], raw["metric_type"], float(raw["value"])))
            accepted += 1

        except (json.JSONDecodeError, ValueError) as e:
            rejected += 1
            dead_letter_write(db_conn, topic, raw, str(e))

    return on_message


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(INGEST_TOPIC)
        print(f"✓ Subscribed to {INGEST_TOPIC}")
    else:
        print(f"✗ MQTT connection failed: {reason_code}")


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
def make_signal_handler(db_conn: psycopg.Connection):
    def handler(signum, frame):
        global batch_count
        print("\n\nShutdown signal received — flushing buffer...")
        shutdown_flag.set()

        with buffer_lock:
            if buffer:
                batch = buffer[:]
                buffer.clear()
                try:
                    with db_conn.cursor() as cur:
                        cur.execute(
                            "CREATE TEMP TABLE tmp_ingest "
                            "(LIKE sensor_readings INCLUDING DEFAULTS) ON COMMIT DROP"
                        )
                        with cur.copy(
                            "COPY tmp_ingest (ts, device_id, metric_type, value) FROM STDIN"
                        ) as copy:
                            for row in batch:
                                copy.write_row(row)
                        cur.execute("""
                            INSERT INTO sensor_readings (ts, device_id, metric_type, value)
                            SELECT ts, device_id, metric_type, value FROM tmp_ingest
                            ON CONFLICT (ts, device_id, metric_type) DO NOTHING
                        """)
                    db_conn.commit()
                    batch_count += 1
                    print(f"✓ Flushed {len(batch)} remaining rows")
                except Exception as e:
                    print(f"✗ Final flush failed: {e}")

        print(f"\nSummary:")
        print(f"  Accepted: {accepted:,}")
        print(f"  Rejected: {rejected:,}  (check dead_letter table)")
        print(f"  Batches:  {batch_count}")

        db_conn.close()
        import os as _os; _os.exit(0)

    return handler


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("UNS GOVERNANCE INGESTION")
    print("=" * 70)
    print(f"\n  Broker:         {BROKER_HOST}:{BROKER_PORT}")
    print(f"  Topic:          {INGEST_TOPIC}")
    print(f"  Flush interval: {FLUSH_INTERVAL}s")

    db_conn = psycopg.connect(TIMESCALE_DSN)
    known_devices = load_known_devices(db_conn)
    print(f"  Known devices:  {len(known_devices)} loaded from equipment_context\n")

    signal.signal(signal.SIGINT,  make_signal_handler(db_conn))
    signal.signal(signal.SIGTERM, make_signal_handler(db_conn))

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="uns-ingest",
    )
    client.on_connect = on_connect
    client.on_message = make_on_message(known_devices, db_conn)
    client.connect(BROKER_HOST, BROKER_PORT, 60)

    threading.Timer(FLUSH_INTERVAL, lambda: flush_buffer(db_conn)).start()
    print("Press Ctrl+C to stop\n")
    client.loop_forever()


if __name__ == "__main__":
    main()
