"""
Export backtest + real-trade results to JSON for the static dashboard in
worker/public/. Reuses the exact same engine/metrics code as run_backtest.py
and run_real_analysis.py -- no separate calculation path.

Usage:
    python export_results.py
    (writes worker/public/data/results.json)
"""
from __future__ import annotations
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import config
from backtest.engine import run_backtest
from backtest.metrics import summarize
from backtest.replay import load_trades
from strategies.bull_put_spread import BullPutSpread
from strategies.bear_call_spread import BearCallSpread
from strategies.iron_condor import IronCondor
from run_backtest import (
    load_price_series, load_price_series_from_moomoo,
    load_price_series_from_yfinance, load_vix_series_from_yfinance,
)
from backtest.engine import _trailing_realized_vol
from strategy_logic.gex_walls import (
    compute_gex_by_strike, compute_gex_from_contracts, find_walls, find_gamma_flip, estimate_oi_proxy,
)
from strategy_logic.event_calendar import load_calendar, upcoming_events
from strategy_logic.gate import evaluate_gate

OUT_PATH = Path(__file__).parent / "worker" / "public" / "data" / "results.json"

SYNTHETIC_CAVEAT = (
    "Demo run over ~50 trading days of SPY-derived SPX-proxy closes with a "
    "synthetic Black-Scholes options chain (no real quotes, no skew). Too "
    "short and too calm a window to validate a strategy -- shown only to "
    "prove the engine runs end-to-end. See README for details."
)
REAL_CAVEAT = (
    "Computed from a real 36-trade signals-service log (data/real/SPX_Backtesting.xlsx). "
    "P&L is recomputed from strikes + settlement close price, not trusted from the "
    "sheet's W/L label -- rows where the label disagreed with the computed outcome "
    "are flagged below."
)
GEX_CAVEAT_PROXY = (
    "FALLBACK PATH -- moomoo/OpenD wasn't reachable when this was generated, so "
    "GEX-by-strike, walls, and gamma flip use estimate_oi_proxy() -- a crude "
    "placeholder open-interest curve concentrated near the money and round "
    "numbers, NOT real chain data. With this proxy, walls often land right at "
    "spot (real OI has actual strike-specific clustering the proxy can't fake). "
    "Do not use these levels for real trading decisions. Modeled at a 0DTE-style "
    "horizon (T=1/365), matching the strike-selection rules in strategy_logic/, "
    "not the 45-DTE backtest track above."
)
GEX_CAVEAT_REAL = (
    "REAL chain -- open interest and gamma are pulled live from moomoo/OpenD "
    "(get_option_chain + get_market_snapshot) for the nearest-to-0DTE SPX "
    "expiration, matching the strike-selection rules in strategy_logic/ (not "
    "the 45-DTE backtest track above). Spot is derived from this same chain "
    "via put-call parity (get_market_snapshot rejects SPX/VIX index codes "
    "directly). Still restricted to strikes within the dashboard's +/- window "
    "around spot, same as the fallback path, for a consistent chart scale -- "
    "not the full chain. No proxy, no BSM re-derivation."
)

YFINANCE_LOOKBACK_DAYS = 5 * 365
YFINANCE_CAVEAT = (
    "Both the underlying price series (real SPX index, ^GSPC) AND the "
    "volatility input (real historical VIX, ^VIX) are real market data "
    "pulled live via Yahoo Finance (yfinance) -- no proxy scaling, no "
    "trailing-realized-vol substitute. This is the most realistic of the "
    "backtest tracks on this dashboard for underlying price and volatility "
    "level, but the option chain itself is STILL the synthetic Black-Scholes "
    "chain in pricing/black_scholes.py -- no real skew, no real bid/ask, no "
    "real strikes/OI. VIX is a ~30-day implied-vol figure applied flat "
    "regardless of the trade's actual DTE (no term structure). See README."
)

