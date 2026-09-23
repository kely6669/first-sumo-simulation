"""Step 2 - insert vehicles at runtime.

Run:  python scripts/2_add_vehicles.py [--gui] [--duration 600] [--scale 1.0]

Nothing here controls the signal yet - the light runs on its built-in plan.
The point of this step is inserting traffic from Python, which is what you
need before a controller can react to it.

Two things are worth knowing before you write your own version:

1. Demand must respect capacity, otherwise queues grow forever.
   The signal cycle is 90 s and each direction gets ~24 s of green, so one
   approach discharges roughly 1800 * 24/90 ~= 480 veh/h - about one
   vehicle every 7.5 s.  The headways in sumo_config.ROUTES are set above
   that on purpose.  Set them to 6 s and watch the queue explode.

2. You do NOT have to remove vehicles when they finish.
   The border nodes are `dead_end`, which makes it look as if a vehicle that
   completes its route would park there forever - and this file used to
   carry a `clear_finished()` method on exactly that assumption.  It was
   wrong: SUMO removes a vehicle that reaches the end of its route, and
   removing it yourself marks it as an arrival anyway.  See the note in
   ``demand.py`` for the measurements.  Count completed trips with
   ``traci.simulation.getArrivedNumber()``, not with a counter of your own.

Insertion lives in ``demand.DemandInjector``, so this file is short and step 3
reuses exactly the same behaviour instead of a second copy of it.
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

    print("Step 2 - add vehicles")
    print("-" * 52)
    print(f"demand: {len(injector.routes)} routes, headways "
          f"{fastest:.0f}-{slowest:.0f} s")
    print()

    traci.start(start_args(gui=args.gui))
    started = time.time()
    arrived = 0

    for step in range(args.duration):
        traci.simulationStep()
        # SUMO reports how many vehicles entered and how many finished in the
        # step just taken.  Both are counted here so the totals below can be
        # reconciled: departed = arrived + still in network.
        arrived += traci.simulation.getArrivedNumber()
        now = traci.simulation.getTime()
        injector.step(now)

        if step % 120 == 0:
            queue = traci.lane.getLastStepHaltingNumber(WATCH_LANE)
            print(f"  t={now:5.0f}s | in network {injector.still_in_network:3d} | "
                  f"requested {injector.inserted:3d} | arrived {arrived:3d} | "
                  f"east queue {queue:2d}")

    still_in = injector.still_in_network
    traci.close()

    print(f"\nsimulation finished in {time.time() - started:.1f} s wall clock")
    print(f"  requested     {injector.inserted}   (how many we asked SUMO to insert)")
    print(f"  arrived       {arrived}   (SUMO removed them at the end of their route)")
    print(f"  still in net  {still_in}   (in transit when the clock ran out)")
    print("\nStep 2 done. Next: 3_signal_control.py replaces the fixed-time "
          "signal plan with a controller written in Python.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
