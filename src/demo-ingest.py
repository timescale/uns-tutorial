#!/usr/bin/env python3
"""
Governed Ingestion Demo — Single Terminal

Shows ungoverned vs governed ingestion side-by-side.
A single publisher sends 240 messages — 192 valid and 48 intentionally
bad — to an MQTT broker. Two handlers subscribe simultaneously:

  Ungoverned  →  demo_ingest_without   (no validation, stores everything)
  Governed    →  sensor_readings       (4 checks, valid rows only)
               + dead_letter           (invalid rows + rejection reason)

Invalid message mix (3 failure types × 16 messages each):
  • Unregistered device  — ghost device not in equipment_context
  • Missing field        — 'value' dropped (firmware bug simulation)
  • Naive timestamp      — no timezone offset (CET misconfiguration)

Run with:
  uv run src/demo-ingest.py
"""

import json
import multiprocessing
import os
import queue
import random
import time
from datetime import datetime, timezone

import psycopg
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

BROKER_HOST   = os.getenv("MQTT_BROKER_HOST", "localhost")
BROKER_PORT   = int(os.getenv("MQTT_BROKER_PORT", 1883))
TIMESCALE_DSN = os.getenv("TIMESCALE_DSN", "postgresql://postgres:postgres@localhost:5432/uns_demo")
ENTERPRISE    = "acme_mfg"

# Message counts (pattern: 4 valid + 1 invalid → 80/20 split)
TOTAL_VALID      = 192   # 4 per cycle × 48 cycles
INVALID_UNREG    = 16    # unregistered device
INVALID_MISSING  = 16    # missing 'value' field
INVALID_NAIVE    = 16    # naive (tz-free) timestamp
TOTAL_MESSAGES   = TOTAL_VALID + INVALID_UNREG + INVALID_MISSING + INVALID_NAIVE  # 240

# 24 registered devices — mirrors db/seed.sql + publisher.py
DEVICES = [
    ("detroit", "assembly",  "line_01", "plc_temp_01"),
    ("detroit", "assembly",  "line_01", "plc_pressure_01"),
    ("detroit", "assembly",  "line_01", "plc_vibration_01"),
    ("detroit", "assembly",  "line_02", "plc_temp_01"),
    ("detroit", "assembly",  "line_02", "plc_pressure_01"),
    ("detroit", "assembly",  "line_02", "plc_vibration_01"),
    ("detroit", "packaging", "line_01", "plc_temp_01"),
    ("detroit", "packaging", "line_01", "plc_pressure_01"),
    ("detroit", "packaging", "line_01", "plc_vibration_01"),
    ("detroit", "packaging", "line_02", "plc_temp_01"),
    ("detroit", "packaging", "line_02", "plc_pressure_01"),
    ("detroit", "packaging", "line_02", "plc_vibration_01"),
    ("munich",  "assembly",  "line_01", "plc_temp_01"),
    ("munich",  "assembly",  "line_01", "plc_pressure_01"),
    ("munich",  "assembly",  "line_01", "plc_vibration_01"),
    ("munich",  "assembly",  "line_02", "plc_temp_01"),
    ("munich",  "assembly",  "line_02", "plc_pressure_01"),
    ("munich",  "assembly",  "line_02", "plc_vibration_01"),
    ("munich",  "packaging", "line_01", "plc_temp_01"),
    ("munich",  "packaging", "line_01", "plc_pressure_01"),
    ("munich",  "packaging", "line_01", "plc_vibration_01"),
    ("munich",  "packaging", "line_02", "plc_temp_01"),
    ("munich",  "packaging", "line_02", "plc_pressure_01"),
    ("munich",  "packaging", "line_02", "plc_vibration_01"),
]

SENSOR_RANGES = {
    "plc_temp":      {"metric_type": "temperature", "min": 65.0,  "max": 82.0},
    "plc_pressure":  {"metric_type": "pressure",    "min": 90.0,  "max": 105.0},
    "plc_vibration": {"metric_type": "vibration",   "min": 0.20,  "max": 0.90},
}


# ---------------------------------------------------------------------------
# Message sequence builder
# ---------------------------------------------------------------------------
def _sensor_for(cell: str) -> dict:
    prefix = "_".join(cell.split("_")[:2])   # "plc_temp" from "plc_temp_01"
    return SENSOR_RANGES.get(prefix, {"metric_type": "temperature", "min": 65.0, "max": 82.0})


