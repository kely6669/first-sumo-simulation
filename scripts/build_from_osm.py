"""Build a SUMO network from real OpenStreetMap data.

The other scripts in this repo build a synthetic four-arm intersection from
hand-written XML.  This one takes real streets instead:

    download / supply an .osm file
        |
        v  netconvert --osm-files
    real network  (net/real.net.xml)
        |
        v  randomTrips.py            (optional)
    demand        (net/real.rou.xml)

Why OSM: the geometry, lane counts and junction structure come from actual
surveyed map data instead of being invented.  That makes a scenario about a
specific place defensible; a hand-drawn grid is fine for method work but not
for saying anything about a real location.

Two ways to get the .osm file
    python scripts/build_from_osm.py --bbox 116.470,39.875,116.485,39.888
        downloads it through SUMO's own osmGet.py
    python scripts/build_from_osm.py --osm path/to/map.osm
        uses a file you already exported from openstreetmap.org

Add --trips to also generate a random route file, which gives you something
to run immediately:

    python scripts/build_from_osm.py --osm map.osm --trips
    sumo-gui -n net/real.net.xml -r net/real.rou.xml

Attribution: OSM data is ODbL licensed.  Keep the source bbox or the source
file in your report, and credit OpenStreetMap contributors.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sumo_config import NET_DIR, find_sumo_home  # noqa: E402

TOOLS = None        # set in main()


def run(cmd: list[str], label: str) -> bool:
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        print(f"  [{label}] FAILED (exit {r.returncode})")
        for line in out.splitlines()[-8:]:
            print(f"      {line}")
        return False
    # netconvert is chatty; only surface warnings
    warns = [l for l in out.splitlines() if l.startswith("Warning")]
    if warns:
        print(f"  [{label}] {len(warns)} warning(s), first: {warns[0][:88]}")
    return True


def download_osm(bbox: str, out_dir: Path, prefix: str = "map") -> Path | None:
    """Fetch an .osm extract with SUMO's own downloader (no browser needed)."""
    script = TOOLS / "osmGet.py"
    if not script.exists():
        print(f"  osmGet.py not found at {script}")
        return None
    ok = run([sys.executable, str(script), "-b", bbox, "-p", prefix,
              "-d", str(out_dir)], "osmGet")
    if not ok:
        return None
    for cand in (out_dir / f"{prefix}.osm", out_dir / f"{prefix}.osm.gz"):
        if cand.exists():
            return cand
    return None


def convert(osm: Path, net: Path, keep_geodata: bool = False) -> bool:
    cmd = [str(SUMO_BIN / "netconvert.exe" if sys.platform == "win32" else SUMO_BIN / "netconvert"),
           "--osm-files", str(osm),
           "-o", str(net),
           "--no-turnarounds", "true",
           # Junctions where OSM gives no right-of-way info default to
           # priority.  Guessing signals keeps busy crossings realistic.
           #
           # Do NOT add --tls.guess.threshold here: combined with
           # --tls.guess it makes netconvert emit the same tlLogic twice and
           # SUMO then refuses to load the network
           # ("Another logic with id ... exists").  Verified with SUMO 1.26:
           #   --tls.guess true                        -> loads fine
           #   --tls.guess true --tls.guess.threshold  -> broken
           "--tls.guess", "true"]
    if keep_geodata:
        cmd += ["--proj.plain-geo", "true"]
    if not run(cmd, "netconvert"):
        return False

    # netconvert can succeed yet write a network SUMO refuses to load, so
    # always open it once before telling the user it worked.
    return smoke_test(net)


def smoke_test(net: Path) -> bool:
    """Load the network in SUMO for one step.  Catches corrupt output."""
    sumo = SUMO_BIN / ("sumo.exe" if sys.platform == "win32" else "sumo")
    r = subprocess.run([str(sumo), "-n", str(net), "--no-step-log", "true",
                        "--end", "1"], capture_output=True, text=True,
                       errors="replace", timeout=180)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        print("  [smoke test] SUMO refused to load the generated network:")
        for line in out.splitlines()[:6]:
            print(f"      {line}")
        return False
    print("  [smoke test] network loads in SUMO")
    return True


