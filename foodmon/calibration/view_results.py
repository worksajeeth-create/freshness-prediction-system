"""
FoodMon Calibration Results Viewer
====================================
Run this anytime to see a summary of completed calibration cycles
without running new tests.

Usage:
    python view_results.py
    python view_results.py --plot          # also shows accuracy bar chart
"""

import csv
import sys
import statistics
from collections import defaultdict
from pathlib import Path
from datetime import datetime

LOG_FILE = Path("calibration_log.csv")

SENSOR_LABELS = {
    "temperature": "Temperature (DHT22/AM2301)",
    "humidity":    "Humidity    (DHT22/AM2301)",
    "co2":         "CO₂         (MH-Z19C)",
    "mq2":         "MQ-2        (LPG/Smoke)",
    "mq3":         "MQ-3        (Alcohol)",
    "mq4":         "MQ-4        (Methane)",
    "mq135":       "MQ-135      (VOCs/NH₃)",
    "mq136":       "MQ-136      (H₂S)",
    "mq137":       "MQ-137      (NH₃)",
}


def load_results():
    if not LOG_FILE.exists():
        print(f"  No calibration log found at {LOG_FILE}")
        print("  Run calibrate.py first.")
        sys.exit(1)
    rows = []
    with open(LOG_FILE, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return rows


def summarise(rows):
    stats = defaultdict(lambda: {"pass": 0, "fail": 0, "errors": [], "readings": [], "conditions": set()})
    for row in rows:
        sid  = row["sensor_id"]
        try:
            epct = float(row["error_pct"])
        except (ValueError, TypeError):
            epct = 0.0
        stats[sid]["readings"].append(row["sensor_reading"])
        stats[sid]["errors"].append(epct)
        stats[sid]["conditions"].add(row["condition"])
        if row["pass_fail"] == "PASS":
            stats[sid]["pass"] += 1
        else:
            stats[sid]["fail"] += 1
    return stats


def print_table(stats):
    print("\n" + "═" * 70)
    print(f"  {'Sensor':<34} {'Cycles':>6}  {'Pass':>5}  {'Fail':>5}  {'Accuracy':>9}  Status")
    print("─" * 70)

    total_p = total_f = 0
    for sid, label in SENSOR_LABELS.items():
        if sid not in stats:
            print(f"  {label:<34} {'—':>6}  {'—':>5}  {'—':>5}  {'—':>9}  (no data yet)")
            continue
        s   = stats[sid]
        tot = s["pass"] + s["fail"]
        acc = s["pass"] / tot * 100 if tot > 0 else 0
        ok  = "✓" if acc >= 95 else "✗"
        print(f"  {label:<34} {tot:>6}  {s['pass']:>5}  {s['fail']:>5}  {acc:>8.1f}%  {ok}")
        total_p += s["pass"]
        total_f += s["fail"]

    grand = total_p + total_f
    gacc  = total_p / grand * 100 if grand > 0 else 0
    ok    = "✓ PASS" if gacc >= 95 else "✗ FAIL"
    print("─" * 70)
    print(f"  {'OVERALL':<34} {grand:>6}  {total_p:>5}  {total_f:>5}  {gacc:>8.1f}%  {ok}")
    print(f"  TARGET: ≥95% across 100 cycles (currently {grand} cycles logged)")
    print("═" * 70)


def plot_chart(stats):
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("\n  matplotlib not installed — skipping chart.")
        print("  Install with: pip install matplotlib")
        return

    labels  = []
    accs    = []
    colours = []

    for sid, label in SENSOR_LABELS.items():
        if sid not in stats:
            continue
        s   = stats[sid]
        tot = s["pass"] + s["fail"]
        acc = s["pass"] / tot * 100 if tot > 0 else 0
        labels.append(sid.upper())
        accs.append(acc)
        colours.append("#4CAF50" if acc >= 95 else "#F44336")

    if not labels:
        print("  No data to plot yet.")
        return

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor("#1a1e26")
    ax.set_facecolor("#1a1e26")

    x = np.arange(len(labels))
    bars = ax.bar(x, accs, color=colours, width=0.5, edgecolor="#333")

    ax.axhline(95, color="#FF9800", linestyle="--", linewidth=1.5, label="95% target")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, color="#e0e0e0", fontsize=10)
    ax.set_ylim(0, 110)
    ax.set_ylabel("Accuracy (%)", color="#e0e0e0")
    ax.set_title("FoodMon Sensor Calibration Results", color="#e0e0e0", fontsize=13)
    ax.tick_params(colors="#e0e0e0")
    ax.spines[:].set_color("#333")
    ax.legend(facecolor="#2a2e36", edgecolor="#555", labelcolor="#e0e0e0")

    for bar, acc in zip(bars, accs):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                f"{acc:.1f}%", ha="center", va="bottom", color="#e0e0e0", fontsize=9)

    plt.tight_layout()
    chart_path = Path("calibration_chart.png")
    plt.savefig(chart_path, dpi=150, facecolor=fig.get_facecolor())
    print(f"\n  Chart saved: {chart_path.resolve()}")
    plt.show()


def main():
    print("\n  FoodMon Calibration Results Viewer")
    print(f"  Log file: {LOG_FILE.resolve()}")
    print(f"  Checked:  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    rows  = load_results()
    stats = summarise(rows)
    print_table(stats)

    if "--plot" in sys.argv:
        plot_chart(stats)
    else:
        print("\n  Tip: run with --plot to generate an accuracy bar chart")
        print("       python view_results.py --plot\n")


if __name__ == "__main__":
    main()