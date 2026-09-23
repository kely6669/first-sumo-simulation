"""Demand: putting vehicles into the network.

This is the "act" half of the loop that does not involve the signal.  It used
to be copied into ``2_add_vehicles.py``, ``3_signal_control.py`` and
``run_experiments.py``; it lives here once.

Vehicles are added from Python rather than from a route file so that the
demand can depend on what is happening in the simulation - which is what you
need the moment a controller, rather than a timetable, decides things.  SUMO
takes them out again by itself when they reach the end of their route; see
the note below, which corrects an earlier version of this file.

The headways are set above the junction's capacity on purpose.  The signal
cycle is 90 s with roughly 24 s of green per direction, so one approach
discharges about 1800 * 24/90 ~= 480 veh/h - one vehicle every 7.5 s.  Go
below that and the queue grows without bound; that is a property of the
junction, not a bug in this file.


WHO REMOVES FINISHED VEHICLES

This file used to do it, with a ``clear_finished()`` method that deleted any
vehicle more than 285 m along an exit edge.  The reasoning was that the four
border nodes are ``dead_end``, so a vehicle that finished its route would
park there and the network would fill up with ghosts.

That reasoning was wrong, and it went untested for a long time.  Measured on
SUMO 1.26 with net/cross.net.xml:

  * 302 vehicles inserted, no removal code at all: 279 completed on their
    own, 23 were still in transit at the end, and 279 + 23 = 302.  The
    books balance, so nothing was stuck.
  * Tracking every vehicle to the step it vanished: all 279 disappeared from
    an exit edge, none from a junction or an approach.
  * The exit edges are 289.60 m long, not the 300 m the node coordinates
    suggest - the junction radius is subtracted.  Vehicles were last seen
    between 275.9 m and 289.5 m, and a vehicle covers at most ~14 m in a
    step, so they all vanished at the far end of the edge.  That is SUMO
    removing a vehicle that finished its route.

So ``dead_end`` does not trap vehicles, and the manual removal was not just
redundant - it was harmful.  It deleted vehicles about 4.6 m (0.33 s) before
SUMO would have, and ``traci.vehicle.remove()`` is counted by SUMO as an
arrival, so those vehicles entered the statistics as trips that completed
normally.  A run with the removal active reported 280 arrivals plus 116
removals plus 22 in the network: 418 outcomes for 302 vehicles.

The method has been deleted.  Insertion is all this module does now, and the
number of completed trips is read from SUMO's own ``arrived`` counter, which
is what ``summary.xml`` records.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sumo_config import ROUTES, setup_traci  # noqa: E402

setup_traci()
import traci  # noqa: E402

#: Every tenth vehicle is a bus.  Buses accelerate and depart more slowly
#: than cars, so having both keeps the results from being a car-only ideal.
BUS_EVERY = 10


class DemandInjector:
    """Inserts vehicles on schedule.

    Typical use, once per simulation second::

        injector.step(now)

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

        Note that this asks SUMO to insert them; it does not guarantee they
        get in.  When the network is full an insertion is refused, and SUMO
        logs a warning.  Use ``traci.simulation.getDepartedNumber()`` to
        count the ones that actually entered.
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

    @property
    def still_in_network(self) -> int:
        """Vehicles currently inside the network."""
        return traci.vehicle.getIDCount()
