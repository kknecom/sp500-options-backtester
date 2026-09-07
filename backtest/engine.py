"""
Core backtest loop.

IMPORTANT CAVEAT (see README): because no real historical SPX option
chain is wired in yet, this engine prices every trade with the synthetic
Black-Scholes chain in pricing/black_scholes.py. That means:
  - No volatility skew/smile (real OTM puts trade at higher IV than ATM).
  - No real bid/ask spread or slippage.
  - Entry/exit prices are theoretical, not tradable quotes.
This is enough to validate that the strategy rules and P&L accounting
are correct (see BetterTrader's "in-sample testing" and "unrepresentative
time period" pitfalls -- this engine is explicitly a synthetic first pass,
not a claim of real trading edge). Swap `build_synthetic_chain` for a
real chain loader (fed by the moomoo/Tiger collectors) to get a
production-grade backtest.

VOLATILITY INPUT: pass `vix_series` (real historical VIX closes, e.g. via
run_backtest.load_vix_series_from_yfinance) to price each day's chain off
REAL market-implied vol instead of trailing REALIZED vol computed from
the underlying series. This matters because realized vol has no forward
premium built in -- index options have historically priced ABOVE
subsequent realized vol on average (the variance risk premium), so a
trailing-realized-vol sigma systematically understates real credit
received. VIX is a ~30-day implied vol figure, so treating it as flat
sigma for a 45 DTE trade is still an approximation (no term structure),
but it is real market data, not backed out of the same price series
being traded. Falls back to trailing realized vol for any date missing
from vix_series (e.g. a holiday mismatch) or when vix_series is omitted
entirely, so nothing about the previous behavior changes unless you
explicitly pass real VIX in.
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


def run_backtest(strategy: Strategy, price_series: list[tuple[date, float]],
                  vix_series: list[tuple[date, float]] | None = None) -> list[Trade]:
    """
    price_series: chronological list of (date, underlying_close).
    vix_series: optional chronological list of (date, VIX_close) -- e.g.
        real VIX pulled via run_backtest.load_vix_series_from_yfinance.
        VIX is quoted in percentage points (e.g. 18.04 == 18.04%), so it's
        divided by 100 to get sigma. When a date has no matching VIX entry,
        falls back to trailing realized vol for that day only.
    Returns the list of Trade objects (closed and any still open at the
    end of the series).
    """
    cfg = config
    closes = [p for _, p in price_series]
    dates = [d for d, _ in price_series]
    vix_sigma_by_date = {d: v / 100.0 for d, v in vix_series} if vix_series else {}

    open_trade: Trade | None = None
    closed_trades: list[Trade] = []
    days_since_last_entry = cfg.ENTRY_FREQUENCY_DAYS  # allow immediate first entry

    for i, (as_of, S) in enumerate(price_series):
        if as_of in vix_sigma_by_date:
            sigma = vix_sigma_by_date[as_of]
        else:
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
