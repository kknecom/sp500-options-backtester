"""
CLI demo for strategy_logic: reads a (synthetic, for now) market snapshot
and prints the direction signal, wall levels, and suggested short strike.

    python run_strike_selector.py

Real usage: swap estimate_oi_proxy() for real OI pulled via
collectors/tiger_daily_collector.py once that's wired to live credentials,
and pass real intraday price/volume bars into compute_vwap() instead of
the synthetic ones below.
"""
from __future__ import annotations
import config
from pricing.black_scholes import build_synthetic_chain
from strategy_logic.direction_matrix import read_direction, compute_vwap
from strategy_logic.gex_walls import compute_gex_by_strike, find_walls, estimate_oi_proxy
from strategy_logic.strike_selector import select_bull_put_strike, select_bear_call_strike
from strategy_logic.checklist import DiscretionaryChecklist


def main():
    # --- synthetic snapshot (stand-in for a real intraday feed) ---
    prior_close = 7700.0
    today_open = 7712.0
    spot = 7728.0
    T = 1 / 365          # 0DTE
    sigma = 0.13

    prices = [7712, 7715, 7720, 7722, 7725, 7728]
    volumes = [1.0, 1.2, 0.9, 1.1, 1.3, 1.0]
    vwap = compute_vwap(prices, volumes)

    direction = read_direction(today_open, prior_close, spot, vwap)
    print("=== Direction (Class #02 matrix) ===")
    print(direction)

    strikes = [spot - spot % config.STRIKE_INCREMENT + config.STRIKE_INCREMENT * i
               for i in range(-30, 31)]
    call_oi, put_oi = estimate_oi_proxy(strikes, spot)
    gex_rows = compute_gex_by_strike(spot, T, config.RISK_FREE_RATE, config.DIVIDEND_YIELD,
                                      sigma, strikes, call_oi, put_oi)
    walls = find_walls(gex_rows, spot)
    print("\n=== GEX walls (standard community formula, NOT from course; proxy OI) ===")
    print(f"Put Wall (support):    {walls.put_wall}")
    print(f"Call Wall (resistance): {walls.call_wall}")

    chain = build_synthetic_chain(spot, T, config.RISK_FREE_RATE, config.DIVIDEND_YIELD,
                                   sigma, config.STRIKE_INCREMENT)

    if direction.signal.value == "BULLISH_CONFIRMED":
        cand = select_bull_put_strike(chain, walls.put_wall, spot)
        print("\n=== Suggested Bull Put Spread (Class #05 placement rule) ===")
    elif direction.signal.value == "BEARISH_CONFIRMED":
        cand = select_bear_call_strike(chain, walls.call_wall, spot)
        print("\n=== Suggested Bear Call Spread (Class #05 placement rule) ===")
    else:
        cand = None
        print("\nDirection signal is WAIT -- course says do not force a trade.")

    if cand:
        print(f"Short strike: {cand.short_strike}  |  Long strike: {cand.long_strike}")
        print(f"Credit: ${cand.credit * 100:.2f}  |  Width: {cand.width} pts")
        print(f"Max profit: ${cand.max_profit:.2f}  |  Max loss: ${cand.max_loss:.2f}")
        print(f"Credit/risk ratio: {cand.credit_to_risk:.3f}")
        print(f"Beyond wall (Class #05 hard rule satisfied): {cand.beyond_wall}")

    print("\n=== Still-discretionary checklist (course never quantifies these) ===")
    checklist = DiscretionaryChecklist(
        price_action_confirms=None,
        market_structure_confirms=None,
        resistance_or_support_clear=True if cand else None,
        premium_worth_the_risk=None,
        timing_confirmed=None,
    )
    confirmed, assessed = checklist.score()
    print(f"Assessed {assessed} of {len(checklist.__dataclass_fields__)} items, {confirmed} confirmed.")
    print("(These require the user's own chart read -- not computed by this module.)")


if __name__ == "__main__":
    main()
