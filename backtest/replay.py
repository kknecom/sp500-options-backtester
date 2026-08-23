"""
Pulls trades already stored in the DB (real trades loaded via
collectors/xlsx_trade_loader.py, or eventually collectors/tiger_daily_collector.py)
and feeds them through the SAME metrics.summarize() used for synthetic
backtest output, so real and synthetic results are directly comparable
and there's exactly one metrics implementation in the codebase.
"""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Optional

import config


@dataclass
class TradeRecord:
    """Minimal stand-in for strategies.base.Trade -- just enough for metrics.summarize()."""
    strategy: str
    entry_date: date
    realized_pnl: Optional[float]
    status: str
    source: str
    notes: str = ""


def load_trades(db_path: str = config.DB_PATH, source: str | None = None, strategy: str | None = None) -> list[TradeRecord]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    q = "SELECT strategy, entry_date, realized_pnl, status, source, notes FROM trades WHERE 1=1"
    params = []
    if source:
        q += " AND source = ?"
        params.append(source)
    if strategy:
        q += " AND strategy = ?"
        params.append(strategy)
    q += " ORDER BY entry_date"
    cur.execute(q, params)
    rows = cur.fetchall()
    conn.close()
    return [TradeRecord(strategy=r['strategy'], entry_date=r['entry_date'], realized_pnl=r['realized_pnl'],
                         status=r['status'], source=r['source'], notes=r['notes'] or "") for r in rows]
