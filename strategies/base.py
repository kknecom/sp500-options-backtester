"""
Shared data structures + base class for the 3 credit-spread strategies.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from pricing.black_scholes import OptionQuote


@dataclass
class Leg:
    right: str          # "P" or "C"
    strike: float
    side: str            # "SHORT" or "LONG"
    entry_price: float   # option premium at entry (per share, x100 for contract value)


@dataclass
class Trade:
    strategy: str
    entry_date: date
    expiration_date: date
    legs: list[Leg]
    entry_credit: float          # net credit received at entry (positive = credit)
    max_loss: float               # width - credit (per spread, x100 per contract)
    contracts: int = 1
    status: str = "OPEN"          # OPEN, CLOSED_PROFIT, CLOSED_STOP, CLOSED_DTE, CLOSED_EXPIRY
    exit_date: Optional[date] = None
    exit_debit: Optional[float] = None   # cost to close (0 if expired worthless)
    realized_pnl: Optional[float] = None
    underlying_entry: float = 0.0
    underlying_exit: Optional[float] = None
    notes: str = ""


class Strategy:
    """
    Base class. A strategy knows how to (a) construct a new trade from a
    synthetic (or real) option chain given the current underlying price,
    and (b) mark an open trade to market on a later date to check exit
    conditions. Subclasses implement `open_trade`.
    """
    name = "base"

    def __init__(self, cfg):
        self.cfg = cfg

    def open_trade(self, as_of: date, expiration: date, S: float, chain: list[OptionQuote]) -> Trade:
        raise NotImplementedError

    @staticmethod
    def mark_to_market(trade: Trade, chain: list[OptionQuote]) -> float:
        """
        Returns the current cost to CLOSE the position (i.e. the debit
        required to buy back shorts and sell longs). Positive = it would
        cost money to close; this is compared against entry_credit to
        determine P&L if closed now.
        """
        by_key = {(q.right, q.strike): q for q in chain}
        debit = 0.0
        for leg in trade.legs:
            q = by_key.get((leg.right, leg.strike))
            if q is None:
                continue
            if leg.side == "SHORT":
                debit += q.price     # cost to buy back the short
            else:
                debit -= q.price     # proceeds from selling the long
        return debit

    @staticmethod
    def settle_at_expiration(trade: Trade, S_final: float) -> float:
        """
        Cash-settle each leg against the final underlying price and
        return the total intrinsic debit owed at expiration.
        """
        debit = 0.0
        for leg in trade.legs:
            if leg.right == "P":
                intrinsic = max(leg.strike - S_final, 0.0)
            else:
                intrinsic = max(S_final - leg.strike, 0.0)
            if leg.side == "SHORT":
                debit += intrinsic
            else:
                debit -= intrinsic
        return debit
