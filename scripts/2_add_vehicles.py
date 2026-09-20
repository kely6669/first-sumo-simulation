"""Step 2 - insert vehicles at runtime and clean them up at the network border.

Run:  python scripts/2_add_vehicles.py [--gui] [--duration 600]

Two things bite everybody the first time:

1. Vehicles do NOT disappear at the network border.
   Our border nodes are `dead_end`, so a vehicle that finishes its route
   just parks there and the network slowly fills up.  Inserting vehicles
   means you are also responsible for removing them.

2. Demand must respect capacity, otherwise queues grow forever.
   The signal cycle is 90 s and each direction gets ~24 s of green, so one
   approach discharges roughly 1800 * 24/90 ~= 480 veh/h - about one
   vehicle every 7.5 s.  The headways in sumo_config.ROUTES are set above
   that on purpose.  Set them to 6 s and watch the queue explode.

Key API
    traci.vehicle.add(vehID, routeID, typeID, depart, ...)
    traci.vehicle.getIDList() / getRoadID() / getLanePosition()
    traci.vehicle.remove(vehID)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sumo_config import ROUTES, setup_traci, start_args  # noqa: E402

setup_traci()
import traci  # noqa: E402

END_OF_EDGE = 285          # metres; past this the vehicle has crossed the border


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gui", action="store_true")
    ap.add_argument("--duration", type=int, default=600, help="simulation seconds")
    args = ap.parse_args()

    print("Step 2 - add vehicles and clean up")
    print("-" * 52)
    print(f"demand: {len(ROUTES)} routes, headways "
          f"{min(g for _, g, _ in ROUTES):.0f}-{max(g for _, g, _ in ROUTES):.0f} s")
    print()

    traci.start(start_args(gui=args.gui))

    next_add = {i: 0.0 for i in range(len(ROUTES))}
    veh_id = 0
    removed = 0
    t0 = time.time()

    for step in range(args.duration):
        traci.simulationStep()
        now = traci.simulation.getTime()

        # insert on schedule
        for i, (route, headway, _) in enumerate(ROUTES):
            if now >= next_add[i]:
                traci.vehicle.add(
                    vehID=f"v{veh_id}",
                    routeID=route,
                    typeID="bus" if veh_id % 10 == 0 else "car",
                    depart=str(now),
                    departLane="best",
                    departSpeed="max",
                )
                next_add[i] = now + headway
                veh_id += 1

        # remove vehicles that reached the far end of an exit edge
        for vid in traci.vehicle.getIDList():
            if (traci.vehicle.getRoadID(vid).startswith("A_")
                    and traci.vehicle.getLanePosition(vid) > END_OF_EDGE):
                traci.vehicle.remove(vid)
                removed += 1

        if step % 120 == 0:
            east = traci.lane.getLastStepHaltingNumber("rightE_A_0")
            print(f"  t={now:5.0f}s | in network {traci.vehicle.getIDCount():3d} | "
                  f"inserted {veh_id:3d} | removed {removed:3d} | "
                  f"east queue {east:2d}")

    still_in = traci.vehicle.getIDCount()
    traci.close()

    print(f"\nsimulation finished in {time.time() - t0:.1f} s wall clock")
    print(f"  inserted      {veh_id}")
    print(f"  completed     {removed}")
    print(f"  still in net  {still_in}   (in transit when the clock ran out)")
    print("\nStep 2 done. Next: 3_signal_control.py replaces the fixed-time "
          "signal plan with a controller written in Python.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
