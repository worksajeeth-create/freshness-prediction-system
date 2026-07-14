"""
calib_common.py — Shared helpers for FoodMon calibration scripts.
Imported by calib_temp_humidity.py, calib_co2.py, calib_mq.py
"""

import csv
import os
import time
from collections import defaultdict
from pathlib import Path

import requests

# ── Config ────────────────────────────────────────────────────────────
FLASK_URL = "http://localhost:5000"
LOG_FILE  = Path("calibration_log.csv")

CSV_FIELDS = [
    "cycle_number",
    "sensor_id",
    "condition",
    "sensor_reading",
    "reference_value",
    "reference_source",
    "abs_error",
    "error_pct",
    "pass_fail",
    "notes",
    "timestamp",
]

SENSOR_LABELS = {
    "temperature": "Temperature (DHT22/AM2301)",
    "humidity":    "Humidity    (DHT22/AM2301)",
    "co2":         "CO2         (MH-Z19C)",
    "mq2":         "MQ-2        (LPG/Smoke)",
    "mq3":         "MQ-3        (Alcohol)",
    "mq4":         "MQ-4        (Methane)",
    "mq135":       "MQ-135      (VOCs/NH3)",
    "mq136":       "MQ-136      (H2S)",
    "mq137":       "MQ-137      (NH3)",
}


# ── Terminal helpers ───────────────────────────────────────────────────
def clear():
    os.system("clear" if os.name != "nt" else "cls")


def banner(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def pause(msg="Press ENTER when ready..."):
    input(f"\n  >>  {msg}")


# ── Flask / sensor access ──────────────────────────────────────────────
def fetch_sensor_data() -> dict:
    try:
        r = requests.get(f"{FLASK_URL}/api/current_data", timeout=5)
        r.raise_for_status()
        data = r.json()
        sd   = data.get("sensor_data", {})
        flat = {
            "temperature": sd.get("temperature"),
            "humidity":    sd.get("humidity"),
        }
        gases = sd.get("gases", {})
        for g in ["mq2", "mq3", "mq4", "mq135", "mq136", "mq137", "co2"]:
            raw = gases.get(g)
            if isinstance(raw, dict):
                flat[g] = raw.get("value")
            elif raw is not None:
                flat[g] = float(raw)
            else:
                flat[g] = None
        return flat
    except Exception as e:
        print(f"\n  [!] Could not reach Flask app: {e}")
        print("      Make sure 'python app.py' is running.")
        return {}


def get_reading(sensor_id: str) -> float | None:
    for _ in range(3):
        val = fetch_sensor_data().get(sensor_id)
        if val is not None:
            return float(val)
        print(f"  [!] No reading for {sensor_id} — retrying in 3 s...")
        time.sleep(3)
    return None


def check_flask() -> bool:
    print("  Checking Flask connection...", end=" ", flush=True)
    if fetch_sensor_data():
        print("OK")
        return True
    print("FAILED")
    print("  Start 'python app.py' in the backend folder, then re-run.")
    return False


# ── Input helpers ──────────────────────────────────────────────────────
def ask_float(prompt: str) -> float:
    while True:
        try:
            return float(input(f"\n  Enter {prompt}: "))
        except ValueError:
            print("  Please enter a number.")


def pf(error_val: float, tolerance: float) -> str:
    return "PASS" if error_val <= tolerance else "FAIL"


# ── CSV helpers ────────────────────────────────────────────────────────
def open_log():
    """Return (file_handle, csv_writer). Appends to existing log."""
    file_exists = LOG_FILE.exists()
    fh = open(LOG_FILE, "a", newline="")
    writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
    if not file_exists:
        writer.writeheader()
    return fh, writer


def next_cycle_number() -> int:
    """Return the next integer cycle number based on existing log rows."""
    if not LOG_FILE.exists():
        return 1
    highest = 0
    with open(LOG_FILE, newline="") as f:
        for row in csv.DictReader(f):
            try:
                n = int(row["cycle_number"])
                if n > highest:
                    highest = n
            except (ValueError, KeyError):
                pass
    return highest + 1


# ── Summary printer (shared across all scripts) ────────────────────────
def print_summary(subset_sensors=None):
    """Print accuracy table for the given sensors (or all if None)."""
    if not LOG_FILE.exists():
        print("  No calibration_log.csv found yet.")
        return

    stats = defaultdict(lambda: {"pass": 0, "fail": 0, "errors": []})
    with open(LOG_FILE, newline="") as f:
        for row in csv.DictReader(f):
            sid = row["sensor_id"]
            if subset_sensors and sid not in subset_sensors:
                continue
            try:
                epct = float(row["error_pct"])
            except (ValueError, TypeError):
                epct = 0.0
            stats[sid]["errors"].append(epct)
            if row["pass_fail"] == "PASS":
                stats[sid]["pass"] += 1
            else:
                stats[sid]["fail"] += 1

    if not stats:
        print("  No data found for requested sensors.")
        return

    print("\n" + "=" * 70)
    print(f"  {'Sensor':<34} {'Cycles':>6}  {'Pass':>5}  {'Fail':>5}  {'Accuracy':>9}  Status")
    print("-" * 70)

    total_p = total_f = 0
    order = subset_sensors if subset_sensors else list(SENSOR_LABELS.keys())
    for sid in order:
        label = SENSOR_LABELS.get(sid, sid)
        if sid not in stats:
            print(f"  {label:<34} {'--':>6}  {'--':>5}  {'--':>5}  {'--':>9}  (no data)")
            continue
        s   = stats[sid]
        tot = s["pass"] + s["fail"]
        acc = s["pass"] / tot * 100 if tot > 0 else 0
        ok  = "PASS" if acc >= 95 else "FAIL"
        print(f"  {label:<34} {tot:>6}  {s['pass']:>5}  {s['fail']:>5}  {acc:>8.1f}%  {ok}")
        total_p += s["pass"]
        total_f += s["fail"]

    grand = total_p + total_f
    gacc  = total_p / grand * 100 if grand > 0 else 0
    ok    = "PASS" if gacc >= 95 else "FAIL"
    print("-" * 70)
    print(f"  {'SUBTOTAL':<34} {grand:>6}  {total_p:>5}  {total_f:>5}  {gacc:>8.1f}%  {ok}")
    print("=" * 70)
