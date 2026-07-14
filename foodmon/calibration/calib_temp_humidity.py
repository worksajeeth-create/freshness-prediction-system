"""
calib_temp_humidity.py
======================
Calibration script for Temperature (DHT22/AM2301) and Humidity sensors.
Runs 30 paired readings across 3 conditions (10 each).

Usage:
    python calib_temp_humidity.py

Results are appended to calibration_log.csv
"""

import time
from datetime import datetime

from calib_common import (
    banner, pause, clear,
    fetch_sensor_data, check_flask,
    ask_float, pf,
    open_log, next_cycle_number,
    print_summary,
)

CONDITIONS = [
    (
        "room_temperature",
        10,
        "Place the sensor and your reference thermometer on the same\n"
        "  surface at normal room temperature. Keep them 5 cm apart.",
    ),
    (
        "cooled_environment",
        10,
        "Place both near a running fan, or next to a cup of cold water\n"
        "  (not touching the water). Wait 2 minutes before starting.",
    ),
    (
        "warm_environment",
        10,
        "Place both in a slightly warmer spot — top of a running laptop,\n"
        "  near (not under) a lamp, or near a sunny window briefly.\n"
        "  Wait 2 minutes before starting.",
    ),
]


def run(writer, cycle_counter: list) -> list:
    results = []

    banner("Temperature & Humidity Calibration  (30 cycles)")
    print("""
  What you need:
    - A digital thermometer/hygrometer (any cheap consumer one)
    - OR your phone's weather app as a fallback for room temperature

  You will take 10 paired readings under each of 3 conditions.
  For each reading:
    1. Note the reference value on your thermometer/hygrometer
    2. Type it in when prompted
    3. The script captures the sensor value automatically
    """)
    pause("Ready to start?")

    for condition, count, instruction in CONDITIONS:
        banner(f"Condition: {condition.replace('_', ' ').upper()}")
        print(f"\n  {instruction}\n")
        pause("Set up the condition, then press ENTER to begin.")

        for i in range(count):
            cn = cycle_counter[0]
            cycle_counter[0] += 1

            print(f"\n  --- Cycle {cn}  ({condition}, reading {i + 1}/{count}) ---")

            ref_temp = ask_float("reference TEMPERATURE in degrees C")
            ref_hum  = ask_float("reference HUMIDITY in percent")

            print("  Capturing sensor readings...", end=" ", flush=True)
            time.sleep(2)
            data   = fetch_sensor_data()
            s_temp = data.get("temperature")
            s_hum  = data.get("humidity")
            ts     = datetime.now().isoformat()
            print("done.")

            for sensor_id, s_val, ref_val, ref_src, tol in [
                ("temperature", s_temp, ref_temp, "digital_thermometer", 5.0),
                ("humidity",    s_hum,  ref_hum,  "digital_hygrometer",  5.0),
            ]:
                if s_val is None:
                    print(f"  [!] No {sensor_id} reading received — skipping.")
                    continue

                abs_err = abs(s_val - ref_val)
                err_pct = (abs_err / ref_val * 100) if ref_val != 0 else 0.0
                result  = pf(err_pct, tol)
                status  = "OK" if result == "PASS" else "!!"

                print(f"  [{status}] {sensor_id.upper():12s} "
                      f"sensor={s_val:.1f}  ref={ref_val:.1f}  "
                      f"error={abs_err:.2f} ({err_pct:.1f}%)  -> {result}")

                row = {
                    "cycle_number":     cn,
                    "sensor_id":        sensor_id,
                    "condition":        condition,
                    "sensor_reading":   round(s_val, 2),
                    "reference_value":  round(ref_val, 2),
                    "reference_source": ref_src,
                    "abs_error":        round(abs_err, 3),
                    "error_pct":        round(err_pct, 2),
                    "pass_fail":        result,
                    "notes":            "",
                    "timestamp":        ts,
                }
                writer.writerow(row)
                results.append(row)

    return results


def main():
    clear()
    banner("FOODMON — Temperature & Humidity Calibration")
    print("""
  This script is INDEPENDENT — it does not depend on CO2 or MQ scripts.
  Results are appended to calibration_log.csv so you can run each
  script separately without losing previous progress.
    """)

    if not check_flask():
        return

    cycle_start = next_cycle_number()
    print(f"\n  Resuming from cycle {cycle_start} "
          f"(based on existing calibration_log.csv)\n")

    cycle_counter = [cycle_start]

    fh, writer = open_log()
    try:
        results = run(writer, cycle_counter)
    finally:
        fh.close()

    banner("Temperature & Humidity — DONE")
    print(f"\n  {len(results)} readings saved to calibration_log.csv")
    print_summary(["temperature", "humidity"])
    print("\n  Next step:  python calib_co2.py")


if __name__ == "__main__":
    main()
