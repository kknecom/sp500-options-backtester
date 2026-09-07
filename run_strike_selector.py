"""
CLI demo for strategy_logic: reads a market snapshot and prints the
direction signal, wall levels, and suggested short strike.

    python run_strike_selector.py                       # synthetic demo (default)
    python run_strike_selector.py --source real          # real moomoo chain

--source real requires moomoo's OpenD gateway running and logged in on
THIS machine (see moomoo_client.py) -- it pulls a live chain via
collectors/moomoo_daily_collector.py, with REAL open interest, REAL
gamma/delta/theta/vega, and REAL bid/ask, instead of the synthetic
Black-Scholes chain + estimate_oi_proxy() placeholder OI used by
--source synthetic (the default, still useful for smoke-testing the
pipeline without a live connection).

NOTE: even in --source real mode, the direction signal (compute_vwap /
read_direction) still runs on hand-typed placeholder intraday bars --
wiring real intraday price/volume is a separate, not-yet-done step. So
today, --source real gives you real walls/GEX/strikes; the BULLISH/
BEARISH/WAIT call above them is still a demo value.
"""
from __future__ import annotations
import argparse

import config
from pricing.black_scholes import build_synthetic_chain
from strategy_logic.direction_matrix import read_direction, compute_vwap
from strategy_logic.gex_walls import (
    compute_gex_by_strike, compute_gex_from_contracts, find_walls,
    find_gamma_flip, estimate_oi_proxy,
)
from strategy_logic.strike_selector import select_bull_put_strike, select_bear_call_strike
from strategy_logic.checklist import DiscretionaryChecklist


def _print_direction_and_pick(direction, walls, chain, spot):
    print("=== Direction (Class #02 matrix) ===")
    print(direction)

    print("\n=== GEX walls ===")
    print(f"Put Wall (support):     {walls.put_wall}")
    print(f"Call Wall (resistance): {walls.call_wall}")

    if direction.signal.value == "BULLISH_CONFIRMED":
        cand = select_bull_put_strike(chain, walls.put_wall, spot=spot) if walls.put_wall else None
        print("\n=== Suggested Bull Put Spread (Class #05 placement rule) ===")
    elif direction.signal.value == "BEARISH_CONFIRMED":
        cand = select_bear_call_strike(chain, walls.call_wall, spot=spot) if walls.call_wall else None
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


def run_synthetic():
    prior_close = 7700.0
    today_open = 7712.0
    spot = 7728.0
    T = 1 / 365          # 0DTE
    sigma = 0.13

    prices = [7712, 7715, 7720, 7722, 7725, 7728]
    volumes = [1.0, 1.2, 0.9, 1.1, 1.3, 1.0]
    vwap = compute_vwap(prices, volumes)
    direction = read_direction(today_open, prior_close, spot, vwap)

    strikes = [spot - spot % config.STRIKE_INCREMENT + config.STRIKE_INCREMENT * i
               for i in range(-30, 31)]
    call_oi, put_oi = estimate_oi_proxy(strikes, spot)
    gex_rows = compute_gex_by_strike(spot, T, config.RISK_FREE_RATE, config.DIVIDEND_YIELD,
                                      sigma, strikes, call_oi, put_oi)
    walls = find_walls(gex_rows, spot)
    print("(synthetic BSM chain + estimate_oi_proxy() placeholder OI)\n")

    chain = build_synthetic_chain(spot, T, config.RISK_FREE_RATE, config.DIVIDEND_YIELD,
                                   sigma, config.STRIKE_INCREMENT)
    _print_direction_and_pick(direction, walls, chain, spot)


def run_real(underlying: str = "SPX"):
    from collectors.moomoo_daily_collector import (
        fetch_option_expirations, pick_expiration_near_dte, fetch_option_chain,
        chain_df_to_quotes, chain_df_to_gex_contracts, estimate_spot_from_chain,
    )

    print(f"Connecting to OpenD for a real {underlying} chain "
          f"(OpenD must already be running and logged in)...\n")
    expirations = fetch_option_expirations(underlying)
    expiry = pick_expiration_near_dte(expirations)
    print(f"Expiry (nearest to {config.TARGET_DTE} DTE): {expiry}")

    chain_df = fetch_option_chain(underlying, expiry)
    if chain_df.empty:
        print("Empty chain returned -- check OpenD connection/permissions.")
        return

    quotes = chain_df_to_quotes(chain_df)
    # get_market_snapshot rejects US index codes ("US stock indices are not
    # supported" -- confirmed live), so spot is backed out of this same
    # chain via put-call parity instead of a separate snapshot call.
    spot = estimate_spot_from_chain(quotes)
    print(f"Spot (parity-estimated from chain): {spot:.2f}\n")
    contracts = chain_df_to_gex_contracts(chain_df)
    gex_rows = compute_gex_from_contracts(spot, contracts)
    walls = find_walls(gex_rows, spot)
    gamma_flip = find_gamma_flip(gex_rows)
    print("(REAL open interest + REAL gamma from moomoo -- no proxy, no BSM re-derivation)")
    print(f"Gamma flip: {gamma_flip}\n")

    # Direction signal still runs on placeholder intraday bars -- see module
    # docstring. Real walls/strikes above are usable now regardless.
    prices = [spot - 4, spot - 2, spot - 1, spot, spot + 1, spot]
    volumes = [1.0, 1.2, 0.9, 1.1, 1.3, 1.0]
    vwap = compute_vwap(prices, volumes)
    direction = read_direction(prices[0], prices[0], spot, vwap)

    _print_direction_and_pick(direction, walls, quotes, spot)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=["synthetic", "real"], default="synthetic",
                         help="synthetic (default): hand-typed demo snapshot, no network. "
                              "real: live chain+greeks from moomoo via OpenD.")
    parser.add_argument("--underlying", default="SPX", help="Only used with --source real.")
    args = parser.parse_args()

    if args.source == "real":
        run_real(args.underlying)
    else:
        run_synthetic()


if __name__ == "__main__":
    main()
