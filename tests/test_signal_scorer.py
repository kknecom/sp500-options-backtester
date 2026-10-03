import sys, os
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategy_logic.signal_scorer import score_signal, Tier
from pricing.black_scholes import build_synthetic_chain


def _chain(spot=7728.0):
    return build_synthetic_chain(spot, T=1/365, r=0.045, q=0.013, sigma=0.13, strike_increment=5)


def test_wait_direction_is_watch():
    # bearish gap but above VWAP -> WAIT
    result = score_signal(
        trade_date=date(2026, 8, 27), today_open=7690, prior_close=7700, spot=7715,
        vwap=7705, recent_closes=[7680, 7690, 7700, 7710, 7715], chain=_chain(7715),
        put_wall=7700, call_wall=7780, calendar=[],
    )
    assert result.tier == Tier.WATCH
    assert result.candidate is None


def test_no_wall_candidate_is_watch():
    # bullish confirmed, but no put_wall provided at all
    result = score_signal(
        trade_date=date(2026, 8, 27), today_open=7710, prior_close=7700, spot=7728,
        vwap=7718, recent_closes=[7690, 7700, 7710, 7720, 7728], chain=_chain(7728),
        put_wall=None, call_wall=7780, calendar=[],
    )
    assert result.tier == Tier.WATCH
    assert result.candidate is None


def test_strong_bullish_setup_scores_prime_or_valid():
    # bullish confirmed, wall very close to spot, steadily rising closes (structure+price action confirm)
    spot = 7728.0
    result = score_signal(
        trade_date=date(2026, 8, 27), today_open=7710, prior_close=7700, spot=spot,
        vwap=7718, recent_closes=[7690, 7700, 7710, 7720, 7728], chain=_chain(spot),
        put_wall=7725, call_wall=7780, calendar=[],
    )
    assert result.direction.signal.value == "BULLISH_CONFIRMED"
    assert result.candidate is not None
    assert result.tier in (Tier.PRIME, Tier.VALID)
    assert result.confirmed_count >= 2


def test_weak_setup_with_falling_closes_scores_lower():
    # bullish confirmed on gap/VWAP, but recent closes are actually falling
    # (contradicts price action + market structure) -> fewer confirmations
    spot = 7728.0
    strong = score_signal(
        trade_date=date(2026, 8, 27), today_open=7710, prior_close=7700, spot=spot,
        vwap=7718, recent_closes=[7690, 7700, 7710, 7720, 7728], chain=_chain(spot),
        put_wall=7725, call_wall=7780, calendar=[],
    )
    weak = score_signal(
        trade_date=date(2026, 8, 27), today_open=7710, prior_close=7700, spot=spot,
        vwap=7718, recent_closes=[7760, 7750, 7740, 7735, 7728], chain=_chain(spot),
        put_wall=7725, call_wall=7780, calendar=[],
    )
    assert weak.confirmed_count <= strong.confirmed_count


def test_event_risk_blocks_confirmation():
    from strategy_logic.event_calendar import MacroEvent
    spot = 7728.0
    calendar = [MacroEvent(event_date=date(2026, 8, 27), name="FOMC", impact="high", source="test")]
    result = score_signal(
        trade_date=date(2026, 8, 27), today_open=7710, prior_close=7700, spot=spot,
        vwap=7718, recent_closes=[7690, 7700, 7710, 7720, 7728], chain=_chain(spot),
        put_wall=7725, call_wall=7780, calendar=calendar,
    )
    assert result.checklist.event_risk_clear is False


if __name__ == "__main__":
    import subprocess
    subprocess.run(["python3", "-m", "pytest", __file__, "-v"])
