"""
Forward daily data collector using the moomoo OpenAPI (moomoo-api SDK,
via a local OpenD gateway -- see moomoo_client.py).

WHY THIS MODULE EXISTS
-----------------------
Mirrors collectors/tiger_daily_collector.py's role but against moomoo,
which (unlike this Tiger account) has live US options quote permission
with no funding/entitlement purchase needed -- confirmed 2026-08-31 by
pulling a full 45-DTE SPX chain (1724 contracts) with real bid/ask and
greeks via get_market_snapshot.

Historical option klines (fetch_option_day_bars) draw against a single
combined history quota shared across all symbols -- observed as
(1 used, 299 remaining) after one call, i.e. ~300 total, refreshed on
some rolling basis (exact refresh period not confirmed live). That's
the same "only pull what's actually held" discipline as the Tiger
collector: keep fetch_option_day_bars calls to the 2-4 legs of open
trades, not full chains.

VERIFY BEFORE TRUSTING OUTPUT
------------------------------
fetch_option_expirations/fetch_option_chain were confirmed against a
live OpenD session (moomoo-api 10.10.7008) -- see conversation history.
fetch_option_day_bars' OHLCV columns (request_history_kline) were also
confirmed live, but request_history_kline does NOT return iv/delta
(those only come from get_market_snapshot, i.e. current quotes, not
history) -- OptionBar.iv/delta will be None for anything pulled through
this path. _parse_option_code's format (e.g. 'US.SPX261016C200000') was
reverse-engineered from two live examples (SPX, AAPL), not from
official docs -- if moomoo ever changes their option code format this
will silently mis-parse, so spot-check parsed underlying/expiration/
strike/right against the 'code'/'name' columns if results look off.

SETUP
-----
OpenD must already be running and logged in -- see moomoo_client.py.
Nothing here reads or logs raw moomoo login credentials (OpenD handles
that entirely outside this process).
"""
from __future__ import annotations
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))
import config
import moomoo as ft
from moomoo_client import get_quote_context

# Indices use moomoo's MARKET..CODE form (double dot), unlike stock-rooted
# options ('US.AAPL'). Extend this map if other underlyings are added.
# NOTE: SPX/VIX resolve fine for get_option_chain/get_option_expiration_date
# (confirmed live), but request_history_kline rejects both with "US stock
# indices are not supported" -- see fetch_underlying_bars. SPY is mapped
# here as the practical historical-close proxy (same one the bundled
# sample CSV already uses, per run_backtest.py's docstring).
UNDERLYING_CODE_MAP = {
    "SPX": "US..SPX",
    "VIX": "US..VIX",
    "SPY": "US.SPY",
}

# Reverse-engineered from live examples: 'US.SPX261016C200000' ->
# underlying=SPX, expiration=2026-10-16, right=C, strike=200.0. See
# module docstring -- not from official docs, spot-check if this ever
# looks wrong.
_OPTION_CODE_RE = re.compile(r"^US\.([A-Z]+)(\d{2})(\d{2})(\d{2})([CP])(\d+)$")


@dataclass
class OptionBar:
    trade_date: date
    underlying: str
    expiration: date
    strike: float
    right: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None
    open_interest: int | None
    iv: float | None
    delta: float | None
    bid: float | None = None
    ask: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None


def _underlying_code(underlying: str) -> str:
    return UNDERLYING_CODE_MAP.get(underlying, underlying)


def _parse_option_code(code: str) -> tuple[str, date, float, str] | None:
    """(underlying, expiration, strike, right) from a moomoo option code, or None if unrecognized."""
    m = _OPTION_CODE_RE.match(code)
    if not m:
        return None
    underlying, yy, mm, dd, right, strike_str = m.groups()
    expiration = date(2000 + int(yy), int(mm), int(dd))
    strike = int(strike_str) / 1000.0
    return underlying, expiration, strike, right


