"""
Unit tests for strategy_logic. Run with: python -m pytest tests/test_strategy_logic.py -q
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategy_logic.direction_matrix import read_direction, compute_vwap, DirectionSignal
from strategy_logic.gex_walls import compute_gex_by_strike, find_walls, estimate_oi_proxy
from strategy_logic.strike_selector import select_bull_put_strike, select_bear_call_strike
from pricing.black_scholes import build_synthetic_chain


def test_bullish_gap_above_vwap_confirms():
    d = read_direction(today_open=7710, prior_close=7700, current_price=7715, vwap=7705)
    assert d.signal == DirectionSignal.BULLISH_CONFIRMED


def test_bullish_gap_below_vwap_waits():
    d = read_direction(today_open=7710, prior_close=7700, current_price=7695, vwap=7705)
    assert d.signal == DirectionSignal.WAIT


def test_bearish_gap_below_vwap_confirms():
    d = read_direction(today_open=7690, prior_close=7700, current_price=7695, vwap=7705)
    assert d.signal == DirectionSignal.BEARISH_CONFIRMED


def test_bearish_gap_above_vwap_waits():
    d = read_direction(today_open=7690, prior_close=7700, current_price=7715, vwap=7705)
    assert d.signal == DirectionSignal.WAIT


def test_vwap_matches_manual_calc():
    v = compute_vwap([100, 102], [10, 30])
    assert abs(v - (100*10 + 102*30) / 40) < 1e-9


def test_walls_land_on_expected_side_of_spot():
    S = 7748.0
    strikes = [S - S % 5 + 5 * i for i in range(-30, 31)]
    call_oi, put_oi = estimate_oi_proxy(strikes, S)
    rows = compute_gex_by_strike(S, T=1/365, r=0.045, q=0.013, sigma=0.13,
                                  strikes=strikes, call_oi_by_strike=call_oi, put_oi_by_strike=put_oi)
    walls = find_walls(rows, S)
    assert walls.put_wall <= S
    assert walls.call_wall >= S


def test_bull_put_selection_stays_beyond_wall_and_matches_course_pnl_shape():
    S = 7728.0
    T = 1/365
    chain = build_synthetic_chain(S, T, 0.045, 0.013, 0.13, strike_increment=5)
    put_wall = 7700.0
    cand = select_bull_put_strike(chain, put_wall, S)
    assert cand is not None
    assert cand.short_strike <= put_wall
    assert cand.long_strike < cand.short_strike
    # Course's worked example: 5-pt wide spread -> $500 max value
    assert abs(cand.width - 5) < 1e-9
    assert abs((cand.max_profit + cand.max_loss) - 500) < 1e-6


def test_bear_call_selection_stays_beyond_wall():
    S = 7728.0
    T = 1/365
    chain = build_synthetic_chain(S, T, 0.045, 0.013, 0.13, strike_increment=5)
    call_wall = 7780.0
    cand = select_bear_call_strike(chain, call_wall, S)
    assert cand is not None
    assert cand.short_strike >= call_wall
    assert cand.long_strike > cand.short_strike
    assert abs((cand.max_profit + cand.max_loss) - 500) < 1e-6


if __name__ == "__main__":
    import subprocess
    subprocess.run(["python3", "-m", "pytest", __file__, "-v"])
