"""
Pulls CURRENT account inventory (open positions) from your real Tiger
account -- read-only, via TradeClient.get_positions(). This is a
point-in-time snapshot for the dashboard's "Inventory" tab, distinct
from tiger_trade_history_collector.py (closed trade history for the
journal): positions show what you're holding RIGHT NOW, not what
already settled.

Never places, modifies, or cancels orders. Reuses the same .env
credential loading as tiger_trade_history_collector.py (TIGER_PROPS_PATH
preferred, or explicit TIGER_ID/TIGER_ACCOUNT/TIGER_PRIVATE_KEY_PATH) --
see that module's docstring for setup.

API REFERENCE (verified against
https://quant.itigerup.com/openapi/en/python/operation/trade/accountInfo.html
and tigeropen/trade/trade_client.py source, Sept 2026):
  TradeClient.get_positions(sec_type=SecurityType.OPT, ...) returns a
  list of Position objects (tigeropen.trade.domain.position.Position):
  account, contract, quantity, average_cost, market_price, market_value,
  realized_pnl, unrealized_pnl, today_pnl, last_close_price, ...
  Default sec_type is STK -- must pass sec_type=SecurityType.OPT
  explicitly to see the option legs this platform trades.
"""
from __future__ import annotations
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from collectors.tiger_trade_history_collector import _make_trade_client, _parse_tiger_expiry


@dataclass
class NormalizedPosition:
    symbol: str
    right: str | None       # 'P' / 'C' / None for non-option positions
    strike: float | None
    expiration: str | None  # ISO date string, or None for non-option positions
    quantity: float
    average_cost: float | None
    market_price: float | None
    market_value: float | None
    unrealized_pnl: float | None
    today_pnl: float | None


def fetch_positions(trade_client, sec_type="OPT"):
    """Thin wrapper so tests can pass a fake trade_client with a canned
    get_positions() -- no live account/network needed to unit test
    normalize_positions() below."""
    from tigeropen.common.consts import SecurityType
    st = getattr(SecurityType, sec_type, sec_type)
    return trade_client.get_positions(sec_type=st) or []


def normalize_positions(positions: list) -> list[NormalizedPosition]:
    out = []
    for p in positions:
        contract = p.contract
        put_call = getattr(contract, "put_call", None) or getattr(contract, "right", None)
        strike = getattr(contract, "strike", None)
        expiry = getattr(contract, "expiry", None)
        symbol = getattr(contract, "symbol", None) or getattr(contract, "identifier", "UNKNOWN")

        right = None
        if put_call:
            right = "P" if str(put_call).upper().startswith("P") else "C"
        exp_iso = None
        if expiry:
            try:
                exp_iso = _parse_tiger_expiry(expiry).isoformat()
            except (ValueError, TypeError):
                exp_iso = None

        out.append(NormalizedPosition(
            symbol=symbol, right=right, strike=float(strike) if strike is not None else None,
            expiration=exp_iso, quantity=float(p.quantity or 0),
            average_cost=float(p.average_cost) if p.average_cost is not None else None,
            market_price=float(p.market_price) if p.market_price is not None else None,
            market_value=float(p.market_value) if p.market_value is not None else None,
            unrealized_pnl=float(p.unrealized_pnl) if p.unrealized_pnl is not None else None,
            today_pnl=float(getattr(p, "today_pnl", None)) if getattr(p, "today_pnl", None) is not None else None,
        ))
    return out


def get_inventory_snapshot(sec_type: str = "OPT") -> list[NormalizedPosition]:
    """Entry point: live-fetch + normalize in one call. Raises on missing
    credentials or a broker/network error -- callers (e.g. export_results.py)
    should catch and skip this section rather than fail the whole export,
    same pattern already used for the moomoo/yfinance/gex sections."""
    client = _make_trade_client()
    positions = fetch_positions(client, sec_type=sec_type)
    return normalize_positions(positions)


if __name__ == "__main__":
    snapshot = get_inventory_snapshot()
    if not snapshot:
        print("No open option positions.")
    for pos in snapshot:
        label = f"{pos.symbol} {pos.right}{pos.strike}" if pos.right else pos.symbol
        print(f"{label:20s} qty={pos.quantity:>6.0f}  avg_cost={pos.average_cost}  "
              f"mkt_val={pos.market_value}  unrealized_pnl={pos.unrealized_pnl}")
