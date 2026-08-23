"""
CLI: test calendar/seasonal effects against a daily price CSV (date,close).

Usage:
    python run_seasonality.py --csv data/sample/spx_proxy_sample.csv

WARNING printed at runtime: this only produces statistically meaningful
results with 20-30+ years of daily history. Point --csv at a long SPX/SPY
series (e.g. pulled via Tiger's "Complete" stock/ETF day-bar history) once
available -- see README "Seasonality data" section.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime

from seasonality.calendar_effects import to_returns, EFFECTS
from seasonality.stats import evaluate_effect, bonferroni_alpha


def load_price_series(csv_path: str):
    series = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = datetime.strptime(row["date"], "%Y-%m-%d").date()
            series.append((d, float(row["close"])))
    series.sort(key=lambda x: x[0])
    return series


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="data/sample/spx_proxy_sample.csv")
    parser.add_argument("--alpha", type=float, default=0.05)
    args = parser.parse_args()

    price_series = load_price_series(args.csv)
    daily_returns = to_returns(price_series)
    years_covered = (price_series[-1][0] - price_series[0][0]).days / 365.25

    print(f"Loaded {len(price_series)} closes ({price_series[0][0]} to {price_series[-1][0]}, "
          f"~{years_covered:.2f} years) from {args.csv}")
    if years_covered < 10:
        print("\n*** WARNING: less than 10 years of history. Seasonal effects need MANY repeated ***")
        print("*** cycles to separate from noise -- treat every result below as illustrative,  ***")
        print("*** not a validated edge. See README 'Seasonality data' section for how to get   ***")
        print("*** a long SPX/SPY series via your Tiger account.                                ***\n")

    corrected_alpha = bonferroni_alpha(args.alpha, len(EFFECTS))
    print(f"Testing {len(EFFECTS)} effects. Base alpha={args.alpha}, "
          f"Bonferroni-corrected alpha={corrected_alpha:.4f} (guards against data-dredging "
          f"across this many simultaneous tests)\n")

    results = []
    for name, tag_fn in EFFECTS.items():
        in_window = [r.ret for r in daily_returns if tag_fn(r)]
        out_window = [r.ret for r in daily_returns if not tag_fn(r)]
        res = evaluate_effect(name, in_window, out_window)
        results.append(res)

    results.sort(key=lambda r: (r.p_value if r.p_value == r.p_value else 1.0))  # NaN-safe sort

    header = f"{'Effect':38s} {'n_in':>6s} {'mean_in%':>9s} {'mean_out%':>10s} {'t-stat':>8s} {'p-value':>9s} {'Sig?':>6s}"
    print(header)
    print("-" * len(header))
    for r in results:
        sig = "**" if (r.p_value == r.p_value and r.p_value < corrected_alpha) else (
              "*" if (r.p_value == r.p_value and r.p_value < args.alpha) else "")
        print(f"{r.name:38s} {r.n_in:6d} {r.mean_in*100:9.3f} {r.mean_out*100:10.3f} "
              f"{r.t_stat:8.2f} {r.p_value:9.4f} {sig:>6s}")

    print("\n*  = significant at uncorrected alpha (naive, expect ~1 false positive per 20 tests)")
    print("** = significant after Bonferroni correction for multiple comparisons")


if __name__ == "__main__":
    main()
