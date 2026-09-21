"""Traffic signal controllers - the "decide" step of the control loop.

Every controller answers one question, once per simulation second:

    is it time to change the phase?

That is the whole interface.  A controller may keep any internal state it
likes, but it must expose:

    name        short id, also the value used on the command line
    switches    how many times it changed the phase
    reset()     called once, right after SUMO has started
    step(now)   called once per simulation second

Adding a new strategy
    Write a class with the four members above and add it to CONTROLLERS.
    Nothing else in the project needs to change - which is what the README
    means by "replace the decision step with reinforcement learning".

Nothing here is specific to one intersection
    The signal plan used to be hard-coded: a TLS id of "A" and three phase
    indices with the cross's lane names spelled out.  That made the
    controller silently useless on any other network - the lane names simply
    do not exist there, so every queue read returned 0 and the controller
    believed the roads were empty.  It now reads the plan out of SUMO at
    startup, so it works on any network that has traffic lights.


HOW SUMO'S TRAFFIC LIGHTS ACTUALLY BEHAVE

This was measured, not assumed.  Probe on SUMO 1.26 with net/cross.net.xml:

  * ``getControlledLinks(tls)`` returns one entry per controlled link, and
    the phase ``state`` string has exactly one character per entry.  So
    ``state[i] in "Gg"`` means "controlled link i has green", and the lane
    that link comes from is ``getControlledLinks(tls)[i][0][0]``.  On the
    cross that is 12 links and a 12-character state.

  * ``setPhase(tls, i)`` on its own does **not** take the light over.  It
    jumps to phase i, and SUMO then carries on through its own program:

        setPhase("A", 2)
        t=25s phase 2 -> 3      <- SUMO moved on by itself
        t=28s phase 3 -> 4
        t=34s phase 4 -> 5

    A controller built on ``setPhase`` alone is therefore fighting the
    built-in plan rather than replacing it.  That is exactly the bug this
    module used to have, and it is why the "actuated" arm used to lose to
    fixed-time: it was not controlling anything, it was interrupting a plan
    that was already tuned.

  * ``setPhase`` **plus** ``setPhaseDuration`` does take over: the phase
    then stays put until it is changed or the duration runs out.

  * A phase that contains a ``y`` anywhere in its state is a transition
    (yellow / all-red), never somewhere to rest.  Green phases are the ones
    with no ``y`` in them.  On the cross that is phases 0, 2, 4 and 6 - note
    phase 4, a 6-second protected-left phase that the old hard-coded table
    had missed entirely.

WHAT THE ACTUATED CONTROLLER IS ALLOWED TO DO

It may end a green **early**; it never extends one past the duration the
network's own plan gives it.  That asymmetry is deliberate.  Extending is
how a controller starves the cross street - on the cross, phase 4 is only 6
seconds long, and holding it for MAX_GREEN would leave the other three
approaches standing still.  Ending early cannot starve anybody, so that is
the only power this controller takes.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sumo_config import setup_traci  # noqa: E402

setup_traci()
import traci  # noqa: E402

# Actuated control limits, in seconds.
MIN_GREEN = 10   # never cut a green shorter than this
MAX_GREEN = 45   # force a switch, for networks whose greens run longer

#: Characters in a phase state that mean "this link may go".
GREEN_CHARS = "Gg"

#: A phase containing this character is a transition, not a resting place.
TRANSITION_CHAR = "y"


def total_queue(lanes) -> int:
    """Number of halted vehicles across the given lanes.

    This is the only sensor the controller gets.  SUMO's
    ``getLastStepHaltingNumber`` counts vehicles that moved slower than
    0.1 m/s during the previous step, which is SUMO's definition of queued.
    Reading one number per lane is cheap enough to do every second.
    """
    return sum(traci.lane.getLastStepHaltingNumber(lane) for lane in lanes)


@dataclass
class SignalPlan:
    """What one traffic light's program looks like, read from SUMO.

    Attributes:
        tls_id: the traffic light this describes.
        phase_count: how many phases the program has.
        lanes: phase index -> the approach lanes that have green in it.
            Only green phases appear here; a transition phase is absent,
            which is how callers recognise one.
        next_green: green phase index -> the next green phase in program
            order.
        losing: green phase index -> the lanes that would LOSE green if the
            controller switched now.
        gaining: green phase index -> the lanes that would GAIN green.
    """

    tls_id: str
    phase_count: int
    lanes: dict[int, list[str]] = field(default_factory=dict)
    next_green: dict[int, int] = field(default_factory=dict)
    losing: dict[int, list[str]] = field(default_factory=dict)
    gaining: dict[int, list[str]] = field(default_factory=dict)

    @property
    def green_phases(self) -> list[int]:
        """Green phase indices, in program order."""
        return sorted(self.lanes)

    @property
    def is_usable(self) -> bool:
        """True if there is at least one green phase to control."""
        return bool(self.lanes)


def discover_plan(tls_id: str) -> SignalPlan:
    """Read one traffic light's plan out of the running simulation.

    Works on any network: nothing about the intersection is assumed.  The
    program that is inspected is the currently active one, which is what
    SUMO would run if the controller did nothing.
    """
    links = traci.trafficlight.getControlledLinks(tls_id)
    program = traci.trafficlight.getAllProgramLogics(tls_id)[0]
    plan = SignalPlan(tls_id=tls_id, phase_count=len(program.phases))

    for index, phase in enumerate(program.phases):
        if TRANSITION_CHAR in phase.state:
            continue                       # yellow / all-red: not ours to hold
        lanes = set()
        for position, char in enumerate(phase.state):
            if char not in GREEN_CHARS or position >= len(links):
                continue
            # one link may fan out to several connections; all share the
            # same approach lane
            lanes.update(connection[0] for connection in links[position])
        if lanes:
            plan.lanes[index] = sorted(lanes)

    # Walk the program forward from each green to find the next one.  The
    # phases in between are the transition, so "next green" is not simply
    # index + 1.
    order = plan.green_phases
    for position, index in enumerate(order):
        following = order[(position + 1) % len(order)]
        plan.next_green[index] = following

        # Which lanes actually CHANGE state if we switch to `following`?
        #
        # This looks like a detail and is not.  Phases share lanes: on the
        # cross, phase 0 and phase 2 both give green to rightE_A_0 and
        # leftW_A_0, because the main-road through movement runs in both.
        # A gap-out rule that asks "is the whole green direction empty?"
        # therefore almost never fires - measured over 600 s of real demand
        # it fired 0 times, and the controller sat there doing nothing while
        # looking perfectly correct.  Asking instead "has everyone who would
        # LOSE the green been served, and is anyone waiting to GAIN it?"
        # fired 9 times over the same 600 s.  Shared lanes drop out of both
        # sets because they are not affected either way, which is exactly
        # right.
        here, there = set(plan.lanes[index]), set(plan.lanes[following])
        plan.losing[index] = sorted(here - there)
        plan.gaining[index] = sorted(there - here)
    return plan


def discover_plans(tls_ids=None) -> dict[str, SignalPlan]:
    """Read every usable traffic light in the loaded network.

    Args:
        tls_ids: which lights to inspect.  ``None`` means all of them, which
            is what you want when the network is somebody else's.

    Returns:
        tls id -> SignalPlan, containing only lights that have at least one
        green phase.
    """
    if tls_ids is None:
        tls_ids = list(traci.trafficlight.getIDList())
    plans = {}
    for tls_id in tls_ids:
        plan = discover_plan(tls_id)
        if plan.is_usable:
            plans[tls_id] = plan
    return plans


class Controller:
    """Base class: the interface every strategy must provide."""

    name = "base"

    def __init__(self) -> None:
        self.switches = 0

    def reset(self) -> None:
        """Called once after SUMO starts, before the first step."""
        self.switches = 0

    def step(self, now: float) -> None:
        """Called once per simulation second.  ``now`` is simulation time."""
        raise NotImplementedError

    def describe(self) -> str:
        """One line for the run log, saying what this controller is doing."""
        return self.name

    def __str__(self) -> str:
        return self.name


class FixedTimeController(Controller):
    """Leave the signal alone and let SUMO's built-in plan run.

    "Doing nothing" is a complete strategy rather than a missing one: the
    plan was written into the network file by netconvert, and SUMO cycles
    through it on its own.  Having it as a class keeps the comparison
    honest, because both arms go through exactly the same code path.
    """

    name = "fixed"

    def step(self, now: float) -> None:
        return None


class ActuatedController(Controller):
    """Gap-out / max-out actuated control, on every light in the network.

    The rule, per traffic light, in full:

        * find the green phase it is currently in and how long it has run
        * if that is less than MIN_GREEN, do nothing - a green is never cut
          short
        * otherwise switch if the serving direction has cleared **and** the
          next direction actually has somebody waiting (gap-out)
        * in any case switch once MAX_GREEN has elapsed (max-out)

    Switching means advancing to the *next phase in the program*, which is
    the yellow.  SUMO then runs the yellow and the all-red that follow it at
    their own durations and arrives at the next green by itself.  That is
    the whole reason this controller does not simply jump to the next green:
    jumping skips the amber, and an intersection that goes straight from
    green to a conflicting green is not a signal plan, it is a collision.

    MAX_GREEN only binds when a green in the network's own plan is longer
    than MAX_GREEN.  On the cross every green is 24 s, so max-out never
    fires there - the network's plan is already stricter.  On a network with
    90-second greens it will fire.
    """

    name = "actuated"

    def __init__(self, tls_ids=None, min_green: float = MIN_GREEN,
                 max_green: float = MAX_GREEN) -> None:
        super().__init__()
        self.requested = tls_ids
        self.min_green = min_green
        self.max_green = max_green
        self.plans: dict[str, SignalPlan] = {}
        self._green_since: dict[str, float] = {}
        self._last_phase: dict[str, int] = {}

    def reset(self) -> None:
        super().reset()
        self.plans = discover_plans(self.requested)
        if not self.plans:
            raise SystemExit(
                "actuated control needs traffic lights, and this network has\n"
                "none that this controller can read.  Either the network has\n"
                "no signals at all (check with netconvert or netedit), or\n"
                "every junction is give-way.  Build it with --tls.guess, or\n"
                "run the fixed strategy, which does not need signals.")
        now = traci.simulation.getTime()
        for tls_id, plan in self.plans.items():
            # start each light from a known phase so runs are repeatable
            first = plan.green_phases[0]
            traci.trafficlight.setPhase(tls_id, first)
            self._green_since[tls_id] = now
            self._last_phase[tls_id] = first

    def step(self, now: float) -> None:
        for tls_id, plan in self.plans.items():
            phase = traci.trafficlight.getPhase(tls_id)
            if phase not in plan.lanes:
                # yellow or all-red: SUMO is running the transition, and the
                # controller has nothing to decide until the next green
                self._last_phase[tls_id] = phase
                continue

            if self._last_phase.get(tls_id) != phase:
                # just arrived at a new green - start its clock.  Waiting one
                # step before judging it also avoids acting on a queue count
                # that still describes the previous phase.
                self._last_phase[tls_id] = phase
                self._green_since[tls_id] = now
                continue

            elapsed = now - self._green_since[tls_id]
            if elapsed < self.min_green:
                continue

            # gap-out and max-out, both spelled out:
            #   - served:  the lanes that would LOSE the green if we switch
            #              now (shared lanes are excluded, see discover_plan)
            #   - waiting: the lanes that would GAIN it
            served = total_queue(plan.losing[phase])
            waiting = total_queue(plan.gaining[phase])
            if elapsed >= self.max_green or (served == 0 and waiting > 0):
                self._advance(tls_id, plan, phase, now)

    def _advance(self, tls_id: str, plan: SignalPlan,
                 phase: int, now: float) -> None:
        """Move to the next phase in the program, which is the amber."""
        following = (phase + 1) % plan.phase_count
        traci.trafficlight.setPhase(tls_id, following)
        self._last_phase[tls_id] = following
        self._green_since[tls_id] = now
        self.switches += 1

    def describe(self) -> str:
        return (f"{self.name}: {len(self.plans)} traffic light(s), "
                f"min_green={self.min_green:.0f}s max_green={self.max_green:.0f}s")


CONTROLLERS: dict[str, type[Controller]] = {
    FixedTimeController.name: FixedTimeController,
    ActuatedController.name: ActuatedController,
}


def make_controller(name: str) -> Controller:
    """Build a controller by name.

    Raises:
        SystemExit: on an unknown name, listing the ones that exist.  This is
            reachable from argparse choices as well, but a plain ValueError
            here would surface as a traceback for anyone calling the module
            directly.
    """
    if name not in CONTROLLERS:
        known = ", ".join(sorted(CONTROLLERS))
        raise SystemExit(f"unknown controller {name!r}; known: {known}")
    return CONTROLLERS[name]()
