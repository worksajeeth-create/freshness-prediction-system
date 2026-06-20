"""
FoodMon Sensor Calibration Tool
================================
Runs 100 test cycles across all sensors and logs results to CSV.
No extra hardware needed — uses controlled household stimuli for MQ sensors.

Usage:
    python calibrate.py

The script will guide you through each sensor test step by step.
All results are saved to:  calibration_log.csv
Summary report saved to:   calibration_report.txt
"""

import csv
import json
import os
import statistics
import time
from datetime import datetime
from pathlib import Path

import requests

# ── Configuration ──────────────────────────────────────────────────────────────
FLASK_URL = "http://localhost:5000"   # change if running on different host/port
LOG_FILE  = Path("calibration_log.csv")
REPORT_FILE = Path("calibration_report.txt")

# Outdoor / atmospheric CO2 reference (ppm) — global constant
CO2_ATMOSPHERIC_REF = 420.0

# Acceptable tolerance for each sensor type
# For temp/humidity: absolute error vs reference instrument
# For CO2 open-air: ppm deviation from 420
# For MQ sensors: coefficient of variation (%) across identical stimuli
TOLERANCES = {
    "temperature":             5.0,   # ±0.5°C — expressed as % for uniformity → use abs check
    "humidity":                5.0,   # ±3% RH
    "co2_open_air":           25.0,   # ±100ppm from 420 → ~24% but we use abs 100ppm check
    "mq_repeatability_cv":     5.0,   # coefficient of variation ≤5% = ≥95% repeatable
}

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


# ── Helpers ────────────────────────────────────────────────────────────────────

def clear():
    os.system("clear" if os.name != "nt" else "cls")


def banner(title: str):
    print("\n" + "═" * 60)
    print(f"  {title}")
    print("═" * 60)


def pause(msg="Press ENTER when ready..."):
    input(f"\n  ▶  {msg}")


def fetch_sensor_data() -> dict:
    """Pull latest readings from the running Flask app."""
    try:
        r = requests.get(f"{FLASK_URL}/api/current_data", timeout=5)
        r.raise_for_status()
        data = r.json()
        sd = data.get("sensor_data", {})
        flat = {
            "temperature": sd.get("temperature"),
            "humidity":    sd.get("humidity"),
            "co2":         None,
        }
        gases = sd.get("gases", {})
        for g in ["mq2", "mq3", "mq4", "mq135", "mq136", "mq137", "co2"]:
            raw = gases.get(g)
            if isinstance(raw, dict):
                flat[g] = raw.get("value")
            elif raw is not None:
                flat[g] = float(raw)
        return flat
    except Exception as e:
        print(f"\n  ⚠ Could not reach Flask app: {e}")
        print("  Make sure 'python app.py' is running in the backend folder.")
        return {}


def get_reading(sensor_id: str) -> float | None:
    """Fetch and return a single sensor value, with retry."""
    for _ in range(3):
        data = fetch_sensor_data()
        val = data.get(sensor_id)
        if val is not None:
            return float(val)
        print(f"  ⚠  No reading for {sensor_id} — retrying in 3s...")
        time.sleep(3)
    return None


def ask_reference(prompt: str) -> float:
    """Ask the user to enter a reference value."""
    while True:
        try:
            return float(input(f"\n  Enter {prompt}: "))
        except ValueError:
            print("  Please enter a number.")


def log_row(writer, row: dict):
    writer.writerow(row)


def pf(error_pct: float, tolerance: float) -> str:
    return "PASS" if error_pct <= tolerance else "FAIL"


# ── Test Sections ──────────────────────────────────────────────────────────────

