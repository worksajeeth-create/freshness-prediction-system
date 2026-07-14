"""
calib_mq.py
===========
Calibration script for all 6 MQ gas sensors.
Runs 10 identical stimulus cycles per sensor (60 cycles total).
Accuracy = repeatability (coefficient of variation <= 5%).

Usage:
    python calib_mq.py              # run all 6 sensors in order
    python calib_mq.py mq3          # run only MQ-3
    python calib_mq.py mq3 mq135    # run MQ-3 then MQ-135

Results are appended to calibration_log.csv
"""

import statistics
import sys
import time
from datetime import datetime

from calib_common import (
    banner, pause, clear,
    get_reading, check_flask,
    pf, open_log, next_cycle_number,
    print_summary,
)

# ── Sensor definitions ─────────────────────────────────────────────────
# (sensor_id, target_gas, stimulus_instruction, recovery_seconds)
MQ_TESTS = [
    (
        "mq2",
        "LPG / Smoke",
        "Flick a lighter ONCE near the sensor (1-2 cm away).\n"
        "  DO NOT ignite the flame — just press the flint to release gas.",
        180,
    ),
    (
        "mq3",
        "Alcohol / Ethanol",
        "Hold a cotton wool ball with 3 drops of rubbing alcohol\n"
        "  (or hand sanitizer) 5 cm from the sensor for 5 seconds.",
        120,
    ),
    (
        "mq4",
        "Methane / Butane",
        "Flick a lighter ONCE near the sensor (1-2 cm away) — same\n"
        "  as MQ-2. DO NOT ignite flame.",
        180,
    ),
    (
        "mq135",
        "VOCs / NH3",
        "Hold a cotton wool ball with 2 drops of nail polish remover\n"
        "  (acetone) 5 cm from the sensor for 5 seconds.",
        120,
    ),
    (
        "mq136",
        "H2S",
        "Strike a match, let it burn for 1 second, then hold it\n"
        "  3 cm from the sensor for 3 seconds, then blow it out.",
        180,
    ),
    (
        "mq137",
        "NH3 / Ammonia",
        "Give ONE brief spray of ammonia-based glass cleaner\n"
        "  (e.g. Mr. Muscle glass spray) in the air 10 cm from\n"
        "  the sensor. Do NOT spray directly at the sensor.",
        150,
    ),
]

MQ_IDS = [t[0] for t in MQ_TESTS]


def test_one_sensor(sensor_id: str, target_gas: str, stimulus: str,
                    recovery_s: int, writer, cycle_counter: list) -> list:
    results   = []
    peak_vals = []

    banner(f"MQ Sensor: {sensor_id.upper()} -- {target_gas}")
    print(f"""
  Stimulus:
    {stimulus}

  You will repeat this stimulus exactly 10 times.
  Between each cycle the script waits {recovery_s} seconds for
  the sensor to recover to near its baseline.

  Each cycle:
    1. Apply the stimulus for exactly 5 seconds
    2. Remove / stop the stimulus
    3. Press ENTER  (script captures peak reading)
    4. Script counts down recovery time automatically
    """)
    pause(f"Ready to start {sensor_id.upper()} tests? Press ENTER.")

    # Capture baseline before any stimulus
    print("\n  Capturing baseline reading...", end=" ", flush=True)
    baseline = get_reading(sensor_id)
    if baseline is not None:
        print(f"{baseline:.1f} ppm")
    else:
        print("unavailable — continuing anyway.")

    for i in range(10):
        cn = cycle_counter[0]
        cycle_counter[0] += 1

        print(f"\n  --- Cycle {cn}  ({sensor_id}, stimulus {i + 1}/10) ---")
        print(f"  APPLY STIMULUS NOW: {stimulus.splitlines()[0]}")
        input("  (Hold for 5 seconds, then press ENTER to capture peak reading)")

        print("  Capturing...", end=" ", flush=True)
        time.sleep(1)   # brief pause so the peak is still in the air
        peak = get_reading(sensor_id)
        if peak is None:
            print(f"  [!] No reading received — skipping cycle {cn}.")
            continue

        peak_vals.append(peak)

        # Safe string for baseline comparison — avoids f-string format errors
        if baseline is not None:
            baseline_str = f"{baseline:.1f}"
            arrow = "UP" if peak > baseline else "~"
        else:
            baseline_str = "?"
            arrow = "~"

        print(f"  [{arrow}] Peak: {peak:.1f} ppm   (baseline: {baseline_str} ppm)")

        # Recovery countdown (skip after last stimulus)
        if i < 9:
            print(f"\n  Recovering — waiting {recovery_s} s before next cycle...")
            for remaining in range(recovery_s, 0, -5):
                print(f"  {remaining} s remaining...   ", end="\r", flush=True)
                time.sleep(5)
            print()

            # Re-measure baseline for next cycle
            new_base = get_reading(sensor_id)
            if new_base is not None:
                baseline = new_base
                print(f"  Recovery reading: {baseline:.1f} ppm")

    # ── Compute repeatability stats ────────────────────────────────────
    if len(peak_vals) < 3:
        print(f"\n  [!] Only {len(peak_vals)} valid readings — need at least 3 to compute CV.")
        return results

    mean_peak = statistics.mean(peak_vals)
    std_peak  = statistics.stdev(peak_vals)
    cv        = (std_peak / mean_peak * 100) if mean_peak != 0 else 0.0
    overall   = pf(cv, 5.0)

    print(f"\n  Results for {sensor_id.upper()}:")
    print(f"    Mean peak : {mean_peak:.1f} ppm")
    print(f"    Std dev   : {std_peak:.2f}")
    print(f"    CV        : {cv:.2f}%   (target <= 5%)")
    print(f"    Result    : {overall}")

    # Log each individual cycle result
    # Assign cycle numbers retrospectively (they were already incremented above)
    start_cn = cycle_counter[0] - len(peak_vals)
    for idx, val in enumerate(peak_vals):
        abs_err = abs(val - mean_peak)
        err_pct = abs_err / mean_peak * 100 if mean_peak != 0 else 0.0
        row = {
            "cycle_number":     start_cn + idx,
            "sensor_id":        sensor_id,
            "condition":        "controlled_stimulus",
            "sensor_reading":   round(val, 1),
            "reference_value":  round(mean_peak, 1),
            "reference_source": "mean_of_10_identical_stimuli",
            "abs_error":        round(abs_err, 2),
            "error_pct":        round(err_pct, 2),
            "pass_fail":        overall,   # pass/fail tied to CV, not individual deviation
            "notes":            f"CV={cv:.2f}%  stimulus={stimulus.splitlines()[0][:50]}",
            "timestamp":        datetime.now().isoformat(),
        }
        writer.writerow(row)
        results.append(row)

    return results


