"""Build a SUMO network from real OpenStreetMap data.

The other scripts in this repo build a synthetic four-arm intersection from
hand-written XML.  This one takes real streets instead:

    download or supply an .osm file
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

Two ways to get the .osm file::

    python scripts/build_from_osm.py --bbox 116.470,39.875,116.485,39.888
        downloads it through SUMO's own osmGet.py

    python scripts/build_from_osm.py --osm path/to/map.osm
        uses a file you exported from openstreetmap.org

Add ``--trips`` to also generate a random route file, which gives you
something to run immediately::

    python scripts/build_from_osm.py --osm map.osm --trips
    sumo-gui -n net/real.net.xml -r net/real.rou.xml

Attribution: OSM data is ODbL licensed.  Keep the source bbox or the source
file in your report, and credit OpenStreetMap contributors.
"""

from __future__ import annotations

import argparse
import gzip
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sumo_config import (  # noqa: E402
    NET_DIR,
    find_sumo_home,
    netconvert_binary,
    sumo_binary,
)


def run(cmd: list[str], label: str) -> tuple[bool, str]:
    """Run an external tool, echo the command, and surface its failures.

    SUMO's tools are chatty, so successful output is suppressed unless it
    contains something worth showing; the full text is returned so callers can
    quote it in a diagnostic.

    Callers must not treat a zero exit as "the output is usable": netconvert
    returns 0 even when it writes a network SUMO then refuses to load, and
    osmGet.py returns 0 after an HTTP 406.  That is what smoke_test() and
    osm_element_count() below are for.
    """
    print(f"  $ {' '.join(str(part) for part in cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        print(f"  [{label}] FAILED (exit {result.returncode})")
        for line in output.splitlines()[-8:]:
            print(f"      {line}")
        return False, output
    warnings = [line for line in output.splitlines() if line.startswith("Warning")]
    if warnings:
        print(f"  [{label}] {len(warnings)} warning(s), first: {warnings[0][:88]}")
    return True, output


def osm_element_count(path: Path) -> int:
    """Number of <node> plus <way> elements in an .osm file, gzipped or not.

    This is the only trustworthy success test for a download.  An Overpass
    server that is unreachable through a middlebox can still answer "200 OK"
    with a well-formed but empty document, and every layer above - osmGet.py
    included - reports that as success.  Counting elements is what tells the
    two apart.
    """
    if not path.exists():
        return 0
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rb") as handle:
                root = ET.fromstring(handle.read())
        else:
            root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return 0
    return sum(len(root.findall(tag)) for tag in ("node", "way", "relation"))


# Overpass mirrors, tried in order.
#
# osmGet.py defaults to www.overpass-api.de, which is not reachable from every
# network - some networks answer it with an intercepted, empty document.  The
# file name osmGet.py writes is "<prefix>_bbox.osm.xml" (it appends "_bbox"),
# which is worth knowing because looking for "<prefix>.osm" finds nothing and
# makes a working download look like a failure.
OSM_MIRRORS = (
    "overpass-api.de/api/interpreter",
    "overpass.osm.ch/api/interpreter",
    "overpass.kumi.systems/api/interpreter",
)


def download_osm(bbox: str, out_dir: Path, prefix: str = "map") -> Path | None:
    """Fetch an .osm extract with SUMO's own downloader.

    osmGet.py accepts a bounding box as west,south,east,north.  Each mirror in
    OSM_MIRRORS is tried until one returns a file that actually contains map
    data; a mirror that answers with an empty document is reported and skipped
    rather than accepted.

    Args:
        bbox: "west,south,east,north" in decimal degrees.
        out_dir: directory to write into; must exist.
        prefix: output file stem.

    Returns:
        Path to the downloaded file, or None on failure.
    """
    script = find_sumo_home() / "tools" / "osmGet.py"
    if not script.exists():
        print(f"  osmGet.py not found at {script}")
        return None

    written = out_dir / f"{prefix}_bbox.osm.xml"
    for mirror in OSM_MIRRORS:
        written.unlink(missing_ok=True)
        print(f"  trying {mirror}")
        ok, output = run([sys.executable, str(script), "-b", bbox, "-p", prefix,
                          "-d", str(out_dir), "-u", mirror, "-v"], "osmGet")
        # osmGet.py prints the HTTP status on its last line when verbose.
        status = output.splitlines()[-1] if output else "(no output)"
        if not ok:
            continue
        count = osm_element_count(written)
        if count:
            print(f"  got {count:,} elements from {mirror}")
            return written
        print(f"  {mirror} answered '{status}' but returned 0 map elements")

    print("  every mirror failed to return map data.  If this network blocks")
    print("  OpenStreetMap, export the area by hand at")
    print("  https://www.openstreetmap.org/export and pass it with --osm")
    return None


def smoke_test(net: Path) -> bool:
    """Load the network in SUMO for one step, to catch corrupt output.

    netconvert can exit 0 while writing a network that SUMO rejects, so
    "the command succeeded" is not evidence that the file works.  Opening it
    once costs well under a second on even a large network.
    """
    result = subprocess.run(
        [str(sumo_binary("sumo")), "-n", str(net), "--no-step-log", "true",
         "--end", "1"],
        capture_output=True, text=True, errors="replace", timeout=180,
    )
    if result.returncode != 0:
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        print("  [smoke test] SUMO refused to load the generated network:")
        for line in output.splitlines()[:6]:
            print(f"      {line}")
        return False
    print("  [smoke test] network loads in SUMO")
    return True


def convert(osm: Path, net: Path, keep_geodata: bool = False) -> bool:
    """Compile an .osm extract into a SUMO network.

    ``--tls.guess`` lets netconvert install traffic lights where a junction
    is busy enough instead of leaving everything as give-way.  Without it a
    large OSM extract yields a network with no signals at all, which is not
    what a city scenario should look like.

    Deliberately NOT passing ``--tls.guess.threshold``.  Combined with
    ``--tls.guess`` it makes netconvert emit the same tlLogic twice and SUMO
    then refuses to load the result with "Another logic with id ... and
    programID '0' exists".  Verified against SUMO 1.26::

        --tls.guess true                           loads fine
        --tls.guess true --tls.guess.threshold 30  broken

    Because netconvert still exits 0 in the broken case, the result is
    smoke-tested before this function reports success.
    """
    cmd = [
        str(netconvert_binary()),
        "--osm-files", str(osm),
        "-o", str(net),
        "--no-turnarounds", "true",
        "--tls.guess", "true",
    ]
    if keep_geodata:
        cmd += ["--proj.plain-geo", "true"]
    if not run(cmd, "netconvert")[0]:
        return False
    return smoke_test(net)


def make_trips(net: Path, routes: Path, end: int, period: float) -> bool:
    """Generate demand with randomTrips.py.

    Args:
        net: the network to route on.
        routes: where to write the route file.
        end: simulation end time in seconds.
        period: mean seconds between inserted vehicles.  Smaller means more
            traffic; randomTrips inserts one vehicle per period on average.
    """
    script = find_sumo_home() / "tools" / "randomTrips.py"
    if not script.exists():
        print(f"  randomTrips.py not found at {script}")
        return False
    # randomTrips writes the intermediate trip list to "trips.trips.xml" in the
    # *current directory* unless told otherwise, which litters the repo root
    # when the script is run from there.  Put it beside the routes it feeds.
    return run([sys.executable, str(script),
                "-n", str(net), "-r", str(routes),
                "-o", str(routes.with_suffix(".trips.xml")),
                "-e", str(end), "-p", str(period),
                "--validate"], "randomTrips")[0]


def describe(net: Path) -> None:
    """Report what came out, so a bad extract is obvious immediately."""
    root = ET.parse(net).getroot()
    edges = [e for e in root.findall("edge") if e.get("function") != "internal"]
    junctions = [j for j in root.findall("junction") if j.get("type") != "internal"]
    signals = root.findall("tlLogic")
    lane_count = sum(len(edge.findall("lane")) for edge in edges)

    print()
    print(f"  network: {net.name}  ({net.stat().st_size / 1024:,.0f} KB)")
    print(f"    drivable edges : {len(edges)}")
    print(f"    lanes          : {lane_count}")
    print(f"    junctions      : {len(junctions)}")
    print(f"    traffic lights : {len(signals)}")

    if not edges:
        print("    [!] no drivable edges - the bounding box probably missed")
        print("        the roads, or the extract only contains buildings")
        return
    print(f"    sample edge ids: {[e.get('id') for e in edges[:3]]}")
    if not signals:
        print("    note: no traffic lights were created; every junction stays")
        print("          give-way. Add --tls.guess if the area should be busy.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--osm", type=Path,
                        help="an .osm file you already have")
    source.add_argument("--bbox", type=str,
                        help="west,south,east,north, downloaded via osmGet.py")
    parser.add_argument("-o", "--out", type=Path, default=NET_DIR / "real.net.xml",
                        help="where to write the network")
    parser.add_argument("--trips", action="store_true",
                        help="also generate a random route file")
    parser.add_argument("--trips-end", type=int, default=3600,
                        help="simulation seconds for the generated demand")
    parser.add_argument("--trips-period", type=float, default=2.0,
                        help="mean seconds between vehicles (smaller = busier)")
    parser.add_argument("--geo", action="store_true",
                        help="keep lon/lat so the network lines up with a map")
    args = parser.parse_args()

    print("=" * 70)
    print("build network from OpenStreetMap")
    print("=" * 70)
    print(f"  SUMO_HOME: {find_sumo_home()}")
    args.out.parent.mkdir(parents=True, exist_ok=True)

    # ---- 1. get the osm file ------------------------------------------
    if args.bbox:
        print(f"\n[1] downloading OSM for bbox {args.bbox}")
        osm = download_osm(args.bbox, args.out.parent)
        if osm is None:
            print("  download failed.  If your network blocks OSM, export the")
            print("  area by hand at https://www.openstreetmap.org/export")
            print("  and pass the file with --osm")
            return 1
    else:
        osm = args.osm
        if not osm.exists():
            print(f"\n[1] file not found: {osm}")
            return 1
    print(f"  using {osm.name} ({osm.stat().st_size / 1024:,.0f} KB)")

    # ---- 2. osm -> network --------------------------------------------
    print("\n[2] netconvert: osm -> sumo network")
    if not convert(osm, args.out, keep_geodata=args.geo):
        return 1
    describe(args.out)

    # ---- 3. optional demand -------------------------------------------
    if not args.trips:
        print("\n[3] skipped (add --trips to generate demand)")
    else:
        routes = args.out.with_suffix(".rou.xml")
        print("\n[3] randomTrips: generating demand")
        if not make_trips(args.out, routes, args.trips_end, args.trips_period):
            return 1
        vehicles = ET.parse(routes).getroot().findall("vehicle")
        print(f"  {routes.name}: {len(vehicles)} vehicles "
              f"over {args.trips_end} s")

    print()
    print("Remember: OSM data is ODbL, credit OpenStreetMap contributors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
