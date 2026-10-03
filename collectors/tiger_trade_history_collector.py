"""
Pulls CLOSED trade history from your real Tiger account and loads it into
the same `trades` / `trade_legs` schema the xlsx loader and synthetic
backtest engine use (source='live:tiger') -- so the journal, replay, and
metrics tooling all work on real fills without a manual export step.

Read-only. Never places, modifies, or cancels orders.

CREDENTIALS -- loaded from a local .env file, never typed into chat or
committed to git. Two supported formats, matching Tiger's own two
config methods (verified against
https://quant.itigerup.com/openapi/en/python/quickStart/prepare.html,
Sept 2026):

  Method 1 (preferred if you exported tiger_openapi_config.properties
  from the developer console -- https://quant.itigerup.com/#developer):
    1. Put tiger_openapi_config.properties in its own folder, e.g.
       ~/.tigeropen/tiger_openapi_config.properties
    2. Copy .env.example -> .env in the project root and set:
           TIGER_PROPS_PATH=/absolute/path/to/the/folder/containing/the/properties/file
       (the folder, not the file itself -- the SDK reads
       tiger_openapi_config.properties from inside it).

  Method 2 (explicit fields, if you only have the raw private key):
           TIGER_ID=...
           TIGER_ACCOUNT=...
           TIGER_PRIVATE_KEY_PATH=/absolute/path/to/your.pem

  .env is already in .gitignore -- confirm before committing anything.
  pip install tigeropen python-dotenv

API REFERENCE (verified against https://quant.itigerup.com/openapi/en/python/operation/trade/orderInfo.html,
Sept 2026):
  TradeClient.get_filled_orders(sec_type, start_time, end_time, seg_type, ...)
  returns Order objects. Key fields used here: id, order_time, trade_time,
  action, quantity, filled, avg_fill_price, commission, realized_pnl,
  combo_type, contract_legs (multi-leg combo orders -- e.g. a spread
  placed as ONE order), contract (single-leg orders), status.

  IMPORTANT CONSTRAINT: start_time/end_time window for get_filled_orders
  cannot exceed 90 days per call -- fetch_filled_orders() below paginates
  in 90-day chunks automatically.

GROUPING LOGIC:
  - If Tiger placed the spread as a combo order, `contract_legs` already
    lists every leg on one Order -- this is the clean, unambiguous path
    (see _trade_from_combo_order).
  - If legs were filled as separate single-leg orders (manual leg-by-leg
    entry), there's no reliable server-side link between them. We group
    same-underlying, same-expiration, opposite-side (one short one long)
    orders whose trade_time falls within GROUPING_WINDOW_SECONDS of each
    other. This is a HEURISTIC, not a guarantee -- any group that doesn't
    cleanly resolve to exactly one short + one long leg per right is left
    UNGROUPED and written with notes='UNGROUPED_LEG: needs manual review'
    rather than guessed at.
"""
from __future__ import annotations
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
import config

GROUPING_WINDOW_SECONDS = 5  # max gap between two single-leg fills to treat as one spread
MAX_WINDOW_DAYS = 90         # Tiger's hard limit per get_filled_orders call