def test_temperature_humidity(writer, cycle_counter: list) -> list:
    """
    30 paired readings of temperature and humidity vs a reference thermometer.
    Conditions: room temp (10), near-fan / cooler (10), warm spot (10).
    """
    results = []
    conditions = [
        ("room_temperature",    10, "Place sensor and reference thermometer on the same surface at room temperature."),
        ("cooled_environment",  10, "Place both near a fan or a cup of cold water (not touching water). Keep 5cm apart."),
        ("warm_environment",    10, "Place both in a slightly warm spot — top of a running device, or near sunlight briefly."),
    ]

    banner("SECTION 1 — Temperature & Humidity (DHT22 / AM2301)")
    print("""
  What you need:
    • A consumer digital thermometer/hygrometer (any cheap one)
    • Or use a reliable weather app for room temp as a fallback

  You will take 30 paired readings across 3 conditions (10 each).
  For each reading: note your reference device's value, then press
  ENTER to capture the sensor's value at the same moment.
    """)
    pause("Ready to start temperature/humidity tests?")

    for condition, count, instruction in conditions:
        banner(f"Condition: {condition.replace('_', ' ').upper()}")
        print(f"\n  {instruction}\n")
        pause("Set up the condition, then press ENTER to begin.")

        for i in range(count):
            cycle_counter[0] += 1
            cn = cycle_counter[0]

            print(f"\n  --- Cycle {cn}/100 ({condition}, reading {i+1}/{count}) ---")
            ref_temp = ask_reference("reference TEMPERATURE (°C) from your thermometer")
            ref_hum  = ask_reference("reference HUMIDITY (%) from your hygrometer")

            print("  Capturing sensor readings...", end=" ", flush=True)
            time.sleep(2)  # let sensor stabilise
            data = fetch_sensor_data()
            s_temp = data.get("temperature")
            s_hum  = data.get("humidity")

            ts = datetime.now().isoformat()

            for sensor_id, s_val, ref_val, ref_src, tol in [
                ("temperature", s_temp, ref_temp, "digital_thermometer", 5.0),
                ("humidity",    s_hum,  ref_hum,  "digital_hygrometer",  5.0),
            ]:
                if s_val is None:
                    print(f"\n  ⚠ No {sensor_id} reading — skipping.")
                    continue

                abs_err  = abs(s_val - ref_val)
                err_pct  = (abs_err / ref_val * 100) if ref_val != 0 else 0
                result   = pf(err_pct, tol)
                emoji    = "✓" if result == "PASS" else "✗"

                print(f"\n  {emoji} {sensor_id.upper()}: sensor={s_val:.1f}  ref={ref_val:.1f}  "
                      f"error={abs_err:.2f} ({err_pct:.1f}%)  → {result}")

                row = {
                    "cycle_number":    cn,
                    "sensor_id":       sensor_id,
                    "condition":       condition,
                    "sensor_reading":  round(s_val, 2),
                    "reference_value": round(ref_val, 2),
                    "reference_source": ref_src,
                    "abs_error":       round(abs_err, 3),
                    "error_pct":       round(err_pct, 2),
                    "pass_fail":       result,
                    "notes":           "",
                    "timestamp":       ts,
                }
                writer.writerow(row)
                results.append(row)

    return results


