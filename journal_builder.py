"""
Builds the Trade Journal's closed-trade list from RAW Tiger executions
(data/real/tiger_raw_dump.json, written by dump_tiger_raw.py).

Why executions and not get_filled_orders: for this account the filled-orders
endpoint returns only expiry / assignment / exercise records (plus a couple of
stray fills), so the old sync produced $0-credit rows. get_transactions returns
every real fill with price, quantity, side and a millisecond timestamp, and each
combo order (vertical = 2 legs, iron condor = 4 legs) shares one order_id.

Method
  1. Walk fills in time order, one FIFO lot queue per contract.
  2. A fill that reduces an existing opposite-side position CLOSES lots (FIFO);
     otherwise it OPENS a lot belonging to the trade for that order_id.
  3. Legs still open after all fills whose contract has expired are closed from
     the matching Expiry / Assignment / Exercise record's realized_pnl (Tiger's own
     number, net of fees on that leg). No settlement price is needed.
  4. A trade is CLOSED once every one of its lots is closed. Anything that can't
     be resolved (opened before the API's history window, or an expiry record
     missing) is returned in `unresolved`, never guessed.

P&L here is GROSS of commissions on legs closed by a buy-back, because the
executions endpoint carries no fees; expired legs use Tiger's net figure (differs
by about $1.50 per leg). Exact fees need the monthly statement CSV. fee=None.
"""
from __future__ import annotations
import json
import re
from collections import defaultdict, deque
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DUMP_PATH = Path(__file__).parent / "data" / "real" / "tiger_raw_dump.json"
_EXPIRY_KINDS = ("Expiry", "Assignment", "Exercise")


def _field(s: str, key: str):
    m = re.search(r"'%s': ([^,]+)" % key, s)
    return m.group(1).strip("'") if m else None


def parse_expiry_records(filled_orders: list) -> dict:
    """{contract_key: [realized_pnl, ...]} from Expiry/Assignment/Exercise orders.
    Orders may arrive as dicts or as their str() form (dump_tiger_raw.py)."""
    out = defaultdict(list)
    for o in filled_orders:
        if isinstance(o, dict):
            kind, pnl, c = o.get("attr_desc"), o.get("realized_pnl"), o.get("contract") or {}
            if kind not in _EXPIRY_KINDS:
                continue
            key = (c.get("symbol"), c.get("expiry"), c.get("put_call"), float(c.get("strike")))
        else:
            kind = _field(o, "attr_desc")
            cm = re.search(r"'contract': (\w+) +(\d{6})([CP])(\d{8})/OPT", o)
            if kind not in _EXPIRY_KINDS or not cm:
                continue
            sym, ymd, cp, k = cm.groups()
            pnl = float(_field(o, "realized_pnl") or 0)
            key = (sym, "20" + ymd, "CALL" if cp == "C" else "PUT", int(k) / 1000.0)
        out[key].append(float(pnl))
    return out


def _key(c: dict):
    return (c["symbol"], c["expiry"], c["put_call"], float(c["strike"]))


def _strategy(legs: list[dict]) -> str:
    rights = {l["right"] for l in legs}
    if len(legs) >= 4 and rights == {"P", "C"}:
        return "iron_condor"
    if rights == {"P"}:
        return "bull_put_spread"
    if rights == {"C"}:
        return "bear_call_spread"
    return "other"


