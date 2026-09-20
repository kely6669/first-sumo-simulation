"""Aggregate every simulation run into a comparison table.

This is where pandas earns its keep.  Running 120 simulations produces 240
CSV files; nobody reads those by hand.  The job is:

    read every run  ->  clean  ->  aggregate per run  ->  group by strategy
                    ->  mean +/- spread  ->  one table you can put in a report

Run:  python scripts/analyse_results.py [--save]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sumo_config import ROOT  # noqa: E402

RUNS_DIR = ROOT / "results" / "runs"
INDEX = ROOT / "results" / "index.csv"

# SUMO writes "csv" files separated by semicolons, not commas.  Reading with
# the default separator silently gives you a single column of text.
SEP = ";"


def load_index() -> pd.DataFrame:
    if not INDEX.exists():
        raise SystemExit(f"{INDEX} not found - run run_experiments.py first")
    df = pd.read_csv(INDEX)
    print(f"[1] index: {len(df)} runs "
          f"({df['strategy'].nunique()} strategies x "
          f"{df['demand'].nunique()} demand levels x "
          f"{df['seed'].nunique()} seeds)")
    return df


def load_tripinfos(index: pd.DataFrame) -> pd.DataFrame:
    """One row per run, summarising all the trips in that run."""
    rows = []
    missing = []
    for r in index.itertuples():
        p = RUNS_DIR / r.tripinfo_file
        if not p.exists():
            missing.append(r.tripinfo_file)
            continue
        t = pd.read_csv(p, sep=SEP)
        if t.empty:
            missing.append(r.tripinfo_file)
            continue
        rows.append({
            "strategy": r.strategy,
            "headway_scale": r.demand,
            "seed": r.seed,
            "trips": len(t),
            "mean_duration": t["duration"].mean(),
            "mean_waiting": t["tripinfo_waitingTime"].mean(),
            "mean_timeloss": t["tripinfo_timeLoss"].mean(),
            "mean_stoptime": t["tripinfo_stopTime"].mean(),
            "p95_duration": t["duration"].quantile(0.95),
            "max_waiting": t["tripinfo_waitingTime"].max(),
        })
    if missing:
        print(f"    [!] {len(missing)} run(s) had no usable tripinfo: "
              f"{missing[:3]}")
    return pd.DataFrame(rows)


def load_summaries(index: pd.DataFrame) -> pd.DataFrame:
    """One row per run, from the per-second summary time series.

    Column names are prefixed with `net_` so they cannot collide with the
    tripinfo columns when the two tables are merged.
    """
    rows = []
    for r in index.itertuples():
        p = RUNS_DIR / r.summary_file
        if not p.exists():
            continue
        s = pd.read_csv(p, sep=SEP)
        if s.empty:
            continue
        # ignore the first 60 s: warm-up, network still filling up
        warm = s[s["time"] >= 60]
        rows.append({
            "strategy": r.strategy,
            "headway_scale": r.demand,
            "seed": r.seed,
            "net_running": warm["running"].mean(),
            "net_halting": warm["halting"].mean(),
            "net_stopped": warm["stopped"].mean(),
            "net_mean_speed": warm["meanSpeed"].mean(),
            # NOTE: summary's meanWaitingTime is reported per second and comes
            # out as 0 in this scenario, so waiting time is taken from the
            # tripinfo table instead (mean_waiting).
            "net_teleports": int(s["teleports"].max()),
            "net_collisions": int(s["collisions"].max()),
            "net_arrived": int(s["arrived"].max()),
            "net_discarded": int(s["discarded"].max()),
        })
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--save", action="store_true",
                    help="write the summary table to results/summary.csv")
    args = ap.parse_args()

    print("=" * 74)
    print("aggregating simulation runs")
    print("=" * 74)

    index = load_index()
    trips = load_tripinfos(index)
    print(f"[2] tripinfo aggregated: {len(trips)} runs, "
          f"{int(trips['trips'].sum())} trips total")

    sums = load_summaries(index)
    print(f"[3] summary aggregated: {len(sums)} runs")

    # ---- sanity checks (the data-cleaning part) --------------------------
    print()
    print("[4] sanity checks")
    problems = []
    if len(trips) != len(index):
        problems.append(f"only {len(trips)}/{len(index)} runs produced tripinfo")
    if sums.empty:
        problems.append("no summary data")
    else:
        if len(sums) != len(index):
            problems.append(f"only {len(sums)}/{len(index)} runs produced summary")
        bad_tp = sums[sums["net_teleports"] > 0]
        if len(bad_tp):
            problems.append(f"{len(bad_tp)} run(s) had teleports "
                            f"(vehicles were teleported: results distorted)")
        bad_col = sums[sums["net_collisions"] > 0]
        if len(bad_col):
            problems.append(f"{len(bad_col)} run(s) had collisions")
    nonnum = int(trips.isna().sum().sum())
    if not sums.empty:
        nonnum += int(sums.isna().sum().sum())
    if nonnum:
        problems.append(f"{nonnum} missing values in aggregated table")
    if problems:
        for p in problems:
            print(f"    [!] {p}")
    else:
        print("    no problems found")

    # ---- the table -------------------------------------------------------
    merged = trips.merge(sums, on=["strategy", "headway_scale", "seed"], how="outer")

    print()
    print("=" * 74)
    print("[5] strategy comparison  (mean +/- std over seeds)")
    print("=" * 74)
    metrics = ["mean_duration", "mean_waiting", "mean_timeloss",
               "mean_halting", "net_running", "net_mean_wait", "net_arrived"]
    metrics = [m for m in metrics if m in merged.columns]
    for demand in sorted(merged["headway_scale"].unique()):
        label = "higher demand" if demand < 1 else "lower demand"
        print(f"\n--- headway scale {demand:.2f}  ({label}) ---")
        sub = merged[merged["headway_scale"] == demand]
        for m in metrics:
            line = f"  {m:16s}"
            for strat in sorted(sub["strategy"].unique()):
                v = sub[sub["strategy"] == strat][m]
                mean = v.mean()
                sd = v.std(ddof=1) if len(v) > 1 else 0.0
                line += f"  {strat}={mean:8.1f}±{sd:5.1f}"
            print(line)

    print()
    print("=" * 74)
    print("[6] spread check - is the difference bigger than the noise?")
    print("=" * 74)
    for demand in sorted(merged["headway_scale"].unique()):
        sub = merged[merged["headway_scale"] == demand]
        strategies = sorted(sub["strategy"].unique())
        if len(strategies) < 2:
            continue
        a, b = strategies[0], strategies[1]
        va = sub[sub["strategy"] == a]["mean_waiting"].dropna()
        vb = sub[sub["strategy"] == b]["mean_waiting"].dropna()
        if va.empty or vb.empty:
            continue
        spread = max(va.std(ddof=1) if len(va) > 1 else 0.0,
                     vb.std(ddof=1) if len(vb) > 1 else 0.0)
        diff = abs(va.mean() - vb.mean())
        verdict = ("difference > spread, likely real"
                   if diff > spread else
                   "difference <= spread, NOT conclusive - more seeds needed")
        print(f"  headway_scale {demand:.2f}: {a}={va.mean():6.1f} vs {b}={vb.mean():6.1f}"
              f"  diff={diff:5.1f}  spread={spread:5.1f}  -> {verdict}")

    if args.save:
        out = ROOT / "results" / "summary.csv"
        merged.to_csv(out, index=False)
        print(f"\nwrote {out.relative_to(ROOT)}")

    print("\nNext steps: plot mean waiting time per strategy, or feed the "
          "per-run table into a statistical test (Mann-Whitney / t-test).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
