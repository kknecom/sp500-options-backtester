"""
GEX-by-strike and Put Wall / Call Wall detection.

IMPORTANT PROVENANCE NOTE: the course (Classes #04-#05) repeatedly refers
to "GEX," "Put Wall," and "Call Wall" as inputs to strike selection, but
never once gives a formula for computing them from data. What's below is
the standard, widely-published retail approximation used across the
options-flow community (SqueezeMetrics-style dealer gamma exposure) --
NOT something extracted from the course PDFs. Treat it as a reasonable
stand-in the course gestures at but doesn't define, not as verified
course content.

Standard per-strike dealer gamma exposure:

    GEX(K) = OI(K) * gamma(K) * S^2 * 0.01 * contract_multiplier * sign

Convention used here (the common retail one, not universal):
  - Dealers are modeled as long gamma on calls, short gamma on puts.
  - Call GEX contributes POSITIVELY (dealers buy dips / sell rips -> support/resistance dampening)
  - Put GEX contributes NEGATIVELY.
  - Net GEX(K) = call_GEX(K) - put_GEX(K)

Put Wall  = strike with the largest put-side notional gamma concentration
            below (or near) spot -> candidate SUPPORT level.
Call Wall = strike with the largest call-side notional gamma concentration
            above (or near) spot -> candidate RESISTANCE level.

This requires real open interest per strike. Until the Tiger daily
collector (collectors/tiger_daily_collector.py) is wired up with live
chain data, feed this module a synthetic/estimated OI curve -- see
`estimate_oi_proxy()` for a crude placeholder that concentrates OI near
round numbers and near-the-money, purely so the pipeline runs end-to-end.
Do not trust wall levels produced from the proxy OI for real trading
decisions.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from pricing.black_scholes import price_option

CONTRACT_MULTIPLIER = 100


@dataclass
class StrikeGex:
    strike: float
    call_oi: float
    put_oi: float
    call_gamma: float
    put_gamma: float
    call_gex: float
    put_gex: float
    net_gex: float


@dataclass
class WallLevels:
    put_wall: float | None       # candidate support strike
    call_wall: float | None      # candidate resistance strike
    by_strike: list[StrikeGex]


def compute_gex_by_strike(S: float, T: float, r: float, q: float, sigma: float,
                           strikes: list[float],
                           call_oi_by_strike: dict[float, float],
                           put_oi_by_strike: dict[float, float]) -> list[StrikeGex]:
    """
    S, T, r, q, sigma: same Black-Scholes inputs used elsewhere in this
    project (see pricing/black_scholes.py). OI dicts keyed by strike.
    """
    rows = []
    for K in strikes:
        call_q = price_option(S, K, T, r, q, sigma, "C")
        put_q = price_option(S, K, T, r, q, sigma, "P")
        call_oi = call_oi_by_strike.get(K, 0.0)
        put_oi = put_oi_by_strike.get(K, 0.0)

        call_gex = call_oi * call_q.gamma * (S ** 2) * 0.01 * CONTRACT_MULTIPLIER
        put_gex = put_oi * put_q.gamma * (S ** 2) * 0.01 * CONTRACT_MULTIPLIER
        net_gex = call_gex - put_gex

        rows.append(StrikeGex(
            strike=K, call_oi=call_oi, put_oi=put_oi,
            call_gamma=call_q.gamma, put_gamma=put_q.gamma,
            call_gex=call_gex, put_gex=put_gex, net_gex=net_gex,
        ))
    return rows


def find_walls(rows: list[StrikeGex], spot: float) -> WallLevels:
    """
    Put Wall: strike at/below spot with the largest put_gex magnitude.
    Call Wall: strike at/above spot with the largest call_gex magnitude.
    Falls back to None if no strikes exist on the relevant side.
    """
    below = [r for r in rows if r.strike <= spot]
    above = [r for r in rows if r.strike >= spot]

    put_wall = max(below, key=lambda r: r.put_gex).strike if below else None
    call_wall = max(above, key=lambda r: r.call_gex).strike if above else None

    return WallLevels(put_wall=put_wall, call_wall=call_wall, by_strike=rows)


def estimate_oi_proxy(strikes: list[float], spot: float,
                       round_number_increment: float = 50) -> tuple[dict, dict]:
    """
    PLACEHOLDER ONLY. Crude synthetic OI curve: concentrates weight near
    the money and near "round" strikes (e.g. multiples of 50), which is a
    loose real-world tendency but is NOT sourced from real chain data.
    Use only to smoke-test the pipeline before real OI is wired in via
    collectors/tiger_daily_collector.py.
    """
    call_oi, put_oi = {}, {}
    for K in strikes:
        distance = abs(K - spot)
        atm_weight = math.exp(-distance / (spot * 0.02))
        round_bonus = 2.0 if K % round_number_increment == 0 else 1.0
        base = 1000 * atm_weight * round_bonus
        call_oi[K] = base * (1.15 if K >= spot else 0.85)
        put_oi[K] = base * (1.15 if K <= spot else 0.85)
    return call_oi, put_oi


def find_gamma_flip(rows: list[StrikeGex]) -> float | None:
    """
    Strike where net GEX crosses from negative to positive (or vice
    versa), linearly interpolated between the two bracketing strikes.
    This is the standard "gamma flip point" -- dealer hedging flips from
    stabilizing (long gamma, above flip) to destabilizing (short gamma,
    below flip). Returns None if net GEX never changes sign across the
    strikes provided (all one side).
    """
    ordered = sorted(rows, key=lambda r: r.strike)
    for a, b in zip(ordered, ordered[1:]):
        if (a.net_gex < 0) != (b.net_gex < 0):
            if b.net_gex == a.net_gex:
                return a.strike
            frac = -a.net_gex / (b.net_gex - a.net_gex)
            return a.strike + frac * (b.strike - a.strike)
    return None


def compute_gex_from_contracts(spot: float, contracts: list[dict]) -> list[StrikeGex]:
    """
    Same GEX formula/sign convention as compute_gex_by_strike, but fed by
    REAL per-contract data instead of BSM-estimated gamma + estimate_oi_proxy().

    contracts: list of {"strike": float, "right": "C"/"P", "oi": float, "gamma": float}
    -- e.g. from collectors/moomoo_daily_collector.chain_df_to_gex_contracts(),
    which reads real open_interest and real (chain-quoted) gamma off a live
    get_market_snapshot merge. No synthetic OI, no BSM gamma re-derivation.

    Grouping is by strike: each strike accumulates call_oi/put_oi separately
    and keeps whichever gamma value moomoo reported for that side (gamma is
    per-contract, not summed across OI the way notional GEX is).
    """
    by_strike: dict[float, dict] = {}
    for c in contracts:
        row = by_strike.setdefault(
            c["strike"], {"call_oi": 0.0, "put_oi": 0.0, "call_gamma": 0.0, "put_gamma": 0.0}
        )
        if c["right"] == "C":
            row["call_oi"] += c.get("oi") or 0.0
            row["call_gamma"] = c.get("gamma") or 0.0
        elif c["right"] == "P":
            row["put_oi"] += c.get("oi") or 0.0
            row["put_gamma"] = c.get("gamma") or 0.0

    rows = []
    for K in sorted(by_strike):
        r = by_strike[K]
        call_gex = r["call_oi"] * r["call_gamma"] * (spot ** 2) * 0.01 * CONTRACT_MULTIPLIER
        put_gex = r["put_oi"] * r["put_gamma"] * (spot ** 2) * 0.01 * CONTRACT_MULTIPLIER
        rows.append(StrikeGex(
            strike=K, call_oi=r["call_oi"], put_oi=r["put_oi"],
            call_gamma=r["call_gamma"], put_gamma=r["put_gamma"],
            call_gex=call_gex, put_gex=put_gex, net_gex=call_gex - put_gex,
        ))
    return rows
