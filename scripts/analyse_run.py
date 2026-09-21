"""Turn SUMO output files into tables, statistics and plots.

This is the analysis layer.  A simulation writes XML; nobody reads XML by
hand.  The job here is:

    read SUMO output  ->  pandas DataFrames  ->  metrics, tables, figures

Usage
    # analyse one run
    python scripts/analyse_run.py --run-dir results/run1

    # run a simulation first and analyse it straight away
    python scripts/analyse_run.py --run-dir results/demo --simulate

    # compare several runs side by side
    python scripts/analyse_run.py --compare results/run1 results/run2

Outputs (written next to --run-dir)
    metrics.csv        one row of headline numbers
    by_vtype.csv       trip statistics grouped by vehicle type
    by_lane_queue.csv  queue statistics grouped by lane
    summary_timeseries.csv  the per-second network state
    plot_*.png         figures (skipped if matplotlib is missing)

Reading SUMO output: the .xml outputs are element-per-record and convert
cleanly with pandas.  Note that a *CSV* written by SUMO uses semicolons, not
commas - reading it with the default separator silently yields one column.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sumo_config import NET_FILE, ROOT, ROUTE_FILE, find_sumo_home  # noqa: E402


# ---------------------------------------------------------------- readers
def read_xml_records(path: Path, tag: str) -> pd.DataFrame:
    """Every <tag> element becomes a row; its attributes become columns."""
    if not path.exists():
        print(f"  [skip] {path.name} not found")
        return pd.DataFrame()
    root = ET.parse(path).getroot()
    return pd.DataFrame([e.attrib for e in root.iter(tag)])


def numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """SUMO writes everything as text; convert the columns we compute on."""
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def read_queue_series(path: Path) -> pd.DataFrame:
    """queues.xml is <data><lanes><lane .../></lanes></data> per step.

    Returns one row per (time, lane) so it can be grouped either way.
    """
    if not path.exists():
        print(f"  [skip] {path.name} not found")
        return pd.DataFrame()
    root = ET.parse(path).getroot()
    rows = []
    for step, data in enumerate(root.findall("data")):
        for lane in data.iter("lane"):
            rows.append({
                "step": step,
                "lane": lane.get("id"),
                "queue_length": pd.to_numeric(lane.get("queueing_length"),
                                              errors="coerce"),
                "queueing": pd.to_numeric(lane.get("queueing"), errors="coerce"),
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- metrics
def trip_metrics(trips: pd.DataFrame) -> dict:
    if trips.empty:
        return {}
    return {
        "trips_completed": len(trips),
        "mean_duration_s": round(trips["duration"].mean(), 1),
        "mean_waiting_s": round(trips["waitingTime"].mean(), 1),
        "median_waiting_s": round(trips["waitingTime"].median(), 1),
        "p95_waiting_s": round(trips["waitingTime"].quantile(0.95), 1),
        "max_waiting_s": round(trips["waitingTime"].max(), 1),
        "mean_timeloss_s": round(trips["timeLoss"].mean(), 1),
        "mean_distance_m": round(trips["routeLength"].mean(), 1),
    }


def network_metrics(steps: pd.DataFrame) -> dict:
    if steps.empty:
        return {}
    warm = steps[steps["time"] >= 60]        # drop the fill-up period
    return {
        "duration_s": int(steps["time"].max()),
        "inserted": int(steps["inserted"].max()),
        "arrived": int(steps["arrived"].max()),
        "mean_running": round(warm["running"].mean(), 1),
        "peak_running": int(steps["running"].max()),
        "mean_halting": round(warm["halting"].mean(), 1),
        "teleports": int(steps["teleports"].max()),      # must be 0
        "collisions": int(steps["collisions"].max()),     # must be 0
    }


def lane_metrics(queues: pd.DataFrame) -> pd.DataFrame:
    if queues.empty:
        return pd.DataFrame()
    out = (queues.groupby("lane")["queue_length"]
           .agg(mean_queue_m="mean", max_queue_m="max")
           .round(2)
           .sort_values("max_queue_m", ascending=False))
    # only lanes that actually queued are interesting
    return out[out["max_queue_m"] > 0]


# ---------------------------------------------------------------- plotting
def make_plots(steps: pd.DataFrame, queues: pd.DataFrame, out_dir: Path) -> list[Path]:
    try:
        import matplotlib
        matplotlib.use("Agg")                # no window, just files
        import matplotlib.pyplot as plt
    except ImportError:
        print("  [skip] matplotlib not installed - no figures")
        return []

    made = []
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    if not steps.empty:
        fig, ax1 = plt.subplots(figsize=(10, 4.5))
        ax1.plot(steps["time"], steps["running"], color="#1f77b4", label="vehicles in network")
        ax1.set_xlabel("simulation time (s)")
        ax1.set_ylabel("vehicles in network", color="#1f77b4")
        ax1.tick_params(axis="y", labelcolor="#1f77b4")

        ax2 = ax1.twinx()
        ax2.plot(steps["time"], steps["halting"], color="#d62728", alpha=0.65,
                 label="halted (queued)")
        ax2.set_ylabel("halted vehicles", color="#d62728")
        ax2.tick_params(axis="y", labelcolor="#d62728")

        plt.title("Network state over time")
        fig.tight_layout()
        p = out_dir / "plot_network.png"
        fig.savefig(p, dpi=130)
        plt.close(fig)
        made.append(p)

    if not queues.empty:
        worst = (queues.groupby("lane")["queue_length"].max()
                 .sort_values(ascending=False).head(5).index)
        sub = queues[queues["lane"].isin(worst)]
        fig, ax = plt.subplots(figsize=(10, 4.5))
        for lane in worst:
            s = sub[sub["lane"] == lane]
            ax.plot(s["step"], s["queue_length"], label=lane, linewidth=1.1)
        ax.set_xlabel("simulation time (s)")
        ax.set_ylabel("queue length (m)")
        ax.set_title("Queue length on the five worst lanes")
        ax.legend(fontsize=8)
        fig.tight_layout()
        p = out_dir / "plot_queue.png"
        fig.savefig(p, dpi=130)
        plt.close(fig)
        made.append(p)

    return made


# ---------------------------------------------------------------- driver
def load_run(run_dir: Path) -> dict:
    """Read every SUMO output file we know about from one run directory."""
    trips = numeric(read_xml_records(run_dir / "tripinfo.xml", "tripinfo"),
                    ["duration", "waitingTime", "timeLoss", "routeLength",
                     "departDelay", "stopTime"])
    steps = numeric(read_xml_records(run_dir / "summary.xml", "step"),
                    ["time", "inserted", "arrived", "running", "halting",
                     "waiting", "teleports", "collisions", "meanSpeed"])
    queues = read_queue_series(run_dir / "queues.xml")
    return {"trips": trips, "steps": steps, "queues": queues}


def report_one(run_dir: Path, save: bool) -> dict:
    print("=" * 70)
    print(f"analysing {run_dir}")
    print("=" * 70)
    d = load_run(run_dir)
    trips, steps, queues = d["trips"], d["steps"], d["queues"]
    print(f"  tripinfo : {len(trips):>6} trips")
    print(f"  summary  : {len(steps):>6} seconds")
    print(f"  queues   : {len(queues):>6} (time, lane) rows")

    m = {**trip_metrics(trips), **network_metrics(steps)}
    if not m:
        print("  nothing to analyse")
        return {}

    print()
    print("--- headline numbers ---")
    for k, v in m.items():
        print(f"  {k:22s} {v}")

    # data-quality gate: these two mean the results are not trustworthy
    if m.get("teleports"):
        print(f"\n  [!] {m['teleports']} teleport(s): vehicles were moved by the")
        print("      simulation, so travel times are distorted. Do not report these.")
    if m.get("collisions"):
        print(f"\n  [!] {m['collisions']} collision(s) - check the network.")

    by_vtype = pd.DataFrame()
    if not trips.empty and "vType" in trips.columns:
        by_vtype = (trips.groupby("vType")[["duration", "waitingTime", "timeLoss"]]
                    .agg(["count", "mean"]).round(1))
        print("\n--- by vehicle type ---")
        print(by_vtype.to_string())

    by_lane = lane_metrics(queues)
    if not by_lane.empty:
        print("\n--- worst lanes by queue (top 8) ---")
        print(by_lane.head(8).to_string())

    if save:
        pd.DataFrame([m]).to_csv(run_dir / "metrics.csv", index=False)
        if not by_vtype.empty:
            by_vtype.to_csv(run_dir / "by_vtype.csv")
        if not by_lane.empty:
            by_lane.to_csv(run_dir / "by_lane_queue.csv")
        if not steps.empty:
            steps.to_csv(run_dir / "summary_timeseries.csv", index=False)
        print(f"\n  wrote metrics.csv, by_vtype.csv, by_lane_queue.csv, "
              f"summary_timeseries.csv")
        for p in make_plots(steps, queues, run_dir):
            print(f"  wrote {p.name}")
    return m


def run_simulation(run_dir: Path, end: int, route_file: Path) -> None:
    """Produce the output files we are about to analyse."""
    run_dir.mkdir(parents=True, exist_ok=True)
    add = run_dir / "outputs.add.xml"
    add.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<additional>
    <edgeData id="edges" file="{run_dir / 'edgeData.xml'}" begin="0" end="{end}"
              period="300" excludeEmpty="false"/>
</additional>
""", encoding="utf-8")
    sumo = find_sumo_home() / "bin" / ("sumo.exe" if sys.platform == "win32" else "sumo")
    cmd = [str(sumo), "-n", str(NET_FILE), "-r", str(route_file), "-a", str(add),
           "--tripinfo-output", str(run_dir / "tripinfo.xml"),
           "--summary-output", str(run_dir / "summary.xml"),
           "--queue-output", str(run_dir / "queues.xml"),
           "--no-step-log", "true", "--no-warnings", "true",
           "--time-to-teleport", "-1", "--end", str(end)]
    print(f"running simulation (route file: {route_file.name}) ...")
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        print(((r.stdout or "") + (r.stderr or ""))[-600:])
        raise SystemExit("simulation failed")