MOOMOO_LOOKBACK_DAYS = 3 * 365
MOOMOO_CAVEAT = (
    "Underlying prices are REAL SPY daily closes pulled live via the moomoo OpenAPI "
    "(OpenD) -- SPX/VIX themselves aren't usable here (moomoo's historical-kline "
    "endpoint rejects US indices outright), so SPY is the same proxy the synthetic "
    "demo below also uses, just over a much longer real window instead of ~50 "
    "cherry-picked calm days. Option prices/greeks are still the SYNTHETIC "
    "Black-Scholes chain in pricing/black_scholes.py -- real historical SPX option "
    "chains (strikes/greeks on the actual days these trades opened) aren't available "
    "from any live quote API. This validates strategy rules and P&L accounting "
    "against real market moves across a longer window, not real tradable option "
    "pricing. See README for details."
)


def json_default(o):
    if isinstance(o, date):
        return o.isoformat()
    raise TypeError(f"not serializable: {o!r}")


def equity_curve(pnls: list[float]) -> list[float]:
    running = 0.0
    out = []
    for p in pnls:
        running += p
        out.append(round(running, 2))
    return out


def _run_strategies_export(price_series: list[tuple[date, float]], caveat: str,
                            vix_series: list[tuple[date, float]] | None = None) -> dict:
    strategies = [BullPutSpread(config), BearCallSpread(config), IronCondor(config)]

    out_strategies = []
    for strat in strategies:
        trades = run_backtest(strat, price_series, vix_series=vix_series)
        stats = summarize(trades)
        closed = [t for t in trades if t.realized_pnl is not None]
        out_strategies.append({
            "name": strat.name,
            "stats": stats,
            "equity_curve": equity_curve([t.realized_pnl for t in closed]),
            "trades": [
                {
                    "entry_date": t.entry_date,
                    "exit_date": t.exit_date,
                    "status": t.status,
                    "legs": [f"{l.side} {l.right}{l.strike:.0f}" for l in t.legs],
                    "entry_credit": round(t.entry_credit, 2),
                    "pnl": t.realized_pnl,
                }
                for t in trades
            ],
        })

    return {
        "caveat": caveat,
        "price_series_range": [price_series[0][0], price_series[-1][0]],
        "strategies": out_strategies,
    }


def export_synthetic() -> dict:
    price_series = load_price_series("data/sample/spx_proxy_sample.csv")
    return _run_strategies_export(price_series, SYNTHETIC_CAVEAT)


def export_moomoo() -> dict:
    """
    Same engine/strategies as export_synthetic, but fed REAL SPY closes
    pulled live via moomoo/OpenD instead of the static sample CSV. Raises
    if OpenD isn't running/logged in -- caller should catch and skip this
    section rather than fail the whole export (see main()).
    """
    start = (date.today() - timedelta(days=MOOMOO_LOOKBACK_DAYS)).isoformat()
    price_series = load_price_series_from_moomoo("SPY", start, None)
    return _run_strategies_export(price_series, MOOMOO_CAVEAT)


def export_yfinance() -> dict:
    """
    Same engine/strategies as export_synthetic, but fed REAL SPX (^GSPC)
    closes AND REAL VIX (both via Yahoo Finance/yfinance) instead of the
    static sample CSV or the SPY-scaled moomoo proxy -- see
    backtest/engine.py's vix_series docstring for why real VIX matters
    (variance risk premium). Raises if yfinance can't reach Yahoo (no
    network, rate-limited, etc.) -- caller should catch and skip this
    section rather than fail the whole export (see main()).
    """
    start = (date.today() - timedelta(days=YFINANCE_LOOKBACK_DAYS)).isoformat()
    price_series = load_price_series_from_yfinance("^GSPC", start, None)
    vix_series = load_vix_series_from_yfinance(start, None)
    return _run_strategies_export(price_series, YFINANCE_CAVEAT, vix_series=vix_series)


