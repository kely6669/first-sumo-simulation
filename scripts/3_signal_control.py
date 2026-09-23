"""Step 3 - control the traffic signal from Python.

Run:  python scripts/3_signal_control.py [--gui] [--duration 900]
      python scripts/3_signal_control.py --compare      # both controllers

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

#: Where --compare puts the two runs it produces.
CONTROL_DIR = ROOT / "results" / "control"


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
                    help="run fixed-time first, then the actuated controller")
    ap.add_argument("--csv", type=Path, default=None,
                    help="write the comparison to a CSV file")
    args = ap.parse_args()

    if not args.compare:
        run_and_measure(args.strategy, args.duration, args.gui)
        print("\nTip: add --compare to benchmark against the built-in "
              "fixed-time plan.")
        return 0

    base = run_and_measure("fixed", args.duration, args.gui)
    ctrl = run_and_measure("actuated", args.duration, args.gui)

    print("\n--- comparison ---")
    # spell the arithmetic out rather than burying it in an f-string:
    # readers should be able to check the sign and the divisor at a glance
    base_queue = base["mean_queue_m"]
    ctrl_queue = ctrl["mean_queue_m"]
    change_pct = (ctrl_queue - base_queue) / max(base_queue, 1e-9) * 100.0
    print(f"  mean queue   {base_queue:7.2f} -> {ctrl_queue:7.2f}  "
          f"({change_pct:+.1f}%)")
    print(f"  mean wait    {base['mean_waiting_s']:7.2f} -> "
          f"{ctrl['mean_waiting_s']:7.2f} s")
    print(f"  completed    {base['trips_completed']:7d} -> "
          f"{ctrl['trips_completed']:7d}   (trips SUMO recorded as finished)")
    print(f"  phase switches {base['switches']:5d} -> {ctrl['switches']:5d}")
    print("\nnote: this setup is deterministic (fixed departures, no random"
          "\n      seed), so repeating the run reproduces these numbers"
          "\n      exactly. That makes the difference real for this scenario"
          "\n      - but it does NOT tell you whether it holds at other"
          "\n      demand levels. Sweep the headways in sumo_config.ROUTES,")
    print("      or run:  python scripts/run_experiments.py")

    if args.csv:
        import csv
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        keys = ["strategy", "requested", "departed", "arrived",
                "still_in_network", "switches", "trips_completed",
                "mean_waiting_s", "mean_queue_m", "max_queue_m", "wall_clock_s"]
        with args.csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows([base, ctrl])
        print(f"  written to {args.csv.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
