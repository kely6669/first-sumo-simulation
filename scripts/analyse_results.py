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
from itertools import combinations
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyse_run as single  # noqa: E402
from sumo_config import ROOT  # noqa: E402

#: Where a batch lives when --results-dir is not given.  Keep in step with
#: run_experiments.DEFAULT_RESULTS: this script reads what that one wrote.
DEFAULT_RESULTS = ROOT / "results"


def results_paths(results_dir: Path) -> tuple[Path, Path, Path]:
    """The three files a batch produces: index in, summary and figure out."""
    return (results_dir / "index.csv", results_dir / "summary.csv",
            results_dir / "plot_strategies.png")


def _show(path: Path) -> str:
    """A path the way the user would type it: relative when it is in the repo."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


#: Metrics compared between strategies.  Only the ones that exist are used.
COMPARED = ["mean_waiting_s", "mean_duration_s", "mean_timeloss_s",
            "mean_queue_m", "max_queue_m", "mean_running", "arrived",
            "trips_completed"]


def load_index(index_path: Path) -> pd.DataFrame:
    if not index_path.exists():
        raise SystemExit(f"{index_path} not found - run run_experiments.py first")
    df = pd.read_csv(index_path)
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
        print(f"    [!] {len(missing)} run(s) had no usable output: {missing[:3]}")
        if len(missing) == len(index):
            print("        (all of them - see the note at the end of this output)")
    df = pd.DataFrame(rows)
    if not df.empty:
        # A run driven by a route file has no demand multiplier: its vehicles
        # are in the file, and run_simulation records demand=None.  Pandas
        # cannot group by NaN - `df[df.demand == nan]` is empty, because
        # nan != nan - so every comparison below would silently come out
        # blank.  Fill it, and remember which runs were filled so the heading
        # can say "route file" instead of claiming a headway scale.
        df["demand_injected"] = df["demand"].notna()
        df["demand"] = df["demand"].fillna(1.0)
    return df


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
        sub = df[df["demand"] == demand]
        if "demand_injected" in df.columns and not sub["demand_injected"].all():
            # the vehicles came from a route file, so there is no multiplier
            # to report - saying "headway scale 1.00" would invent one
            label = "demand from the route file"
        else:
            label = "headway scale {:.2f}  ({})".format(
                demand, "higher demand" if demand < 1 else (
                    "lower demand" if demand > 1 else "baseline demand"))
        print(f"\n--- {label} ---")
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
    a finding.  For every pair of strategies it compares the gap between them
    against the scatter across seeds; when the scatter is the bigger of the
    two, the honest verdict is "not enough runs", not "strategy A wins".

    Every pair, not just the first two.  With four strategies the pairs
    nobody thought to look at are usually the ones that decide whether the
    headline is safe - and a check that quietly compares two of them while
    printing no warning is worse than no check at all.
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
        if "demand_injected" in df.columns and not sub["demand_injected"].all():
            print("\n  demand from the route file")
        else:
            print(f"\n  headway scale {demand:.2f}")
        if len(strategies) < 2:
            print("    only one strategy - nothing to compare")
            continue
        series = {name: sub[sub["strategy"] == name]["mean_waiting_s"].dropna()
                  for name in strategies}
        for first, second in combinations(strategies, 2):
            a, b = series[first], series[second]
            if a.empty or b.empty:
                continue
            scatter = max(a.std(ddof=1) if len(a) > 1 else 0.0,
                          b.std(ddof=1) if len(b) > 1 else 0.0)
            difference = abs(a.mean() - b.mean())
            verdict = ("difference > spread, likely real" if difference > scatter
                       else "difference <= spread, NOT conclusive")
            print(f"    {first:9s} vs {second:9s}  diff={difference:5.1f}  "
                  f"spread={scatter:4.1f}  -> {verdict}")


def plot(df: pd.DataFrame, figure_path: Path) -> Path | None:
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
    figure_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(figure_path, dpi=130)
    plt.close(figure)
    return figure_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--save", action="store_true",
                    help="write the per-run table to summary.csv")
    ap.add_argument("--results-dir", type=Path, default=None,
                    help="the batch to read.  Default results/, which is what "
                         "run_experiments.py writes when it is not given one.")
    args = ap.parse_args()

    results_dir = (args.results_dir or DEFAULT_RESULTS).resolve()
    index_path, summary_csv, figure_path = results_paths(results_dir)

    print("=" * 74)
    print("aggregating simulation runs")
    print("=" * 74)

    index = load_index(index_path)
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
        # The usual cause is a fresh clone: results/index.csv is committed but
        # results/runs/ is not, because the raw output is regenerable.  Saying
        # only "nothing to report" would leave the reader stuck, so say what
        # to run.
        print()
        print("  Nothing to aggregate.  results/index.csv lists runs whose raw")
        print("  output is not in this checkout - results/runs/ is not committed,")
        print("  because it is regenerated.  Produce it with:")
        print()
        print("      python scripts/run_experiments.py")
        print()
        print("  then run this script again.")
        return 1

    compare(df)
    spread_check(df)

    if args.save:
        summary_csv.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(summary_csv, index=False)
        print(f"\nwrote {_show(summary_csv)}")
    figure = plot(df, figure_path)
    if figure:
        print(f"wrote {_show(figure)}")

    print(f"\nNext steps: feed {_show(summary_csv)} into a statistical test "
          "(Mann-Whitney / t-test), or raise --runs until the spread check "
          "stops asking for more seeds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
