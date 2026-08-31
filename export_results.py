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
from datetime import date, datetime
from pathlib import Path

import config
from backtest.engine import run_backtest
from backtest.metrics import summarize
from backtest.replay import load_trades
from strategies.bull_put_spread import BullPutSpread
from strategies.bear_call_spread import BearCallSpread
from strategies.iron_condor import IronCondor
from run_backtest import load_price_series

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


def export_synthetic() -> dict:
    price_series = load_price_series("data/sample/spx_proxy_sample.csv")
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
        "caveat": SYNTHETIC_CAVEAT,
        "price_series_range": [price_series[0][0], price_series[-1][0]],
        "strategies": out_strategies,
    }


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
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(result, indent=2, default=json_default))
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