def compare(run_dirs: list[Path]) -> None:
    print("=" * 70)
    print("comparison")
    print("=" * 70)
    rows = {}
    for d in run_dirs:
        m = report_one(d, save=False)
        if m:
            rows[d.name] = m
    if len(rows) < 2:
        print("\nneed at least two runs with data to compare")
        return
    table = pd.DataFrame(rows).T
    print()
    print(table.to_string())
    out = ROOT / "results" / "run_comparison.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(out)
    print(f"\nwrote {out.relative_to(ROOT)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", type=Path, default=ROOT / "results" / "demo")
    ap.add_argument("--simulate", action="store_true",
                    help="run a simulation into --run-dir first")
    ap.add_argument("--route-file", type=Path, default=None,
                    help="route file to simulate; defaults to whichever exists "
                         "(flow.rou.xml preferred - simple.rou.xml has no vehicles)")
    ap.add_argument("--end", type=int, default=1800, help="seconds, with --simulate")
    ap.add_argument("--no-save", action="store_true",
                    help="print only, write no files")
    ap.add_argument("--compare", nargs="+", type=Path, metavar="DIR",
                    help="compare several run directories")
    args = ap.parse_args()

    if args.compare:
        compare(args.compare)
        return 0

    if args.simulate:
        route = args.route_file
        if route is None:
            # simple.rou.xml only defines types and routes - no vehicles - so
            # prefer a file that actually contains demand.
            for cand in (ROOT / "net" / "flow.rou.xml", ROUTE_FILE):
                if cand.exists():
                    route = cand
                    break
        if route is None or not route.exists():
            raise SystemExit(
                "no route file with demand found. Pass --route-file, or copy\n"
                "net/flow.rou.xml into the repo (see the README).")
        run_simulation(args.run_dir, args.end, route)
        print()
    m = report_one(args.run_dir, save=not args.no_save)
    if not m:
        print("\nnothing analysed. Try:  python scripts/analyse_run.py "
              "--run-dir results/demo --simulate")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
