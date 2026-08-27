"""
Gap direction x VWAP-position confirmation matrix.

Source: Class #02 (How I Determine Market Direction), reaffirmed in
Classes #04 and #05. This is the one fully-specified, unambiguous rule
in the whole course -- a 2x2 lookup table, no free parameters.

    Bullish Gap + Above VWAP  -> BULLISH_CONFIRMED
    Bullish Gap + Below VWAP  -> WAIT
    Bearish Gap + Below VWAP  -> BEARISH_CONFIRMED
    Bearish Gap + Above VWAP  -> WAIT
    Flat gap (open == prior close) is not addressed by the course; treated
    here as WAIT (no directional edge to confirm).
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class GapDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    FLAT = "FLAT"


class VwapPosition(str, Enum):
    ABOVE = "ABOVE"
    BELOW = "BELOW"


class DirectionSignal(str, Enum):
    BULLISH_CONFIRMED = "BULLISH_CONFIRMED"   # -> look for Bull Put Spread
    BEARISH_CONFIRMED = "BEARISH_CONFIRMED"   # -> look for Bear Call Spread
    WAIT = "WAIT"                              # signals conflict, no trade yet


@dataclass
class DirectionRead:
    gap_direction: GapDirection
    vwap_position: VwapPosition
    signal: DirectionSignal
    gap_points: float
    distance_to_vwap: float


def classify_gap(today_open: float, prior_close: float) -> GapDirection:
    if today_open > prior_close:
        return GapDirection.BULLISH
    if today_open < prior_close:
        return GapDirection.BEARISH
    return GapDirection.FLAT


def classify_vwap_position(current_price: float, vwap: float) -> VwapPosition:
    return VwapPosition.ABOVE if current_price >= vwap else VwapPosition.BELOW


def compute_vwap(prices: list[float], volumes: list[float]) -> float:
    """Standard cumulative VWAP: sum(price*volume) / sum(volume)."""
    if not prices or len(prices) != len(volumes):
        raise ValueError("prices and volumes must be non-empty and same length")
    total_vol = sum(volumes)
    if total_vol <= 0:
        raise ValueError("total volume must be positive")
    return sum(p * v for p, v in zip(prices, volumes)) / total_vol


def read_direction(today_open: float, prior_close: float,
                    current_price: float, vwap: float) -> DirectionRead:
    gap = classify_gap(today_open, prior_close)
    vwap_pos = classify_vwap_position(current_price, vwap)

    if gap == GapDirection.BULLISH and vwap_pos == VwapPosition.ABOVE:
        signal = DirectionSignal.BULLISH_CONFIRMED
    elif gap == GapDirection.BEARISH and vwap_pos == VwapPosition.BELOW:
        signal = DirectionSignal.BEARISH_CONFIRMED
    else:
        # Bullish gap + below VWAP, bearish gap + above VWAP, or flat gap
        signal = DirectionSignal.WAIT

    return DirectionRead(
        gap_direction=gap,
        vwap_position=vwap_pos,
        signal=signal,
        gap_points=round(today_open - prior_close, 2),
        distance_to_vwap=round(current_price - vwap, 2),
    )
