"""Shared configuration and SUMO discovery helpers.

Every script in this project uses these helpers instead of hard-coded
absolute paths, so the repository runs on someone else's machine after
they install SUMO and set SUMO_HOME.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# --- project layout -------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
NET_DIR = ROOT / "net"

NET_FILE = NET_DIR / "cross.net.xml"
ROUTE_FILE = NET_DIR / "simple.rou.xml"
NOD_FILE = NET_DIR / "cross.nod.xml"
EDG_FILE = NET_DIR / "cross.edg.xml"
CON_FILE = NET_DIR / "cross.con.xml"

# --- network / demand parameters (change these to experiment) -------------
LANES = 2            # lanes per approach
ARM_LENGTH = 300     # metres from junction centre to network border
SPEED = 13.9         # m/s (50 km/h)
TLS_ID = "A"         # traffic light id in cross.net.xml

# Vehicle insertion intervals, in seconds.  The signal cycle is 90 s and
# each direction gets ~24 s of green, so one approach can discharge about
# 1800 * 24/90 ~= 480 veh/h, i.e. one vehicle every 7.5 s.
# Intervals must stay above that, otherwise queues grow without bound.
ROUTES = [
    # (route id, headway s, final edge)
    ("r_EW", 12.0, "A_leftW"),      # east approach -> west (straight)
    ("r_WE", 12.0, "A_rightE"),     # west approach -> east (straight)
    ("r_NS", 16.0, "A_topS"),       # south approach -> north (straight)
    ("r_SN", 16.0, "A_bottomN"),    # north approach -> south (straight)
    ("r_EW_L", 24.0, "A_bottomN"),  # east approach -> south (left turn)
]

# --- SUMO discovery -------------------------------------------------------
_SEARCH_PATHS = [
    # 1. a portable SUMO shipped next to this repository (see README)
    str(ROOT.parent / "SUMO"),
    # 2. the usual install locations
    r"C:\Program Files (x86)\Eclipse\Sumo",
    r"C:\Program Files\Eclipse\Sumo",
    "/usr/share/sumo",
    "/usr/local/share/sumo",
    str(Path.home() / "sumo"),
    str(Path.home() / "Desktop" / "SUMO"),
]


def find_sumo_home() -> Path:
    """Locate a SUMO installation.

    Order: SUMO_HOME environment variable, then a list of common install
    locations.  Raises a helpful error if nothing is found.
    """
    env = os.environ.get("SUMO_HOME")
    if env and (Path(env) / "bin").is_dir():
        return Path(env).resolve()

    for candidate in _SEARCH_PATHS:
        p = Path(candidate)
        if (p / "bin").is_dir():
            return p.resolve()

    raise RuntimeError(
        "SUMO not found. Install SUMO and set the SUMO_HOME environment "
        "variable to its installation directory.\n"
        "  Windows: setx SUMO_HOME \"C:\\Program Files (x86)\\Eclipse\\Sumo\"\n"
        "  Linux/macOS: export SUMO_HOME=/usr/share/sumo"
    )


def sumo_binary(name: str = "sumo", gui: bool = False) -> Path:
    """Full path to a SUMO executable, e.g. sumo, sumo-gui, netconvert.

    SUMO ships several command line programs side by side in <SUMO_HOME>/bin.
    The .exe suffix is Windows-only, so both spellings are tried and the first
    match wins.

    Raises:
        RuntimeError: if the program is not present in <SUMO_HOME>/bin.
    """
    if gui:
        name = f"{name}-gui"
    bin_dir = find_sumo_home() / "bin"
    for suffix in (".exe", ""):
        candidate = bin_dir / f"{name}{suffix}"
        if candidate.exists():
            return candidate
    raise RuntimeError(f"{name} not found under {bin_dir}")


def netconvert_binary() -> Path:
    """Full path to netconvert, the network compiler."""
    return sumo_binary("netconvert")


def random_trips_script() -> Path:
    """Full path to randomTrips.py, SUMO's demand generator."""
    return find_sumo_home() / "tools" / "randomTrips.py"


def setup_traci() -> None:
    """Make `import traci` work.

    traci ships inside SUMO's tools directory, so it must be added to
    sys.path *before* it is imported.
    """
    home = find_sumo_home()
    tools = str(home / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    os.environ["SUMO_HOME"] = str(home)


def ensure_network(net_file: Path | None = None) -> Path:
    """Return the compiled network, building it first if it is not there.

    ``net/cross.net.xml`` is build output: it is compiled from the
    hand-written ``net/cross.{nod,edg,con}.xml`` and it is gitignored, for the
    same reason ``net/real.net.xml`` is - it is reproducible, and netconvert
    stamps a fresh generation time into it on every build.  A fresh clone
    therefore has the inputs but not the network.

    So anything that wants to load the network has to be able to produce it.
    That is this function.  It runs ``generate_network.py`` in a subprocess
    rather than importing it, because that module already imports this one
    and importing it back would be a circular import.

    Args:
        net_file: network to check.  Defaults to the hand-written cross.

    Returns:
        The path to a network that exists.

    Raises:
        RuntimeError: if the file is missing and cannot be built.  This is
            deliberately loud - silently running on a stale or wrong network
            is worse than stopping.
    """
    target = Path(net_file) if net_file is not None else NET_FILE
    if target.exists():
        return target

    if target.resolve() != NET_FILE.resolve():
        raise RuntimeError(
            f"{target} does not exist and is not a network this project can\n"
            f"build automatically.  Build it with:\n"
            f"    python scripts/build_from_osm.py --osm <file.osm> -o {target}")

    builder = SCRIPTS_DIR / "generate_network.py"
    print(f"{target.name} is missing - compiling it from the hand-written XML")
    print(f"  $ {sys.executable} {builder}")
    result = subprocess.run([sys.executable, str(builder)],
                            capture_output=True, text=True, errors="replace")
    if result.returncode != 0 or not target.exists():
        print((result.stdout or "") + (result.stderr or ""))
        raise RuntimeError(
            f"{builder.name} could not build {target}.  Run it on its own to "
            f"see the full output.")
    return target


def start_args(gui: bool = False, extra: list[str] | None = None) -> list[str]:
    """Standard command line for launching SUMO through TraCI.

    ``--time-to-teleport -1`` disables teleporting on purpose.  By default
    SUMO removes a vehicle that has been stuck for too long and re-inserts it
    further along, which silently corrupts travel-time statistics.  For a
    study that reports waiting times, failing loudly is better than a quietly
    wrong number.
    """
    args = [
        str(sumo_binary("sumo", gui=gui)),
        "-n", str(ensure_network()),
        "-r", str(ROUTE_FILE),
        "--no-step-log", "true",
        "--no-warnings", "true",
        "--time-to-teleport", "-1",
    ]
    if gui:
        args += ["--start", "true", "--delay", "60"]
    if extra:
        args += extra
    return args