def fetch_option_expirations(underlying: str = "SPX") -> list[date]:
    """Available option expiration dates for `underlying`, via get_option_expiration_date. Sorted."""
    with get_quote_context() as ctx:
        ret, data = ctx.get_option_expiration_date(code=_underlying_code(underlying))
        if ret != ft.RET_OK:
            raise RuntimeError(f"get_option_expiration_date failed: {data}")
    return sorted(datetime.strptime(d, "%Y-%m-%d").date() for d in data["strike_time"])


def pick_expiration_near_dte(expirations: list[date], target_dte: int = None, as_of: date = None) -> date:
    """
    Nearest available expiration to config.TARGET_DTE calendar days out
    from `as_of` (default today). Pure date math, no API call.
    """
    target_dte = target_dte if target_dte is not None else config.TARGET_DTE
    as_of = as_of or date.today()
    target_date = as_of + timedelta(days=target_dte)
    return min(expirations, key=lambda d: abs((d - target_date).days))


def _safe_float(val, default: float = 0.0) -> float:
    """
    float(val), but treats None AND NaN as `default` -- plain `float(x or
    default)` does NOT catch NaN (NaN is truthy in Python: `float('nan')
    or 0.0` returns nan, not 0.0). Real chain rows for deep/illiquid
    strikes commonly come back with NaN greeks/OI from moomoo, and that
    NaN silently propagating into GEX math produced a phantom gamma-flip
    strike far from spot -- see conversation. Use this everywhere a
    chain value is converted to float.
    """
    try:
        f = float(val)
    except (TypeError, ValueError):
        return default
    return default if f != f else f  # f != f is True only for NaN


def fetch_spot(underlying: str = "SPX") -> float:
    """
    Current underlying price via get_market_snapshot on the index/stock
    code itself (not an option code), e.g. 'US..SPX'. Used as the real
    spot input for GEX/wall/strike-selection calculations instead of a
    hand-typed number.
    """
    with get_quote_context() as ctx:
        ret, snap = ctx.get_market_snapshot([_underlying_code(underlying)])
        if ret != ft.RET_OK:
            raise RuntimeError(f"get_market_snapshot failed for {underlying}: {snap}")
    return float(snap.iloc[0]["last_price"])


def fetch_option_chain(underlying: str, expiry: date, snapshot_batch_size: int = 200):
    """
    Full option chain (both rights, all strikes) for one expiration, with
    live greeks/OI merged in from get_market_snapshot. Returns a
    DataFrame -- use this at entry time to pick strikes by delta against
    REAL quotes/OI. Read the 'code' column off the row you select and use
    that as the identifier for later fetch_option_day_bars calls (don't
    hand-build option code strings).

    get_market_snapshot is called in batches of `snapshot_batch_size`
    codes (a full SPX chain can be 1700+ contracts) -- the actual
    per-call cap wasn't confirmed live, so this chunks defensively.
    """
    expiry_str = expiry.strftime("%Y-%m-%d")
    with get_quote_context() as ctx:
        ret, chain = ctx.get_option_chain(code=_underlying_code(underlying), start=expiry_str, end=expiry_str)
        if ret != ft.RET_OK:
            raise RuntimeError(f"get_option_chain failed: {chain}")
        if chain.empty:
            return chain

        codes = chain["code"].tolist()
        snapshots = []
        for i in range(0, len(codes), snapshot_batch_size):
            batch = codes[i:i + snapshot_batch_size]
            ret2, snap = ctx.get_market_snapshot(batch)
            if ret2 != ft.RET_OK:
                raise RuntimeError(f"get_market_snapshot failed: {snap}")
            snapshots.append(snap)

    import pandas as pd
    snap_all = pd.concat(snapshots, ignore_index=True)
    snap_cols = [
        "code", "last_price", "bid_price", "ask_price",
        "option_open_interest", "option_implied_volatility",
        "option_delta", "option_gamma", "option_vega", "option_theta", "option_rho",
    ]
    return chain.merge(snap_all[snap_cols], on="code", how="left")