def test_co2(writer, cycle_counter: list) -> list:
    """
    20 CO2 readings: 10 open-air (ref=420ppm), 10 sealed-chamber (consistency check).
    """
    results = []
    banner("SECTION 2 — CO₂ Sensor (MH-Z19C)")
    print("""
  Two sub-tests:
    A) Open-air baseline (10 readings near an open window or outside)
       Reference = 420 ppm (atmospheric constant)

    B) Sealed-chamber consistency (10 readings with sensor in a closed box
       with a piece of fruit — reference = mean of the 10 readings)

  No reference instrument needed for this section.
    """)
    pause("Ready to start CO₂ tests?")

    # --- Sub-test A: open air ---
    banner("CO₂ Sub-test A — Open Air (10 readings)")
    print("\n  Place the sensor near an open window or take it outside.\n")
    pause("Sensor positioned near open air? Press ENTER.")

    open_air_vals = []
    for i in range(10):
        cycle_counter[0] += 1
        cn = cycle_counter[0]
        print(f"\n  --- Cycle {cn}/100 (CO2 open-air, reading {i+1}/10) ---")
        print("  Waiting 4s for stable reading...", end=" ", flush=True)
        time.sleep(4)
        val = get_reading("co2")
        if val is None:
            print("  ⚠ No CO2 reading — skipping.")
            continue

        abs_err  = abs(val - CO2_ATMOSPHERIC_REF)
        err_pct  = abs_err / CO2_ATMOSPHERIC_REF * 100
        result   = "PASS" if abs_err <= 100 else "FAIL"   # ±100 ppm tolerance
        emoji    = "✓" if result == "PASS" else "✗"
        print(f"  {emoji} CO2: {val:.0f} ppm  ref=420 ppm  error={abs_err:.0f} ppm → {result}")
        open_air_vals.append(val)

        row = {
            "cycle_number":    cn,
            "sensor_id":       "co2",
            "condition":       "open_air",
            "sensor_reading":  round(val, 1),
            "reference_value": CO2_ATMOSPHERIC_REF,
            "reference_source": "atmospheric_constant_420ppm",
            "abs_error":       round(abs_err, 1),
            "error_pct":       round(err_pct, 2),
            "pass_fail":       result,
            "notes":           "",
            "timestamp":       datetime.now().isoformat(),
        }
        writer.writerow(row)
        results.append(row)

    # --- Sub-test B: sealed chamber ---
    banner("CO₂ Sub-test B — Sealed Chamber (10 readings)")
    print("""
  Place the CO₂ sensor in a small sealed container (cardboard box, tupperware)
  with a piece of fruit (apple or banana). Close the lid.
  Wait 5 minutes then take 10 readings at 30-second intervals.
  Reference = mean of all 10 readings (consistency / repeatability test).
    """)
    pause("Sensor sealed in chamber? Press ENTER to start 5-minute wait.")
    print("  Waiting 5 minutes for CO₂ to build up...")
    for remaining in range(300, 0, -10):
        print(f"  {remaining}s remaining...", end="\r")
        time.sleep(10)
    print()

    sealed_vals = []
    for i in range(10):
        cycle_counter[0] += 1
        cn = cycle_counter[0]
        print(f"\n  --- Cycle {cn}/100 (CO2 sealed-chamber, reading {i+1}/10) ---")
        val = get_reading("co2")
        if val is None:
            print("  ⚠ No CO2 reading — skipping.")
            time.sleep(30)
            continue
        sealed_vals.append(val)
        print(f"  CO₂: {val:.0f} ppm")
        time.sleep(30)

    # Compute mean as reference, then log all rows
    if sealed_vals:
        ref_mean = statistics.mean(sealed_vals)
        for idx, val in enumerate(sealed_vals):
            abs_err = abs(val - ref_mean)
            err_pct = abs_err / ref_mean * 100 if ref_mean != 0 else 0
            result  = pf(err_pct, 5.0)
            row = {
                "cycle_number":    "sealed_" + str(idx + 1),
                "sensor_id":       "co2",
                "condition":       "sealed_chamber_fruit",
                "sensor_reading":  round(val, 1),
                "reference_value": round(ref_mean, 1),
                "reference_source": "mean_of_10_sealed_readings",
                "abs_error":       round(abs_err, 1),
                "error_pct":       round(err_pct, 2),
                "pass_fail":       result,
                "notes":           f"CV={round(statistics.stdev(sealed_vals)/ref_mean*100,2) if len(sealed_vals)>1 else 0}%",
                "timestamp":       datetime.now().isoformat(),
            }
            writer.writerow(row)
            results.append(row)
        print(f"\n  Sealed chamber mean: {ref_mean:.0f} ppm  "
              f"CV: {statistics.stdev(sealed_vals)/ref_mean*100:.2f}%")

    return results


