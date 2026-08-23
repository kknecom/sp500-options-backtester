"""
CLI entry point: run all 3 strategies over a CSV price series and print
summary metrics.

Usage:
    python run_backtest.py --csv data/sample/spx_proxy_sample.csv

The bundled sample CSV is only ~50 trading days of SPY-derived SPX-proxy
closes (see README) -- it exists to prove the engine runs end-to-end, NOT
as a real strategy validation. Point --csv at a real multi-year SPX daily
close file (date,close columns) for a real backtest.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime

import config
from backtest.engine import run_backtest
from backtest.metrics import summarize, print_summary
from strategies.bull_put_spread import BullPutSpread
from strategies.bear_call_spread import BearCallSpread
from strategies.iron_condor import IronCondor


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
    args = parser.parse_args()

    price_series = load_price_series(args.csv)
    print(f"Loaded {len(price_series)} daily closes from {args.csv} "
          f"({price_series[0][0]} to {price_series[-1][0]})")

    strategies = [
        BullPutSpread(config),
        BearCallSpread(config),
        IronCondor(config),
    ]

    for strat in strategies:
        trades = run_backtest(strat, price_series)
        stats = summarize(trades)
        print_summary(strat.name, stats)
        for t in trades:
            legs_desc = ", ".join(f"{l.side} {l.right}{l.strike:.0f}" for l in t.legs)
            print(f"  [{t.status}] entry {t.entry_date} -> exit {t.exit_date} | "
                  f"{legs_desc} | credit {t.entry_credit:.2f} | pnl {t.realized_pnl}")


if __name__ == "__main__":
    main()
