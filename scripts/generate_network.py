"""Build a four-arm signalised intersection and compile it with netconvert.

Pipeline
    cross.nod.xml  (5 nodes: 4 borders + 1 junction)
    cross.edg.xml  (8 edges: 1 in + 1 out per arm)
    cross.con.xml  (12 connections: left / straight / right per approach)
        |
        v  netconvert
    cross.net.xml  (the network SUMO actually loads)

Why hand-written XML instead of netedit: the topology is data, so it can be
version-controlled, diffed and regenerated.  Change LANES and re-run.

Coordinate system: +x east, +y north.

                 N (300, 0)
                 |
    W (0,300) --- A (300,300) --- E (600,300)
                 |
                 S (300, 600)

Edge naming:   X_A  = traffic entering the junction (2 lanes)
               A_X  = traffic leaving the junction (1 lane)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sumo_config import (  # noqa: E402
    ARM_LENGTH, CON_FILE, EDG_FILE, LANES, NET_DIR, NET_FILE, NOD_FILE, SPEED,
    netconvert_binary,
)

CENTRE = ARM_LENGTH          # keeps nodes on a square
HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n'

NODES = {
    "N": (CENTRE, CENTRE - ARM_LENGTH),
    "S": (CENTRE, CENTRE + ARM_LENGTH),
    "W": (CENTRE - ARM_LENGTH, CENTRE),
    "E": (CENTRE + ARM_LENGTH, CENTRE),
    "A": (CENTRE, CENTRE),
}

# (edge id, from node, to node, lanes)
EDGES = [
    ("leftW_A",    "W", "A", LANES),   # west approach
    ("A_rightE",   "A", "E", 1),       # east exit
    ("rightE_A",   "E", "A", LANES),   # east approach
    ("A_leftW",    "A", "W", 1),       # west exit
    ("topS_A",     "N", "A", LANES),   # north approach
    ("A_bottomN",  "A", "N", 1),       # north exit
    ("bottomN_A",  "S", "A", LANES),   # south approach
    ("A_topS",     "A", "S", 1),       # south exit
]

# approach -> (left turn exit, straight exit, right turn exit)
TURNS = {
    "rightE_A":  ("A_bottomN", "A_leftW",   "A_topS"),     # from east, travels west
    "leftW_A":   ("A_topS",    "A_rightE",  "A_bottomN"),  # from west, travels east
    "bottomN_A": ("A_leftW",   "A_topS",    "A_rightE"),   # from south, travels north
    "topS_A":    ("A_rightE",  "A_bottomN", "A_leftW"),    # from north, travels south
}


def write_nodes(path: Path) -> None:
    lines = [HEADER, "<nodes>\n"]
    for nid, (x, y) in NODES.items():
        ntype = "traffic_light" if nid == "A" else "dead_end"
        lines.append(f'    <node id="{nid}" x="{x}" y="{y}" type="{ntype}"/>\n')
    lines.append("</nodes>\n")
    path.write_text("".join(lines), encoding="utf-8")


def write_edges(path: Path) -> None:
    lines = [HEADER, "<edges>\n"]
    for eid, frm, to, lanes in EDGES:
        lines.append(
            f'    <edge id="{eid}" from="{frm}" to="{to}" '
            f'numLanes="{lanes}" speed="{SPEED}" priority="2"/>\n'
        )
    lines.append("</edges>\n")
    path.write_text("".join(lines), encoding="utf-8")


def write_connections(path: Path) -> None:
    lines = [HEADER, "<connections>\n"]
    for frm, exits in TURNS.items():
        for to in exits:
            lines.append(f'    <connection from="{frm}" to="{to}"/>\n')
    lines.append("</connections>\n")
    path.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    NET_DIR.mkdir(parents=True, exist_ok=True)

    write_nodes(NOD_FILE)
    write_edges(EDG_FILE)
    write_connections(CON_FILE)
    print(f"wrote {NOD_FILE.name}, {EDG_FILE.name}, {CON_FILE.name}")

    cmd = [
        str(netconvert_binary()),
        "-n", str(NOD_FILE),
        "-e", str(EDG_FILE),
        "-x", str(CON_FILE),
        "-o", str(NET_FILE),
        "--no-turnarounds", "true",
    ]
    print("running:", " ".join(Path(c).name for c in cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if result.returncode != 0 or not NET_FILE.exists():
        print(result.stdout, result.stderr)
        print("netconvert failed")
        return 1

    # sanity check the result
    import xml.etree.ElementTree as ET

    root = ET.parse(NET_FILE).getroot()
    edges = [e for e in root.findall("edge") if e.get("function") != "internal"]
    conns = [c for c in root.findall("connection")
             if not c.get("from", "").startswith(":")]
    tls = root.findall("tlLogic")
    print(f"built {NET_FILE.name}: {len(edges)} edges, "
          f"{len(conns)} connections, {len(tls)} traffic light(s)")
    if len(edges) != 8 or len(conns) != 12:
        print("WARNING: expected 8 edges and 12 connections")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
