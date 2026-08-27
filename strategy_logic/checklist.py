"""
Discretionary checklist items the course uses repeatedly (Class #01
Section 9, Class #03/#04 checklists, Class #05 Section 16) but never
quantifies: Market Structure, Price Action, Squeeze Risk, Timing, etc.

These are NOT computed from data anywhere in the course -- they're the
trader's own visual/discretionary read of the chart. Rather than fake a
formula for them, this module just gives them a first-class, explicit
place in the data model: a checklist the user (or a future manual UI
toggle) fills in, which is then combined with the objectively-computed
signals (direction_matrix, gex_walls, strike_selector) into one summary.

This keeps the dashboard honest about which parts are formulas and
which parts are still "does this look right to you."
"""
from __future__ import annotations
from dataclasses import dataclass, fields


@dataclass
class DiscretionaryChecklist:
    """Each field: None = not yet assessed, True = confirms the setup, False = fails it."""
    price_action_confirms: bool | None = None      # lower highs/lows or rejection of rallies, etc.
    market_structure_confirms: bool | None = None   # course never defines this threshold
    resistance_or_support_clear: bool | None = None  # is there a level to hide behind at all
    premium_worth_the_risk: bool | None = None       # course never gives a min credit/risk ratio
    timing_confirmed: bool | None = None             # "has the market shown enough information"
    squeeze_or_reversal_risk_low: bool | None = None  # bear-call-specific (Class #04 Sec 9)
    event_risk_clear: bool | None = None              # no FOMC/CPI/major news in window

    def score(self) -> tuple[int, int]:
        """(confirmed_count, assessed_count) -- unassessed (None) fields don't count either way."""
        assessed = [getattr(self, f.name) for f in fields(self) if getattr(self, f.name) is not None]
        confirmed = [v for v in assessed if v is True]
        return len(confirmed), len(assessed)

    def any_hard_fail(self) -> bool:
        """True if anything explicitly assessed as False -- course treats a single clear
        red flag (e.g. resistance repeatedly breaking) as reason enough to pass on the trade."""
        return any(getattr(self, f.name) is False for f in fields(self))
