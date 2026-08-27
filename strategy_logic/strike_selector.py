"""
Short-strike selection, per Class #05 (How I Select My Short Strikes).

The ONE hard rule the course actually gives (Section 9/10/17):
    Bull Put:  short Put strike must sit AT or BEYOND (<=) the Put Wall / support
    Bear Call: short Call strike must sit AT or BEYOND (>=) the Call Wall / resistance

Everything else the course lists as inputs to strike selection --
"distance," "premium," "time remaining," "risk fit" -- is qualitative
with no stated weights or thresholds (Sections 12-16). This module
implements the one hard constraint plus a simple, transparent objective
(maximize credit-to-max-loss ratio) to break ties among the strikes that
satisfy it. That tie-break rule is NOT from the course -- the course
never says how to choose among multiple wall-protected strikes -- it's a
reasonable default you should feel free to swap out.

P&L math (Sections 8 of Classes #03/#04, restated in Class #05 Section 14)
matches strategies/base.py exactly:
    max_spread_value = width * 100
    max_profit        = credit_received
    max_loss          = max_spread_value - credit_received   (approx, ignores multiplier on credit)
"""
from __future__ import annotations
from dataclasses import dataclass
from pricing.black_scholes import OptionQuote

CONTRACT_MULTIPLIER = 100


@dataclass
class SpreadCandidate:
    short_strike: float
    long_strike: float
    credit: float
    width: float
    max_profit: float
    max_loss: float
    credit_to_risk: float   # max_profit / max_loss, higher = more paid per $ risked
    beyond_wall: bool


def _spread_pnl(short_price: float, long_price: float, short_strike: float, long_strike: float):
    credit = short_price - long_price
    width = abs(short_strike - long_strike)
    max_profit = credit * CONTRACT_MULTIPLIER
    max_loss = max(width * CONTRACT_MULTIPLIER - max_profit, 0.0)
    return credit, width, max_profit, max_loss


def select_bull_put_strike(chain: list[OptionQuote], put_wall: float,
                            spot: float, min_distance_pts: float = 0.0,
                            long_leg_offset: float = 5.0) -> SpreadCandidate | None:
    """
    Among all puts strictly below spot AND at-or-below the Put Wall (i.e.
    protected per Class #05's placement rule), pick the one with the best
    credit-to-risk ratio. Pairs each short strike with a long strike
    `long_leg_offset` points further OTM (below) as the protective leg.
    Returns None if no strike satisfies the wall constraint.
    """
    puts = sorted([q for q in chain if q.right == "P"], key=lambda q: q.strike)
    eligible = [q for q in puts if q.strike <= put_wall and q.strike <= spot - min_distance_pts]
    if not eligible:
        return None

    best = None
    for short_q in eligible:
        long_strike = short_q.strike - long_leg_offset
        long_q = min(puts, key=lambda q: abs(q.strike - long_strike), default=None)
        if long_q is None or long_q.strike >= short_q.strike:
            continue
        credit, width, max_profit, max_loss = _spread_pnl(
            short_q.price, long_q.price, short_q.strike, long_q.strike)
        if max_loss <= 0:
            continue
        ratio = max_profit / max_loss
        cand = SpreadCandidate(short_strike=short_q.strike, long_strike=long_q.strike,
                                credit=credit, width=width, max_profit=max_profit,
                                max_loss=max_loss, credit_to_risk=ratio,
                                beyond_wall=short_q.strike <= put_wall)
        if best is None or ratio > best.credit_to_risk:
            best = cand
    return best


def select_bear_call_strike(chain: list[OptionQuote], call_wall: float,
                             spot: float, min_distance_pts: float = 0.0,
                             long_leg_offset: float = 5.0) -> SpreadCandidate | None:
    """Mirror of select_bull_put_strike for the resistance/Call Wall side."""
    calls = sorted([q for q in chain if q.right == "C"], key=lambda q: q.strike)
    eligible = [q for q in calls if q.strike >= call_wall and q.strike >= spot + min_distance_pts]
    if not eligible:
        return None

    best = None
    for short_q in eligible:
        long_strike = short_q.strike + long_leg_offset
        long_q = min(calls, key=lambda q: abs(q.strike - long_strike), default=None)
        if long_q is None or long_q.strike <= short_q.strike:
            continue
        credit, width, max_profit, max_loss = _spread_pnl(
            short_q.price, long_q.price, short_q.strike, long_q.strike)
        if max_loss <= 0:
            continue
        ratio = max_profit / max_loss
        cand = SpreadCandidate(short_strike=short_q.strike, long_strike=long_q.strike,
                                credit=credit, width=width, max_profit=max_profit,
                                max_loss=max_loss, credit_to_risk=ratio,
                                beyond_wall=short_q.strike >= call_wall)
        if best is None or ratio > best.credit_to_risk:
            best = cand
    return best
