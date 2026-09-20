"""Shared configuration and SUMO discovery helpers.

Every script in this project uses these helpers instead of hard-coded
absolute paths, so the repository runs on someone else's machine after
they install SUMO and set SUMO_HOME.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# --- project layout -------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
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


def sumo_binary(gui: bool = False) -> Path:
    """Full path to sumo(.exe) or sumo-gui(.exe)."""
    name = "sumo-gui" if gui else "sumo"
    home = find_sumo_home()
    for suffix in (".exe", ""):
        p = home / "bin" / f"{name}{suffix}"
        if p.exists():
            return p
    raise RuntimeError(f"{name} not found under {home / 'bin'}")


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


def start_args(gui: bool = False, extra: list[str] | None = None) -> list[str]:
    """Standard command line for launching SUMO through TraCI."""
    args = [
        str(sumo_binary(gui)),
        "-n", str(NET_FILE),
        "-r", str(ROUTE_FILE),
        "--no-step-log", "true",
        "--no-warnings", "true",
        "--time-to-teleport", "-1",      # never teleport: keeps results honest
    ]
    if gui:
        args += ["--start", "true", "--delay", "60"]
    if extra:
        args += extra
    return args
