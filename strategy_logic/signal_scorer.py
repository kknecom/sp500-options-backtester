"""
Combines direction_matrix + gex_walls (placement rule) + strike_selector +
a set of AUTOMATED checklist heuristics into a single PRIME / VALID / WATCH
tier per signal, meant to run unattended (see daily_signal_feeder.py) --
no human in the loop to fill in checklist.py's manual booleans.

IMPORTANT PROVENANCE: the course (Classes #01-#05) lists Price Action,
Market Structure, "enough premium," and timing as things to check but
never gives a formula for any of them (see strategy_logic/checklist.py).
Everything below that isn't a direct call into direction_matrix.py /
gex_walls.py / strike_selector.py / gate.py is THIS MODULE'S OWN
substitute heuristic -- a reasonable default, not course content, and
explicitly not validated against real outcomes yet (that's what the
feeder + grader are for). Tune SCORE_THRESHOLDS and the heuristic
functions once real signal_log data accumulates.

TIER RULE (this module's own default, confirmed with the user Sept 2026):
  Hard gates (fail either -> WATCH, no checklist score computed):
    1. Direction confirmed (BULLISH_CONFIRMED or BEARISH_CONFIRMED, not WAIT)
    2. A wall-protected short strike candidate exists (Class #05 placement rule)
  Then, out of 5 automated checklist items (price action, market structure,
  resistance/support clarity, premium-worth-risk, event risk clear):
    PRIME: >=4 of 5 confirm
    VALID: 2-3 of 5 confirm
    WATCH: <=1 of 5 confirm (even though hard gates passed)
  squeeze_or_reversal_risk_low and timing_confirmed are NOT automated in v1
  -- left None/not-applicable, consistent with checklist.py's "don't fake
  a formula" principle; can be added once a defensible heuristic exists.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from enum import Enum
from statistics import mean

from strategy_logic.direction_matrix import read_direction, DirectionSignal, DirectionRead
from strategy_logic.strike_selector import select_bull_put_strike, select_bear_call_strike, SpreadCandidate
from strategy_logic.checklist import DiscretionaryChecklist
from strategy_logic.gate import evaluate_gate
from strategy_logic.event_calendar import MacroEvent

PRIME_MIN_CONFIRMED = 4     # out of 5 automated items
VALID_MIN_CONFIRMED = 2
PREMIUM_RISK_MIN_RATIO = 0.30     # credit / max_loss -- this module's own default, not course-sourced
STRUCTURE_SMA_WINDOW = 10          # bars, for market_structure_confirms
PRICE_ACTION_LOOKBACK = 3          # bars, for price_action_confirms
WALL_CLEAR_MAX_DISTANCE_PCT = 0.02  # wall must be within 2% of spot to count as "clear" protection


class Tier(str, Enum):
    PRIME = "PRIME"
    VALID = "VALID"
    WATCH = "WATCH"


@dataclass
class SignalScore:
    trade_date: date
    direction: DirectionRead
    tier: Tier
    candidate: SpreadCandidate | None
    checklist: DiscretionaryChecklist
    confirmed_count: int
    assessed_count: int
    reasons: list[str]   # human-readable notes on why it landed at this tier


def _price_action_confirms(recent_closes: list[float], bullish: bool) -> bool:
    """Heuristic (not course-sourced): majority of the last
    PRICE_ACTION_LOOKBACK daily changes point in the trade's direction."""
    window = recent_closes[-(PRICE_ACTION_LOOKBACK + 1):]
    if len(window) < 2:
        return False
    changes = [window[i] - window[i - 1] for i in range(1, len(window))]
    up_days = sum(1 for c in changes if c > 0)
    down_days = sum(1 for c in changes if c < 0)
    return (up_days > down_days) if bullish else (down_days > up_days)


