"""Run a batch of simulations: strategies x demand levels x seeds.

This is the experiment layer.  One simulation produces one set of output
files; running many of them is what turns "a simulation" into "a result".

    strategies : any name in control.CONTROLLERS (fixed, actuated, ...)
    seeds      : SUMO's --seed, which controls departure randomness
    demand     : a multiplier on the headways in sumo_config.ROUTES

Each run gets its own directory, holding exactly the files a manual
``sumo-gui`` run would produce::

    results/runs/<strategy>_d<demand>_s<seed>/tripinfo.xml
                                             /summary.xml
                                             /queues.xml
                                             /sumo.log
    results/index.csv      one row per run, listing the parameters

The batch is atomic: results/runs/ is wiped at the start so the index and
the directory can never disagree.  A half-replaced batch is worse than no
batch, because every number in it looks equally trustworthy.  Pass --keep to
add to an existing batch instead.

Run:  python scripts/run_experiments.py [--runs 2] [--duration 900]
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from control import CONTROLLERS  # noqa: E402
from runner import run_simulation  # noqa: E402
from sumo_config import ROOT  # noqa: E402

RUNS_DIR = ROOT / "results" / "runs"
INDEX = ROOT / "results" / "index.csv"


def tag_for(strategy: str, demand: float, seed: int) -> str:
    """Directory name for one run.

    The parameters are in the name so a directory can be identified without
    opening index.csv, and ``demand`` is formatted to two decimals so 0.8 and
    0.80 do not produce two different names for the same thing.
    """
    return f"{strategy}_d{demand:.2f}_s{seed}"


def run_once(strategy: str, seed: int, demand: float, duration: int) -> dict:
    """One simulation.  Returns the row that goes into results/index.csv."""
    tag = tag_for(strategy, demand, seed)
    row = run_simulation(RUNS_DIR / tag, strategy=strategy, seed=seed,
                         demand=demand, duration=duration, label=tag)
    # store the directory relative to the repo so index.csv survives being
    # moved, cloned or opened on another machine
    row["run_dir"] = str(Path(row["run_dir"]).relative_to(ROOT))
    row["tag"] = tag
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=2, help="seeds per combination")
    ap.add_argument("--duration", type=int, default=900)
    ap.add_argument("--demands", type=float, nargs="+", default=[1.0],
                    help="headway multipliers, e.g. 0.8 1.0 1.2")
    ap.add_argument("--strategies", nargs="+", default=["fixed", "actuated"],
                    choices=sorted(CONTROLLERS))
    ap.add_argument("--keep", action="store_true",
                    help="do not wipe results/runs/ first (results may then "
                         "mix incompatible batches)")
    args = ap.parse_args()

    combos = [(s, seed, d)
              for s in args.strategies
              for d in sorted(args.demands)
              for seed in range(args.runs)]

    print("=" * 74)
    print("running simulations")
    print("=" * 74)
    print(f"  {len(combos)} runs = {len(args.strategies)} strategies "
          f"x {len(args.demands)} demand levels x {args.runs} seeds, "
          f"{args.duration} s each")

    if args.keep:
        print("  --keep: adding to the existing results/runs/")
    elif RUNS_DIR.exists():
        stale = sum(1 for _ in RUNS_DIR.iterdir())
        shutil.rmtree(RUNS_DIR)
        print(f"  cleared results/runs/ ({stale} old entr(ies) removed)")
    print()

    rows = []
    for i, (strategy, seed, demand) in enumerate(combos, 1):
        row = run_once(strategy, seed, demand, args.duration)
        rows.append(row)
        print(f"  [{i:2d}/{len(combos)}] {tag_for(strategy, demand, seed):22s} "
              f"requested={row['requested']:4d}  departed={row['departed']:4d}  "
              f"arrived={row['arrived']:4d}  left={row['still_in_network']:3d}  "
              f"switches={row['switches']:3d}  ({row['wall_clock_s']}s)")

    INDEX.parent.mkdir(parents=True, exist_ok=True)
    # wall_clock_s is the machine's stopwatch, not a result.  It is printed
    # above while the batch runs, but leaving it in the committed index would
    # mean the file differs on every run - the same problem as SUMO's `step`
    # duration attribute, which analyse_run.read_summary_steps drops.
    fields = [name for name in rows[0] if name != "wall_clock_s"]
    with INDEX.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nwrote {INDEX.relative_to(ROOT)}  ({len(rows)} runs)")
    print(f"raw output in results/runs/  "
          f"({sum(1 for _ in RUNS_DIR.rglob('*.xml'))} xml files)")
    print("\nnext: python scripts/analyse_results.py --save")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
