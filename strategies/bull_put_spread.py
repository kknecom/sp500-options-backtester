"""
Bull Put Spread (short put vertical): sell an OTM put, buy a further OTM
put for protection. Collects a credit; profits if SPX stays above the
short strike. Bullish-to-neutral bias.
"""
from __future__ import annotations
from datetime import date
from pricing.black_scholes import find_strike_by_delta
from .base import Strategy, Trade, Leg


class BullPutSpread(Strategy):
    name = "bull_put_spread"

    def open_trade(self, as_of: date, expiration: date, S: float, chain) -> Trade:
        cfg = self.cfg
        short_put = find_strike_by_delta(chain, "P", cfg.SHORT_LEG_TARGET_DELTA)
        long_put = find_strike_by_delta(chain, "P", cfg.LONG_LEG_TARGET_DELTA)

        # Safety: long strike must be below short strike for a put spread
        if long_put.strike >= short_put.strike:
            long_put = min(
                [q for q in chain if q.right == "P" and q.strike < short_put.strike],
                key=lambda q: abs(q.strike - (short_put.strike - 5 * cfg.STRIKE_INCREMENT)),
                default=long_put,
            )

        credit = short_put.price - long_put.price
        width = short_put.strike - long_put.strike
        max_loss = max(width - credit, 0.0)

        legs = [
            Leg(right="P", strike=short_put.strike, side="SHORT", entry_price=short_put.price),
            Leg(right="P", strike=long_put.strike, side="LONG", entry_price=long_put.price),
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
