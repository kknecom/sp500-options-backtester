"""
Go/No-Go gate -- the "prime signal" the project brief asks for: a single
pass/fail check, using scheduled macro events (and, when available, the
current VIX level) that runs BEFORE direction/strike-selection logic.
The idea: don't even look for a trade on a day this gate fails.

This intentionally stays a simple, auditable rule set (not a model) --
every reason a day is blocked is named explicitly in GateResult.reasons,
so the trader can see exactly why and override by hand if they disagree
(the course's own checklist.py already treats event_risk_clear as
overridable -- this module supplies the default the trader can look at
before starting their own discretionary walk-through).

VIX thresholds are NOT hard research-backed cutoffs -- config.py values
below are reasonable placeholders (extremely low VIX = complacency/thin
premium not worth the risk; extremely high VIX = crisis-regime moves too
large and fast for defined-risk credit spreads to size safely). Tune them
in config.py as you build a track record.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date

import config
from strategy_logic.event_calendar import MacroEvent, events_in_window


@dataclass
class GateResult:
    decision: str            # "GO" or "NO-GO"
    event_risk_clear: bool   # feeds checklist.DiscretionaryChecklist.event_risk_clear
    reasons: list[str] = field(default_factory=list)   # non-empty only when decision == "NO-GO"
    blocking_events: list[MacroEvent] = field(default_factory=list)
    vix: float | None = None

    def __str__(self) -> str:
        lines = [f"Go/No-Go: {self.decision}"]
        if self.vix is not None:
            lines.append(f"VIX: {self.vix:.2f}")
        if self.reasons:
            lines.append("Reasons:")
            lines.extend(f"  - {r}" for r in self.reasons)
        else:
            lines.append("No blocking events or vol-regime flags found.")
        return "\n".join(lines)


def evaluate_gate(trade_date: date, calendar: list[MacroEvent],
                   vix: float | None = None,
                   lookahead_days: int = None, lookback_days: int = None) -> GateResult:
    """
    trade_date: the day being evaluated (usually today, for a 0DTE decision).
    calendar: from strategy_logic.event_calendar.load_calendar() -- passed
        in rather than loaded here so callers can cache/mock it.
    vix: current VIX level, if available (e.g. from a real chain/quote) --
        omit to skip the vol-regime check entirely (event-only gate).
    lookahead_days/lookback_days: override config.EVENT_BLACKOUT_LOOKAHEAD_DAYS/
        EVENT_BLACKOUT_LOOKBACK_DAYS -- lookahead catches an event happening
        TODAY or in the next N days (e.g. "day before FOMC"), lookback catches
        one that happened in the last N days (lingering post-event volatility).
    """
    lookahead_days = config.EVENT_BLACKOUT_LOOKAHEAD_DAYS if lookahead_days is None else lookahead_days
    lookback_days = config.EVENT_BLACKOUT_LOOKBACK_DAYS if lookback_days is None else lookback_days

    reasons: list[str] = []
    blocking = events_in_window(trade_date, calendar, days_before=lookback_days, days_after=lookahead_days)
    for e in blocking:
        when = "today" if e.event_date == trade_date else e.event_date.isoformat()
        reasons.append(f"{e.name} ({when})")

    if vix is not None:
        if vix >= config.VIX_MAX_FOR_ENTRY:
            reasons.append(f"VIX {vix:.2f} >= {config.VIX_MAX_FOR_ENTRY} (crisis-regime vol, size/skip)")
        elif vix <= config.VIX_MIN_FOR_ENTRY:
            reasons.append(f"VIX {vix:.2f} <= {config.VIX_MIN_FOR_ENTRY} (premium likely too thin to be worth the risk)")

    decision = "NO-GO" if reasons else "GO"
    return GateResult(
        decision=decision,
        event_risk_clear=(len(blocking) == 0),
        reasons=reasons,
        blocking_events=blocking,
        vix=vix,
    )