def make_trips(net: Path, rou: Path, end: int, period: float) -> bool:
    script = TOOLS / "randomTrips.py"
    if not script.exists():
        print(f"  randomTrips.py not found at {script}")
        return False
    return run([sys.executable, str(script),
                "-n", str(net), "-r", str(rou),
                "-e", str(end), "-p", str(period),
                "--validate"], "randomTrips")


def describe(net: Path) -> None:
    """Report what came out, so a failure is visible immediately."""
    root = ET.parse(net).getroot()
    edges = [e for e in root.findall("edge") if e.get("function") != "internal"]
    nodes = [j for j in root.findall("junction") if j.get("type") != "internal"]
    tls = root.findall("tlLogic")
    lanes = sum(len(e.findall("lane")) for e in edges)
    print()
    print(f"  network: {net.name}  ({net.stat().st_size / 1024:,.0f} KB)")
    print(f"    drivable edges : {len(edges)}")
    print(f"    lanes          : {len(lanes) if isinstance(lanes, list) else lanes}")
    print(f"    junctions      : {len(nodes)}")
    print(f"    traffic lights : {len(tls)}")
    if not edges:
        print("    [!] no drivable edges - the bbox probably missed the roads")
        return
    # sanity: is anything actually connected?
    dead = [e.get("id") for e in edges
            if not e.get("from", "").startswith(":")][:3]
    print(f"    sample edge ids: {dead}")
    if len(tls) == 0:
        print("    note: no traffic lights were guessed - junctions stay priority")


def main() -> int:
    global TOOLS, SUMO_BIN
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--osm", type=Path, help="an .osm file you already have")
    src.add_argument("--bbox", type=str,
                     help="west,south,east,north - downloaded via osmGet.py")
    ap.add_argument("-o", "--out", type=Path, default=NET_DIR / "real.net.xml")
    ap.add_argument("--trips", action="store_true",
                    help="also generate a random route file to run")
    ap.add_argument("--trips-end", type=int, default=3600)
    ap.add_argument("--trips-period", type=float, default=2.0,
                    help="seconds between inserted vehicles")
    ap.add_argument("--geo", action="store_true",
                    help="keep lon/lat so the network lines up with a map")
    args = ap.parse_args()

    home = find_sumo_home()
    TOOLS = home / "tools"
    SUMO_BIN = home / "bin"
    args.out.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("build network from OpenStreetMap")
    print("=" * 70)
    print(f"  SUMO_HOME: {home}")

    # ---- 1. get the osm file -------------------------------------------
    if args.bbox:
        print(f"\n[1] downloading OSM for bbox {args.bbox}")
        osm = download_osm(args.bbox, args.out.parent)
        if osm is None:
            print("  download failed - if your network blocks OSM, export the")
            print("  area manually at https://www.openstreetmap.org/export")
            print("  and pass it with --osm")
            return 1
        print(f"  got {osm.name} ({osm.stat().st_size / 1024:,.0f} KB)")
    else:
        osm = args.osm
        if not osm.exists():
            print(f"\n[1] file not found: {osm}")
            return 1
        print(f"\n[1] using {osm} ({osm.stat().st_size / 1024:,.0f} KB)")

    # ---- 2. osm -> net --------------------------------------------------
    print("\n[2] netconvert: osm -> sumo network")
    if not convert(osm, args.out, keep_geodata=args.geo):
        return 1
    describe(args.out)

    # ---- 3. optional demand ---------------------------------------------
    if args.trips:
        rou = args.out.with_suffix(".rou.xml")
        print(f"\n[3] randomTrips: generating demand")
        if not make_trips(args.out, rou, args.trips_end, args.trips_period):
            return 1
        trips = ET.parse(rou).getroot().findall("vehicle")
        print(f"  {rou.name}: {len(trips)} vehicles over {args.trips_end} s")
        print(f"\n  run it with:")
        print(f"    sumo-gui -n {args.out.name} -r {rou.name}")
    else:
        print("\n[3] skipped (add --trips to generate demand)")

    print()
    print("Remember: OSM data is ODbL - credit OpenStreetMap contributors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
