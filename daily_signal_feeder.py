"""
Logs today's PRIME/VALID/WATCH signal to the signal_log table, whether or
not you actually trade it. Meant to run once per trading day (cron/
scheduled task) so a track record accumulates for strategy_logic/
signal_scorer.py to eventually be judged against real outcomes -- see
that module's docstring for exactly what's automated vs. this module's
own heuristic defaults.

Data sources (in priority order, each with a documented fallback):
  - Spot/VWAP: real moomoo intraday snapshot if reachable, else a
    synthetic proxy from the daily close (see _get_market_snapshot).
  - GEX walls: real moomoo chain (via export_results.py's same
    _export_gex_real path) if reachable, else strategy_logic.gex_walls's
    proxy-OI estimate.
  - VIX: real via yfinance if reachable, else omitted (gate.py's
    vol-regime check is skipped, not faked).
  - Event calendar: strategy_logic/event_calendar.py's maintained static
    calendar (see that module for update instructions).

Usage:
    python daily_signal_feeder.py                  # today
    python daily_signal_feeder.py --date 2026-08-27  # backfill a specific day (best-effort;
                                                        # intraday VWAP for a past day is NOT
                                                        # reconstructable, see caveat printed)
"""
from __future__ import annotations
import json
import sqlite3
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta

import config
from strategy_logic.signal_scorer import score_signal
from strategy_logic.event_calendar import load_calendar
from pricing.black_scholes import build_synthetic_chain
from strategy_logic.gex_walls import compute_gex_by_strike, find_walls, estimate_oi_proxy


def _get_market_snapshot(trade_date: date):
    """
    Returns (today_open, prior_close, spot, vwap, put_wall, call_wall, vix,
    recent_closes, chain, data_source_note). Tries real moomoo/yfinance
    sources first; falls back to the synthetic sample series so the feeder
    never hard-crashes for lack of a live connection -- but the note makes
    clear when that happened, and callers should treat a proxy-sourced row
    with appropriate skepticism (it's logging what the ENGINE would have
    said on synthetic inputs, not a real market read).
    """
    notes = []
    vix = None
    try:
        from run_backtest import load_vix_series_from_yfinance
        vix_series = load_vix_series_from_yfinance((trade_date - timedelta(days=5)).isoformat(),
                                                     (trade_date + timedelta(days=1)).isoformat())
        matching = [v for d, v in vix_series if d == trade_date]
        if matching:
            vix = matching[0]
    except Exception as e:
        notes.append(f"VIX unavailable ({e})")

    try:
        from collectors.moomoo_daily_collector import (
            fetch_option_expirations, pick_expiration_near_dte, fetch_option_chain,
            chain_df_to_quotes, chain_df_to_gex_contracts, estimate_spot_from_chain,
        )
        from strategy_logic.gex_walls import compute_gex_from_contracts

        expirations = fetch_option_expirations("SPX")
        expiry = pick_expiration_near_dte(expirations, target_dte=0)
        chain_df = fetch_option_chain("SPX", expiry)
        if chain_df.empty:
            raise RuntimeError("empty chain from moomoo")
        quotes = chain_df_to_quotes(chain_df)
        spot = estimate_spot_from_chain(quotes)
        contracts = chain_df_to_gex_contracts(chain_df)
        rows = compute_gex_from_contracts(spot, contracts)
        walls = find_walls(rows, spot)
        put_wall, call_wall = walls.put_wall, walls.call_wall
        chain = quotes
        notes.append("real moomoo chain")
    except Exception as e:
        notes.append(f"real chain unavailable ({e}), using proxy OI")
        spot = None
        put_wall = call_wall = None
        chain = None

    from run_backtest import load_price_series
    price_series = load_price_series("data/sample/spx_proxy_sample.csv")
    closes = [p for _, p in price_series]
    if spot is None:
        spot = closes[-1]
    prior_close = closes[-2] if len(closes) >= 2 else closes[-1]
    today_open = spot  # no real intraday open feed wired up yet -- see README caveat
    vwap = spot  # no real intraday VWAP feed wired up yet -- see README caveat
    notes.append("today_open/vwap proxied as spot (no intraday tick feed wired up -- see README)")

    if chain is None:
        strikes = [spot - spot % config.STRIKE_INCREMENT + config.STRIKE_INCREMENT * i for i in range(-30, 31)]
        call_oi, put_oi = estimate_oi_proxy(strikes, spot)
        rows = compute_gex_by_strike(spot, 1 / 365, config.RISK_FREE_RATE, config.DIVIDEND_YIELD,
                                      0.13, strikes, call_oi, put_oi)
        walls = find_walls(rows, spot)
        put_wall, call_wall = walls.put_wall, walls.call_wall
        chain = build_synthetic_chain(spot, 1 / 365, config.RISK_FREE_RATE, config.DIVIDEND_YIELD, 0.13,
                                       config.STRIKE_INCREMENT)

    recent_closes = closes[-10:]
    return today_open, prior_close, spot, vwap, put_wall, call_wall, vix, recent_closes, chain, "; ".join(notes)


def log_signal(trade_date: date, db_path: str = config.DB_PATH) -> None:
    (today_open, prior_close, spot, vwap, put_wall, call_wall, vix,
     recent_closes, chain, source_note) = _get_market_snapshot(trade_date)
    calendar = load_calendar()

    result = score_signal(
        trade_date=trade_date, today_open=today_open, prior_close=prior_close, spot=spot,
        vwap=vwap, recent_closes=recent_closes, chain=chain, put_wall=put_wall,
        call_wall=call_wall, calendar=calendar, vix=vix,
    )

    expected_move = spot * (vix / 100.0) * (1 / 365) ** 0.5 if vix is not None else None

    checklist_dict = asdict(result.checklist)
    reasons = result.reasons + [f"data source: {source_note}"]

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT OR REPLACE INTO signal_log
            (trade_date, direction, tier, strategy, short_strike, long_strike, credit, max_loss,
             width, confirmed_count, assessed_count, checklist_json, reasons_json, spot, vwap, vix,
             created_at, expiry_date, expected_move_1sigma)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade_date.isoformat(), result.direction.signal.value, result.tier.value,
            result.candidate and _strategy_name(result), result.candidate and result.candidate.short_strike,
            result.candidate and result.candidate.long_strike,
            # NOTE: candidate.credit is the raw PER-SHARE credit (e.g. 2.11); candidate.max_profit
            # is that same credit in DOLLAR terms (x100), matching max_loss's units -- store the
            # dollar figure here so credit and max_loss are directly comparable/addable downstream.
            result.candidate and result.candidate.max_profit,
            result.candidate and result.candidate.max_loss, result.candidate and result.candidate.width,
            result.confirmed_count, result.assessed_count, json.dumps(checklist_dict), json.dumps(reasons),
            spot, vwap, vix, datetime.now().isoformat(timespec="seconds"), trade_date.isoformat(), expected_move,
        ),
    )
    conn.commit()
    conn.close()
    print(f"Logged signal for {trade_date}: {result.tier.value} ({result.direction.signal.value}), "
          f"{result.confirmed_count}/{result.assessed_count} confirmed. [{source_note}]")


def _strategy_name(result) -> str:
    return "bull_put_spread" if result.direction.signal.value == "BULLISH_CONFIRMED" else "bear_call_spread"


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None, help="YYYY-MM-DD, defaults to today")
    args = p.parse_args()
    trade_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    log_signal(trade_date)
