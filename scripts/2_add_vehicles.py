"""Step 2 - insert vehicles at runtime and clean them up at the border.

Run:  python scripts/2_add_vehicles.py [--gui] [--duration 600]

Nothing here controls the signal yet - the light runs on its built-in plan.
The point of this step is the two things that bite everybody the first time:

1. Vehicles do NOT disappear at the network border.
   Our border nodes are `dead_end`, so a vehicle that finishes its route
   just parks there and the network slowly fills up.  Inserting vehicles
   means you are also responsible for removing them.

2. Demand must respect capacity, otherwise queues grow forever.
   The signal cycle is 90 s and each direction gets ~24 s of green, so one
   approach discharges roughly 1800 * 24/90 ~= 480 veh/h - about one
   vehicle every 7.5 s.  The headways in sumo_config.ROUTES are set above
   that on purpose.  Set them to 6 s and watch the queue explode.

Both jobs live in ``demand.DemandInjector``, so this file is short and step 3
can reuse exactly the same behaviour instead of a second copy of it.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from demand import DemandInjector  # noqa: E402
from sumo_config import setup_traci, start_args  # noqa: E402

setup_traci()
import traci  # noqa: E402

#: lane whose queue is printed as a progress indicator
WATCH_LANE = "rightE_A_0"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gui", action="store_true")
    ap.add_argument("--duration", type=int, default=600, help="simulation seconds")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="headway multiplier; below 1.0 means more traffic")
    args = ap.parse_args()

    injector = DemandInjector(scale=args.scale)
    fastest, slowest = injector.headways

    print("Step 2 - add vehicles and clean up")
    print("-" * 52)
    print(f"demand: {len(injector.routes)} routes, headways "
          f"{fastest:.0f}-{slowest:.0f} s")
    print()

    traci.start(start_args(gui=args.gui))
    started = time.time()

    for step in range(args.duration):
        traci.simulationStep()
        now = traci.simulation.getTime()
        injector.step(now)
        injector.clear_finished()

        if step % 120 == 0:
            queue = traci.lane.getLastStepHaltingNumber(WATCH_LANE)
            print(f"  t={now:5.0f}s | in network {injector.still_in_network:3d} | "
                  f"inserted {injector.inserted:3d} | removed {injector.removed:3d} | "
                  f"east queue {queue:2d}")

    still_in = injector.still_in_network
    traci.close()

    print(f"\nsimulation finished in {time.time() - started:.1f} s wall clock")
    print(f"  inserted      {injector.inserted}")
    print(f"  completed     {injector.removed}")
    print(f"  still in net  {still_in}   (in transit when the clock ran out)")
    print("\nStep 2 done. Next: 3_signal_control.py replaces the fixed-time "
          "signal plan with a controller written in Python.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