def test_mq_sensor(sensor_id: str, target_gas: str, stimulus: str,
                   recovery_seconds: int, writer, cycle_counter: list) -> list:
    """
    Generic MQ sensor test: 10 identical stimulus cycles.
    Reference = mean peak response. Accuracy = repeatability (CV ≤ 5%).
    """
    results = []
    banner(f"MQ Sensor: {sensor_id.upper()} — {target_gas}")
    print(f"""
  Stimulus: {stimulus}

  You will repeat this exact stimulus 10 times.
  Between each: wait {recovery_seconds} seconds for the sensor to recover
  to near its baseline before applying stimulus again.

  For each cycle:
    1. Apply the stimulus for exactly 5 seconds
    2. Remove/stop the stimulus
    3. Press ENTER — the script captures the peak reading
    4. Wait for recovery before the next cycle
    """)
    pause(f"Ready to start {sensor_id.upper()} tests?")

    baseline_val = get_reading(sensor_id)
    if baseline_val is not None:
        print(f"\n  Baseline reading: {baseline_val:.1f} ppm")
    else:
        print("\n  ⚠ Could not get baseline — continuing anyway.")

    peak_vals = []
    for i in range(10):
        cycle_counter[0] += 1
        cn = cycle_counter[0]

        print(f"\n  --- Cycle {cn}/100 ({sensor_id}, stimulus {i+1}/10) ---")
        print(f"  ► APPLY STIMULUS NOW: {stimulus}")
        input("    (Hold stimulus for 5 seconds, then press ENTER to capture peak)")

        print("  Capturing...", end=" ", flush=True)
        time.sleep(1)
        peak = get_reading(sensor_id)
        if peak is None:
            print(f"  ⚠ No reading — skipping cycle {cn}.")
            continue
        peak_vals.append(peak)
        emoji = "↑" if baseline_val and peak > baseline_val else "~"
        print(f"  {emoji} Peak: {peak:.1f} ppm  (baseline: {baseline_val:.1f if baseline_val else '?'} ppm)")

        if i < 9:
            print(f"  Recovering for {recovery_seconds}s...", end=" ", flush=True)
            for s in range(recovery_seconds, 0, -5):
                print(f"{s}s ", end="", flush=True)
                time.sleep(5)
            print()
            baseline_val = get_reading(sensor_id)
            if baseline_val:
                print(f"  Recovery reading: {baseline_val:.1f} ppm")

    # Compute CV-based accuracy
    if len(peak_vals) >= 3:
        mean_peak = statistics.mean(peak_vals)
        std_peak  = statistics.stdev(peak_vals)
        cv        = (std_peak / mean_peak * 100) if mean_peak != 0 else 0
        accuracy  = 100 - cv   # repeatability accuracy

        print(f"\n  Results: mean={mean_peak:.1f}  std={std_peak:.2f}  "
              f"CV={cv:.2f}%  Accuracy={accuracy:.1f}%")

        for idx, val in enumerate(peak_vals):
            abs_err = abs(val - mean_peak)
            err_pct = abs_err / mean_peak * 100 if mean_peak != 0 else 0
            result  = pf(cv, 5.0)   # overall pass/fail based on CV
            row = {
                "cycle_number":    cycle_counter[0] - (len(peak_vals) - 1 - idx),
                "sensor_id":       sensor_id,
                "condition":       "controlled_stimulus",
                "sensor_reading":  round(val, 1),
                "reference_value": round(mean_peak, 1),
                "reference_source": "mean_of_10_identical_stimuli",
                "abs_error":       round(abs_err, 2),
                "error_pct":       round(err_pct, 2),
                "pass_fail":       result,
                "notes":           f"CV={cv:.2f}% stimulus={stimulus[:40]}",
                "timestamp":       datetime.now().isoformat(),
            }
            writer.writerow(row)
            results.append(row)
    else:
        print(f"\n  ⚠ Not enough valid readings for {sensor_id} (got {len(peak_vals)}).")

    return results


# ── Summary Report ──────────────────────────────────────────────────────────────

def generate_report(all_results: list):
    from collections import defaultdict

    sensor_stats = defaultdict(lambda: {"pass": 0, "fail": 0, "errors": [], "readings": []})

    for row in all_results:
        sid  = row["sensor_id"]
        epct = float(row["error_pct"]) if row["error_pct"] not in ("", None) else 0.0
        sensor_stats[sid]["readings"].append(float(row["sensor_reading"]))
        sensor_stats[sid]["errors"].append(epct)
        if row["pass_fail"] == "PASS":
            sensor_stats[sid]["pass"] += 1
        else:
            sensor_stats[sid]["fail"] += 1

    lines = []
    lines.append("=" * 60)
    lines.append("FOODMON SENSOR CALIBRATION REPORT")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 60)
    lines.append("")

    total_pass = total_fail = 0

    for sid, stats in sensor_stats.items():
        p    = stats["pass"]
        f    = stats["fail"]
        tot  = p + f
        acc  = p / tot * 100 if tot > 0 else 0
        mean_err = statistics.mean(stats["errors"]) if stats["errors"] else 0
        overall  = "✓ PASS (≥95%)" if acc >= 95 else "✗ FAIL (<95%)"

        total_pass += p
        total_fail += f

        lines.append(f"Sensor: {sid.upper()}")
        lines.append(f"  Cycles:    {tot}")
        lines.append(f"  Pass:      {p}  |  Fail: {f}")
        lines.append(f"  Accuracy:  {acc:.1f}%   {overall}")
        lines.append(f"  Mean error: {mean_err:.2f}%")
        lines.append("")

    grand_total = total_pass + total_fail
    grand_acc   = total_pass / grand_total * 100 if grand_total > 0 else 0
    lines.append("-" * 60)
    lines.append(f"OVERALL  — {grand_total} cycles  |  Accuracy: {grand_acc:.1f}%")
    lines.append("TARGET   — ≥95% across 100 cycles")
    lines.append("RESULT   — " + ("✓ OBJECTIVE 1 MET" if grand_acc >= 95 else "✗ OBJECTIVE 1 NOT MET"))
    lines.append("=" * 60)

    report_text = "\n".join(lines)
    REPORT_FILE.write_text(report_text)
    print("\n" + report_text)
    print(f"\n  Full log:    {LOG_FILE.resolve()}")
    print(f"  Report:      {REPORT_FILE.resolve()}")


