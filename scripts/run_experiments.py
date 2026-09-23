"""Run a batch of simulations: strategies x demand levels x seeds.

This is the experiment layer.  One simulation produces one set of output
files; running many of them is what turns "a simulation" into "a result".

    strategies : any name in control.CONTROLLERS
                 (fixed, timed, actuated, pressure - see the README)
    seeds      : SUMO's --seed, which controls departure randomness
    demand     : a multiplier on the headways in sumo_config.ROUTES

Each run gets its own directory, holding exactly the files a manual
``sumo-gui`` run would produce::

    results/runs/<strategy>_d<demand>_s<seed>/tripinfo.xml
                                             /summary.xml
                                             /queues.xml
                                             /sumo.log
                                             /control.txt
    results/index.csv      one row per run, listing the parameters

The batch is atomic: results/runs/ is wiped at the start so the index and
the directory can never disagree.  A half-replaced batch is worse than no
batch, because every number in it looks equally trustworthy.  Pass --keep to
add to an existing batch instead.

Run:  python scripts/run_experiments.py [--runs 2] [--duration 900]

Your own network instead of the cross::

    python scripts/run_experiments.py \\
        --net-file net/mine.net.xml --route-file net/mine.rou.xml \\
        --begin 25200 --duration 3600 --runs 5 \\
        --results-dir results/mine
    python scripts/analyse_results.py --results-dir results/mine --save

``--route-file`` hands demand to the route file, which is what a downloaded
scenario needs: its vehicles have real departure times and real routes, and
Python inserting its own would be measuring a different experiment.
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

#: Where a batch goes when --results-dir is not given.  A batch is atomic, so
#: two scenarios cannot share one directory without overwriting each other.
DEFAULT_RESULTS = ROOT / "results"


def _show(path: Path) -> str:
    """A path the way the user would type it: relative when it is in the repo."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def tag_for(strategy: str, demand: float, seed: int) -> str:
    """Directory name for one run.

    The parameters are in the name so a directory can be identified without
    opening index.csv, and ``demand`` is formatted to two decimals so 0.8 and
    0.80 do not produce two different names for the same thing.
    """
    return f"{strategy}_d{demand:.2f}_s{seed}"


def run_once(strategy: str, seed: int, demand: float, duration: int,
             runs_dir: Path, scenario: dict) -> dict:
    """One simulation.  Returns the row that goes into the index."""
    tag = tag_for(strategy, demand, seed)
    row = run_simulation(runs_dir / tag, strategy=strategy, seed=seed,
                         demand=demand, duration=duration, label=tag,
                         **scenario)
    # store the directory relative to the repo so index.csv survives being
    # moved, cloned or opened on another machine.  If the results are outside
    # the repo, the absolute path is the only one that still resolves.
    try:
        row["run_dir"] = str(Path(row["run_dir"]).relative_to(ROOT))
    except ValueError:
        row["run_dir"] = str(Path(row["run_dir"]))
    row["tag"] = tag
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=2, help="seeds per combination")
    ap.add_argument("--duration", type=int, default=900)
    ap.add_argument("--demands", type=float, nargs="+", default=[1.0],
                    help="headway multipliers, e.g. 0.8 1.0 1.2")
    ap.add_argument("--strategies", nargs="+",
                    default=["fixed", "timed", "actuated", "pressure"],
                    choices=sorted(CONTROLLERS))
    ap.add_argument("--net-file", type=Path, default=None,
                    help="network to simulate.  Default is the hand-written "
                         "cross.  Point this at your own .net.xml to run the "
                         "same comparison on your own junction.")
    ap.add_argument("--route-file", type=Path, default=None,
                    help="demand.  Giving one switches to the route file's "
                         "own vehicles, which is what a real scenario needs; "
                         "without it, Python inserts vehicles itself and only "
                         "the cross works.")
    ap.add_argument("--begin", type=int, default=0,
                    help="simulation clock time to start at.  Real scenarios "
                         "carry real departure times - cologne1's traffic "
                         "leaves at 07:00 - so starting at 0 simulates an "
                         "empty hour and measures nothing.")
    ap.add_argument("--results-dir", type=Path, default=None,
                    help="where runs/ and index.csv live.  Default results/. "
                         "Use a different one per scenario, or the next batch "
                         "overwrites this one.")
    ap.add_argument("--keep", action="store_true",
                    help="do not wipe runs/ first (results may then mix "
                         "incompatible batches)")
    args = ap.parse_args()

    if args.net_file is None and args.route_file is not None:
        ap.error("--route-file needs --net-file as well")
    if (args.net_file or args.route_file) and args.demands != [1.0]:
        ap.error("--demands scales the cross's own headways; it does nothing "
                 "to a scenario that carries its own vehicles.  Drop one of "
                 "--demands / --route-file.")

    results_dir = (args.results_dir or DEFAULT_RESULTS).resolve()
    runs_dir = results_dir / "runs"
    index = results_dir / "index.csv"
    scenario = {
        "drive_demand": args.route_file is None,
        "begin": args.begin,
    }
    if args.net_file is not None:
        scenario["net_file"] = args.net_file.resolve()
    if args.route_file is not None:
        scenario["route_file"] = args.route_file.resolve()
    if "net_file" in scenario and "route_file" not in scenario:
        scenario["route_file"] = ROOT / "net" / "simple.rou.xml"

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
        print(f"  --keep: adding to the existing {_show(runs_dir)}")
    elif runs_dir.exists():
        stale = sum(1 for _ in runs_dir.iterdir())
        shutil.rmtree(runs_dir)
        print(f"  cleared {_show(runs_dir)} ({stale} old entr(ies) removed)")
    print()

    rows = []
    for i, (strategy, seed, demand) in enumerate(combos, 1):
        row = run_once(strategy, seed, demand, args.duration, runs_dir, scenario)
        rows.append(row)
        print(f"  [{i:2d}/{len(combos)}] {tag_for(strategy, demand, seed):22s} "
              f"requested={row['requested'] if row['requested'] is not None else '-':>4}  "
              f"departed={row['departed']:5d}  "
              f"arrived={row['arrived']:5d}  left={row['still_in_network']:4d}  "
              f"switches={row['switches']:4d}  ({row['wall_clock_s']}s)")

    index.parent.mkdir(parents=True, exist_ok=True)
    # wall_clock_s is the machine's stopwatch, not a result.  It is printed
    # above while the batch runs, but leaving it in the committed index would
    # mean the file differs on every run - the same problem as SUMO's `step`
    # duration attribute, which analyse_run.read_summary_steps drops.
    fields = [name for name in rows[0] if name != "wall_clock_s"]
    with index.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nwrote {_show(index)}  ({len(rows)} runs)")
    print(f"raw output in {_show(runs_dir)}  "
          f"({sum(1 for _ in runs_dir.rglob('*.xml'))} xml files)")
    print("\nnext: python scripts/analyse_results.py --save"
          + (f" --results-dir {_show(results_dir)}"
             if results_dir != DEFAULT_RESULTS else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
