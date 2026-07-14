"""
calib_co2.py
============
Calibration script for the CO2 sensor (MH-Z19C).
Runs 20 readings: 10 open-air (reference = 420 ppm) +
                  10 sealed-chamber with fruit (repeatability test).

Usage:
    python calib_co2.py

Results are appended to calibration_log.csv
"""

import statistics
import time
from datetime import datetime

from calib_common import (
    banner, pause, clear,
    get_reading, check_flask,
    pf, open_log, next_cycle_number,
    print_summary,
)

CO2_ATMOSPHERIC_REF = 420.0   # ppm — globally accepted atmospheric constant
OPEN_AIR_TOLERANCE_PPM = 100  # pass if within ±100 ppm of 420


def run_open_air(writer, cycle_counter: list) -> list:
    results = []
    banner("CO2 Sub-test A — Open Air  (10 readings)")
    print("""
  Reference = 420 ppm  (atmospheric constant — no instrument needed)

  Steps:
    1. Take the sensor / device near an open window or outside
    2. Wait 2 minutes for the sensor to stabilise in fresh air
    3. The script will capture 10 readings automatically,
       4 seconds apart
    """)
    pause("Sensor positioned in fresh/open air? Press ENTER to begin.")

    print()
    for i in range(10):
        cn = cycle_counter[0]
        cycle_counter[0] += 1

        print(f"  --- Cycle {cn}  (CO2 open-air, reading {i + 1}/10) ---")
        print("  Waiting 4 s for stable reading...", end=" ", flush=True)
        time.sleep(4)

        val = get_reading("co2")
        if val is None:
            print("  [!] No CO2 reading — skipping this cycle.")
            continue

        abs_err = abs(val - CO2_ATMOSPHERIC_REF)
        err_pct = abs_err / CO2_ATMOSPHERIC_REF * 100
        result  = "PASS" if abs_err <= OPEN_AIR_TOLERANCE_PPM else "FAIL"
        status  = "OK" if result == "PASS" else "!!"

        print(f"  [{status}] CO2: {val:.0f} ppm   "
              f"ref=420 ppm   error={abs_err:.0f} ppm   -> {result}")

        row = {
            "cycle_number":     cn,
            "sensor_id":        "co2",
            "condition":        "open_air",
            "sensor_reading":   round(val, 1),
            "reference_value":  CO2_ATMOSPHERIC_REF,
            "reference_source": "atmospheric_constant_420ppm",
            "abs_error":        round(abs_err, 1),
            "error_pct":        round(err_pct, 2),
            "pass_fail":        result,
            "notes":            "",
            "timestamp":        datetime.now().isoformat(),
        }
        writer.writerow(row)
        results.append(row)

    return results


def run_sealed_chamber(writer, cycle_counter: list) -> list:
    results = []
    banner("CO2 Sub-test B — Sealed Chamber  (10 readings)")
    print("""
  Reference = mean of the 10 readings (repeatability / consistency test)
  Pass criterion = coefficient of variation (CV) <= 5%

  Steps:
    1. Place the CO2 sensor inside a small sealed container
       (a cardboard box, Tupperware, or ziplock bag works fine)
    2. Put a piece of fruit inside (apple or banana)
    3. Seal it completely
    4. Wait 5 minutes for CO2 to build up, then readings begin automatically
    """)
    pause("Sensor sealed in chamber with fruit? Press ENTER to start the 5-min wait.")

    print("\n  Waiting 5 minutes for CO2 to build up...")
    for remaining in range(300, 0, -10):
        print(f"  {remaining} s remaining...   ", end="\r", flush=True)
        time.sleep(10)
    print("\n  Done waiting — taking readings now.\n")

    sealed_vals = []
    sealed_cycles = []

    for i in range(10):
        cn = cycle_counter[0]
        cycle_counter[0] += 1
        sealed_cycles.append(cn)

        print(f"  --- Cycle {cn}  (CO2 sealed-chamber, reading {i + 1}/10) ---")
        val = get_reading("co2")
        if val is None:
            print("  [!] No CO2 reading — skipping.")
            time.sleep(30)
            continue

        sealed_vals.append(val)
        print(f"  CO2: {val:.0f} ppm")

        if i < 9:
            time.sleep(30)

    if len(sealed_vals) < 3:
        print("\n  [!] Not enough valid readings to compute repeatability.")
        return results

    ref_mean = statistics.mean(sealed_vals)
    std_dev  = statistics.stdev(sealed_vals)
    cv       = (std_dev / ref_mean * 100) if ref_mean != 0 else 0.0

    print(f"\n  Mean: {ref_mean:.1f} ppm   Std dev: {std_dev:.2f}   CV: {cv:.2f}%")

    for idx, val in enumerate(sealed_vals):
        abs_err = abs(val - ref_mean)
        err_pct = abs_err / ref_mean * 100 if ref_mean != 0 else 0.0
        # pass/fail based on overall CV, not individual deviation
        result  = pf(cv, 5.0)
        cn_for_row = sealed_cycles[idx] if idx < len(sealed_cycles) else sealed_cycles[-1]

        row = {
            "cycle_number":     cn_for_row,
            "sensor_id":        "co2",
            "condition":        "sealed_chamber_fruit",
            "sensor_reading":   round(val, 1),
            "reference_value":  round(ref_mean, 1),
            "reference_source": "mean_of_10_sealed_readings",
            "abs_error":        round(abs_err, 1),
            "error_pct":        round(err_pct, 2),
            "pass_fail":        result,
            "notes":            f"CV={cv:.2f}%",
            "timestamp":        datetime.now().isoformat(),
        }
        writer.writerow(row)
        results.append(row)

    overall = "PASS" if cv <= 5.0 else "FAIL"
    print(f"\n  Sealed-chamber repeatability -> {overall}  (CV={cv:.2f}%, target <=5%)")

    return results


def main():
    clear()
    banner("FOODMON — CO2 Sensor Calibration")
    print("""
  This script is INDEPENDENT — it does not depend on the temp/humidity
  or MQ scripts. Results are appended to calibration_log.csv so
  previous progress is preserved.

  Two sub-tests:
    A) Open air         — 10 readings, reference = 420 ppm
    B) Sealed chamber   — 10 readings, reference = mean (repeatability)
    """)

    if not check_flask():
        return

    cycle_start = next_cycle_number()
    print(f"\n  Resuming from cycle {cycle_start} "
          f"(based on existing calibration_log.csv)\n")

    cycle_counter = [cycle_start]

    fh, writer = open_log()
    try:
        results_a = run_open_air(writer, cycle_counter)
        results_b = run_sealed_chamber(writer, cycle_counter)
    finally:
        fh.close()

    total = len(results_a) + len(results_b)
    banner("CO2 Calibration — DONE")
    print(f"\n  {total} readings saved to calibration_log.csv")
    print_summary(["co2"])
    print("\n  Next step:  python calib_mq.py")


if __name__ == "__main__":
    main()
