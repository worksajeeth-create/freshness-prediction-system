"""
view_results.py
===============
View a summary of all completed calibration cycles at any time.
Does not run any new tests — read-only.

Usage:
    python view_results.py              # table only
    python view_results.py --plot       # table + accuracy bar chart
"""

import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from calib_common import LOG_FILE, SENSOR_LABELS, print_summary

REPORT_FILE = Path("calibration_report.txt")


def load_all_rows():
    if not LOG_FILE.exists():
        print(f"  No {LOG_FILE} found — run a calibration script first.")
        sys.exit(0)
    with open(LOG_FILE, newline="") as f:
        return list(csv.DictReader(f))


def write_report(rows):
    """Write a plain-text report file for your capstone submission."""
    stats = defaultdict(lambda: {"pass": 0, "fail": 0, "errors": []})
    for row in rows:
        sid = row["sensor_id"]
        try:
            epct = float(row["error_pct"])
        except (ValueError, TypeError):
            epct = 0.0
        stats[sid]["errors"].append(epct)
        if row["pass_fail"] == "PASS":
            stats[sid]["pass"] += 1
        else:
            stats[sid]["fail"] += 1

    import statistics as _stats

    lines = [
        "=" * 60,
        "FOODMON SENSOR CALIBRATION REPORT",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 60,
        "",
    ]

    total_p = total_f = 0
    for sid, label in SENSOR_LABELS.items():
        if sid not in stats:
            continue
        s   = stats[sid]
        tot = s["pass"] + s["fail"]
        acc = s["pass"] / tot * 100 if tot > 0 else 0
        mean_err = _stats.mean(s["errors"]) if s["errors"] else 0
        overall  = "PASS (>=95%)" if acc >= 95 else "FAIL (<95%)"
        total_p += s["pass"]
        total_f += s["fail"]

        lines += [
            f"Sensor  : {label}",
            f"  Cycles  : {tot}",
            f"  Pass    : {s['pass']}   Fail: {s['fail']}",
            f"  Accuracy: {acc:.1f}%   {overall}",
            f"  Mean err: {mean_err:.2f}%",
            "",
        ]

    grand = total_p + total_f
    gacc  = total_p / grand * 100 if grand > 0 else 0
    lines += [
        "-" * 60,
        f"OVERALL  -- {grand} cycles   Accuracy: {gacc:.1f}%",
        "TARGET   -- >=95% across 100 cycles",
        "RESULT   -- " + ("OBJECTIVE 1 MET" if gacc >= 95 else "OBJECTIVE 1 NOT MET"),
        "=" * 60,
    ]

    text = "\n".join(lines)
    REPORT_FILE.write_text(text)
    print(f"\n  Report saved to {REPORT_FILE.resolve()}")
    return text


def plot_chart(rows):
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("\n  matplotlib not installed.")
        print("  pip install matplotlib --break-system-packages")
        return

    stats = defaultdict(lambda: {"pass": 0, "fail": 0})
    for row in rows:
        sid = row["sensor_id"]
        if row["pass_fail"] == "PASS":
            stats[sid]["pass"] += 1
        else:
            stats[sid]["fail"] += 1

    labels  = []
    accs    = []
    colours = []
    for sid in SENSOR_LABELS:
        if sid not in stats:
            continue
        s   = stats[sid]
        tot = s["pass"] + s["fail"]
        acc = s["pass"] / tot * 100 if tot > 0 else 0
        labels.append(sid.upper())
        accs.append(acc)
        colours.append("#4CAF50" if acc >= 95 else "#F44336")

    if not labels:
        print("  No data to plot.")
        return

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor("#1a1e26")
    ax.set_facecolor("#1a1e26")

    x    = np.arange(len(labels))
    bars = ax.bar(x, accs, color=colours, width=0.5, edgecolor="#333")
    ax.axhline(95, color="#FF9800", linestyle="--", linewidth=1.5, label="95% target")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, color="#e0e0e0", fontsize=10)
    ax.set_ylim(0, 110)
    ax.set_ylabel("Accuracy (%)", color="#e0e0e0")
    ax.set_title("FoodMon Sensor Calibration Results", color="#e0e0e0", fontsize=13)
    ax.tick_params(colors="#e0e0e0")
    for spine in ax.spines.values():
        spine.set_color("#333")
    ax.legend(facecolor="#2a2e36", edgecolor="#555", labelcolor="#e0e0e0")

    for bar, acc in zip(bars, accs):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 1,
            f"{acc:.1f}%",
            ha="center", va="bottom", color="#e0e0e0", fontsize=9,
        )

    plt.tight_layout()
    chart_path = Path("calibration_chart.png")
    plt.savefig(chart_path, dpi=150, facecolor=fig.get_facecolor())
    print(f"  Chart saved to {chart_path.resolve()}")
    plt.show()


def main():
    print("\n  FoodMon Calibration Results Viewer")
    print(f"  Log   : {LOG_FILE.resolve()}")
    print(f"  Viewed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    rows = load_all_rows()
    print(f"  Total rows in log: {len(rows)}\n")

    print_summary()          # full summary across all sensors
    write_report(rows)       # always writes the .txt report

    if "--plot" in sys.argv:
        plot_chart(rows)
    else:
        print("\n  Tip: add --plot to also generate an accuracy bar chart")
        print("       python view_results.py --plot\n")


if __name__ == "__main__":
    main()
