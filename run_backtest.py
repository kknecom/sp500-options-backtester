"""
CLI entry point: run all 3 strategies over a price series and print
summary metrics.

Usage:
    python run_backtest.py --csv data/sample/spx_proxy_sample.csv
    python run_backtest.py --source moomoo --symbol SPY --start 2024-01-01
    python run_backtest.py --source yfinance --start 2015-01-01

The bundled sample CSV is only ~50 trading days of SPY-derived SPX-proxy
closes (see README) -- it exists to prove the engine runs end-to-end, NOT
as a real strategy validation.

--source moomoo pulls real daily closes live via OpenD instead (requires
it running and logged in -- see moomoo_client.py); SPX/VIX themselves
aren't usable there ("US stock indices are not supported" from moomoo's
history API, confirmed live), so SPY is the default symbol, same proxy
as the sample CSV. No real VIX available through this path.

--source yfinance pulls REAL historical data via Yahoo Finance (free, no
API key, no local gateway needed) -- unlike moomoo, Yahoo does not reject
index symbols, so this gets the actual SPX index (^GSPC) directly instead
of a SPY-scaled proxy, AND real historical VIX (^VIX) to drive the
synthetic chain's volatility input (see backtest/engine.py's vix_series
docstring) instead of realized vol computed from the price series itself.
This is the most realistic of the three CSV/moomoo/yfinance options for a
genuine multi-year backtest, while still using a SYNTHETIC option chain
underneath (see next paragraph) -- it fixes the underlying-price and
volatility-input realism, not the chain-pricing realism.

Whichever source, this only supplies the UNDERLYING price series (and,
for yfinance, the volatility series) -- every trade is still priced with
the synthetic Black-Scholes chain in pricing/black_scholes.py (see
backtest/engine.py's docstring). Real historical SPX option chains
(strikes/greeks/OI on the actual days trades were opened) aren't
available from a live quote API like moomoo's at all -- that needs either
a paid historical options vendor or letting
collectors/moomoo_daily_collector.py accumulate real daily snapshots
over time.
"""
from __future__ import annotations
import argparse
import csv
from datetime import date, datetime

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


def load_price_series_from_yfinance(symbol: str, start: str, end: str | None = None):
    """
    Real daily closes via Yahoo Finance (yfinance) -- free, no API key,
    no local gateway. Unlike moomoo, Yahoo does not reject index symbols:
    '^GSPC' (SPX) and '^VIX' both work directly here, so this can pull
    the REAL SPX index level, not a SPY-scaled proxy.

    yfinance's returned columns are sometimes a flat Index and sometimes
    a MultiIndex (varies by version/call shape) -- handled defensively
    below rather than assuming one.
    """
    import yfinance as yf

    end = end or date.today().isoformat()
    df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=False)
    if df.empty:
        raise RuntimeError(f"yfinance returned no data for {symbol} ({start} to {end})")

    close_col = df["Close"]
    if hasattr(close_col, "columns"):  # MultiIndex column case
        close_col = close_col.iloc[:, 0]

    series = [(idx.date(), float(v)) for idx, v in close_col.items()]
    series.sort(key=lambda x: x[0])
    return series


def load_vix_series_from_yfinance(start: str, end: str | None = None):
    """Real historical VIX closes (percentage points, e.g. 18.04 == 18.04%) via yfinance."""
    return load_price_series_from_yfinance("^VIX", start, end)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["csv", "moomoo", "yfinance"], default="csv",
                         help="csv (default): load --csv file. moomoo: pull live daily "
                              "closes via OpenD -- see moomoo_client.py. yfinance: real "
                              "SPX (^GSPC) + real VIX via Yahoo Finance, no gateway needed.")
    parser.add_argument("--csv", default="data/sample/spx_proxy_sample.csv")
    parser.add_argument("--symbol", default=None,
                         help="Underlying symbol. Defaults to SPY for --source moomoo "
                              "(SPX/VIX aren't supported for historical bars there) or "
                              "^GSPC (real SPX index) for --source yfinance.")
    parser.add_argument("--start", help="Start date YYYY-MM-DD, required for --source moomoo/yfinance.")
    parser.add_argument("--end", help="End date YYYY-MM-DD (default: today).")
    args = parser.parse_args()

    vix_series = None
    if args.source == "moomoo":
        if not args.start:
            parser.error("--start is required with --source moomoo")
        symbol = args.symbol or "SPY"
        price_series = load_price_series_from_moomoo(symbol, args.start, args.end)
        print(f"Loaded {len(price_series)} daily closes from moomoo ({symbol}) "
              f"({price_series[0][0]} to {price_series[-1][0]}) -- sigma uses trailing "
              f"realized vol, no real VIX available through this path.")
    elif args.source == "yfinance":
        if not args.start:
            parser.error("--start is required with --source yfinance")
        symbol = args.symbol or "^GSPC"
        price_series = load_price_series_from_yfinance(symbol, args.start, args.end)
        vix_series = load_vix_series_from_yfinance(args.start, args.end)
        print(f"Loaded {len(price_series)} real {symbol} closes and {len(vix_series)} "
              f"real VIX closes via yfinance ({price_series[0][0]} to {price_series[-1][0]})")
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
        trades = run_backtest(strat, price_series, vix_series=vix_series)
        stats = summarize(trades)
        print_summary(strat.name, stats)
        for t in trades:
            legs_desc = ", ".join(f"{l.side} {l.right}{l.strike:.0f}" for l in t.legs)
            print(f"  [{t.status}] entry {t.entry_date} -> exit {t.exit_date} | "
                  f"{legs_desc} | credit {t.entry_credit:.2f} | pnl {t.realized_pnl}")


if __name__ == "__main__":
    main()
