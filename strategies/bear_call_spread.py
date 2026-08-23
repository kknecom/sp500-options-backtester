"""
Bear Call Spread (short call vertical): sell an OTM call, buy a further
OTM call for protection. Collects a credit; profits if SPX stays below
the short strike. Bearish-to-neutral bias.
"""
from __future__ import annotations
from datetime import date
from pricing.black_scholes import find_strike_by_delta
from .base import Strategy, Trade, Leg


class BearCallSpread(Strategy):
    name = "bear_call_spread"

    def open_trade(self, as_of: date, expiration: date, S: float, chain) -> Trade:
        cfg = self.cfg
        short_call = find_strike_by_delta(chain, "C", cfg.SHORT_LEG_TARGET_DELTA)
        long_call = find_strike_by_delta(chain, "C", cfg.LONG_LEG_TARGET_DELTA)

        if long_call.strike <= short_call.strike:
            long_call = min(
                [q for q in chain if q.right == "C" and q.strike > short_call.strike],
                key=lambda q: abs(q.strike - (short_call.strike + 5 * cfg.STRIKE_INCREMENT)),
                default=long_call,
            )

        credit = short_call.price - long_call.price
        width = long_call.strike - short_call.strike
        max_loss = max(width - credit, 0.0)

        legs = [
            Leg(right="C", strike=short_call.strike, side="SHORT", entry_price=short_call.price),
            Leg(right="C", strike=long_call.strike, side="LONG", entry_price=long_call.price),
        ]
        return Trade(
            strategy=self.name,
            entry_date=as_of,
            expiration_date=expiration,
            legs=legs,
            entry_credit=credit,
            max_loss=max_loss,
            contracts=cfg.CONTRACTS_PER_TRADE,
            underlying_entry=S,
        )