def build_messages() -> list[tuple[str, dict]]:
    """
    Build the 240-message sequence interleaving valid and invalid messages.
    Pattern: positions 0–3 valid, position 4 invalid; repeats 48 times.
    Invalid types cycle: unregistered → missing_field → naive_ts → repeat.
    """
    messages = []
    valid_idx   = 0
    invalid_idx = 0

    for i in range(TOTAL_MESSAGES):
        if (i + 1) % 5 == 0:
            # Invalid message — cycle through 3 failure types
            failure_type = invalid_idx % 3
            site, area, line, cell = DEVICES[invalid_idx % len(DEVICES)]
            sensor = _sensor_for(cell)

            if failure_type == 0:
                # Unregistered device — topic not in equipment_context
                topic   = f"{ENTERPRISE}/ghost/factory/line_01/plc_temp_01"
                payload = {
                    "ts":          datetime.now(timezone.utc).isoformat(),
                    "metric_type": "temperature",
                    "value":       round(random.uniform(65.0, 82.0), 2),
                }
            elif failure_type == 1:
                # Missing required field — 'value' dropped (firmware bug)
                topic   = f"{ENTERPRISE}/{site}/{area}/{line}/{cell}"
                payload = {
                    "ts":          datetime.now(timezone.utc).isoformat(),
                    "metric_type": sensor["metric_type"],
                    # 'value' intentionally absent
                }
            else:
                # Naive timestamp — no timezone offset (CET misconfiguration)
                topic   = f"{ENTERPRISE}/{site}/{area}/{line}/{cell}"
                payload = {
                    "ts":          datetime.now().isoformat(),   # naive — no tz!
                    "metric_type": sensor["metric_type"],
                    "value":       round(random.uniform(sensor["min"], sensor["max"]), 2),
                }

            invalid_idx += 1
            messages.append((topic, payload))

        else:
            # Valid message
            site, area, line, cell = DEVICES[valid_idx % len(DEVICES)]
            sensor = _sensor_for(cell)
            topic  = f"{ENTERPRISE}/{site}/{area}/{line}/{cell}"
            payload = {
                "ts":          datetime.now(timezone.utc).isoformat(),
                "metric_type": sensor["metric_type"],
                "value":       round(random.uniform(sensor["min"], sensor["max"]), 2),
            }
            valid_idx += 1
            messages.append((topic, payload))

    return messages


# ---------------------------------------------------------------------------
# Setup / cleanup  (demo_ingest_without is ephemeral — created and dropped here)
# ---------------------------------------------------------------------------
def setup(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS demo_ingest_without CASCADE")
        cur.execute("""
            CREATE TABLE demo_ingest_without (
                id          BIGSERIAL    PRIMARY KEY,
                received_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                topic       TEXT,
                payload_raw JSONB
            )
        """)
    conn.commit()


def cleanup(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS demo_ingest_without CASCADE")
    conn.commit()


# ---------------------------------------------------------------------------
# Shared validation logic (mirrors ingest.py)
# ---------------------------------------------------------------------------
def load_known_devices(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT uns_path FROM equipment_context")
        return {row[0] for row in cur.fetchall()}


def parse_topic(topic: str) -> str:
    """Returns uns_path, raises ValueError if not exactly 5 ISA-95 segments."""
    parts = topic.split("/")
    if len(parts) != 5:
        raise ValueError(
            f"Bad topic structure: '{topic}' (expected enterprise/site/area/line/cell)"
        )
    return topic


def validate_payload(payload: dict) -> None:
    for field in ("ts", "metric_type", "value"):
        if field not in payload:
            raise ValueError(f"Missing required field: '{field}'")
    try:
        ts = datetime.fromisoformat(str(payload["ts"]))
    except (ValueError, TypeError):
        raise ValueError(f"Cannot parse timestamp: '{payload['ts']}'")
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(
            f"Timestamp '{payload['ts']}' has no timezone offset — "
            f"must be UTC (e.g. 2026-02-19T14:00:00+00:00)"
        )
    if not isinstance(payload["value"], (int, float)):
        raise ValueError(f"'value' must be numeric, got {type(payload['value']).__name__}")


# ---------------------------------------------------------------------------
# Publisher process
# ---------------------------------------------------------------------------
def publisher_process(messages: list, done_event: multiprocessing.Event) -> None:
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="demo-ingest-publisher",
    )
    client.connect(BROKER_HOST, BROKER_PORT, 60)
    client.loop_start()
    time.sleep(0.3)

    for topic, payload in messages:
        client.publish(topic, json.dumps(payload), qos=1)
        time.sleep(0.01)   # ~100 msg/s — gives ingest handlers time to keep up

    time.sleep(0.5)   # let QoS 1 ACKs settle before disconnecting
    client.loop_stop()
    client.disconnect()
    done_event.set()


# ---------------------------------------------------------------------------
# Ungoverned ingest process — stores every message without validation
# ---------------------------------------------------------------------------
def ungoverned_process(result_queue: multiprocessing.Queue,
                        ready_event:  multiprocessing.Event,
                        stop_event:   multiprocessing.Event) -> None:
    conn     = psycopg.connect(TIMESCALE_DSN)
    stored   = 0

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            client.subscribe(f"{ENTERPRISE}/#", qos=1)
            ready_event.set()

    def on_message(client, userdata, message, properties=None):
        nonlocal stored
        try:
            payload = json.loads(message.payload.decode())
        except Exception:
            payload = None
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO demo_ingest_without (topic, payload_raw) VALUES (%s, %s)",
                (message.topic, json.dumps(payload) if payload is not None else None),
            )
        conn.commit()
        stored += 1

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="demo-ingest-ungoverned",
    )
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER_HOST, BROKER_PORT, 60)
    client.loop_start()

    stop_event.wait()
    time.sleep(0.5)
    client.loop_stop()
    client.disconnect()
    conn.close()

    result_queue.put({"handler": "ungoverned", "stored": stored})


