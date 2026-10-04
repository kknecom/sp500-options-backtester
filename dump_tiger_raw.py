"""
Diagnostic: dump RAW Tiger filled orders + executions (transactions) to
data/real/tiger_raw_dump.json so the journal's grouping/P&L logic can be
checked against exactly what Tiger returns (the synced trades table
currently shows $0 credits and expiry-before-entry rows, which points at
a parsing/pairing problem only visible in the raw fields).

    python dump_tiger_raw.py                     # 2025-01-01 -> today
    python dump_tiger_raw.py --start 2024-01-01

Read-only. Output contains your account number -> gitignored.
"""
import argparse, json
from datetime import date, datetime, timedelta
from pathlib import Path

from collectors.tiger_trade_history_collector import _make_trade_client

OUT = Path(__file__).parent / "data" / "real" / "tiger_raw_dump.json"


def to_plain(o, depth=0):
    if depth > 6:
        return repr(o)
    if isinstance(o, (str, int, float, bool)) or o is None:
        return o
    if isinstance(o, (list, tuple, set)):
        return [to_plain(x, depth + 1) for x in o]
    if isinstance(o, dict):
        return {str(k): to_plain(v, depth + 1) for k, v in o.items()}
    if hasattr(o, "__dict__"):
        return {k: to_plain(v, depth + 1) for k, v in vars(o).items() if not k.startswith("_")}
    return str(o)


def windows(start, end, days=90):
    s = start
    while s < end:
        e = min(s + timedelta(days=days), end)
        yield s, e
        s = e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-01-01")
    args = ap.parse_args()
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = date.today() + timedelta(days=1)
    client = _make_trade_client()
    dump = {"generated_at": datetime.now().isoformat(timespec="seconds"),
            "filled_orders": [], "transactions": [], "errors": []}

    for s, e in windows(start, end):
        try:
            orders = client.get_filled_orders(sec_type="OPT", start_time=s.isoformat(), end_time=e.isoformat())
            dump["filled_orders"].extend(to_plain(orders or []))
        except Exception as ex:
            dump["errors"].append(f"filled_orders {s}..{e}: {ex}")
        try:
            tx = client.get_transactions(sec_type="OPT", since_date=s.isoformat(), to_date=e.isoformat(), limit=500)
            tx = getattr(tx, "result", tx)
            dump["transactions"].extend(to_plain(tx or []))
        except Exception as ex:
            dump["errors"].append(f"transactions {s}..{e}: {ex}")

    OUT.write_text(json.dumps(dump, indent=1, default=str))
    print(f"Wrote {OUT}: {len(dump['filled_orders'])} filled orders, "
          f"{len(dump['transactions'])} transactions, {len(dump['errors'])} errors")
    for er in dump["errors"][:5]:
        print("  ", er[:200])


if __name__ == "__main__":
    main()
