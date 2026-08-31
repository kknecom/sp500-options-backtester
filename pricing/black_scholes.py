"""
Black-Scholes-Merton pricer for European options (SPX index options are
cash-settled and European-exercise, so BSM applies directly -- no early
exercise adjustment needed, unlike SPY equity options).

This is a SYNTHETIC pricer: given an underlying price and a flat
volatility input, it prices/greeks any strike. It does not know about
real bid/ask spreads, volatility skew, or open interest. Use it for
first-pass strategy validation; swap in real chain data (via the Tiger
collector) once enough history has accumulated.

Performance note: `build_synthetic_chain` prices the *entire* strike grid
in one vectorized NumPy pass instead of looping `price_option` once per
strike/right. Calling scipy.stats.norm.cdf/pdf per-scalar carries heavy
per-call overhead (argument validation, broadcasting machinery) -- with
~750+ strikes x2 rights rebuilt on every backtest day, that overhead
dominated runtime (~0.5s/chain, i.e. minutes for a single 50-day/3-strategy
run and effectively unusable for a multi-year real backtest). Batching the
whole strike array through one norm.cdf/pdf call each cuts that by roughly
two orders of magnitude. `price_option` (single-strike) is unchanged in
behavior/signature for existing single-quote callers (e.g. gex_walls.py)
but now uses math.erf directly instead of scipy, which is also faster for
one-off scalar calls and drops the scipy dependency from that path.
"""
from __future__ import annotations
import math
from dataclasses import dataclass

import numpy as np
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


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(S: float, K: float, T: float, r: float, q: float, sigma: float):
    if T <= 0 or sigma <= 0:
        raise ValueError("T and sigma must be positive")
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


def price_option(S: float, K: float, T: float, r: float, q: float, sigma: float, right: str) -> OptionQuote:
    """
    Single-strike pricer, used where only one quote is needed (e.g.
    strategy_logic/gex_walls.py). For a full chain, use
    build_synthetic_chain instead -- it's vectorized and much faster for
    many strikes.

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
        price = S * disc_q * _norm_cdf(d1) - K * disc_r * _norm_cdf(d2)
        delta = disc_q * _norm_cdf(d1)
        theta = (
            -(S * disc_q * _norm_pdf(d1) * sigma) / (2 * math.sqrt(T))
            - r * K * disc_r * _norm_cdf(d2)
            + q * S * disc_q * _norm_cdf(d1)
        ) / 365.0
    elif right.upper() == "P":
        price = K * disc_r * _norm_cdf(-d2) - S * disc_q * _norm_cdf(-d1)
        delta = -disc_q * _norm_cdf(-d1)
        theta = (
            -(S * disc_q * _norm_pdf(d1) * sigma) / (2 * math.sqrt(T))
            + r * K * disc_r * _norm_cdf(-d2)
            - q * S * disc_q * _norm_cdf(-d1)
        ) / 365.0
    else:
        raise ValueError("right must be 'P' or 'C'")

    gamma = (disc_q * _norm_pdf(d1)) / (S * sigma * math.sqrt(T))
    vega = S * disc_q * _norm_pdf(d1) * math.sqrt(T) / 100.0  # per 1 vol point

    return OptionQuote(strike=K, right=right.upper(), price=max(price, 0.0),
                        delta=delta, gamma=gamma, theta=theta, vega=vega)


def build_synthetic_chain(S: float, T: float, r: float, q: float, sigma: float,
                           strike_increment: float, width_pct: float = 0.15) -> list[OptionQuote]:
    """
    Generate a synthetic strip of puts and calls around the current spot,
    spanning +/- width_pct of S, spaced by strike_increment. This stands
    in for a real option chain snapshot.

    Vectorized: computes price/delta/gamma/theta/vega for the whole strike
    grid (both rights) via NumPy array ops and one scipy.stats.norm.cdf /
    .pdf call per array, instead of looping price_option per strike. See
    module docstring for why this matters.
    """
    if T <= 0 or sigma <= 0:
        raise ValueError("T and sigma must be positive")

    low = S * (1 - width_pct)
    high = S * (1 + width_pct)
    k0 = math.floor(low / strike_increment) * strike_increment
    n = int(math.floor((high - k0) / strike_increment)) + 1
    strikes = k0 + strike_increment * np.arange(n, dtype=np.float64)

    sqrt_T = math.sqrt(T)
    disc_q = math.exp(-q * T)
    disc_r = math.exp(-r * T)

    d1 = (np.log(S / strikes) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    Nd1, Nd2 = norm.cdf(d1), norm.cdf(d2)
    Nnd1, Nnd2 = norm.cdf(-d1), norm.cdf(-d2)
    pdf_d1 = norm.pdf(d1)

    call_price = np.maximum(S * disc_q * Nd1 - strikes * disc_r * Nd2, 0.0)
    call_delta = disc_q * Nd1
    call_theta = (
        -(S * disc_q * pdf_d1 * sigma) / (2 * sqrt_T)
        - r * strikes * disc_r * Nd2
        + q * S * disc_q * Nd1
    ) / 365.0

    put_price = np.maximum(strikes * disc_r * Nnd2 - S * disc_q * Nnd1, 0.0)
    put_delta = -disc_q * Nnd1
    put_theta = (
        -(S * disc_q * pdf_d1 * sigma) / (2 * sqrt_T)
        + r * strikes * disc_r * Nnd2
        - q * S * disc_q * Nnd1
    ) / 365.0

    gamma = (disc_q * pdf_d1) / (S * sigma * sqrt_T)
    vega = S * disc_q * pdf_d1 * sqrt_T / 100.0

    chain: list[OptionQuote] = []
    for i, K in enumerate(strikes):
        Kf = float(K)
        chain.append(OptionQuote(strike=Kf, right="P", price=float(put_price[i]),
                                  delta=float(put_delta[i]), gamma=float(gamma[i]),
                                  theta=float(put_theta[i]), vega=float(vega[i])))
        chain.append(OptionQuote(strike=Kf, right="C", price=float(call_price[i]),
                                  delta=float(call_delta[i]), gamma=float(gamma[i]),
                                  theta=float(call_theta[i]), vega=float(vega[i])))
    return chain


def find_strike_by_delta(chain: list[OptionQuote], right: str, target_abs_delta: float) -> OptionQuote:
    """
    Pick the strike whose |delta| is closest to target_abs_delta, among
    options of the requested right ("P" or "C").
    """
    candidates = [q for q in chain if q.right == right.upper()]
    return min(candidates, key=lambda q: abs(abs(q.delta) - target_abs_delta))
