#!/usr/bin/env python3
"""
ISA-95 UNS Publisher

Publishes synthetic sensor readings to MQTT topics that follow the
ISA-95 hierarchy:
    acme_mfg/{site}/{area}/{line}/{cell}

Used by:
  - ingest.py  (run the publisher to generate messages for the happy-path demo)
  - wildcard-demo.py  (imports the device list and topic builder)

Device list mirrors the equipment_context seed data — all 24 devices.
"""

import json
import os
import random
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", 1883))
PUBLISH_RATE_HZ = int(os.getenv("PUBLISH_RATE_HZ", 1))

# Full device list — mirrors db/seed.sql equipment_context rows.
# Each tuple is (site, area, line, cell).
DEVICES = [
    # Detroit / Assembly
    ("detroit", "assembly", "line_01", "plc_temp_01"),
    ("detroit", "assembly", "line_01", "plc_pressure_01"),
    ("detroit", "assembly", "line_01", "plc_vibration_01"),
    ("detroit", "assembly", "line_02", "plc_temp_01"),
    ("detroit", "assembly", "line_02", "plc_pressure_01"),
    ("detroit", "assembly", "line_02", "plc_vibration_01"),
    # Detroit / Packaging
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
    # Munich / Packaging
    ("munich", "packaging", "line_01", "plc_temp_01"),
    ("munich", "packaging", "line_01", "plc_pressure_01"),
    ("munich", "packaging", "line_01", "plc_vibration_01"),
    ("munich", "packaging", "line_02", "plc_temp_01"),
    ("munich", "packaging", "line_02", "plc_pressure_01"),
    ("munich", "packaging", "line_02", "plc_vibration_01"),
]

# Realistic value ranges per sensor type (keyed on cell prefix)
SENSOR_RANGES = {
    "plc_temp":      {"metric_type": "temperature", "min": 65.0,  "max": 82.0},
    "plc_pressure":  {"metric_type": "pressure",    "min": 90.0,  "max": 105.0},
    "plc_vibration": {"metric_type": "vibration",   "min": 0.20,  "max": 0.90},
}


def device_to_topic(site: str, area: str, line: str, cell: str, enterprise: str = "acme_mfg") -> str:
    """Build the ISA-95 MQTT topic for a device."""
    return f"{enterprise}/{site}/{area}/{line}/{cell}"


def generate_reading(site: str, area: str, line: str, cell: str) -> dict:
    """Generate a synthetic sensor reading with a UTC timestamp."""
    sensor_prefix = "_".join(cell.split("_")[:2])  # "plc_temp" from "plc_temp_01"
    sensor = SENSOR_RANGES.get(sensor_prefix, {"metric_type": "unknown", "min": 0, "max": 1})
    return {
        "ts":          datetime.now(timezone.utc).isoformat(),
        "metric_type": sensor["metric_type"],
        "value":       round(random.uniform(sensor["min"], sensor["max"]), 2),
    }


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print(f"✓ Connected to MQTT broker at {BROKER_HOST}:{BROKER_PORT}")
    else:
        print(f"✗ Connection failed: {reason_code}")


def main():
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="uns-publisher",
    )
    client.on_connect = on_connect
    client.connect(BROKER_HOST, BROKER_PORT, 60)
    client.loop_start()
    time.sleep(0.5)

    total_rate = len(DEVICES) * PUBLISH_RATE_HZ
    sleep_per_msg = 1.0 / total_rate

    print(f"\nPublishing to ISA-95 UNS topics:")
    print(f"  Devices: {len(DEVICES)}")
    print(f"  Rate:    {PUBLISH_RATE_HZ} Hz per device  ({total_rate} msg/s total)")
    print(f"  Prefix:  acme_mfg/{{site}}/{{area}}/{{line}}/{{cell}}")
    print(f"\nPress Ctrl+C to stop\n")

    count = 0
    try:
        while True:
            site, area, line, cell = DEVICES[count % len(DEVICES)]
            topic = device_to_topic(site, area, line, cell)
            reading = generate_reading(site, area, line, cell)
            client.publish(topic, json.dumps(reading), qos=1)
            count += 1
            if count % (len(DEVICES) * 10) == 0:
                print(f"  Published {count:,} messages...")
            time.sleep(sleep_per_msg)
    except KeyboardInterrupt:
        print(f"\nStopped. Published {count:,} total messages.")
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
