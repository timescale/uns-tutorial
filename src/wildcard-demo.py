#!/usr/bin/env python3
"""
MQTT Wildcard Subscription Demo

Demonstrates how the ISA-95 topic hierarchy enables precise, role-based
data access through MQTT wildcards — without any application-side filtering.

The same governed UNS structure that enforces data quality also serves as
an access-control layer: each consumer role subscribes to exactly the
data slice it needs.

Three subscriber roles:
  Plant Manager       acme_mfg/detroit/#
    → All data from the Detroit site only

  Assembly Supervisor acme_mfg/+/assembly/#
    → Assembly area data across all sites

  Quality Analyst     acme_mfg/munich/+/+/plc_pressure_01
    → Pressure sensors from Munich, all areas and lines

A publisher sends 200 messages covering the full ISA-95 hierarchy
(both sites, all areas, lines, and sensor types). Each subscriber
counts only the messages that match its wildcard pattern.

Key insight:
  The same governed UNS hierarchy that enforces data quality also enables
  precise, role-based data access through MQTT wildcards. No application-side
  filtering needed — the topic structure does the work.

Refactored from: mqtt-sql-timescale-repo/src/experiments/back-pressure-scaling.py
  (reuses: multiprocessing pattern, Queue/Event coordination, MQTT client setup)
  (removed: all DB write logic — this script only counts messages)
"""

import json
import multiprocessing
import os
import queue
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", 1883))
ENTERPRISE  = "acme_mfg"

# Messages to publish across the full hierarchy before stopping
TOTAL_MESSAGES = 200

# Subscriber role definitions
SUBSCRIBER_ROLES = [
    {
        "name":    "Plant Manager",
        "pattern": f"{ENTERPRISE}/detroit/#",
        "description": "All data from the Detroit site",
    },
    {
        "name":    "Assembly Supervisor",
        "pattern": f"{ENTERPRISE}/+/assembly/#",
        "description": "Assembly area data across all sites",
    },
    {
        "name":    "Quality Analyst",
        "pattern": f"{ENTERPRISE}/munich/+/+/plc_pressure_01",
        "description": "Pressure sensors from Munich, all areas and lines",
    },
]

# Full device list — mirrors publisher.py and seed data (24 devices)
DEVICES = [
    # Detroit / Assembly
    ("detroit", "assembly", "line_01", "plc_temp_01"),
    ("detroit", "assembly", "line_01", "plc_pressure_01"),
    ("detroit", "assembly", "line_01", "plc_vibration_01"),
    ("detroit", "assembly", "line_02", "plc_temp_01"),
    ("detroit", "assembly", "line_02", "plc_pressure_01"),
    ("detroit", "assembly", "line_02", "plc_vibration_01"),
    ("detroit", "packaging", "line_01", "plc_temp_01"),
    ("detroit", "packaging", "line_01", "plc_pressure_01"),
    ("detroit", "packaging", "line_01", "plc_vibration_01"),
    ("detroit", "packaging", "line_02", "plc_temp_01"),
    ("detroit", "packaging", "line_02", "plc_pressure_01"),
    ("detroit", "packaging", "line_02", "plc_vibration_01"),
    # Munich / Assembly
    ("munich", "assembly", "line_01", "plc_temp_01"),
    ("munich", "assembly", "line_01", "plc_pressure_01"),
    ("munich", "assembly", "line_01", "plc_vibration_01"),
    ("munich", "assembly", "line_02", "plc_temp_01"),
    ("munich", "assembly", "line_02", "plc_pressure_01"),
    ("munich", "assembly", "line_02", "plc_vibration_01"),
    ("munich", "packaging", "line_01", "plc_temp_01"),
    ("munich", "packaging", "line_01", "plc_pressure_01"),
    ("munich", "packaging", "line_01", "plc_vibration_01"),
    ("munich", "packaging", "line_02", "plc_temp_01"),
    ("munich", "packaging", "line_02", "plc_pressure_01"),
    ("munich", "packaging", "line_02", "plc_vibration_01"),
]


def expected_count(pattern: str) -> int:
    """
    Calculate how many of the TOTAL_MESSAGES should match a wildcard pattern,
    using the exact round-robin distribution (not a simple average).

    Round-robin across 24 devices with 200 messages: the first
    (TOTAL_MESSAGES % len(DEVICES)) devices get one extra message each.
    """
    base   = TOTAL_MESSAGES // len(DEVICES)
    extras = TOTAL_MESSAGES  % len(DEVICES)   # first N devices get base+1

    total = 0
    for i, (site, area, line, cell) in enumerate(DEVICES):
        topic   = f"{ENTERPRISE}/{site}/{area}/{line}/{cell}"
        per_dev = base + (1 if i < extras else 0)

        if "#" in pattern:
            # Match all segments up to (but not including) '#'; '+' = any one level
            prefix_parts = pattern.split("#")[0].rstrip("/").split("/")
            t_parts      = topic.split("/")
            if (len(t_parts) >= len(prefix_parts) and
                    all(pp == "+" or pp == tp
                        for pp, tp in zip(prefix_parts, t_parts))):
                total += per_dev
        else:
            # All segments must match; '+' = any one level
            p_parts = pattern.split("/")
            t_parts = topic.split("/")
            if (len(p_parts) == len(t_parts) and
                    all(pp == "+" or pp == tp
                        for pp, tp in zip(p_parts, t_parts))):
                total += per_dev

    return total


