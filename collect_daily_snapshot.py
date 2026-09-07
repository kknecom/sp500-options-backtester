"""
Run this ONCE PER TRADING DAY to capture a real SPX option chain
snapshot (real bid/ask, real OI, real greeks -- no proxy, no synthetic
Black-Scholes) into the local database, slowly building a real dataset
to eventually replace/validate the synthetic backtest.

    python collect_daily_snapshot.py                  # nearest to config.TARGET_DTE (45)
    python collect_daily_snapshot.py --dte 0           # nearest to 0DTE instead

REQUIRES moomoo's OpenD gateway running and logged in on THIS machine
(see moomoo_client.py) -- this will NOT work from a sandboxed dev bridge,
only your own Mac terminal where OpenD actually runs.

SCHEDULING (macOS): OpenD only exists while you're logged in and the
gateway app/process is running, so a plain crontab entry works fine as
long as that's true when it fires. Once daily, after the entry you
actually trade off has settled (e.g. shortly after market close, ~4:15pm
ET) is reasonable:

    crontab -e
    # add a line like (adjust python path/venv activation as needed):
    15 16 * * 1-5 cd /Users/kk.naing/Documents/ai/trading-project/sp500_options_backtester && \\
        /path/to/venv/bin/python collect_daily_snapshot.py >> logs/daily_snapshot.log 2>&1

If you'd rather not fight cron+timezone+login-session edge cases, running
it manually once a day (or asking your own Claude session to run it) is
just as valid -- the collector is idempotent per day (INSERT OR REPLACE),
so there's no harm running it more than once, and no harm skipping a day
occasionally. Data quality > perfect automation for a slowly-accumulating
dataset like this.
"""
from __future__ import annotations
import argparse
from datetime import date, datetime

from collectors.moomoo_daily_collector import (
    fetch_daily_chain_snapshot, write_option_bars, write_underlying_bar,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--underlying", default="SPX")
    parser.add_argument("--dte", type=int, default=None,
                         help="Target DTE for the expiration to snapshot (default: config.TARGET_DTE, 45).")
    args = parser.parse_args()

    print(f"[{datetime.now().isoformat()}] Fetching real {args.underlying} chain "
          f"(OpenD must be running and logged in)...")
    spot, bars = fetch_daily_chain_snapshot(args.underlying, target_dte=args.dte)

    write_underlying_bar(date.today(), args.underlying, spot)
    count = write_option_bars(bars)

    expiries = sorted({b.expiration for b in bars})
    print(f"[{datetime.now().isoformat()}] Spot: {spot:.2f}  |  Wrote {count} option "
          f"bars for expiry {expiries[0] if expiries else '?'} ({len(bars)} strikes/rights).")


if __name__ == "__main__":
    main()
