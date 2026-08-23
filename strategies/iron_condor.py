"""
Iron Condor: a bull put spread + a bear call spread opened simultaneously
on the same underlying/expiration. Profits if SPX stays between the two
short strikes. Market-neutral / range-bound bias.
"""
from __future__ import annotations
from datetime import date
from .base import Strategy, Trade, Leg
from .bull_put_spread import BullPutSpread
from .bear_call_spread import BearCallSpread


class IronCondor(Strategy):
    name = "iron_condor"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._put_side = BullPutSpread(cfg)
        self._call_side = BearCallSpread(cfg)

    def open_trade(self, as_of: date, expiration: date, S: float, chain) -> Trade:
        put_trade = self._put_side.open_trade(as_of, expiration, S, chain)
        call_trade = self._call_side.open_trade(as_of, expiration, S, chain)

        legs = put_trade.legs + call_trade.legs
        credit = put_trade.entry_credit + call_trade.entry_credit
        # Max loss for an iron condor = max loss of whichever side is breached
        # (the two sides can't both be ITM at once), so it's the larger of
        # the two individual spread widths minus the combined credit.
        put_width = put_trade.legs[0].strike - put_trade.legs[1].strike
        call_width = call_trade.legs[1].strike - call_trade.legs[0].strike
        max_loss = max(put_width, call_width) - credit
        max_loss = max(max_loss, 0.0)

        return Trade(
            strategy=self.name,
            entry_date=as_of,
            expiration_date=expiration,
            legs=legs,
            entry_credit=credit,
            max_loss=max_loss,
            contracts=self.cfg.CONTRACTS_PER_TRADE,
            underlying_entry=S,
        )
