"""
CLI entry point: run all 3 strategies over a price series and print
summary metrics.

Usage:
    python run_backtest.py --csv data/sample/spx_proxy_sample.csv
    python run_backtest.py --source moomoo --symbol SPY --start 2024-01-01

The bundled sample CSV is only ~50 trading days of SPY-derived SPX-proxy
closes (see README) -- it exists to prove the engine runs end-to-end, NOT
as a real strategy validation. --source moomoo pulls real daily closes
live via OpenD instead (requires it running and logged in -- see
moomoo_client.py); SPX/VIX themselves aren't usable there ("US stock
indices are not supported" from moomoo's history API, confirmed live),
so SPY is the default symbol, same proxy as the sample CSV.

Either way, this only supplies the UNDERLYING price series -- every
trade is still priced with the synthetic Black-Scholes chain in
pricing/black_scholes.py (see backtest/engine.py's docstring). Real
historical SPX option chains (strikes/greeks/OI on the actual days
trades were opened) aren't available from a live quote API like moomoo's
at all -- that needs either a paid historical options vendor or letting
collectors/moomoo_daily_collector.py accumulate real daily snapshots
over time.
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


def load_price_series_from_moomoo(symbol: str, start: str, end: str | None):
    from collectors.moomoo_daily_collector import fetch_underlying_bars
    df = fetch_underlying_bars(symbol, start=start, end=end)
    series = [
        (datetime.strptime(str(row["time_key"])[:10], "%Y-%m-%d").date(), float(row["close"]))
        for _, row in df.iterrows()
    ]
    series.sort(key=lambda x: x[0])
    return series


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["csv", "moomoo"], default="csv",
                         help="csv (default): load --csv file. moomoo: pull live daily "
                              "closes via OpenD -- see moomoo_client.py.")
    parser.add_argument("--csv", default="data/sample/spx_proxy_sample.csv")
    parser.add_argument("--symbol", default="SPY",
                         help="Underlying for --source moomoo. SPX/VIX aren't supported "
                              "for historical bars there -- see module docstring.")
    parser.add_argument("--start", help="Start date YYYY-MM-DD, required for --source moomoo.")
    parser.add_argument("--end", help="End date YYYY-MM-DD for --source moomoo (default: today).")
    args = parser.parse_args()

    if args.source == "moomoo":
        if not args.start:
            parser.error("--start is required with --source moomoo")
        price_series = load_price_series_from_moomoo(args.symbol, args.start, args.end)
        print(f"Loaded {len(price_series)} daily closes from moomoo ({args.symbol}) "
              f"({price_series[0][0]} to {price_series[-1][0]})")
    else:
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
