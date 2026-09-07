"""
High-impact US macro event calendar -- the data half of the Go/No-Go gate
(strategy_logic/gate.py). This is the project's stated top priority: a
"prime signal" using news/events to gate whether to trade at all, before
any strike-selection logic runs.

DATA SOURCE
-----------
data/calendar/economic_calendar_YYYY.json -- FOMC dates from
federalreserve.gov, CPI dates from BLS's published schedule (both
official and stable months/years in advance). NFP (Employment Situation)
dates are normally "first Friday of the month" but BLS has recently
published irregular dates, so some entries are marked estimated -- see
that file's _readme for how to keep it current. No paid news API, no
scraping: this is a maintained static calendar, which is honest about
what it is (a known, mostly-fixed schedule) rather than pretending to be
a live news feed.

This is NOT a replacement for actual news awareness (Fed speakers,
geopulitical shocks, surprise data revisions) -- see checklist.py's
event_risk_clear field, which this module feeds automatically for the
KNOWN scheduled events, but which the trader can still override by hand
for anything unscheduled.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import json

DEFAULT_CALENDAR_DIR = Path(__file__).resolve().parents[1] / "data" / "calendar"


@dataclass
class MacroEvent:
    event_date: date
    name: str
    impact: str
    source: str


def load_calendar(year: int | None = None, calendar_dir: Path = DEFAULT_CALENDAR_DIR) -> list[MacroEvent]:
    """
    Loads all economic_calendar_*.json files in calendar_dir (or just the
    requested year's file, if it exists) into a flat list[MacroEvent].
    Missing/empty directory returns [] rather than raising -- the gate
    should degrade to "can't confirm event risk" rather than crash a
    trading-decision script over a missing data file.
    """
    calendar_dir = Path(calendar_dir)
    if year is not None:
        paths = [calendar_dir / f"economic_calendar_{year}.json"]
    else:
        paths = sorted(calendar_dir.glob("economic_calendar_*.json")) if calendar_dir.exists() else []

    events: list[MacroEvent] = []
    for path in paths:
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        for row in data.get("events", []):
            events.append(MacroEvent(
                event_date=datetime.strptime(row["date"], "%Y-%m-%d").date(),
                name=row["event"],
                impact=row.get("impact", "high"),
                source=row.get("source", ""),
            ))
    return sorted(events, key=lambda e: e.event_date)


def events_in_window(trade_date: date, calendar: list[MacroEvent],
                      days_before: int = 1, days_after: int = 0,
                      high_impact_only: bool = True) -> list[MacroEvent]:
    """
    Events falling within [trade_date - days_before, trade_date + days_after]
    (inclusive). Defaults to catching same-day and previous-day events --
    for a 0DTE strategy, a same-day print (CPI/FOMC decision, both released
    intraday) can move spot violently after entry, and the day before an
    FOMC decision often sees positioning-driven chop.
    """
    lo = trade_date - timedelta(days=days_before)
    hi = trade_date + timedelta(days=days_after)
    return [
        e for e in calendar
        if lo <= e.event_date <= hi and (not high_impact_only or e.impact == "high")
    ]


def upcoming_events(as_of: date, calendar: list[MacroEvent], within_days: int = 14,
                     high_impact_only: bool = True) -> list[MacroEvent]:
    """Events from as_of through as_of + within_days -- for a dashboard "what's coming up" list."""
    hi = as_of + timedelta(days=within_days)
    return [
        e for e in calendar
        if as_of <= e.event_date <= hi and (not high_impact_only or e.impact == "high")
    ]
