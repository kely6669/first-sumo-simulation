"""Step 1 - connect Python to SUMO through TraCI and read live data.

Run:  python scripts/1_connect.py [--gui]

What this shows
    traci.start() launches a SUMO process and opens a two-way channel to it.
    From then on every traci.* call is a message to that running process -
    nothing is re-read from disk.

Key API
    traci.start([...])                    launch SUMO + connect
    traci.simulationStep()                advance one simulation second
    traci.simulation.getTime()            current simulation time
    traci.vehicle.getIDCount()            vehicles currently in the network
    traci.lane.getLastStepHaltingNumber() vehicles stopped on a lane
    traci.close()                         disconnect
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sumo_config import setup_traci, start_args  # noqa: E402

setup_traci()
import traci  # noqa: E402

STEPS = 10


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gui", action="store_true", help="show the SUMO window")
    ap.add_argument("--steps", type=int, default=STEPS, help="simulation seconds")
    args = ap.parse_args()

    print("Step 1 - connect and read")
    print("-" * 52)

    traci.start(start_args(gui=args.gui))
    print(f"connected, SUMO version {traci.getVersion()[1]}")

    for step in range(1, args.steps + 1):
        traci.simulationStep()
        now = traci.simulation.getTime()
        n_veh = traci.vehicle.getIDCount()
        queue = traci.lane.getLastStepHaltingNumber("rightE_A_0")
        print(f"  step {step:2d} | t={now:5.1f}s | vehicles {n_veh} | "
              f"east-approach queue {queue}")

    traci.close()
    print("\nStep 1 done - the channel is open and readable.")
    print("Next: 2_add_vehicles.py inserts vehicles while the simulation runs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
