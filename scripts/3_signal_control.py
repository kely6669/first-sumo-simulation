"""Step 3 - control the traffic signal from Python (actuated control).

Run:  python scripts/3_signal_control.py [--gui] [--duration 900]
      python scripts/3_signal_control.py --compare      # both controllers

This is the starting point of any signal-optimisation study:

    1. read state      traci.lane.getLastStepHaltingNumber(lane)
    2. decide          plain Python logic (here: gap-out / max-out)
    3. act             traci.trafficlight.setPhase(tls, phase)

Replace step 2 with reinforcement learning, a genetic algorithm or fuzzy
logic and you have a research contribution.  The infrastructure around it
does not change.

Controller
    minimum green  10 s   - never switch earlier
    maximum green  45 s   - force a switch to avoid starving the cross street
    gap-out              - after minimum green, switch if the serving
                           direction has cleared and the other one has demand
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

# allow running as `python scripts/3_signal_control.py` from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sumo_config import (  # noqa: E402
    ROOT, ROUTES, TLS_ID, setup_traci, start_args,
)

setup_traci()
import traci  # noqa: E402

END_OF_EDGE = 285
MIN_GREEN = 10
MAX_GREEN = 45

# Which lanes are served by each green phase (from the state strings in
# cross.net.xml: phase 0 and 6 give green to the east-west movements).
PHASE_LANES = {
    0: ["rightE_A_0", "rightE_A_1", "leftW_A_0", "leftW_A_1"],
    2: ["topS_A_0", "topS_A_1", "bottomN_A_0", "bottomN_A_1"],
    6: ["rightE_A_0", "rightE_A_1", "leftW_A_0", "leftW_A_1"],
}
NEXT_GREEN = {0: 2, 2: 6, 6: 0}
GREEN_PHASES = tuple(PHASE_LANES)


def total_queue(lanes: list[str]) -> int:
    return sum(traci.lane.getLastStepHaltingNumber(l) for l in lanes)


def run(duration: int, use_gui: bool, control: bool) -> dict:
    """Run one simulation and return summary metrics."""
    label = "actuated (python)" if control else "fixed-time (built in)"
    print(f"\n--- {label}, {duration} s ---")

    traci.start(start_args(gui=use_gui))
    if control:
        traci.trafficlight.setPhase(TLS_ID, 0)
        green_since = traci.simulation.getTime()
    else:
        green_since = 0.0

    next_add = {i: 0.0 for i in range(len(ROUTES))}
    veh_id = 0
    removed = 0
    switches = 0
    queue_samples: list[int] = []
    wait_samples: list[float] = []

    t0 = time.time()
    for step in range(duration):
        traci.simulationStep()
        now = traci.simulation.getTime()

        for i, (route, headway, _) in enumerate(ROUTES):
            if now >= next_add[i]:
                traci.vehicle.add(f"v{veh_id}", routeID=route,
                                  typeID="bus" if veh_id % 10 == 0 else "car",
                                  depart=str(now), departLane="best",
                                  departSpeed="max")
                next_add[i] = now + headway
                veh_id += 1

        for vid in traci.vehicle.getIDList():
            if (traci.vehicle.getRoadID(vid).startswith("A_")
                    and traci.vehicle.getLanePosition(vid) > END_OF_EDGE):
                traci.vehicle.remove(vid)
                removed += 1

        if control:
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

        # sample every 10 s for the summary statistics
        if step % 10 == 0:
            queue_samples.append(sum(total_queue(PHASE_LANES[p])
                                     for p in (0, 2)))
            wait_samples.append(sum(traci.lane.getWaitingTime(l)
                                    for l in PHASE_LANES[0]))

    result = {
        "controller": label,
        "inserted": veh_id,
        "completed": removed,
        "still_in_network": traci.vehicle.getIDCount(),
        "switches": switches,
        "mean_queue": sum(queue_samples) / len(queue_samples) if queue_samples else 0,
        "max_queue": max(queue_samples) if queue_samples else 0,
        "wall_clock_s": round(time.time() - t0, 1),
    }
    traci.close()
    for k, v in result.items():
        print(f"  {k:18s} {v if isinstance(v, str) else round(v, 2)}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gui", action="store_true")
    ap.add_argument("--duration", type=int, default=900)
    ap.add_argument("--compare", action="store_true",
                    help="run fixed-time first, then the Python controller")
    ap.add_argument("--csv", type=Path, default=None,
                    help="write the comparison to a CSV file")
    args = ap.parse_args()

    if args.compare:
        rows = [
            run(args.duration, args.gui, control=False),
            run(args.duration, args.gui, control=True),
        ]
        base, ctrl = rows
        print("\n--- comparison ---")
        print(f"  mean queue   {base['mean_queue']:7.1f} -> "
              f"{ctrl['mean_queue']:7.1f}  "
              f"({(ctrl['mean_queue'] - base['mean_queue']) / max(base['mean_queue'], 1) * 100:+.1f}%)")
        print(f"  completed    {base['completed']:7d} -> {ctrl['completed']:7d}")
        print("\nnote: this setup is deterministic (fixed departures, no random"
              "\n      seed), so repeating the run reproduces these numbers"
              "\n      exactly. That makes the difference real for this scenario"
              "\n      - but it does NOT tell you whether it holds at other"
              "\n      demand levels. Sweep the headways in sumo_config.ROUTES.")
        if args.csv:
            args.csv.parent.mkdir(parents=True, exist_ok=True)
            with args.csv.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            print(f"  written to {args.csv.relative_to(ROOT)}")
    else:
        run(args.duration, args.gui, control=True)
        print("\nTip: add --compare to benchmark against the built-in "
              "fixed-time plan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