def main():
    # Allow running a subset: python calib_mq.py mq3 mq135
    requested = [a.lower() for a in sys.argv[1:]] if len(sys.argv) > 1 else MQ_IDS
    invalid   = [r for r in requested if r not in MQ_IDS]
    if invalid:
        print(f"  Unknown sensor IDs: {invalid}")
        print(f"  Valid options: {MQ_IDS}")
        sys.exit(1)

    tests_to_run = [t for t in MQ_TESTS if t[0] in requested]

    clear()
    banner("FOODMON — MQ Gas Sensor Calibration")
    print(f"""
  This script is INDEPENDENT — it does not depend on the temp/humidity
  or CO2 scripts. Results are appended to calibration_log.csv so
  previous progress is preserved.

  Sensors to calibrate this run: {[t[0].upper() for t in tests_to_run]}
  Cycles per sensor             : 10
  Total cycles this run         : {len(tests_to_run) * 10}

  Safety reminders:
    - Work in a well-ventilated room
    - Keep all stimuli brief (3-5 seconds maximum)
    - For lighters: gas release only, NO open flame near sensors
    - For sprays: one brief burst in the air, NOT directly at sensor
    """)

    if not check_flask():
        return

    cycle_start = next_cycle_number()
    print(f"\n  Resuming from cycle {cycle_start} "
          f"(based on existing calibration_log.csv)\n")

    cycle_counter = [cycle_start]
    all_results   = []

    fh, writer = open_log()
    try:
        for sensor_id, target_gas, stimulus, recovery_s in tests_to_run:
            sensor_results = test_one_sensor(
                sensor_id, target_gas, stimulus, recovery_s,
                writer, cycle_counter,
            )
            all_results.extend(sensor_results)
            print(f"\n  {sensor_id.upper()} complete — {len(sensor_results)} readings saved.")

            # Offer to pause between sensors
            if tests_to_run.index((sensor_id, target_gas, stimulus, recovery_s)) < len(tests_to_run) - 1:
                next_sensor = tests_to_run[
                    tests_to_run.index((sensor_id, target_gas, stimulus, recovery_s)) + 1
                ][0].upper()
                pause(f"Press ENTER when ready to start {next_sensor}, or Ctrl+C to stop here.")

    except KeyboardInterrupt:
        print("\n\n  Stopped by user — progress saved to calibration_log.csv")
    finally:
        fh.close()

    banner("MQ Calibration — DONE")
    print(f"\n  {len(all_results)} readings saved to calibration_log.csv")
    print_summary([t[0] for t in tests_to_run])
    print("\n  Run 'python view_results.py' to see your full calibration summary.")


if __name__ == "__main__":
    main()
