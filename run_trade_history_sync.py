"""
CLI: sync closed Tiger trade history into the local DB trade journal.

    python run_trade_history_sync.py                          # full history: EARLIEST_DEFAULT -> today
    python run_trade_history_sync.py --start 2026-06-01 --end 2026-09-27   # a specific window

Requires .env with TIGER_PROPS_PATH (or TIGER_ID / TIGER_ACCOUNT /
TIGER_PRIVATE_KEY_PATH) set -- copy .env.example -> .env first. Read-only
against your account -- pulls filled order history only, never
places/modifies/cancels orders.

FULL-HISTORY NOTE: Tiger's get_filled_orders has no documented total
lookback limit, only a 90-day window per call (already paginated
automatically -- see fetch_filled_orders in the collector). Omitting
--start defaults to EARLIEST_DEFAULT below, well before Tiger Brokers'
2014 founding, so it's guaranteed to cover your actual account inception
whenever that was -- pagination just returns empty windows for any
period before your account had fills, at the cost of a few extra (fast,
free) API calls. If Tiger's API ever rejects a window that old with an
explicit error, narrow --start to your account's actual opening date and
rerun.
"""
from collectors.tiger_trade_history_collector import run_sync
from datetime import date, datetime

EARLIEST_DEFAULT = date(2010, 1, 1)

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--start", default=None, help="YYYY-MM-DD (default: full history from 2010-01-01)")
    p.add_argument("--end", default=None, help="YYYY-MM-DD (default: today)")
    args = p.parse_args()
    start = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else EARLIEST_DEFAULT
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today()
    print(f"Syncing Tiger trade history from {start} to {end} (this may take a while for full history) ...")
    run_sync(start, end)
