"""
Performance metrics for a list of closed Trades, following the framework
from BetterTrader's "Backtesting 101" guide: expected value alone is not
enough -- report volatility, max drawdown, and a Sharpe-style
risk-adjusted return alongside win rate and total P&L.
"""
from __future__ import annotations
import math
from statistics import mean, pstdev


def summarize(trades) -> dict:
    closed = [t for t in trades if t.realized_pnl is not None]
    if not closed:
        return {"trades": 0}

    pnls = [t.realized_pnl for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    equity_curve = []
    running = 0.0
    for p in pnls:
        running += p
        equity_curve.append(running)

    peak = -math.inf
    max_dd = 0.0
    for e in equity_curve:
        peak = max(peak, e)
        max_dd = min(max_dd, e - peak)

    mu = mean(pnls)
    sigma = pstdev(pnls) if len(pnls) > 1 else 0.0
    sharpe_per_trade = (mu / sigma) if sigma > 0 else float("nan")

    return {
        "trades": len(closed),
        "win_rate": len(wins) / len(closed),
        "total_pnl": sum(pnls),
        "avg_pnl_per_trade": mu,
        "avg_win": mean(wins) if wins else 0.0,
        "avg_loss": mean(losses) if losses else 0.0,
        "stdev_pnl": sigma,
        "sharpe_per_trade": sharpe_per_trade,   # NOT annualized -- see README caveat
        "max_drawdown": max_dd,
        "final_equity": equity_curve[-1],
    }


def print_summary(strategy_name: str, stats: dict) -> None:
    print(f"\n=== {strategy_name} ===")
    if stats.get("trades", 0) == 0:
        print("No closed trades.")
        return
    print(f"Trades:            {stats['trades']}")
    print(f"Win rate:          {stats['win_rate']:.1%}")
    print(f"Total P&L:         {stats['total_pnl']:.2f} (index points x contracts)")
    print(f"Avg P&L / trade:   {stats['avg_pnl_per_trade']:.2f}")
    print(f"Avg win / loss:    {stats['avg_win']:.2f} / {stats['avg_loss']:.2f}")
    print(f"Stdev P&L:         {stats['stdev_pnl']:.2f}")
    print(f"Sharpe (per-trade):{stats['sharpe_per_trade']:.2f}")
    print(f"Max drawdown:      {stats['max_drawdown']:.2f}")
