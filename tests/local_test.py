"""
Run this directly on your own machine's terminal (NOT through a sandboxed
bridge/VM) to confirm moomoo OpenAPI connectivity end-to-end and discover
the real shape of the data this project's collector code assumes.

    cd sp500_options_backtester
    source .venv/bin/activate   # or your venv
    # OpenD must already be running and logged in -- see moomoo_client.py
    python tests/local_test.py

What it does, and why each step matters:
  1. Connects to OpenD (moomoo_client.get_quote_context) -- confirms the
     gateway is reachable and logged in to a quote-capable session.
  2. Calls get_global_state -- cheapest possible live round-trip, proves
     the socket to OpenD is live and returns real market-state data.
  3. Calls get_option_expiration_date('SPX') via
     collectors.moomoo_daily_collector.fetch_option_expirations -- lists
     real available expiration dates.
  4. Picks the expiration nearest 45 DTE (config.TARGET_DTE) and pulls
     that one chain via fetch_option_chain, which merges in live
     bid/ask/greeks/OI from get_market_snapshot.
  5. Prints the chain DataFrame's column names and the first couple of
     rows near the current ATM strike.

Nothing here places a trade or writes to the database -- it only reads
and prints. Bring the printed column names back so
moomoo_daily_collector.py's field-name assumptions can be corrected
against what your account's data actually looks like, if they differ.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
import config
from moomoo_client import get_quote_context
from collectors.moomoo_daily_collector import fetch_option_expirations, pick_expiration_near_dte, fetch_option_chain


def main():
    print("1. Connecting to OpenD...")
    with get_quote_context() as ctx:
        print("   OK -- OpenD reachable and logged in.\n")

        print("2. Checking global state (cheapest live round-trip)...")
        _, state = ctx.get_global_state()
        print(state, "\n")

    print("3. Fetching SPX option expirations...")
    expirations = fetch_option_expirations("SPX")
    print(f"   {len(expirations)} expirations found. First 10: {expirations[:10]}\n")

    if not expirations:
        print("No expirations returned -- stopping here. Check the underlying code "
              "convention in moomoo_daily_collector.UNDERLYING_CODE_MAP ('US..SPX' as "
              "of this writing) still matches your account/market.")
        return

    target = pick_expiration_near_dte(expirations, target_dte=config.TARGET_DTE)
    print(f"4. Nearest expiration to {config.TARGET_DTE} DTE: {target}")
    chain = fetch_option_chain("SPX", target)
    print(f"   Chain shape: {chain.shape}")
    print(f"   Columns: {list(chain.columns)}\n")

    print("5. Sample rows (first 5):")
    print(chain.head(5).to_string())


if __name__ == "__main__":
    main()