def export_real() -> dict:
    all_trades = load_trades(source="real:xlsx")
    overall = summarize(all_trades)
    overall["equity_curve"] = equity_curve([t.realized_pnl for t in all_trades if t.realized_pnl is not None])

    mismatches = [t for t in all_trades if "MISMATCH" in t.notes]

    by_strategy = []
    for strat in sorted(set(t.strategy for t in all_trades)):
        subset = [t for t in all_trades if t.strategy == strat]
        stats = summarize(subset)
        stats["equity_curve"] = equity_curve([t.realized_pnl for t in subset if t.realized_pnl is not None])
        by_strategy.append({"name": strat, "stats": stats})

    return {
        "caveat": REAL_CAVEAT,
        "overall": overall,
        "mismatches": [
            {"entry_date": t.entry_date, "strategy": t.strategy, "notes": t.notes}
            for t in mismatches
        ],
        "by_strategy": by_strategy,
        "trades": [
            {
                "entry_date": t.entry_date,
                "strategy": t.strategy,
                "status": t.status,
                "pnl": t.realized_pnl,
                "notes": t.notes,
            }
            for t in all_trades
        ],
    }


def _export_gex_real(width_pct: float) -> dict:
    """
    Real path: live SPX chain via moomoo/OpenD, nearest to 0DTE (matching
    this snapshot's intended horizon -- see GEX_CAVEAT_REAL). Raises if
    OpenD isn't reachable/logged in or the chain comes back empty --
    caller falls back to _export_gex_proxy.
    """
    from collectors.moomoo_daily_collector import (
        fetch_option_expirations, pick_expiration_near_dte, fetch_option_chain,
        chain_df_to_quotes, chain_df_to_gex_contracts, estimate_spot_from_chain,
    )

    expirations = fetch_option_expirations("SPX")
    expiry = pick_expiration_near_dte(expirations, target_dte=0)
    chain_df = fetch_option_chain("SPX", expiry)
    if chain_df.empty:
        raise RuntimeError("empty chain from moomoo")

    quotes = chain_df_to_quotes(chain_df)
    spot = estimate_spot_from_chain(quotes)
    contracts = chain_df_to_gex_contracts(chain_df)

    # Same +/- window as the proxy path, applied to the real contracts before
    # computing GEX, so both paths produce a comparably-scaled chart/totals
    # instead of one being a narrow window and the other a 1700+ strike dump.
    low = round((spot * (1 - width_pct)) / config.STRIKE_INCREMENT) * config.STRIKE_INCREMENT
    high = round((spot * (1 + width_pct)) / config.STRIKE_INCREMENT) * config.STRIKE_INCREMENT
    windowed = [c for c in contracts if low <= c["strike"] <= high]

    rows = compute_gex_from_contracts(spot, windowed)
    return {
        "caveat": GEX_CAVEAT_REAL,
        "spot": spot,
        "spot_source": f"moomoo:SPX real chain ({expiry.isoformat()})",
        "sigma": None,
        "rows": rows,
    }


def _export_gex_proxy(width_pct: float) -> dict:
    """Fallback path: proxy OI + BSM-derived gamma, as before."""
    try:
        start = (date.today() - timedelta(days=90)).isoformat()
        price_series = load_price_series_from_moomoo("SPY", start, None)
        spot_source = "moomoo:SPY x10 (proxy OI)"
        closes = [p * 10 for _, p in price_series]
    except Exception:
        price_series = load_price_series("data/sample/spx_proxy_sample.csv")
        spot_source = "synthetic sample (proxy OI)"
        closes = [p for _, p in price_series]

    spot = closes[-1]
    sigma = _trailing_realized_vol(closes, config.REALIZED_VOL_WINDOW)

    low = round((spot * (1 - width_pct)) / config.STRIKE_INCREMENT) * config.STRIKE_INCREMENT
    high = round((spot * (1 + width_pct)) / config.STRIKE_INCREMENT) * config.STRIKE_INCREMENT
    strikes = [low + i * config.STRIKE_INCREMENT for i in range(int((high - low) / config.STRIKE_INCREMENT) + 1)]

    call_oi, put_oi = estimate_oi_proxy(strikes, spot)
    rows = compute_gex_by_strike(spot, 1 / 365, config.RISK_FREE_RATE, config.DIVIDEND_YIELD,
                                  sigma, strikes, call_oi, put_oi)
    return {
        "caveat": GEX_CAVEAT_PROXY,
        "spot": spot,
        "spot_source": spot_source,
        "sigma": sigma,
        "rows": rows,
    }


