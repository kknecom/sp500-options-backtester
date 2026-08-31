"""
Run this directly on your own machine's terminal (NOT through a sandboxed
bridge/VM) to confirm Tiger Open API connectivity end-to-end and discover
the real shape of the data this project's collector code assumes.

    cd sp500_options_backtester
    source .venv/bin/activate   # or your venv
    python scripts/tiger_discover.py

What it does, and why each step matters:
  1. Confirms credentials load (tiger_id/account/private_key present) --
     doesn't print their values.
  2. Calls get_market_status -- cheapest possible live round-trip, proves
     the signed request reaches Tiger's server and comes back OK.
  3. Calls get_option_expirations('SPX') -- lists real available
     expiration dates.
  4. Picks the expiration nearest 45 DTE (config.TARGET_DTE) and pulls
     that one chain via get_option_chain(..., return_greek_value=True).
  5. Prints the chain DataFrame's column names and the first couple of
     rows near the current ATM strike.

Nothing here places a trade or writes to the database -- it only reads
and prints. Bring the printed column names back so tiger_daily_collector.py's
field-name guesses (identifier/expiry/strike/put_call/delta/etc.) can be
corrected against what your account's data actually looks like, if they
differ.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
import config
from tiger_client import get_quote_client
from collectors.tiger_daily_collector import fetch_option_expirations, pick_expiration_near_dte


def main():
    print("1. Loading credentials...")
    client = get_quote_client()
    print("   OK -- config loaded, client constructed.\n")

    print("2. Checking market status (cheapest live round-trip)...")
    status = client.get_market_status(market="US")
    print(status, "\n")

    print("3. Fetching SPX option expirations...")
    expirations = fetch_option_expirations("SPX")
    print(f"   {len(expirations)} expirations found. First 10: {expirations[:10]}\n")

    if not expirations:
        print("No expirations returned -- stopping here. Check symbol convention "
              "('SPX' may need to be '.SPX' or similar for your account/market).")
        return

    target = pick_expiration_near_dte(expirations, target_dte=config.TARGET_DTE)
    print(f"4. Nearest expiration to {config.TARGET_DTE} DTE: {target}")
    chain = client.get_option_chain(symbol="SPX", expiry=target.strftime("%Y-%m-%d"),
                                     return_greek_value=True, market="US")
    print(f"   Chain shape: {chain.shape}")
    print(f"   Columns: {list(chain.columns)}\n")

    print("5. Sample rows (first 5):")
    print(chain.head(5).to_string())


if __name__ == "__main__":
    main()