def _load_env() -> dict:
    """Load TIGER_* settings from a local .env file. Never hardcode
    credentials here or pass them via chat -- see module docstring."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        raise RuntimeError("pip install python-dotenv") from None
    env_path = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(env_path)
    return {
        "props_path": os.environ.get("TIGER_PROPS_PATH"),
        "tiger_id": os.environ.get("TIGER_ID"),
        "account": os.environ.get("TIGER_ACCOUNT"),
        "key_path": os.environ.get("TIGER_PRIVATE_KEY_PATH"),
    }, env_path


def _make_trade_client():
    """Constructs a real tigeropen TradeClient from .env credentials.
    Isolated into its own function so the rest of this module (grouping,
    DB writes) can be unit-tested with fake Order objects, no live
    account or network access required.

    Supports both of Tiger's config methods -- see module docstring:
    TIGER_PROPS_PATH (a tiger_openapi_config.properties folder) is tried
    first; falls back to the explicit TIGER_ID/TIGER_ACCOUNT/
    TIGER_PRIVATE_KEY_PATH trio if that's not set.
    """
    from tigeropen.trade.trade_client import TradeClient
    from tigeropen.tiger_open_config import TigerOpenClientConfig, get_client_config

    env, env_path = _load_env()

    if env["props_path"]:
        client_config = TigerOpenClientConfig(props_path=env["props_path"])
        return TradeClient(client_config)

    if all([env["tiger_id"], env["account"], env["key_path"]]):
        client_config = get_client_config(
            private_key_path=env["key_path"], tiger_id=env["tiger_id"], account=env["account"]
        )
        return TradeClient(client_config)

    raise RuntimeError(
        f"Missing Tiger credentials in {env_path}. Set either TIGER_PROPS_PATH (pointing to the "
        "folder containing tiger_openapi_config.properties) or all three of TIGER_ID / "
        "TIGER_ACCOUNT / TIGER_PRIVATE_KEY_PATH. Copy .env.example to .env and fill in one of "
        "the two (see module docstring)."
    )


def fetch_filled_orders(trade_client, start: date, end: date, sec_type="OPT"):
    """
    Paginates get_filled_orders across the requested [start, end) range in
    <=90-day windows (Tiger's hard limit per call) and returns the
    concatenated list of Order objects, most-recent-first per Tiger's
    default ordering flattened back into a single chronological list.
    """
    from tigeropen.common.consts import SegmentType

    all_orders = []
    window_start = start
    while window_start < end:
        window_end = min(window_start + timedelta(days=MAX_WINDOW_DAYS), end)
        orders = trade_client.get_filled_orders(
            sec_type=sec_type,
            start_time=window_start.isoformat(),
            end_time=window_end.isoformat(),
            seg_type=SegmentType.SEC,
        )
        all_orders.extend(orders)
        window_start = window_end
    all_orders.sort(key=lambda o: o.trade_time or o.order_time or 0)
    return all_orders


@dataclass
class NormalizedLeg:
    right: str          # 'P' or 'C'
    strike: float
    side: str            # 'SHORT' or 'LONG'
    entry_price: float
    expiration: date


@dataclass
class NormalizedTrade:
    strategy: str          # inferred: bull_put_spread / bear_call_spread / iron_condor / unknown
    entry_date: date
    expiration_date: date
    entry_credit: float
    max_loss: float
    contracts: int
    underlying_entry: float
    commission: float
    legs: list[NormalizedLeg]
    source_order_ids: list
    notes: str = ""


def _leg_from_contract(contract, action: str, fill_price: float) -> NormalizedLeg | None:
    """contract is a tigeropen Contract object for an option leg. Returns
    None if it isn't an option (defensive -- fetch_filled_orders already
    filters sec_type='OPT', this is a second guard)."""
    put_call = getattr(contract, "put_call", None) or getattr(contract, "right", None)
    strike = getattr(contract, "strike", None)
    expiry = getattr(contract, "expiry", None)
    if put_call is None or strike is None:
        return None
    right = "P" if str(put_call).upper().startswith("P") else "C"
    side = "SHORT" if str(action).upper() == "SELL" else "LONG"
    exp_date = _parse_tiger_expiry(expiry) if expiry else None
    return NormalizedLeg(right=right, strike=float(strike), side=side,
                          entry_price=float(fill_price or 0.0), expiration=exp_date)


def _parse_tiger_expiry(expiry) -> date:
    """Tiger expiry strings are 'yyyyMMdd' in some endpoints, ISO elsewhere.
    Handle both defensively."""
    s = str(expiry)
    if "-" in s:
        return datetime.strptime(s, "%Y-%m-%d").date()
    return datetime.strptime(s, "%Y%m%d").date()


def _infer_strategy(legs: list[NormalizedLeg]) -> str:
    rights = {l.right for l in legs}
    if rights == {"P"}:
        return "bull_put_spread"
    if rights == {"C"}:
        return "bear_call_spread"
    if rights == {"P", "C"}:
        return "iron_condor"
    return "unknown"


def _credit_and_max_loss(legs: list[NormalizedLeg], contracts: int) -> tuple[float, float]:
    """Same math as strategies/base.py: credit = sum(short premiums) -
    sum(long premiums); max_loss = width*100 - credit, per right group."""
    credit = 0.0
    max_loss = 0.0
    for right in ("P", "C"):
        side_legs = [l for l in legs if l.right == right]
        if not side_legs:
            continue
        shorts = [l for l in side_legs if l.side == "SHORT"]
        longs = [l for l in side_legs if l.side == "LONG"]
        if not shorts or not longs:
            continue  # naked leg, not a vertical -- can't compute width-based max_loss
        short_prem = sum(l.entry_price for l in shorts)
        long_prem = sum(l.entry_price for l in longs)
        leg_credit = (short_prem - long_prem) * 100 * contracts
        width = abs(shorts[0].strike - longs[0].strike)
        leg_max_loss = max(width * 100 * contracts - leg_credit, 0.0)
        credit += leg_credit
        max_loss += leg_max_loss
    return round(credit, 2), round(max_loss, 2)


def trade_from_combo_order(order) -> NormalizedTrade | None:
    """Clean path: order.contract_legs already lists every leg of the
    spread on a single Order object (Tiger combo order)."""
    contract_legs = getattr(order, "contract_legs", None)
    if not contract_legs:
        return None

    legs = []
    for leg_contract in contract_legs:
        action = getattr(leg_contract, "action", None) or getattr(order, "action", "BUY")
        fill_price = getattr(leg_contract, "avg_fill_price", None) or order.avg_fill_price
        leg = _leg_from_contract(leg_contract, action, fill_price)
        if leg:
            legs.append(leg)
    if len(legs) < 2:
        return None

    contracts = int(order.filled or order.quantity or 1)
    credit, max_loss = _credit_and_max_loss(legs, contracts)
    entry_dt = datetime.fromtimestamp((order.trade_time or order.order_time) / 1000).date()
    exp_date = legs[0].expiration or entry_dt

    return NormalizedTrade(
        strategy=_infer_strategy(legs), entry_date=entry_dt, expiration_date=exp_date,
        entry_credit=credit, max_loss=max_loss, contracts=contracts,
        underlying_entry=0.0,  # Tiger order objects don't carry underlying spot at fill time
        commission=float(order.commission or 0.0), legs=legs,
        source_order_ids=[order.id], notes="from combo order",
    )


def group_single_leg_orders(orders: list) -> tuple[list[NormalizedTrade], list]:
    """
    Fallback path for accounts where each leg was filled as its own
    single-leg order. Groups by (underlying symbol, expiration, right)
    pairs whose trade_time is within GROUPING_WINDOW_SECONDS of each
    other AND resolve to exactly one SHORT + one LONG. Anything that
    doesn't resolve cleanly is returned in the second list (ungrouped),
    untouched, for manual review -- never silently guessed.
    """
    singles = []
    for o in orders:
        contract = getattr(o, "contract", None)
        if contract is None:
            continue
        leg = _leg_from_contract(contract, o.action, o.avg_fill_price)
        if leg is None:
            continue
        t = (o.trade_time or o.order_time)
        singles.append((o, leg, t))

    singles.sort(key=lambda x: x[2] or 0)
    used = set()
    trades = []
    ungrouped = []

    for i, (o1, leg1, t1) in enumerate(singles):
        if o1.id in used:
            continue
        match = None
        for o2, leg2, t2 in singles[i + 1:]:
            if o2.id in used:
                continue
            if t2 - t1 > GROUPING_WINDOW_SECONDS * 1000:
                break  # sorted by time -- no later candidate can be closer
            if leg2.right == leg1.right and leg2.expiration == leg1.expiration and leg2.side != leg1.side:
                match = (o2, leg2)
                break
        if match is None:
            ungrouped.append(o1)
            continue
        o2, leg2 = match
        used.add(o1.id)
        used.add(o2.id)
        legs = [leg1, leg2]
        contracts = int(o1.filled or o1.quantity or 1)
        credit, max_loss = _credit_and_max_loss(legs, contracts)
        entry_dt = datetime.fromtimestamp(t1 / 1000).date()
        trades.append(NormalizedTrade(
            strategy=_infer_strategy(legs), entry_date=entry_dt,
            expiration_date=leg1.expiration or entry_dt, entry_credit=credit,
            max_loss=max_loss, contracts=contracts, underlying_entry=0.0,
            commission=float((o1.commission or 0) + (o2.commission or 0)), legs=legs,
            source_order_ids=[o1.id, o2.id], notes="grouped from 2 single-leg orders (heuristic)",
        ))

    return trades, ungrouped


def normalize_orders(orders: list) -> tuple[list[NormalizedTrade], list]:
    """Splits orders into combo-order trades (clean) vs single-leg orders
    to run through the heuristic grouper, and returns (trades, ungrouped)."""
    combo_trades = []
    single_leg_orders = []
    for o in orders:
        t = trade_from_combo_order(o)
        if t:
            combo_trades.append(t)
        else:
            single_leg_orders.append(o)

    grouped_trades, ungrouped = group_single_leg_orders(single_leg_orders)
    return combo_trades + grouped_trades, ungrouped


def _existing_order_ids(db_path: str = config.DB_PATH, source: str = "live:tiger") -> set:
    """Every Tiger order id already written to `trades.source_order_ids` for this
    source -- the dedup key write_trades checks against so re-running a sync
    over an overlapping/full-history date range never inserts the same closed
    trade twice. Missing table/column (brand new DB, not yet migrated) is
    treated as "nothing synced yet" rather than an error."""
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT source_order_ids FROM trades WHERE source = ? AND source_order_ids IS NOT NULL", (source,)
        ).fetchall()
        conn.close()
    except sqlite3.OperationalError:
        return set()
    ids = set()
    for (raw,) in rows:
        try:
            ids.update(json.loads(raw))
        except (TypeError, ValueError):
            continue
    return ids


def write_trades(trades: list[NormalizedTrade], db_path: str = config.DB_PATH,
                  source: str = "live:tiger") -> tuple[int, int]:
    """Writes `trades`, skipping any whose source_order_ids fully overlap a
    trade already in the DB for this source (see _existing_order_ids) -- makes
    run_sync safe to call repeatedly, including the README's default
    full-history (2010-01-01 -> today) resync. Returns (written, skipped)."""
    already_synced = _existing_order_ids(db_path, source)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    written = 0
    skipped = 0
    for t in trades:
        if already_synced.intersection(t.source_order_ids):
            skipped += 1
            continue
        notes = t.notes + f"; commission=${t.commission:.2f}; order_ids={t.source_order_ids}"
        order_ids_json = json.dumps(t.source_order_ids)
        cur.execute(
            """
            INSERT INTO trades (strategy, entry_date, expiration_date, entry_credit, max_loss,
                                 contracts, underlying_entry, status, source, notes, source_order_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)
            """,
            (t.strategy, t.entry_date.isoformat(), t.expiration_date.isoformat(),
             t.entry_credit, t.max_loss, t.contracts, t.underlying_entry, source, notes, order_ids_json),
        )
        trade_id = cur.lastrowid
        for leg in t.legs:
            cur.execute(
                "INSERT INTO trade_legs (trade_id, right, strike, side, entry_price) VALUES (?, ?, ?, ?, ?)",
                (trade_id, leg.right, leg.strike, leg.side, leg.entry_price),
            )
        already_synced.update(t.source_order_ids)  # guard within this same batch too (e.g. duplicate combo fills)
        written += 1
    conn.commit()
    conn.close()
    return written, skipped


def run_sync(start: date, end: date, db_path: str = config.DB_PATH) -> None:
    """Entry point: pull closed history for [start, end), group into
    spreads, write to DB. Safe to re-run over the same or an overlapping
    date range -- write_trades skips anything already synced by Tiger
    order id, so the README's default full-history resync never
    duplicates existing rows. Prints a summary including anything left
    ungrouped for manual review -- never silently drops fills."""
    client = _make_trade_client()
    orders = fetch_filled_orders(client, start, end)
    trades, ungrouped = normalize_orders(orders)
    written, skipped = write_trades(trades, db_path=db_path)
    print(f"Synced {written} new trade(s) from {len(orders)} filled order(s) into {db_path} "
          f"(source='live:tiger'){f' -- {skipped} already synced, skipped' if skipped else ''}.")
    if ungrouped:
        print(f"WARNING: {len(ungrouped)} single-leg order(s) could not be paired into a spread "
              f"(order ids: {[o.id for o in ungrouped]}). Not written -- review manually, they may be "
              f"naked legs, one side of a spread outside the {GROUPING_WINDOW_SECONDS}s grouping window, "
              f"or a strategy this platform doesn't model yet.")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Sync closed Tiger trade history into the local DB.")
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    args = p.parse_args()
    run_sync(datetime.strptime(args.start, "%Y-%m-%d").date(),
             datetime.strptime(args.end, "%Y-%m-%d").date())
