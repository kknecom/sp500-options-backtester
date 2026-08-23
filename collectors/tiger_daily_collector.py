"""
Forward daily data collector using the Tiger Open API (tigeropen SDK).

WHY THIS MODULE EXISTS
-----------------------
Tiger's historical options quota is small (10-200 unique OPTION SYMBOLS
total, refreshed on a 30-day rolling basis per symbol -- see
https://quant.itigerup.com/openapi/en/python/permission/historySubscribe.html).
That is nowhere near enough to backtest years of daily SPX chains. But it
IS enough to pull TODAY's relevant strikes every day going forward, e.g.
~10-30 contracts spanning the strikes this platform actually trades
(short/long legs for whichever DTE bucket is active). Run this once per
day (e.g. via cron / the Telegram bot's scheduler in phase 2) and the
`option_daily_bar` table becomes a real, growing, non-synthetic dataset
over months/years -- which is far more valuable for these strategies
than a short backtest on synthetic prices.

SETUP (not wired up here -- fill in with your own credentials):
    pip install tigeropen
    from tigeropen.tiger_open_config import TigerOpenClientConfig
    from tigeropen.quote.quote_client import QuoteClient

    client_config = TigerOpenClientConfig(sandbox_debug=False)
    client_config.private_key = "<your private key>"
    client_config.tiger_id = "<your tiger id>"
    client_config.account = "<your account>"
    quote_client = QuoteClient(client_config)

This module is a STUB: it defines the collection logic and DB writes,
with a `fetch_option_day_bars` function you plug the real SDK calls into
once credentials are available. It runs today against nothing (returns
an empty list) so the rest of the platform (backtest engine, DB schema)
can be developed and tested independently of having Tiger credentials on
hand right now.
"""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))
import config


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


def fetch_option_day_bars(underlying: str, expirations: list[date], strikes: list[float]) -> list[OptionBar]:
    """
    Plug in the real Tiger SDK call here, e.g. quote_client.get_option_briefs
    or the day-bar endpoint described in the Options table of the Tiger
    docs (fields: time, open/high/low/close, volume, preclose, openInterest).

    Keep the requested symbol count per day within your account's quota
    tier (see module docstring) -- e.g. only pull the strikes actually
    used by open trades plus a small watchlist around the current ATM
    strike for the active DTE bucket, not a full chain.
    """
    raise NotImplementedError(
        "Wire up tigeropen QuoteClient calls here once API credentials are configured. "
        "See the module docstring for the setup snippet."
    )


def write_option_bars(bars: list[OptionBar], db_path: str = config.DB_PATH) -> int:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    n = 0
    for b in bars:
        cur.execute(
            """
            INSERT OR REPLACE INTO option_daily_bar
                (trade_date, underlying, expiration, strike, right, open, high, low, close,
                 volume, open_interest, iv, delta, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'tiger_api')
            """,
            (
                b.trade_date.isoformat(), b.underlying, b.expiration.isoformat(), b.strike, b.right,
                b.open, b.high, b.low, b.close, b.volume, b.open_interest, b.iv, b.delta,
            ),
        )
        n += 1
    conn.commit()
    conn.close()
    return n


def run_daily_collection(underlying: str = "SPX"):
    """
    Entry point intended to be run once per trading day (cron, or later
    triggered by the Telegram bot). Determine which strikes/expirations
    matter today (e.g. from any OPEN trades in the `trades` table, plus a
    small ATM watchlist), fetch them, and persist.
    """
    print(f"[{datetime.now().isoformat()}] Daily collection stub for {underlying} -- "
          f"not yet wired to live Tiger credentials. See module docstring.")
    # Example of the intended flow once fetch_option_day_bars is implemented:
    # bars = fetch_option_day_bars(underlying, expirations=[...], strikes=[...])
    # count = write_option_bars(bars)
    # print(f"Wrote {count} option day-bars.")


if __name__ == "__main__":
    run_daily_collection()