def build_journal(transactions: list, filled_orders: list, today: date | None = None) -> dict:
    today = today or datetime.now(ET).date()
    expiry_pnl = parse_expiry_records(filled_orders)
    fills = sorted(transactions, key=lambda r: r["transaction_time"])

    lots = defaultdict(deque)          # contract -> deque of open lots (FIFO)
    trades = {}                        # open order_id -> trade dict
    order_of = {}                      # ensures stable ordering

    def new_trade(oid, ts):
        t = {"order_id": oid, "open_ms": ts, "legs": {}, "close_ms": None}
        trades[oid] = t
        return t

    for r in fills:
        c = r["contract"]; k = _key(c)
        sgn = 1 if r["action"] == "BUY" else -1
        qty = int(r["filled_quantity"]); px = float(r["filled_price"]); ts = int(r["transaction_time"])
        q = lots[k]
        # CLOSE against opposite-side lots first (FIFO)
        while qty and q and q[0]["sgn"] == -sgn:
            lot = q[0]; take = min(qty, lot["qty"])
            # lot opened with sign lot.sgn: pnl = (close - open) * lot.sgn * 100 * take
            pnl = (px - lot["px"]) * lot["sgn"] * 100 * take
            lot["trade"]["legs"][lot["leg"]]["pnl"] += pnl
            lot["trade"]["legs"][lot["leg"]]["closed_qty"] += take
            lot["trade"]["legs"][lot["leg"]]["close_ms"] = ts
            lot["qty"] -= take; qty -= take
            if lot["qty"] == 0:
                q.popleft()
        if qty:                         # OPEN remainder
            oid = r["order_id"]
            t = trades.get(oid) or new_trade(oid, ts)
            leg_id = (k, sgn)
            leg = t["legs"].setdefault(leg_id, {
                "key": k, "right": "P" if k[2] == "PUT" else "C", "strike": k[3], "side": "LONG" if sgn > 0 else "SHORT",
                "qty": 0, "open_px_sum": 0.0, "pnl": 0.0, "closed_qty": 0, "close_ms": None, "expired": False})
            leg["qty"] += qty; leg["open_px_sum"] += px * qty
            q.append({"sgn": sgn, "qty": qty, "px": px, "trade": t, "leg": leg_id})

    # Close expired remainders with Tiger's own expiry/assignment figure
    for k, q in lots.items():
        if not q:
            continue
        exp_date = datetime.strptime(k[1], "%Y%m%d").date()
        recs = expiry_pnl.get(k)
        if exp_date >= today or not recs:
            continue                    # still open, or no record -> unresolved
        total_open = sum(l["qty"] for l in q)
        total_pnl = sum(recs)
        exp_ms = int(datetime(exp_date.year, exp_date.month, exp_date.day, 16, 0, tzinfo=ET).timestamp() * 1000)
        for lot in q:
            share = total_pnl * lot["qty"] / total_open
            leg = lot["trade"]["legs"][lot["leg"]]
            leg["pnl"] += share; leg["closed_qty"] += lot["qty"]; leg["close_ms"] = exp_ms; leg["expired"] = True
            lot["qty"] = 0
        q.clear()

    closed, unresolved, open_trades = [], [], []
    for t in sorted(trades.values(), key=lambda t: t["open_ms"]):
        legs = list(t["legs"].values())
        all_closed = all(l["closed_qty"] >= l["qty"] for l in legs)
        # legs have the same qty per order; contracts = qty of the first leg
        contracts = legs[0]["qty"]
        rows = [{"right": l["right"], "strike": l["strike"], "side": l["side"], "qty": l["qty"],
                 "price": round(l["open_px_sum"] / l["qty"], 4)} for l in sorted(legs, key=lambda l: (l["right"], l["strike"]))]
        credit = sum((-1 if l["side"] == "LONG" else 1) * l["open_px_sum"] for l in legs) * 100 / max(contracts, 1) / 100
        opened = datetime.fromtimestamp(t["open_ms"] / 1000, ET)
        base = {
            "order_id": t["order_id"], "entry_time": opened.strftime("%Y-%m-%d %H:%M:%S"),
            "entry_date": opened.strftime("%Y-%m-%d"), "expiration": datetime.strptime(legs[0]["key"][1], "%Y%m%d").strftime("%Y-%m-%d"),
            "underlying": legs[0]["key"][0], "strategy": _strategy(legs), "contracts": contracts,
            "legs": rows, "entry_credit": round(credit, 2), "strikes": sorted({l["strike"] for l in legs}),
        }
        if all_closed:
            close_ms = max(l["close_ms"] for l in legs)
            closed_at = datetime.fromtimestamp(close_ms / 1000, ET)
            base.update({
                "exit_time": closed_at.strftime("%Y-%m-%d %H:%M:%S"), "exit_date": closed_at.strftime("%Y-%m-%d"),
                "hold_minutes": round((close_ms - t["open_ms"]) / 60000, 1),
                "pnl": round(sum(l["pnl"] for l in legs), 2), "fee": None,
                "held_to_expiry": any(l["expired"] for l in legs),
            })
            closed.append(base)
        else:
            exp = datetime.strptime(legs[0]["key"][1], "%Y%m%d").date()
            (open_trades if exp >= today else unresolved).append(base)
    return {"closed_trades": closed, "open_trades": open_trades, "unresolved": unresolved}


def load_journal(path: Path = DUMP_PATH) -> dict:
    if not Path(path).exists():
        raise FileNotFoundError(f"{path} missing -- run `python dump_tiger_raw.py` first")
    d = json.loads(Path(path).read_text())
    j = build_journal(d["transactions"], d["filled_orders"])
    j["generated_from"] = d.get("generated_at")
    return j


if __name__ == "__main__":
    j = load_journal()
    c = j["closed_trades"]
    print(f"closed {len(c)}  open {len(j['open_trades'])}  unresolved {len(j['unresolved'])}")
    print(f"total P&L {sum(t['pnl'] for t in c):,.2f}")
    for t in c[-5:]:
        print(t["entry_time"], t["strategy"], t["strikes"], t["contracts"], t["pnl"], t["hold_minutes"])
