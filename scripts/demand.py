"""Demand: putting vehicles into the network and taking them out again.

This is the "act" half of the loop that does not involve the signal.  It used
to be copied into ``2_add_vehicles.py``, ``3_signal_control.py`` and
``run_experiments.py``; it lives here once.

Two jobs, and the second one surprises everybody:

1. Insert vehicles on a schedule.
   ``sumo_config.ROUTES`` gives one route and a mean headway per movement.
   Vehicles are added from Python rather than from a route file so that the
   demand can depend on what is happening in the simulation - which is what
   you need the moment a controller, rather than a timetable, decides things.

2. Remove vehicles that have finished.
   The four border nodes are ``dead_end``, so a vehicle that completes its
   route does **not** disappear.  It parks at the end of the exit edge and
   the network fills up with ghosts.  Inserting vehicles yourself means
   removing them yourself.

The headways are set above the junction's capacity on purpose.  The signal
cycle is 90 s with roughly 24 s of green per direction, so one approach
discharges about 1800 * 24/90 ~= 480 veh/h - one vehicle every 7.5 s.  Go
below that and the queue grows without bound; that is a property of the
junction, not a bug in this file.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sumo_config import ROUTES, setup_traci  # noqa: E402

setup_traci()
import traci  # noqa: E402

#: An exit edge is ARM_LENGTH (300 m) long.  A vehicle past this point has
#: left the area we are studying, so it is removed.
END_OF_EDGE = 285

#: Prefix of the exit edges, used to recognise "this vehicle is leaving".
EXIT_EDGE_PREFIX = "A_"

#: Every tenth vehicle is a bus.  Buses accelerate and depart more slowly
#: than cars, so having both keeps the results from being a car-only ideal.
BUS_EVERY = 10


class DemandInjector:
    """Inserts vehicles on schedule and clears the ones that finished.

    Typical use, once per simulation second::

        injector.step(now)
        injector.clear_finished()

    Args:
        routes: (route id, headway seconds, final edge) triples.  Defaults to
            ``sumo_config.ROUTES``.
        scale: multiplier on every headway.  ``0.8`` means "20% more traffic",
            ``1.2`` means "20% less".  This is the demand knob the experiment
            sweep varies.
    """

    def __init__(self, routes=ROUTES, scale: float = 1.0) -> None:
        self.routes = list(routes)
        self.scale = scale
        self.inserted = 0
        self.removed = 0
        self._next_add = [0.0] * len(self.routes)

    @property
    def headways(self) -> tuple[float, float]:
        """The narrowest and widest effective headway, in seconds."""
        values = [h * self.scale for _, h, _ in self.routes]
        return min(values), max(values)

    def step(self, now: float) -> int:
        """Insert every vehicle that is due at time ``now``.

        Returns:
            How many vehicles were inserted this step.
        """
        added = 0
        for i, (route, headway, _) in enumerate(self.routes):
            if now < self._next_add[i]:
                continue
            traci.vehicle.add(
                f"v{self.inserted}",
                routeID=route,
                typeID="bus" if self.inserted % BUS_EVERY == 0 else "car",
                depart=str(now),
                departLane="best",
                departSpeed="max",
            )
            self._next_add[i] = now + headway * self.scale
            self.inserted += 1
            added += 1
        return added

    def clear_finished(self) -> int:
        """Remove vehicles that have driven past the end of an exit edge.

        Returns:
            How many vehicles were removed this step.
        """
        removed = 0
        for veh_id in traci.vehicle.getIDList():
            on_exit = traci.vehicle.getRoadID(veh_id).startswith(EXIT_EDGE_PREFIX)
            if on_exit and traci.vehicle.getLanePosition(veh_id) > END_OF_EDGE:
                traci.vehicle.remove(veh_id)
                removed += 1
        self.removed += removed
        return removed

    @property
    def still_in_network(self) -> int:
        """Vehicles currently inside the network."""
        return traci.vehicle.getIDCount()