def fetch_option_day_bars(codes: list[str], start: str = None, end: str = None) -> list[OptionBar]:
    """
    Daily OHLCV bars for specific option codes (e.g. the 2-4 legs of a
    currently open trade), via request_history_kline -- one call per
    code (moomoo's historical kline API is single-symbol per call).
    iv/delta are always None here -- history kline doesn't carry them,
    only current quotes do (see fetch_option_chain). Keep `codes` to only
    what's actually held plus a small ATM watchlist -- see module
    docstring on the shared ~300 history quota.
    """
    if not codes:
        return []
    bars: list[OptionBar] = []
    with get_quote_context() as ctx:
        for code in codes:
            parsed = _parse_option_code(code)
            if parsed is None:
                continue
            underlying, expiration, strike, right = parsed
            ret, data, _ = ctx.request_history_kline(code, start=start, end=end, ktype=ft.KLType.K_DAY)
            if ret != ft.RET_OK:
                raise RuntimeError(f"request_history_kline failed for {code}: {data}")
            for _, row in data.iterrows():
                bars.append(OptionBar(
                    trade_date=datetime.strptime(str(row["time_key"])[:10], "%Y-%m-%d").date(),
                    underlying=underlying, expiration=expiration, strike=strike, right=right,
                    open=row.get("open"), high=row.get("high"), low=row.get("low"), close=row.get("close"),
                    volume=row.get("volume"), open_interest=None, iv=None, delta=None,
                ))
    return bars


def fetch_underlying_bars(symbol: str = "SPY", start: str = None, end: str = None):
    """
    Daily underlying bars via request_history_kline. Counts against the
    same shared history quota as option bars (unconfirmed whether moomoo
    splits quota by asset type the way Tiger's docstring claims Tiger
    does) -- don't assume this is free.

    IMPORTANT: 'SPX' and 'VIX' raise here -- confirmed live, moomoo's
    request_history_kline rejects US indices outright ("US stock indices
    are not supported"), even though they work fine for
    fetch_option_expirations/fetch_option_chain. Use 'SPY' (the default)
    as the historical-close proxy instead -- consistent with the
    project's existing sample CSV, which is also SPY-derived per
    run_backtest.py's docstring.

    IMPORTANT #2: passing end=None does NOT mean "through today" --
    confirmed live, moomoo silently caps it at start + 1 year (e.g.
    start='2023-09-01', end=None returned only 251 rows through
    2024-08-30, while passing an explicit end=<today> returned the full
    751 rows through today). This function defaults end to today itself
    to avoid that footgun; pass an explicit end if you want otherwise.
    """
    if end is None:
        end = date.today().isoformat()
    with get_quote_context() as ctx:
        ret, data, _ = ctx.request_history_kline(
            _underlying_code(symbol), start=start, end=end, ktype=ft.KLType.K_DAY,
        )
        if ret != ft.RET_OK:
            raise RuntimeError(f"request_history_kline failed for {symbol}: {data}")
        return data


def estimate_spot_from_chain(quotes: list) -> float:
    """
    Back out the underlying spot from put-call parity on the chain
    itself, instead of a separate get_market_snapshot call.

    CONFIRMED LIVE (2026-09-07): get_market_snapshot rejects US index
    codes outright ("US stock indices are not supported") -- the same
    restriction request_history_kline has (see fetch_underlying_bars),
    even though get_option_chain/get_option_expiration_date accept
    'US..SPX' fine. So for SPX there is no direct snapshot spot to pull;
    parity gives an exact-enough answer for free using data we already
    fetched for this expiration.

    Put-call parity (ignoring the small dividend/discount term, which is
    negligible for short-dated SPX): S ~= K + call_mid - put_mid for any
    strike. Computes that per strike that has both a call and a put
    quote and returns the median across strikes -- median is robust to
    the wide bid/ask spreads on deep OTM strikes that would otherwise
    skew a mean.

    `quotes` is a list[OptionQuote] for ONE expiration, both rights --
    e.g. straight from chain_df_to_quotes().
    """
    import statistics

    by_strike: dict[float, dict] = {}
    for q in quotes:
        by_strike.setdefault(q.strike, {})[q.right] = q.price
    estimates = [
        K + sides["C"] - sides["P"]
        for K, sides in by_strike.items()
        if "C" in sides and "P" in sides
    ]
    if not estimates:
        raise RuntimeError(
            "Could not estimate spot from chain -- no strike had both a call and put quote."
        )
    return statistics.median(estimates)