# ---------------------------------------------------------------------------
# Governed ingest process — validates, accepts or dead-letters
# ---------------------------------------------------------------------------
def governed_process(result_queue: multiprocessing.Queue,
                      ready_event:  multiprocessing.Event,
                      stop_event:   multiprocessing.Event) -> None:
    conn          = psycopg.connect(TIMESCALE_DSN)
    known_devices = load_known_devices(conn)
    accepted      = 0
    rejected      = 0

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            client.subscribe(f"{ENTERPRISE}/#", qos=1)
            ready_event.set()

    def on_message(client, userdata, message, properties=None):
        nonlocal accepted, rejected
        topic = message.topic
        raw   = None

        try:
            raw      = json.loads(message.payload.decode())
            uns_path = parse_topic(topic)

            if uns_path not in known_devices:
                raise ValueError(
                    f"Unknown device '{uns_path}' — not registered in equipment_context"
                )

            validate_payload(raw)

            ts = datetime.fromisoformat(str(raw["ts"]))
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sensor_readings (ts, device_id, metric_type, value) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (ts, device_id, metric_type) DO NOTHING",
                    (ts, uns_path, raw["metric_type"], float(raw["value"])),
                )
            conn.commit()
            accepted += 1

        except (json.JSONDecodeError, ValueError) as e:
            rejected += 1
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO dead_letter (topic, payload, reason) VALUES (%s, %s, %s)",
                        (topic, json.dumps(raw) if raw is not None else None, str(e)),
                    )
                conn.commit()
            except Exception:
                conn.rollback()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="demo-ingest-governed",
    )
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER_HOST, BROKER_PORT, 60)
    client.loop_start()

    stop_event.wait()
    time.sleep(0.5)
    client.loop_stop()
    client.disconnect()
    conn.close()

    result_queue.put({"handler": "governed", "accepted": accepted, "rejected": rejected})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    demo_start = datetime.now(timezone.utc)

    print("=" * 70)
    print("GOVERNED INGESTION DEMO")
    print("=" * 70)
    print(f"""
  Publishing {TOTAL_MESSAGES} messages through two ingest handlers simultaneously.

  Message mix:
    Valid     {TOTAL_VALID:>4}   registered devices · UTC timestamps · all fields present
    Invalid   {TOTAL_MESSAGES - TOTAL_VALID:>4}   three failure types × {INVALID_UNREG} messages each:
                  • Unregistered device  — ghost device not in equipment_context
                  • Missing field        — 'value' dropped (firmware bug)
                  • Naive timestamp      — no timezone offset (CET misconfiguration)

  Handlers:
    Ungoverned  →  demo_ingest_without   (no validation — stores everything)
    Governed    →  sensor_readings       (4 checks — valid rows only)
                   dead_letter           (rejected rows + reason)
""")

    conn     = psycopg.connect(TIMESCALE_DSN)
    setup(conn)
    messages = build_messages()

    # ── Spawn ingest handlers ─────────────────────────────────────────────────
    result_queue = multiprocessing.Queue()
    stop_event   = multiprocessing.Event()
    ungov_ready  = multiprocessing.Event()
    gov_ready    = multiprocessing.Event()

    ungov_proc = multiprocessing.Process(
        target=ungoverned_process,
        args=(result_queue, ungov_ready, stop_event),
    )
    gov_proc = multiprocessing.Process(
        target=governed_process,
        args=(result_queue, gov_ready, stop_event),
    )
    ungov_proc.start()
    gov_proc.start()

    if not ungov_ready.wait(timeout=10):
        print("  [Warning] Ungoverned handler did not connect in time")
    if not gov_ready.wait(timeout=10):
        print("  [Warning] Governed handler did not connect in time")
    print("  Both handlers connected. Publishing messages...\n")

    # ── Publish ───────────────────────────────────────────────────────────────
    done_event = multiprocessing.Event()
    pub_proc   = multiprocessing.Process(
        target=publisher_process,
        args=(messages, done_event),
    )
    pub_proc.start()
    done_event.wait(timeout=60)
    pub_proc.join(timeout=5)

    print(f"  Published {TOTAL_MESSAGES} messages. Collecting results...\n")
    time.sleep(1.0)   # let any in-flight messages land before stopping

    # ── Collect results ───────────────────────────────────────────────────────
    stop_event.set()
    for proc in (ungov_proc, gov_proc):
        proc.join(timeout=10)
        if proc.is_alive():
            proc.terminate()

    results = {}
    while True:
        try:
            r = result_queue.get_nowait()
            results[r["handler"]] = r
        except queue.Empty:
            break

    ungov = results.get("ungoverned", {})
    gov   = results.get("governed",   {})

    # Dead-letter breakdown from DB (categorised by reason pattern)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                CASE
                    WHEN reason LIKE '%%not registered%%'      THEN 'unregistered device'
                    WHEN reason LIKE '%%Missing required%%'    THEN 'missing field'
                    WHEN reason LIKE '%%timezone%%'            THEN 'naive timestamp'
                    ELSE 'other'
                END AS category,
                count(*) AS cnt
            FROM dead_letter
            WHERE received_at >= %s
            GROUP BY 1
            ORDER BY cnt DESC
        """, (demo_start,))
        dl_breakdown = {row[0]: row[1] for row in cur.fetchall()}

    # ── Results table ─────────────────────────────────────────────────────────
    w = 22
    print("-" * 70)
    print(f"  {'':36}  {'Without':>{w}}  {'With':>{w}}")
    print(f"  {'':36}  {'Governance':>{w}}  {'Governance':>{w}}")
    print("-" * 70)
    print(f"  {'Rows written to DB':<36}  {ungov.get('stored', '?'):>{w}}  {gov.get('accepted', '?'):>{w}}")
    print(f"  {'Rows rejected':<36}  {'0':>{w}}  {gov.get('rejected', '?'):>{w}}")
    print()
    print(f"  {'dead_letter breakdown':36}  {'':>{w}}")
    for label in ("unregistered device", "missing field", "naive timestamp"):
        count = dl_breakdown.get(label, 0)
        print(f"  {'  ' + label:<36}  {'—':>{w}}  {count:>{w}}")
    print("-" * 70)

    # ── Key insight ───────────────────────────────────────────────────────────
    total_rejected = gov.get("rejected", TOTAL_MESSAGES - TOTAL_VALID)
    print()
    print("=" * 70)
    print("KEY INSIGHT")
    print("=" * 70)
    print(f"""
  Both handlers receive the same {TOTAL_MESSAGES} messages from the MQTT broker.

  Without governance: all {ungov.get('stored', TOTAL_MESSAGES)} rows land in the database — including
  {TOTAL_MESSAGES - TOTAL_VALID} that carry bad data. Unregistered devices, missing fields, and
  naive timestamps store silently. Downstream queries return wrong results.

  With governance: {gov.get('accepted', TOTAL_VALID)} valid rows in sensor_readings. {total_rejected} rows in
  dead_letter with exact rejection reasons. The database stays clean
  and every failure is visible and actionable.
""")
    print("✓ Demo complete!\n")

    # Cleanup ephemeral test table
    cleanup(conn)
    conn.close()


if __name__ == "__main__":
    main()
