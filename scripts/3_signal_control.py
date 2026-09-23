"""Step 3 - control the traffic signal from Python.

Run:  python scripts/3_signal_control.py [--gui] [--duration 900]
      python scripts/3_signal_control.py --compare      # three strategies

This is the starting point of any signal-optimisation study:

    1. read state      traci.lane.getLastStepHaltingNumber(lane)
    2. decide          plain Python logic (see scripts/control.py)
    3. act             traci.trafficlight.setPhase(tls, phase)

Replace step 2 with reinforcement learning, a genetic algorithm or fuzzy
logic and you have a research contribution.  The infrastructure around it -
this file, the runner, the analysis - does not change.  Adding a strategy
means adding one class to ``control.CONTROLLERS``.

The comparison reads the same output files the analysis layer reads, so the
numbers printed here and the numbers in ``results/`` cannot drift apart.

``--compare`` runs three strategies, not two.  The third one - ``timed``,
fixed-time that ignores the traffic - is the control group, and it is there
because "the controller beats fixed-time" and "a shorter cycle beats a longer
one" look identical until you separate them.  See 坑三第六幕 in the README.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyse_run import load_run, queue_metrics, trip_metrics  # noqa: E402
from control import CONTROLLERS  # noqa: E402
from runner import run_simulation  # noqa: E402
from sumo_config import ROOT  # noqa: E402

#: Where --compare puts the runs it produces.
CONTROL_DIR = ROOT / "results" / "control"

#: What --compare runs, and what each one is for.  The third entry is the
#: point: without it, "the controller beats fixed-time" cannot be told apart
#: from "a shorter cycle beats a longer one", and the first reading is the
#: flattering one.  See 坑三第六幕 in the README.
COMPARISON = [
    ("fixed", "the network's own plan, written by netconvert - not tuned"),
    ("timed", "fixed-time that ignores traffic; cycles faster, nothing else"),
    ("actuated", "the Python controller: reacts to queues"),
]


def run_and_measure(strategy: str, duration: int, gui: bool) -> dict:
    """Run one strategy and combine the run summary with the measured data."""
    run_dir = CONTROL_DIR / strategy
    print(f"\n--- {strategy}, {duration} s ---")
    print(f"  output -> {run_dir.relative_to(ROOT)}")
    summary = run_simulation(run_dir, strategy=strategy, duration=duration,
                             gui=gui, label=strategy)

    # measure from the files rather than from counters kept during the loop:
    # a second set of numbers computed a second way is a second thing to be
    # wrong, and the two would drift apart the moment either changes
    data = load_run(run_dir)
    measured = {**trip_metrics(data["trips"]), **queue_metrics(data["queues"])}

    result = {**summary, **measured}
    for key in ("requested", "departed", "arrived", "still_in_network",
                "switches", "mean_queue_m", "max_queue_m", "trips_completed",
                "mean_waiting_s", "wall_clock_s"):
        if key in result:
            value = result[key]
            shown = f"{value:.2f}" if isinstance(value, float) else value
            print(f"  {key:18s} {shown}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gui", action="store_true")
    ap.add_argument("--duration", type=int, default=900)
    ap.add_argument("--strategy", default="actuated", choices=sorted(CONTROLLERS),
                    help="which controller to run on its own")
    ap.add_argument("--compare", action="store_true",
                    help="run all three: the network's own plan, a fixed-time "
                         "control group, and the Python controller")
    ap.add_argument("--csv", type=Path, default=None,
                    help="write the comparison to a CSV file")
    args = ap.parse_args()

    if not args.compare:
        run_and_measure(args.strategy, args.duration, args.gui)
        print("\nTip: add --compare to benchmark against the built-in "
              "fixed-time plan.")
        return 0

    rows = [(name, run_and_measure(name, args.duration, args.gui), why)
            for name, why in COMPARISON]
    base, control, ctrl = rows[0][1], rows[1][1], rows[2][1]

    print("\n--- comparison ---")
    print(f"  {'strategy':<11}{'mean wait':>11}{'mean queue':>13}{'switches':>10}")
    for name, row, why in rows:
        print(f"  {name:<11}{row['mean_waiting_s']:>9.2f} s"
              f"{row['mean_queue_m']:>11.2f} m{row['switches']:>10d}   {why}")

    # spell the arithmetic out rather than burying it in an f-string:
    # readers should be able to check the sign and the divisor at a glance
    def change(before: float, after: float) -> float:
        return (after - before) / max(before, 1e-9) * 100.0

    print("\n  the controller against the network's own plan:")
    for metric, unit in (("mean_waiting_s", "s"), ("mean_queue_m", "m")):
        print(f"    {metric:15s} {base[metric]:7.2f} -> {ctrl[metric]:7.2f} {unit}"
              f"   ({change(base[metric], ctrl[metric]):+.1f}%)")

    print("\n  the controller against `timed` - fixed-time that ignores the")
    print("  traffic completely and only cycles faster:")
    for metric, unit in (("mean_waiting_s", "s"), ("mean_queue_m", "m")):
        print(f"    {metric:15s} {control[metric]:7.2f} -> {ctrl[metric]:7.2f} {unit}"
              f"   ({change(control[metric], ctrl[metric]):+.1f}%)")

    print("\nnote: read the second table before the first.")
    print("      `fixed` is whatever netconvert wrote into the network, and")
    print("      the controller shortens the cycle as well as reacting to")
    print("      queues - so most of its lead over `fixed` is the shorter")
    print("      cycle, not the reacting.  `timed` is the control group that")
    print("      separates the two, and it is the honest comparison.")
    print("      On this network they come out about level.")
    print("      Deterministic setup, one scenario: this says nothing about")
    print("      other demand levels.  For that: run_experiments.py.")

    if args.csv:
        import csv
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        keys = ["strategy", "requested", "departed", "arrived",
                "still_in_network", "switches", "trips_completed",
                "mean_waiting_s", "mean_queue_m", "max_queue_m", "wall_clock_s"]
        with args.csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows([row for _n, row, _w in rows])
        print(f"  written to {args.csv.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