def chain_df_to_quotes(chain_df) -> list:
    """
    Convert a real moomoo chain (from fetch_option_chain -- already merged
    with get_market_snapshot greeks/quotes) into the list[OptionQuote]
    shape strategy_logic/strike_selector.py expects, so real strikes get
    picked by real delta/price instead of the synthetic BSM chain.

    Strike/right are re-derived from each row's 'code' via
    _parse_option_code (confirmed-working parsing, see module docstring)
    rather than trusted from get_option_chain's own strike/type columns,
    since those haven't been individually spot-checked.

    Price = mid of bid/ask; falls back to last_price when bid/ask are
    missing or non-positive (illiquid deep-OTM strikes commonly quote
    zero bid).
    """
    from pricing.black_scholes import OptionQuote

    quotes = []
    for _, row in chain_df.iterrows():
        parsed = _parse_option_code(str(row["code"]))
        if parsed is None:
            continue
        _underlying, _expiration, strike, right = parsed
        bid, ask = row.get("bid_price"), row.get("ask_price")
        if bid and ask and bid > 0 and ask > 0:
            price = (float(bid) + float(ask)) / 2.0
        else:
            price = _safe_float(row.get("last_price"))
        quotes.append(OptionQuote(
            strike=strike, right=right, price=price,
            delta=_safe_float(row.get("option_delta")),
            gamma=_safe_float(row.get("option_gamma")),
            theta=_safe_float(row.get("option_theta")),
            vega=_safe_float(row.get("option_vega")),
        ))
    return quotes


def chain_df_to_gex_contracts(chain_df) -> list[dict]:
    """
    Convert a real moomoo chain into the {strike, right, oi, gamma}
    contract list strategy_logic.gex_walls.compute_gex_from_contracts
    expects -- real open interest and real gamma, no BSM re-derivation
    and no estimate_oi_proxy() involved.
    """
    contracts = []
    for _, row in chain_df.iterrows():
        parsed = _parse_option_code(str(row["code"]))
        if parsed is None:
            continue
        _underlying, _expiration, strike, right = parsed
        contracts.append({
            "strike": strike, "right": right,
            "oi": _safe_float(row.get("option_open_interest")),
            "gamma": _safe_float(row.get("option_gamma")),
        })
    return contracts


def write_option_bars(bars: list[OptionBar], db_path: str = config.DB_PATH) -> int:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    n = 0
    for b in bars:
        cur.execute(
            """
            INSERT OR REPLACE INTO option_daily_bar
                (trade_date, underlying, expiration, strike, right, open, high, low, close,
                 volume, open_interest, iv, delta, bid, ask, gamma, theta, vega, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'moomoo_api')
            """,
            (
                b.trade_date.isoformat(), b.underlying,
                b.expiration.isoformat() if b.expiration else None, b.strike, b.right,
                b.open, b.high, b.low, b.close, b.volume, b.open_interest, b.iv, b.delta,
                b.bid, b.ask, b.gamma, b.theta, b.vega,
            ),
        )
        n += 1
    conn.commit()
    conn.close()
    return n


def write_underlying_bar(trade_date: date, symbol: str, close: float,
                          db_path: str = config.DB_PATH) -> None:
    """
    One row in underlying_daily for `symbol` on `trade_date` -- e.g. the
    real SPX spot (via estimate_spot_from_chain) captured alongside a
    daily chain snapshot. Only `close` is populated (a chain snapshot
    doesn't give a real day's OHLC/volume); INSERT OR REPLACE so re-running
    collect_daily_snapshot.py the same day overwrites rather than errors.
    """
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO underlying_daily (trade_date, symbol, close) VALUES (?, ?, ?)",
        (trade_date.isoformat(), symbol, close),
    )
    conn.commit()
    conn.close()


