"""
Black-Scholes-Merton pricer for European options (SPX index options are
cash-settled and European-exercise, so BSM applies directly -- no early
exercise adjustment needed, unlike SPY equity options).

This is a SYNTHETIC pricer: given an underlying price and a flat
volatility input, it prices/greeks any strike. It does not know about
real bid/ask spreads, volatility skew, or open interest. Use it for
first-pass strategy validation; swap in real chain data (via the Tiger
collector) once enough history has accumulated.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from scipy.stats import norm


@dataclass
class OptionQuote:
    strike: float
    right: str          # "P" or "C"
    price: float
    delta: float
    gamma: float
    theta: float         # per calendar day
    vega: float


def _d1_d2(S: float, K: float, T: float, r: float, q: float, sigma: float):
    if T <= 0 or sigma <= 0:
        raise ValueError("T and sigma must be positive")
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


def price_option(S: float, K: float, T: float, r: float, q: float, sigma: float, right: str) -> OptionQuote:
    """
    S: underlying price
    K: strike
    T: time to expiration in YEARS (e.g. 30/365)
    r: risk-free rate (annualized, continuous-comp approx via cont. div model)
    q: dividend yield (annualized)
    sigma: annualized volatility (flat, no skew)
    right: "P" for put, "C" for call
    """
    d1, d2 = _d1_d2(S, K, T, r, q, sigma)
    disc_q = math.exp(-q * T)
    disc_r = math.exp(-r * T)

    if right.upper() == "C":
        price = S * disc_q * norm.cdf(d1) - K * disc_r * norm.cdf(d2)
        delta = disc_q * norm.cdf(d1)
        theta = (
            -(S * disc_q * norm.pdf(d1) * sigma) / (2 * math.sqrt(T))
            - r * K * disc_r * norm.cdf(d2)
            + q * S * disc_q * norm.cdf(d1)
        ) / 365.0
    elif right.upper() == "P":
        price = K * disc_r * norm.cdf(-d2) - S * disc_q * norm.cdf(-d1)
        delta = -disc_q * norm.cdf(-d1)
        theta = (
            -(S * disc_q * norm.pdf(d1) * sigma) / (2 * math.sqrt(T))
            + r * K * disc_r * norm.cdf(-d2)
            - q * S * disc_q * norm.cdf(-d1)
        ) / 365.0
    else:
        raise ValueError("right must be 'P' or 'C'")

    gamma = (disc_q * norm.pdf(d1)) / (S * sigma * math.sqrt(T))
    vega = S * disc_q * norm.pdf(d1) * math.sqrt(T) / 100.0  # per 1 vol point

    return OptionQuote(strike=K, right=right.upper(), price=max(price, 0.0),
                        delta=delta, gamma=gamma, theta=theta, vega=vega)


def build_synthetic_chain(S: float, T: float, r: float, q: float, sigma: float,
                           strike_increment: float, width_pct: float = 0.15) -> list[OptionQuote]:
    """
    Generate a synthetic strip of puts and calls around the current spot,
    spanning +/- width_pct of S, spaced by strike_increment. This stands
    in for a real option chain snapshot.
    """
    low = S * (1 - width_pct)
    high = S * (1 + width_pct)
    strikes = []
    k = math.floor(low / strike_increment) * strike_increment
    while k <= high:
        strikes.append(k)
        k += strike_increment

    chain = []
    for K in strikes:
        for right in ("P", "C"):
            chain.append(price_option(S, K, T, r, q, sigma, right))
    return chain


def find_strike_by_delta(chain: list[OptionQuote], right: str, target_abs_delta: float) -> OptionQuote:
    """
    Pick the strike whose |delta| is closest to target_abs_delta, among
    options of the requested right ("P" or "C").
    """
    candidates = [q for q in chain if q.right == right.upper()]
    return min(candidates, key=lambda q: abs(abs(q.delta) - target_abs_delta))
