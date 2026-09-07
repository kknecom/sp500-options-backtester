"""
Unit tests for strategy_logic.event_calendar / strategy_logic.gate.
Run with: python -m pytest tests/test_gate.py -q
"""
import sys, os
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategy_logic.event_calendar import MacroEvent, events_in_window, upcoming_events, load_calendar
from strategy_logic.gate import evaluate_gate


def _cal():
    return [
        MacroEvent(date(2026, 1, 28), "FOMC Decision", "high", "test"),
        MacroEvent(date(2026, 1, 13), "CPI", "high", "test"),
        MacroEvent(date(2026, 1, 20), "Some low-impact release", "low", "test"),
    ]


def test_same_day_high_impact_event_blocks():
    result = events_in_window(date(2026, 1, 28), _cal(), days_before=0, days_after=1)
    assert len(result) == 1
    assert result[0].name == "FOMC Decision"


def test_day_before_event_blocks():
    # trade_date is the day BEFORE the FOMC decision -- lookahead (days_after) should catch it.
    result = events_in_window(date(2026, 1, 27), _cal(), days_before=0, days_after=1)
    assert len(result) == 1
    assert result[0].name == "FOMC Decision"


def test_two_days_before_does_not_block():
    result = events_in_window(date(2026, 1, 26), _cal(), days_before=0, days_after=1)
    assert result == []


def test_low_impact_excluded_by_default():
    result = events_in_window(date(2026, 1, 20), _cal(), days_before=0, days_after=0, high_impact_only=True)
    assert result == []
    result_all = events_in_window(date(2026, 1, 20), _cal(), days_before=0, days_after=0, high_impact_only=False)
    assert len(result_all) == 1


def test_upcoming_events_window():
    result = upcoming_events(date(2026, 1, 10), _cal(), within_days=10)
    names = {e.name for e in result}
    assert names == {"CPI"}


def test_gate_go_on_quiet_day():
    result = evaluate_gate(date(2026, 1, 5), _cal())
    assert result.decision == "GO"
    assert result.event_risk_clear is True
    assert result.reasons == []


def test_gate_nogo_on_fomc_day():
    result = evaluate_gate(date(2026, 1, 28), _cal())
    assert result.decision == "NO-GO"
    assert result.event_risk_clear is False
    assert any("FOMC" in r for r in result.reasons)


def test_gate_vix_extreme_flags_nogo_even_without_events():
    result = evaluate_gate(date(2026, 1, 5), _cal(), vix=60.0)
    assert result.decision == "NO-GO"
    assert result.event_risk_clear is True   # events themselves were clear -- vol regime is the blocker
    assert any("VIX" in r for r in result.reasons)


def test_gate_str_is_readable():
    result = evaluate_gate(date(2026, 1, 28), _cal())
    text = str(result)
    assert "NO-GO" in text
    assert "FOMC Decision" in text


def test_real_calendar_file_loads_and_parses():
    calendar = load_calendar(2026)
    assert len(calendar) > 20
    assert all(e.impact == "high" for e in calendar)
    # FOMC decision day should be present per federalreserve.gov's published schedule
    assert any(e.event_date.isoformat() == "2026-09-16" for e in calendar)