# ---------------------------------------------------------------------------
# Subscriber process
# ---------------------------------------------------------------------------
def subscriber_process(role: dict, result_queue: multiprocessing.Queue,
                        ready_event: multiprocessing.Event,
                        stop_event: multiprocessing.Event) -> None:
    """
    Subscribe to the role's wildcard pattern and count received messages.
    Signals ready_event once connected, then counts until stop_event is set.
    """
    count = 0

    def on_connect(client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            client.subscribe(role["pattern"], qos=1)
            ready_event.set()

    def on_message(client, userdata, message, properties=None):
        nonlocal count
        count += 1

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"subscriber-{role['name'].lower().replace(' ', '-')}",
    )
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER_HOST, BROKER_PORT, 60)
    client.loop_start()

    stop_event.wait()
    # Brief pause to catch any in-flight messages before disconnecting
    time.sleep(0.3)

    client.loop_stop()
    client.disconnect()
    result_queue.put({"name": role["name"], "pattern": role["pattern"], "count": count})


# ---------------------------------------------------------------------------
# Publisher process
# ---------------------------------------------------------------------------
def publisher_process(total: int, done_event: multiprocessing.Event) -> None:
    """
    Publish `total` messages round-robin across all 24 ISA-95 devices.
    Sets done_event when finished.
    """
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="wildcard-publisher",
    )
    client.connect(BROKER_HOST, BROKER_PORT, 60)
    client.loop_start()
    time.sleep(0.2)  # wait for connection

    for i in range(total):
        site, area, line, cell = DEVICES[i % len(DEVICES)]
        topic = f"{ENTERPRISE}/{site}/{area}/{line}/{cell}"
        payload = json.dumps({
            "ts":          datetime.now(timezone.utc).isoformat(),
            "metric_type": cell.split("_")[1],   # "temp", "pressure", "vibration"
            "value":       round(20.0 + i * 0.01, 2),
        })
        client.publish(topic, payload, qos=1)

    # Ensure all QoS 1 messages are acknowledged before signalling done
    time.sleep(0.5)
    client.loop_stop()
    client.disconnect()
    done_event.set()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("WILDCARD SUBSCRIPTION DEMO")
    print("=" * 70)
    print(f"""
Publisher:  {TOTAL_MESSAGES} messages across the full ISA-95 hierarchy
            ({len(DEVICES)} devices: 2 sites × 2 areas × 2 lines × 3 sensors)

Subscribers:""")
    for role in SUBSCRIBER_ROLES:
        print(f"  {role['name']:<22}  {role['pattern']}")
        print(f"  {'':22}  → {role['description']}")
    print()

    # ── Spawn subscriber processes ────────────────────────────────────────────
    result_queue = multiprocessing.Queue()
    stop_event   = multiprocessing.Event()
    processes    = []

    for role in SUBSCRIBER_ROLES:
        ready_event = multiprocessing.Event()
        p = multiprocessing.Process(
            target=subscriber_process,
            args=(role, result_queue, ready_event, stop_event),
        )
        p.start()
        processes.append(p)
        # Wait for this subscriber to connect and subscribe before publishing
        if not ready_event.wait(timeout=10):
            print(f"  [Warning] {role['name']} did not connect within timeout")

    print("All subscribers connected. Publishing messages...\n")

    # ── Publish messages ──────────────────────────────────────────────────────
    done_event = multiprocessing.Event()
    pub = multiprocessing.Process(
        target=publisher_process,
        args=(TOTAL_MESSAGES, done_event),
    )
    pub.start()
    done_event.wait(timeout=30)
    pub.join(timeout=5)

    # Signal subscribers to stop and collect results
    stop_event.set()
    for p in processes:
        p.join(timeout=5)
        if p.is_alive():
            p.terminate()

    # ── Results ───────────────────────────────────────────────────────────────
    results = {}
    while True:
        try:
            r = result_queue.get_nowait()
            results[r["name"]] = r
        except queue.Empty:
            break

    print(f"Published {TOTAL_MESSAGES} messages across full UNS hierarchy\n")
    print("-" * 70)
    print(f"{'Subscriber':<24}  {'Pattern':<44}  {'Received':>8}")
    print("-" * 70)
    for role in SUBSCRIBER_ROLES:
        name = role["name"]
        r = results.get(name, {})
        received = r.get("count", "?")
        exp = expected_count(role["pattern"])
        note = f"(expected ~{exp})"
        print(f"  {name:<22}  {role['pattern']:<44}  {received:>6}  {note}")
    print("-" * 70)
    print(f"  Total published:  {TOTAL_MESSAGES}")
    print()

    # ── Key insight ───────────────────────────────────────────────────────────
    print("=" * 70)
    print("KEY INSIGHT")
    print("=" * 70)
    print("""
  The same governed UNS hierarchy that enforces data quality also enables
  precise, role-based data access through MQTT wildcards.

    acme_mfg/detroit/#              → Plant Manager sees Detroit only
    acme_mfg/+/assembly/#           → Supervisor sees assembly across all sites
    acme_mfg/munich/+/+/plc_pressure_01  → Analyst sees pressure at Munich only

  No application-side filtering needed. The topic structure does the work —
  but only because the hierarchy is governed and consistent.
""")
    print("✓ Demo complete!\n")


if __name__ == "__main__":
    main()
