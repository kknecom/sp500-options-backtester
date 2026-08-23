"""
Core backtest loop.

IMPORTANT CAVEAT (see README): because no real historical SPX option
chain is wired in yet, this engine prices every trade with the synthetic
Black-Scholes chain in pricing/black_scholes.py, using trailing realized
volatility off the underlying series as a flat IV proxy. That means:
  - No volatility skew/smile (real OTM puts trade at higher IV than ATM).
  - No real bid/ask spread or slippage.
  - Entry/exit prices are theoretical, not tradable quotes.
This is enough to validate that the strategy rules and P&L accounting
are correct (see BetterTrader's "in-sample testing" and "unrepresentative
time period" pitfalls -- this engine is explicitly a synthetic first pass,
not a claim of real trading edge). Swap `build_synthetic_chain` for a
real chain loader (fed by the Tiger Open API collector) to get a
production-grade backtest.
"""
from __future__ import annotations
import math
from datetime import date, timedelta
from statistics import pstdev

import config
from pricing.black_scholes import build_synthetic_chain
from strategies.base import Strategy, Trade


def _trailing_realized_vol(closes: list[float], window: int) -> float:
    if len(closes) < window + 1:
        window = len(closes) - 1
    if window < 2:
        return 0.15  # fallback default vol if not enough history yet
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - window, len(closes))]
    daily_sigma = pstdev(rets)
    return daily_sigma * math.sqrt(config.TRADING_DAYS_PER_YEAR)


def run_backtest(strategy: Strategy, price_series: list[tuple[date, float]]) -> list[Trade]:
    """
    price_series: chronological list of (date, underlying_close).
    Returns the list of Trade objects (closed and any still open at the
    end of the series).
    """
    cfg = config
    closes = [p for _, p in price_series]
    dates = [d for d, _ in price_series]

    open_trade: Trade | None = None
    closed_trades: list[Trade] = []
    days_since_last_entry = cfg.ENTRY_FREQUENCY_DAYS  # allow immediate first entry

    for i, (as_of, S) in enumerate(price_series):
        trailing_closes = closes[: i + 1]
        sigma = _trailing_realized_vol(trailing_closes, cfg.REALIZED_VOL_WINDOW)

        # --- manage open trade ---
        if open_trade is not None:
            dte = (open_trade.expiration_date - as_of).days
            if as_of >= open_trade.expiration_date or dte <= 0:
                debit = Strategy.settle_at_expiration(open_trade, S)
                _close_trade(open_trade, as_of, S, debit, "CLOSED_EXPIRY")
                closed_trades.append(open_trade)
                open_trade = None
            else:
                T = max(dte, 1) / 365.0
                chain = build_synthetic_chain(S, T, cfg.RISK_FREE_RATE, cfg.DIVIDEND_YIELD,
                                               sigma, cfg.STRIKE_INCREMENT)
                debit = Strategy.mark_to_market(open_trade, chain)
                pnl_if_closed = (open_trade.entry_credit - debit) * 100 * open_trade.contracts

                profit_target = cfg.PROFIT_TARGET_PCT * open_trade.entry_credit * 100 * open_trade.contracts
                stop_loss = -cfg.STOP_LOSS_MULT * open_trade.entry_credit * 100 * open_trade.contracts

                if pnl_if_closed >= profit_target:
                    _close_trade(open_trade, as_of, S, debit, "CLOSED_PROFIT")
                    closed_trades.append(open_trade)
                    open_trade = None
                elif pnl_if_closed <= stop_loss:
                    _close_trade(open_trade, as_of, S, debit, "CLOSED_STOP")
                    closed_trades.append(open_trade)
                    open_trade = None
                elif dte <= cfg.MIN_DTE_EXIT:
                    _close_trade(open_trade, as_of, S, debit, "CLOSED_DTE")
                    closed_trades.append(open_trade)
                    open_trade = None

        # --- consider opening a new trade ---
        if open_trade is None:
            days_since_last_entry += 1
            if days_since_last_entry >= cfg.ENTRY_FREQUENCY_DAYS:
                expiration = as_of + timedelta(days=cfg.TARGET_DTE)
                T = cfg.TARGET_DTE / 365.0
                chain = build_synthetic_chain(S, T, cfg.RISK_FREE_RATE, cfg.DIVIDEND_YIELD,
                                               sigma, cfg.STRIKE_INCREMENT)
                open_trade = strategy.open_trade(as_of, expiration, S, chain)
                days_since_last_entry = 0

    # if a trade is still open at the end of the series, mark it (not settled)
    if open_trade is not None:
        open_trade.notes = "still open at end of backtest window"

    return closed_trades


def _close_trade(trade: Trade, as_of: date, S: float, debit: float, status: str) -> None:
    trade.status = status
    trade.exit_date = as_of
    trade.exit_debit = debit
    trade.underlying_exit = S
    trade.realized_pnl = (trade.entry_credit - debit) * 100 * trade.contracts
