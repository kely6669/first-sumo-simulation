"""Run one simulation - the single place where a simulation is started.

Everything that runs a simulation goes through :func:`run_simulation`,
whether it wants one run (``3_signal_control.py --compare``) or a hundred
(``run_experiments.py``).  Keeping it in one function is what makes runs
comparable: two runs differ only in the arguments, never in some incidental
detail of how they were launched.

This module used to be two functions with the same name - one here and one
inside ``analyse_run.py`` - each building its own SUMO command line and
wiring up its own copy of the output files.  They drifted.  There is now one.

WHO PUTS THE VEHICLES IN

``drive_demand`` picks between the two ways demand can reach a simulation:

    drive_demand=True    Python inserts vehicles through TraCI, using the
                         route list in sumo_config.ROUTES.  This only works
                         on the hand-written cross, because those route ids
                         (r_EW, r_WE, ...) are defined in net/simple.rou.xml
                         and do not exist anywhere else.

    drive_demand=False   The route file already contains <flow> or <vehicle>
                         elements and SUMO inserts them itself; Python only
                         advances the clock.  This is the mode that works on
                         *any* network, including one a stranger built from
                         their own OpenStreetMap extract, and it is how every
                         SUMO tutorial does it.

Both modes produce the same four output files, so the analysis layer cannot
tell them apart - and must not care.

Output files, all written into ``run_dir``::

    tripinfo.xml   one record per completed trip
    summary.xml    one record per simulation second
    queues.xml     queue length per lane per second
    sumo.log       SUMO's own stdout, so warnings are inspectable afterwards

On warnings
    SUMO's stdout goes to ``sumo.log`` rather than being suppressed.
    Warnings are how SUMO says "vehicle v431 could not be inserted", which is
    the first symptom of demand exceeding capacity - the mistake this project
    exists to teach about.  Silencing them would hide it.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from control import make_controller  # noqa: E402
from demand import DemandInjector  # noqa: E402
from sumo_config import (  # noqa: E402
    NET_FILE, ROUTE_FILE, ensure_network, setup_traci, sumo_binary,
)

setup_traci()
import traci  # noqa: E402

#: Everything the analysis layer reads.  Defined once so that a run started
#: from here and a run started by hand in sumo-gui leave the same traces.
OUTPUT_FILES = {
    "tripinfo": "tripinfo.xml",
    "summary": "summary.xml",
    "queues": "queues.xml",
}

#: Queue sampling period for edgeData, when it is requested.
EDGE_DATA_PERIOD = 300


def _edge_data_additional(run_dir: Path, end: int) -> Path:
    """Write the additional file that makes SUMO emit edgeData.xml.

    SUMO resolves a relative ``file=`` inside an additional file **relative
    to the additional file itself**, so a relative run_dir would be joined
    twice and SUMO would abort with "Could not build output file".  The path
    written here is absolute for that reason.
    """
    path = run_dir / "outputs.add.xml"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<additional>\n"
        f'    <edgeData id="edges" file="{run_dir / "edgeData.xml"}"\n'
        f'              begin="0" end="{end}" period="{EDGE_DATA_PERIOD}"\n'
        '              excludeEmpty="false"/>\n'
        "</additional>\n",
        encoding="utf-8")
    return path


def run_simulation(
    run_dir: Path,
    *,
    strategy: str = "fixed",
    seed: int = 0,
    demand: float = 1.0,
    duration: int = 900,
    net_file: Path = NET_FILE,
    route_file: Path = ROUTE_FILE,
    drive_demand: bool = True,
    edge_data: bool = False,
    gui: bool = False,
    label: str = "default",
) -> dict:
    """Run one simulation and return a summary row.

    Args:
        run_dir: directory for the output files; created if missing.
        strategy: controller name, see ``control.CONTROLLERS``.
        seed: SUMO's ``--seed``, which controls departure randomness.
        demand: headway multiplier; below 1.0 means more traffic.  Ignored
            when ``drive_demand`` is False, because the route file decides.
        duration: how many simulation seconds to run.
        net_file: the network to load.  Built on demand if it is the
            hand-written cross and the file is missing.
        route_file: route definitions.  With ``drive_demand`` it only needs
            to define types and routes; without it, it must contain the
            vehicles too.
        drive_demand: see the module docstring.
        edge_data: also ask SUMO for edgeData.xml, a per-edge summary over
            time windows.  Off by default because it is by far the largest
            output file.
        gui: show the SUMO window.
        label: TraCI connection label, so nested runs cannot collide.

    Returns:
        A dict of scalars describing the run.  This becomes one row of
        ``results/index.csv``.
    """
    run_dir = run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    net_file = ensure_network(net_file)

    cmd = [
        str(sumo_binary("sumo", gui=gui)),
        "-n", str(net_file),
        "-r", str(route_file),
        "--tripinfo-output", str(run_dir / OUTPUT_FILES["tripinfo"]),
        "--summary-output", str(run_dir / OUTPUT_FILES["summary"]),
        "--queue-output", str(run_dir / OUTPUT_FILES["queues"]),
        "--seed", str(seed),
        "--no-step-log", "true",
        # never teleport: SUMO would otherwise move a stuck vehicle forward
        # and quietly improve the travel times we are trying to measure
        "--time-to-teleport", "-1",
    ]
    if edge_data:
        cmd += ["-a", str(_edge_data_additional(run_dir, duration))]
    if gui:
        cmd += ["--start", "true", "--delay", "60"]

    controller = make_controller(strategy)
    injector = DemandInjector(scale=demand) if drive_demand else None

    started = time.time()
    log_handle = (run_dir / "sumo.log").open("w", encoding="utf-8")
    traci.start(cmd, label=label, stdout=log_handle)
    try:
        controller.reset()
        for _ in range(duration):
            traci.simulationStep()
            now = traci.simulation.getTime()
            if injector is not None:
                injector.step(now)
                injector.clear_finished()
            controller.step(now)
    finally:
        # close even if the loop raised, otherwise SUMO keeps running and the
        # next run fails to start
        still_in_network = traci.vehicle.getIDCount()
        traci.close()
        log_handle.close()

    return {
        "strategy": strategy,
        "seed": seed,
        "demand": demand if drive_demand else None,
        "duration": duration,
        "drive_demand": drive_demand,
        "inserted": injector.inserted if injector else None,
        "completed": injector.removed if injector else None,
        "still_in_network": still_in_network,
        "switches": controller.switches,
        "wall_clock_s": round(time.time() - started, 1),
        "run_dir": str(run_dir),
    }
