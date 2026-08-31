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
from run_backtest import load_price_series, load_price_series_from_moomoo

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


def _run_strategies_export(price_series: list[tuple[date, float]], caveat: str) -> dict:
    strategies = [BullPutSpread(config), BearCallSpread(config), IronCondor(config)]

    out_strategies = []
    for strat in strategies:
        trades = run_backtest(strat, price_series)
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


def main():
    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "synthetic": export_synthetic(),
        "real": export_real(),
    }
    try:
        result["moomoo"] = export_moomoo()
    except Exception as e:
        print(f"Skipping moomoo section (OpenD not reachable, or errored): {e}")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(result, indent=2, default=json_default))
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
