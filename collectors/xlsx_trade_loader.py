"""
Loader for real trade logs exported as .xlsx (e.g. from a signals service).
Parses the same "Sell X / Buy Y" strike format used by SPX_Backtesting.xlsx,
recomputes exact settlement P&L from strikes + close price (rather than
trusting a W/L label -- see the standalone xlsx analysis this reuses the
logic from), and writes normalized rows into the `trades` / `trade_legs`
tables so real trades sit in the same schema the synthetic backtest
engine uses. This is what turns "Track B" (forward daily collection)
from a plan into something queryable.

Expected input columns (case-sensitive, matching the source sheet):
Date, Time, Symbol, Strategy, Contracts, Risk Level, Expiration, Side,
Strikes, Side, Strike, Max Profit, Max Loss, ..., Close Price, W/L

Usage:
    python collectors/xlsx_trade_loader.py data/real/SPX_Backtesting.xlsx
"""
from __future__ import annotations
import re
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, date, timedelta

import openpyxl

sys.path.append(str(Path(__file__).resolve().parents[1]))
import config


def parse_strikes(s):
    if not s:
        return None
    m = re.search(r'Sell\s*([\d.]+)\s*/\s*Buy\s*([\d.]+)', str(s), re.IGNORECASE)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


def leg_debit_points(short_k: float, long_k: float, close: float, is_call: bool) -> tuple[float, float]:
    """Returns (debit_points, width_points) for one vertical leg at settlement."""
    width = abs(long_k - short_k)
    if is_call:
        k_short = min(short_k, long_k)
        debit = min(max(close - k_short, 0.0), width)
    else:
        k_short = max(short_k, long_k)
        debit = min(max(k_short - close, 0.0), width)
    return debit, width


def load_rows(xlsx_path: str) -> list[dict]:
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb['Sheet1']
    raw_rows = []
    for r in ws.iter_rows(min_row=3, max_row=ws.max_row):
        vals = [c.value for c in r]
        if all(v is None for v in vals):
            continue
        raw_rows.append(vals)

    out = []
    for r in raw_rows:
        (dt, tm, symbol, strategy, contracts, risk, expiration, side1, strikes1,
         side2, strikes2, max_profit, max_loss, call_dist, put_dist, current_price,
         dte_trend, st_trend, score, oi, gex, vol, close_price, wl) = r

        side1u = str(side1).strip().upper() if side1 else None
        side2u = str(side2).strip().upper() if side2 else None
        s1 = parse_strikes(strikes1)
        s2 = parse_strikes(strikes2)
        call_strikes = s1 if side1u == 'CALL' else (s2 if side2u and 'CALL' in side2u else None)
        put_strikes = s1 if side1u == 'PUT' else (s2 if side2u and 'PUT' in side2u else None)

        if close_price is None or max_profit is None or (call_strikes is None and put_strikes is None):
            continue  # skip malformed rows rather than crash the whole load

        total_debit = 0.0
        if call_strikes:
            deb, _ = leg_debit_points(call_strikes[0], call_strikes[1], close_price, True)
            total_debit += deb
        if put_strikes:
            deb, _ = leg_debit_points(put_strikes[0], put_strikes[1], close_price, False)
            total_debit += deb

        pnl_per_contract = max_profit - total_debit * 100
        contracts = contracts or 1

        strategy_norm = str(strategy).strip().upper().replace(' ', '_')
        if strategy_norm not in ('IRON_CONDOR', 'VERTICAL'):
            strategy_norm = strategy_norm  # keep as-is, unknown strategy label

        entry_date = dt.date() if isinstance(dt, datetime) else dt
        exp_date = expiration.date() if isinstance(expiration, datetime) else (expiration or entry_date)

        out.append(dict(
            strategy=strategy_norm, entry_date=entry_date, expiration_date=exp_date,
            contracts=contracts, max_profit=max_profit, max_loss=max_loss,
            close_price=close_price, call_strikes=call_strikes, put_strikes=put_strikes,
            pnl_per_contract=round(pnl_per_contract, 2),
            total_pnl=round(pnl_per_contract * contracts, 2),
            sheet_label=str(wl).strip().upper() if wl else None,
        ))
    return out


def write_trades(rows: list[dict], db_path: str = config.DB_PATH, source: str = 'real:xlsx') -> int:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    n = 0
    for row in rows:
        computed_status = 'WIN' if row['pnl_per_contract'] > 0 else ('LOSE' if row['pnl_per_contract'] < 0 else 'BREAKEVEN')
        notes = f"sheet_label={row['sheet_label']}; computed={computed_status}"
        label = row['sheet_label']
        mismatch = (label == 'WIN' and computed_status != 'WIN') or (label in ('LOSE', 'LOSS') and computed_status != 'LOSE')
        if mismatch:
            notes += "; MISMATCH vs sheet label"

        cur.execute(
            """
            INSERT INTO trades (strategy, entry_date, expiration_date, entry_credit, max_loss,
                                 contracts, underlying_entry, status, exit_date, exit_debit,
                                 underlying_exit, realized_pnl, source, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'CLOSED_EXPIRY', ?, NULL, ?, ?, ?, ?)
            """,
            (
                row['strategy'], row['entry_date'].isoformat(), row['expiration_date'].isoformat(),
                row['max_profit'], row['max_loss'], row['contracts'], row['close_price'],
                row['expiration_date'].isoformat(), row['close_price'], row['total_pnl'], source, notes,
            ),
        )
        trade_id = cur.lastrowid
        if row['call_strikes']:
            k_short, k_long = row['call_strikes']
            cur.execute("INSERT INTO trade_legs (trade_id, right, strike, side, entry_price) VALUES (?, 'C', ?, 'SHORT', 0)", (trade_id, k_short))
            cur.execute("INSERT INTO trade_legs (trade_id, right, strike, side, entry_price) VALUES (?, 'C', ?, 'LONG', 0)", (trade_id, k_long))
        if row['put_strikes']:
            k_short, k_long = row['put_strikes']
            cur.execute("INSERT INTO trade_legs (trade_id, right, strike, side, entry_price) VALUES (?, 'P', ?, 'SHORT', 0)", (trade_id, k_short))
            cur.execute("INSERT INTO trade_legs (trade_id, right, strike, side, entry_price) VALUES (?, 'P', ?, 'LONG', 0)", (trade_id, k_long))
        n += 1
    conn.commit()
    conn.close()
    return n


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else 'data/real/SPX_Backtesting.xlsx'
    rows = load_rows(path)
    count = write_trades(rows)
    print(f"Loaded {count} real trades from {path} into {config.DB_PATH} (source='real:xlsx')")