def export_gex_snapshot(width_pct: float = 0.03) -> dict:
    """
    Point-in-time GEX-by-strike + wall/gamma-flip levels around the latest
    spot. Prefers a REAL live SPX chain (real OI, real gamma, real spot via
    put-call parity) via moomoo/OpenD; falls back to the proxy-OI/BSM path
    if OpenD isn't reachable. See GEX_CAVEAT_REAL / GEX_CAVEAT_PROXY.
    """
    try:
        result = _export_gex_real(width_pct)
    except Exception as e:
        print(f"Real chain unavailable for GEX snapshot ({e}); falling back to proxy OI.")
        result = _export_gex_proxy(width_pct)

    rows = result.pop("rows")
    walls = find_walls(rows, result["spot"])
    gamma_flip = find_gamma_flip(rows)

    positive_gex = sum(r.call_gex for r in rows)
    negative_gex = sum(r.put_gex for r in rows)  # magnitude; displayed negated (dealer short-gamma side)

    result.update({
        "spot": round(result["spot"], 2),
        "sigma": round(result["sigma"], 4) if result["sigma"] is not None else None,
        "put_wall": walls.put_wall,
        "call_wall": walls.call_wall,
        "gamma_flip": round(gamma_flip, 2) if gamma_flip is not None else None,
        "positive_gex": round(positive_gex, 2),
        "negative_gex": round(-negative_gex, 2),
        "net_gex": round(positive_gex - negative_gex, 2),
        "gross_gex": round(positive_gex + negative_gex, 2),
        "by_strike": [
            {
                "strike": r.strike,
                "positive_gex": round(r.call_gex, 2),
                "negative_gex": round(-r.put_gex, 2),
                "net_gex": round(r.net_gex, 2),
            }
            for r in rows
        ],
    })
    return result


GATE_CAVEAT = (
    "Go/No-Go gate is event-only here (no VIX passed in) -- blocks same-day/next-day "
    "high-impact scheduled macro events (FOMC, CPI, NFP) from a maintained calendar "
    "(data/calendar/, see strategy_logic/event_calendar.py). This is NOT live news -- "
    "unscheduled events (Fed speakers, geopolitical shocks) are not covered. See README."
)


def export_gate() -> dict:
    """Today's Go/No-Go read plus the next 14 days of scheduled high-impact events,
    for the dashboard. Pure calendar lookup -- no network/broker dependency, so this
    should never need a try/except fallback the way the moomoo/yfinance sections do."""
    today = date.today()
    calendar = load_calendar()
    gate = evaluate_gate(today, calendar)
    upcoming = upcoming_events(today, calendar, within_days=14)
    return {
        "caveat": GATE_CAVEAT,
        "as_of": today,
        "decision": gate.decision,
        "event_risk_clear": gate.event_risk_clear,
        "reasons": gate.reasons,
        "upcoming_events": [
            {"date": e.event_date, "event": e.name, "impact": e.impact}
            for e in upcoming
        ],
    }


def main():
    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "synthetic": export_synthetic(),
        "real": export_real(),
    }
    try:
        result["gate"] = export_gate()
    except Exception as e:
        print(f"Skipping gate section (errored): {e}")
    try:
        result["moomoo"] = export_moomoo()
    except Exception as e:
        print(f"Skipping moomoo section (OpenD not reachable, or errored): {e}")
    try:
        result["yfinance"] = export_yfinance()
    except Exception as e:
        print(f"Skipping yfinance section (network unreachable, or errored): {e}")
    try:
        result["gex"] = export_gex_snapshot()
    except Exception as e:
        print(f"Skipping gex section (errored): {e}")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(result, indent=2, default=json_default))
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