def fetch_daily_chain_snapshot(underlying: str = "SPX", target_dte: int | None = None) -> tuple[float, list[OptionBar]]:
    """
    Full real chain snapshot for one expiration (nearest to target_dte,
    default config.TARGET_DTE) via get_option_chain + get_market_snapshot
    -- real bid/ask/OI/greeks for every strike, not just the 2-4 legs of
    an open trade. Unlike fetch_option_day_bars (historical klines), this
    is NOT quota-limited the way that endpoint is -- confirmed live
    pulling a full 1700+ contract SPX chain with real bid/ask and greeks,
    no funding/entitlement purchase needed (see module docstring).

    Returns (spot, bars) -- spot from estimate_spot_from_chain (put-call
    parity on this same chain; get_market_snapshot rejects index codes
    directly, see that function's docstring), bars as one OptionBar per
    strike/right with today's date, ready for write_option_bars().

    This is the function collect_daily_snapshot.py calls once per day to
    slowly build a REAL (non-synthetic) options dataset over time -- see
    that script's docstring for how to schedule it.
    """
    expirations = fetch_option_expirations(underlying)
    target = pick_expiration_near_dte(expirations, target_dte=target_dte)
    chain_df = fetch_option_chain(underlying, target)
    if chain_df.empty:
        raise RuntimeError(f"empty chain for {underlying} expiry {target}")

    quotes = chain_df_to_quotes(chain_df)
    spot = estimate_spot_from_chain(quotes)

    today = date.today()
    bars = []
    for _, row in chain_df.iterrows():
        parsed = _parse_option_code(str(row["code"]))
        if parsed is None:
            continue
        _underlying, expiration, strike, right = parsed
        bid, ask = row.get("bid_price"), row.get("ask_price")
        if bid and ask and bid > 0 and ask > 0:
            close = (float(bid) + float(ask)) / 2.0
        else:
            close = _safe_float(row.get("last_price"))
        bars.append(OptionBar(
            trade_date=today, underlying=underlying, expiration=expiration,
            strike=strike, right=right,
            open=None, high=None, low=None, close=close, volume=None,
            open_interest=int(_safe_float(row.get("option_open_interest"))) or None,
            iv=_safe_float(row.get("option_implied_volatility")) or None,
            delta=_safe_float(row.get("option_delta")) or None,
            bid=_safe_float(bid) or None, ask=_safe_float(ask) or None,
            gamma=_safe_float(row.get("option_gamma")) or None,
            theta=_safe_float(row.get("option_theta")) or None,
            vega=_safe_float(row.get("option_vega")) or None,
        ))
    return spot, bars


def _open_trade_identifiers(db_path: str = config.DB_PATH) -> list[str]:
    """
    Codes for currently OPEN real trades' legs, so daily collection only
    pulls what's actually held. Requires trade_legs to have been recorded
    with moomoo's own option code at entry time (via fetch_option_chain's
    'code' column) -- the current db/schema.sql doesn't have that column
    yet, so this returns [] until that's added.
    """
    return []


def run_daily_collection(underlying: str = "SPX"):
    """Entry point intended to run once per trading day (cron, or later the Telegram bot's scheduler)."""
    codes = _open_trade_identifiers()
    if not codes:
        print(f"[{datetime.now().isoformat()}] No open real trades with recorded "
              f"codes yet -- nothing to collect. Record each leg's chain 'code' "
              f"at entry time before this can pull anything.")
        return
    bars = fetch_option_day_bars(codes)
    count = write_option_bars(bars)
    print(f"[{datetime.now().isoformat()}] Wrote {count} option day-bars for {len(codes)} codes.")


if __name__ == "__main__":
    run_daily_collection()
