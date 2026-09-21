"""Aggregate every simulation run into a comparison table, and plot it.

This is where pandas earns its keep.  Twenty-four simulations produce
seventy-two XML files; nobody reads those by hand.  The job is:

    read every run  ->  clean  ->  one row per run  ->  group by strategy
                    ->  mean +/- spread  ->  a table and a figure

The readers are imported from ``analyse_run``; this file does not have its
own copy.  That is deliberate.  When the single-run analysis was reading XML
and this one was reading semicolon-separated CSV, the two could - and did -
disagree about what "mean waiting time" meant.

Run:  python scripts/analyse_results.py [--save]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyse_run as single  # noqa: E402
from sumo_config import ROOT  # noqa: E402

INDEX = ROOT / "results" / "index.csv"
SUMMARY_CSV = ROOT / "results" / "summary.csv"
FIGURE = ROOT / "results" / "plot_strategies.png"

#: Metrics compared between strategies.  Only the ones that exist are used.
COMPARED = ["mean_waiting_s", "mean_duration_s", "mean_timeloss_s",
            "mean_queue_m", "max_queue_m", "mean_running", "arrived",
            "trips_completed"]


def load_index() -> pd.DataFrame:
    if not INDEX.exists():
        raise SystemExit(f"{INDEX} not found - run run_experiments.py first")
    df = pd.read_csv(INDEX)
    print(f"[1] index: {len(df)} runs "
          f"({df['strategy'].nunique()} strategies x "
          f"{df['demand'].nunique()} demand levels x "
          f"{df['seed'].nunique()} seeds)")
    return df


def collect(index: pd.DataFrame) -> pd.DataFrame:
    """Clean the runs and reduce each one to a single row of metrics."""
    rows, missing = [], []
    for record in index.itertuples():
        run_dir = ROOT / record.run_dir
        if not run_dir.is_dir():
            missing.append(f"{record.tag} (directory gone)")
            continue
        data = single.load_run(run_dir)
        if data["trips"].empty and data["steps"].empty:
            missing.append(f"{record.tag} (no usable output)")
            continue
        rows.append({
            "strategy": record.strategy,
            "demand": record.demand,
            "seed": record.seed,
            "tag": record.tag,
            # one shared definition of every metric, from analyse_run
            **single.trip_metrics(data["trips"]),
            **single.network_metrics(data["steps"]),
            **single.queue_metrics(data["queues"]),
        })
    if missing:
        print(f"    [!] dropped {len(missing)} run(s) with no usable data: "
              f"{missing[:3]}")
    return pd.DataFrame(rows)


def quality_gate(df: pd.DataFrame) -> list[str]:
    """Data-quality checks that decide whether the table may be trusted."""
    problems = []
    if df.empty:
        return ["every run was dropped - nothing to report"]
    for column, limit, why in (
        ("teleports", 0, "vehicles were moved by SUMO: travel times distorted"),
        ("collisions", 0, "vehicles overlapped: the network is wrong"),
    ):
        if column in df.columns:
            bad = df[df[column] > limit]
            if len(bad):
                problems.append(f"{len(bad)} run(s) with {column} > {limit} "
                                f"({why}): {list(bad['tag'])[:3]}")
    nulls = int(df.isna().sum().sum())
    if nulls:
        problems.append(f"{nulls} missing value(s) in the aggregated table")
    short = df[df["duration_s"] < 60] if "duration_s" in df.columns else df.iloc[:0]
    if len(short):
        problems.append(f"{len(short)} run(s) shorter than the 60 s warm-up")
    return problems


def compare(df: pd.DataFrame) -> None:
    """Print mean +/- std per strategy, then the spread check."""
    print()
    print("=" * 74)
    print("[4] strategy comparison  (mean +/- std over seeds)")
    print("=" * 74)
    present = [m for m in COMPARED if m in df.columns]
    for demand in sorted(df["demand"].unique()):
        label = "higher demand" if demand < 1 else (
            "lower demand" if demand > 1 else "baseline demand")
        print(f"\n--- headway scale {demand:.2f}  ({label}) ---")
        sub = df[df["demand"] == demand]
        for metric in present:
            line = f"  {metric:16s}"
            for strategy in sorted(sub["strategy"].unique()):
                values = sub[sub["strategy"] == strategy][metric]
                mean = values.mean()
                spread = values.std(ddof=1) if len(values) > 1 else 0.0
                line += f"  {strategy}={mean:8.1f}±{spread:5.1f}"
            print(line)


def spread_check(df: pd.DataFrame) -> None:
    """Is the difference between strategies bigger than the seed noise?

    This is the check that stops a two-seed experiment from being reported as
    a finding.  It compares the gap between strategies against the scatter
    across seeds; when the scatter is the bigger of the two, the honest
    verdict is "not enough runs", not "strategy A wins".
    """
    print()
    print("=" * 74)
    print("[5] spread check - is the difference bigger than the noise?")
    print("=" * 74)
    if "mean_waiting_s" not in df.columns:
        print("  no mean_waiting_s column to check")
        return
    for demand in sorted(df["demand"].unique()):
        sub = df[df["demand"] == demand]
        strategies = sorted(sub["strategy"].unique())
        if len(strategies) < 2:
            continue
        first, second = strategies[0], strategies[1]
        a = sub[sub["strategy"] == first]["mean_waiting_s"].dropna()
        b = sub[sub["strategy"] == second]["mean_waiting_s"].dropna()
        if a.empty or b.empty:
            continue
        scatter = max(a.std(ddof=1) if len(a) > 1 else 0.0,
                      b.std(ddof=1) if len(b) > 1 else 0.0)
        difference = abs(a.mean() - b.mean())
        verdict = ("difference > spread, likely real" if difference > scatter
                   else "difference <= spread, NOT conclusive - more seeds needed")
        print(f"  scale {demand:.2f}: {first}={a.mean():6.1f} vs "
              f"{second}={b.mean():6.1f}  diff={difference:5.1f}  "
              f"spread={scatter:5.1f}  -> {verdict}")


def plot(df: pd.DataFrame) -> Path | None:
    """Mean waiting time per strategy against demand, with seed error bars."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n  [skip] matplotlib not installed - no figure")
        return None
    if "mean_waiting_s" not in df.columns or df["demand"].nunique() < 2:
        print("\n  [skip] need two or more demand levels for a figure")
        return None

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    figure, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    for metric, axis, unit in (("mean_waiting_s", axes[0], "s"),
                               ("mean_queue_m", axes[1], "m")):
        if metric not in df.columns:
            continue
        for strategy in sorted(df["strategy"].unique()):
            sub = df[df["strategy"] == strategy]
            grouped = sub.groupby("demand")[metric]
            mean, spread = grouped.mean(), grouped.std(ddof=1).fillna(0.0)
            axis.errorbar(mean.index, mean.values, yerr=spread.values,
                          marker="o", capsize=4, label=strategy)
        axis.set_xlabel("headway scale  (<1 = more traffic)")
        axis.set_ylabel(f"{metric}  [{unit}]")
        axis.set_title(metric)
        axis.grid(alpha=0.3)
        axis.legend()
    figure.suptitle("Signal strategy comparison")
    figure.tight_layout()
    FIGURE.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(FIGURE, dpi=130)
    plt.close(figure)
    return FIGURE


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--save", action="store_true",
                    help="write the per-run table to results/summary.csv")
    args = ap.parse_args()

    print("=" * 74)
    print("aggregating simulation runs")
    print("=" * 74)

    index = load_index()
    df = collect(index)
    print(f"[2] cleaned: {len(df)}/{len(index)} runs usable")

    print()
    print("[3] quality gate")
    problems = quality_gate(df)
    if problems:
        for problem in problems:
            print(f"    [!] {problem}")
    else:
        print("    no problems found")

    if df.empty:
        return 1

    compare(df)
    spread_check(df)

    if args.save:
        SUMMARY_CSV.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(SUMMARY_CSV, index=False)
        print(f"\nwrote {SUMMARY_CSV.relative_to(ROOT)}")
    figure = plot(df)
    if figure:
        print(f"wrote {figure.relative_to(ROOT)}")

    print("\nNext steps: feed results/summary.csv into a statistical test "
          "(Mann-Whitney / t-test), or raise --runs until the spread check "
          "stops asking for more seeds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