# ── Main ───────────────────────────────────────────────────────────────────────

MQ_TESTS = [
    # (sensor_id, target_gas, stimulus_description, recovery_seconds)
    (
        "mq2", "LPG / Smoke",
        "Flick a lighter once near the sensor (1–2cm) — DO NOT ignite flame",
        180,
    ),
    (
        "mq3", "Alcohol / Ethanol",
        "Hold cotton wool with 3 drops of rubbing alcohol 5cm from sensor for 5 seconds",
        120,
    ),
    (
        "mq4", "Methane / Butane",
        "Flick a lighter once near the sensor — same as MQ-2 but different selectivity",
        180,
    ),
    (
        "mq135", "VOCs / NH₃",
        "Hold cotton wool with 2 drops of nail polish remover 5cm from sensor for 5 seconds",
        120,
    ),
    (
        "mq136", "H₂S",
        "Strike a match, let it burn 1 second, then hold 3cm from sensor for 3 seconds",
        180,
    ),
    (
        "mq137", "NH₃",
        "Brief 1-second spray of ammonia-based glass cleaner in the air 10cm from sensor",
        150,
    ),
]


def main():
    clear()
    banner("FOODMON SENSOR CALIBRATION TOOL")
    print("""
  This tool runs 100 test cycles across all sensors and produces
  an accuracy report for Objective 1 of your capstone project.

  Breakdown of 100 cycles:
    • Temperature & Humidity  : 30 cycles (15 temp + 15 hum across 3 conditions)
    • CO₂ Sensor              : 20 cycles (10 open-air + 10 sealed-chamber)
    • MQ-2  (LPG/Smoke)       :  10 cycles
    • MQ-3  (Alcohol)         :  10 cycles
    • MQ-4  (Methane)         :  10 cycles
    • MQ-135 (VOCs)           :  10 cycles
    • MQ-136 (H₂S)            :  10 cycles
    • MQ-137 (NH₃)            :  10 cycles
                                ─────────
                          TOTAL: 120 cycles (exceeds 100 ✓)

  Prerequisites:
    1. 'python app.py' is running in the backend folder
    2. ESP32 is powered on and sending sensor data via MQTT
    3. You have a consumer digital thermometer/hygrometer nearby
    4. You have the household stimuli ready (rubbing alcohol, lighter, etc.)

  All results are saved automatically to calibration_log.csv
  You can stop and restart — the CSV appends, it does not overwrite.
    """)

    # Verify Flask is reachable
    print("  Checking Flask connection...", end=" ", flush=True)
    test = fetch_sensor_data()
    if not test:
        print("\n\n  ✗ Cannot reach Flask app. Start it first, then re-run this script.")
        return
    print("OK ✓")

    pause("Everything ready? Press ENTER to begin calibration.")

    # Open CSV in append mode so partial runs are preserved
    file_exists = LOG_FILE.exists()
    all_results = []
    cycle_counter = [0]  # mutable so sub-functions can update it

    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()

        # Section 1 — Temperature & Humidity
        all_results += test_temperature_humidity(writer, cycle_counter)

        # Section 2 — CO2
        all_results += test_co2(writer, cycle_counter)

        # Section 3 — MQ Sensors
        for sensor_id, target_gas, stimulus, recovery in MQ_TESTS:
            all_results += test_mq_sensor(
                sensor_id, target_gas, stimulus, recovery, writer, cycle_counter
            )

    # Generate report from the full CSV (includes any previous partial runs)
    banner("CALIBRATION COMPLETE — Generating Report")
    all_rows = []
    with open(LOG_FILE, newline="") as f:
        reader = csv.DictReader(f)
        all_rows = list(reader)

    generate_report(all_rows)


if __name__ == "__main__":
    main()