"""
Grades prior signal_log rows once the actual expiry-date close is known:
fills in settle_price, hold_win, graded_at.

WIN DEFINITION (this module's choice, not the course's, and a deliberate
departure from a generic "closed within +/-1sigma of entry" band): a
signal is a HOLD WIN if, at expiry, the underlying closed on the safe
side of the short strike THIS SPECIFIC SIGNAL selected --
  bull put (BULLISH_CONFIRMED):  settle_price > short_strike
  bear call (BEARISH_CONFIRMED): settle_price < short_strike
i.e. the spread would have expired at max profit. This is the outcome
that actually determines P&L for a credit spread, so it's a more
rigorous target than a generic expected-move band. expected_move_1sigma
(recorded by daily_signal_feeder.py) is kept as a companion diagnostic,
not folded into this win definition.

Rows with no candidate (WATCH from a hard-gate failure -- no short_strike
recorded) have nothing to grade and are left with hold_win = NULL
permanently: there was no trade to judge.

Requires the expiry date's actual SPX close in underlying_daily (written
by collect_daily_snapshot.py / moomoo_daily_collector.py) -- rows for
dates without that data are left ungraded rather than guessed at; rerun
this after the daily collector has run for that date.

Usage:
    python signal_grader.py
"""
from __future__ import annotations
import sqlite3
from datetime import date, datetime

import config


def _lookup_settle_price(conn: sqlite3.Connection, expiry_date: str, symbol: str = "SPX") -> float | None:
    row = conn.execute(
        "SELECT close FROM underlying_daily WHERE trade_date = ? AND symbol = ?",
        (expiry_date, symbol),
    ).fetchone()
    return row[0] if row else None


def grade_signals(db_path: str = config.DB_PATH, today: date | None = None) -> dict:
    today = today or date.today()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    ungraded = conn.execute(
        """
        SELECT signal_id, direction, short_strike, expiry_date
        FROM signal_log
        WHERE hold_win IS NULL AND expiry_date <= ? AND short_strike IS NOT NULL
        """,
        (today.isoformat(),),
    ).fetchall()

    graded, skipped_no_price = 0, 0
    for row in ungraded:
        settle_price = _lookup_settle_price(conn, row["expiry_date"])
        if settle_price is None:
            skipped_no_price += 1
            continue
        if row["direction"] == "BULLISH_CONFIRMED":
            hold_win = 1 if settle_price > row["short_strike"] else 0
        elif row["direction"] == "BEARISH_CONFIRMED":
            hold_win = 1 if settle_price < row["short_strike"] else 0
        else:
            continue  # shouldn't happen (WAIT rows have no short_strike), defensive skip

        conn.execute(
            "UPDATE signal_log SET settle_price = ?, hold_win = ?, graded_at = ? WHERE signal_id = ?",
            (settle_price, hold_win, datetime.now().isoformat(timespec="seconds"), row["signal_id"]),
        )
        graded += 1

    conn.commit()
    conn.close()
    return {"graded": graded, "skipped_no_price": skipped_no_price, "total_pending": len(ungraded)}


if __name__ == "__main__":
    result = grade_signals()
    print(f"Graded {result['graded']} signal(s). {result['skipped_no_price']} still pending "
          f"(expiry-date close not yet in underlying_daily -- rerun after the daily collector runs).")