def _market_structure_confirms(recent_closes: list[float], spot: float, bullish: bool) -> bool:
    """Heuristic (not course-sourced): spot positioned on the trend side of
    a simple moving average -- a crude proxy for 'higher highs/higher lows'
    (bullish) or 'lower highs/lower lows' (bearish) structure."""
    window = recent_closes[-STRUCTURE_SMA_WINDOW:]
    if len(window) < 2:
        return False
    sma = mean(window)
    return (spot > sma) if bullish else (spot < sma)


def _resistance_or_support_clear(candidate: SpreadCandidate | None, spot: float) -> bool:
    """True only if the wall-protected strike's wall sits reasonably close
    to spot (WALL_CLEAR_MAX_DISTANCE_PCT) -- a wall found only at the edge
    of a wide search window is weaker, less 'clear' protection than one
    close to current price."""
    if candidate is None:
        return False
    wall_strike = candidate.short_strike
    return abs(wall_strike - spot) / spot <= WALL_CLEAR_MAX_DISTANCE_PCT


def _premium_worth_the_risk(candidate: SpreadCandidate | None) -> bool:
    if candidate is None or candidate.max_loss <= 0:
        return False
    return candidate.credit_to_risk >= PREMIUM_RISK_MIN_RATIO


def score_signal(trade_date: date, today_open: float, prior_close: float, spot: float,
                  vwap: float, recent_closes: list[float], chain: list, put_wall: float | None,
                  call_wall: float | None, calendar: list[MacroEvent], vix: float | None = None,
                  min_wall_distance_pts: float = 0.0) -> SignalScore:
    """
    recent_closes: chronological daily closes ending the day BEFORE
    trade_date (point-in-time -- must not include trade_date's own close,
    which isn't known yet intraday; see daily_signal_feeder.py).
    chain: synthetic or real OptionQuote list for strike selection.
    """
    direction = read_direction(today_open, prior_close, spot, vwap)
    reasons = []

    if direction.signal == DirectionSignal.WAIT:
        reasons.append("Direction not confirmed (gap/VWAP conflict) -- hard gate fails, course says don't force it.")
        return SignalScore(trade_date, direction, Tier.WATCH, None, DiscretionaryChecklist(), 0, 0, reasons)

    bullish = direction.signal == DirectionSignal.BULLISH_CONFIRMED
    if bullish:
        candidate = select_bull_put_strike(chain, put_wall, spot, min_distance_pts=min_wall_distance_pts) if put_wall else None
    else:
        candidate = select_bear_call_strike(chain, call_wall, spot, min_distance_pts=min_wall_distance_pts) if call_wall else None

    if candidate is None:
        reasons.append("No wall-protected short strike found -- hard gate fails (Class #05 placement rule).")
        return SignalScore(trade_date, direction, Tier.WATCH, None, DiscretionaryChecklist(), 0, 0, reasons)

    gate = evaluate_gate(trade_date, calendar, vix=vix)

    checklist = DiscretionaryChecklist(
        price_action_confirms=_price_action_confirms(recent_closes, bullish),
        market_structure_confirms=_market_structure_confirms(recent_closes, spot, bullish),
        resistance_or_support_clear=_resistance_or_support_clear(candidate, spot),
        premium_worth_the_risk=_premium_worth_the_risk(candidate),
        event_risk_clear=gate.event_risk_clear,
        # Not automated in v1 -- left unscored rather than guessed:
        timing_confirmed=None,
        squeeze_or_reversal_risk_low=None,
    )
    confirmed, assessed = checklist.score()

    if confirmed >= PRIME_MIN_CONFIRMED:
        tier = Tier.PRIME
    elif confirmed >= VALID_MIN_CONFIRMED:
        tier = Tier.VALID
    else:
        tier = Tier.WATCH
    reasons.append(f"{confirmed}/{assessed} automated checklist items confirmed -> {tier.value}.")
    if not gate.event_risk_clear:
        reasons.append(f"Event risk: {'; '.join(gate.reasons)}")

    return SignalScore(trade_date, direction, tier, candidate, checklist, confirmed, assessed, reasons)
