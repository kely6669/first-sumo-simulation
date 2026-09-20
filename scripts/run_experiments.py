"""Run a batch of simulations: strategies x seeds x demand levels.

This is the experiment layer.  One simulation produces one set of output
files; running many of them is what turns "a simulation" into "a result".

    strategies : fixed-time, actuated (python), and you can add RL later
    seeds      : SUMO's --seed, which controls departure randomness
    demand     : a multiplier on the headways in sumo_config.ROUTES

Everything lands in results/runs/ as CSV so pandas can read it directly:

    results/runs/<strategy>_d<demand>_s<seed>_tripinfo.csv
    results/runs/<strategy>_d<demand>_s<seed>_summary.csv

Run:  python scripts/run_experiments.py [--runs 2] [--duration 900]
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sumo_config import (  # noqa: E402
    ROOT, ROUTES, TLS_ID, find_sumo_home, setup_traci, sumo_binary,
)

setup_traci()
import traci  # noqa: E402

END_OF_EDGE = 285
MIN_GREEN = 10
MAX_GREEN = 45

PHASE_LANES = {
    0: ["rightE_A_0", "rightE_A_1", "leftW_A_0", "leftW_A_1"],
    2: ["topS_A_0", "topS_A_1", "bottomN_A_0", "bottomN_A_1"],
    6: ["rightE_A_0", "rightE_A_1", "leftW_A_0", "leftW_A_1"],
}
NEXT_GREEN = {0: 2, 2: 6, 6: 0}

RUNS_DIR = ROOT / "results" / "runs"


def total_queue(lanes):
    return sum(traci.lane.getLastStepHaltingNumber(l) for l in lanes)


def run_once(strategy: str, seed: int, demand: float, duration: int) -> dict:
    """One simulation.  Returns the row that goes into the result table."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"{strategy}_d{demand:.2f}_s{seed}"
    trip_csv = RUNS_DIR / f"{tag}_tripinfo.csv"
    summary_csv = RUNS_DIR / f"{tag}_summary.csv"

    cmd = [
        str(sumo_binary(gui=False)),
        "-n", str(ROOT / "net" / "cross.net.xml"),
        "-r", str(ROOT / "net" / "simple.rou.xml"),
        "--output.format", "csv",           # <- makes pandas' life easy
        "--tripinfo-output", str(trip_csv),
        "--summary-output", str(summary_csv),
        "--seed", str(seed),
        "--no-step-log", "true",
        "--no-warnings", "true",
        "--time-to-teleport", "-1",         # never teleport: keeps results honest
    ]
    traci.start(cmd)

    if strategy == "actuated":
        traci.trafficlight.setPhase(TLS_ID, 0)
        green_since = traci.simulation.getTime()

    next_add = {i: 0.0 for i in range(len(ROUTES))}
    veh_id = 0
    switches = 0
    t0 = time.time()

    for _ in range(duration):
        traci.simulationStep()
        now = traci.simulation.getTime()

        for i, (route, headway, _) in enumerate(ROUTES):
            if now >= next_add[i]:
                traci.vehicle.add(
                    f"v{veh_id}", routeID=route,
                    typeID="bus" if veh_id % 10 == 0 else "car",
                    depart=str(now), departLane="best", departSpeed="max",
                )
                next_add[i] = now + headway * demand
                veh_id += 1

        for vid in traci.vehicle.getIDList():
            if (traci.vehicle.getRoadID(vid).startswith("A_")
                    and traci.vehicle.getLanePosition(vid) > END_OF_EDGE):
                traci.vehicle.remove(vid)

        if strategy == "actuated":
            phase = traci.trafficlight.getPhase(TLS_ID)
            if phase in PHASE_LANES:
                elapsed = now - green_since
                served = total_queue(PHASE_LANES[phase])
                waiting = total_queue(PHASE_LANES[NEXT_GREEN[phase]])
                if elapsed >= MAX_GREEN or (elapsed >= MIN_GREEN
                                            and served == 0 and waiting > 0):
                    traci.trafficlight.setPhase(TLS_ID, NEXT_GREEN[phase])
                    green_since = now
                    switches += 1

    left = traci.vehicle.getIDCount()
    traci.close()

    return {
        "strategy": strategy,
        "seed": seed,
        "demand": demand,
        "duration": duration,
        "inserted": veh_id,
        "switches": switches,
        "still_in_network": left,
        "wall_clock_s": round(time.time() - t0, 1),
        "tripinfo_file": trip_csv.name,
        "summary_file": summary_csv.name,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=2, help="seeds per combination")
    ap.add_argument("--duration", type=int, default=900)
    ap.add_argument("--demands", type=float, nargs="+", default=[1.0],
                    help="headway multipliers, e.g. 0.8 1.0 1.2")
    ap.add_argument("--strategies", nargs="+",
                    default=["fixed", "actuated"],
                    choices=["fixed", "actuated"])
    args = ap.parse_args()

    combos = [(s, seed, d)
              for s in args.strategies
              for d in args.demands
              for seed in range(args.runs)]
    print(f"running {len(combos)} simulations "
          f"({len(args.strategies)} strategies x {len(args.demands)} demand "
          f"levels x {args.runs} seeds), {args.duration} s each")
    print()

    rows = []
    for i, (strategy, seed, demand) in enumerate(combos, 1):
        row = run_once(strategy, seed, demand, args.duration)
        rows.append(row)
        print(f"  [{i:2d}/{len(combos)}] {strategy:8s} seed={seed} "
              f"demand={demand:.2f}  inserted={row['inserted']:4d}  "
              f"left={row['still_in_network']:3d}  "
              f"switches={row['switches']:3d}  ({row['wall_clock_s']}s)")

    index = ROOT / "results" / "index.csv"
    index.parent.mkdir(parents=True, exist_ok=True)
    with index.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"\nwrote {index.relative_to(ROOT)}  ({len(rows)} runs)")
    print(f"raw outputs in results/runs/  "
          f"({len(list(RUNS_DIR.glob('*.csv')))} files)")
    print("\nnext: python scripts/analyse_results.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
