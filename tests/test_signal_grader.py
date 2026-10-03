import sys, os, sqlite3
from datetime import date, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_grader import grade_signals

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db", "schema.sql")


def _make_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(open(SCHEMA_PATH).read())
    conn.commit()
    conn.close()
    return db_path


def _insert_signal(db_path, trade_date, direction, short_strike, expiry_date=None):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO signal_log (trade_date, direction, tier, strategy, short_strike, long_strike,
                                 credit, max_loss, width, confirmed_count, assessed_count,
                                 checklist_json, reasons_json, spot, vwap, created_at, expiry_date)
        VALUES (?, ?, 'PRIME', 'x', ?, ?, 100, 400, 5, 4, 5, '{}', '[]', 7700, 7700, ?, ?)
        """,
        (trade_date, direction, short_strike, short_strike - 5,
         datetime.now().isoformat(), expiry_date or trade_date),
    )
    conn.commit()
    conn.close()


def test_bull_put_win_when_settle_above_short_strike(tmp_path):
    db = _make_db(tmp_path)
    _insert_signal(db, "2026-08-27", "BULLISH_CONFIRMED", 7650.0)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO underlying_daily (trade_date, symbol, close) VALUES ('2026-08-27','SPX',7740.0)")
    conn.commit(); conn.close()

    result = grade_signals(db_path=db, today=date(2026, 8, 28))
    assert result["graded"] == 1

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT hold_win, settle_price FROM signal_log").fetchone()
    conn.close()
    assert row == (1, 7740.0)


def test_bull_put_loss_when_settle_below_short_strike(tmp_path):
    db = _make_db(tmp_path)
    _insert_signal(db, "2026-08-27", "BULLISH_CONFIRMED", 7650.0)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO underlying_daily (trade_date, symbol, close) VALUES ('2026-08-27','SPX',7600.0)")
    conn.commit(); conn.close()

    grade_signals(db_path=db, today=date(2026, 8, 28))
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT hold_win FROM signal_log").fetchone()
    conn.close()
    assert row[0] == 0


def test_bear_call_win_when_settle_below_short_strike(tmp_path):
    db = _make_db(tmp_path)
    _insert_signal(db, "2026-08-27", "BEARISH_CONFIRMED", 7780.0)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO underlying_daily (trade_date, symbol, close) VALUES ('2026-08-27','SPX',7700.0)")
    conn.commit(); conn.close()

    grade_signals(db_path=db, today=date(2026, 8, 28))
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT hold_win FROM signal_log").fetchone()
    conn.close()
    assert row[0] == 1


def test_ungraded_when_no_settle_price_available(tmp_path):
    db = _make_db(tmp_path)
    _insert_signal(db, "2026-08-27", "BULLISH_CONFIRMED", 7650.0)
    # no underlying_daily row inserted at all
    result = grade_signals(db_path=db, today=date(2026, 8, 28))
    assert result["graded"] == 0
    assert result["skipped_no_price"] == 1

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT hold_win FROM signal_log").fetchone()
    conn.close()
    assert row[0] is None


def test_future_expiry_not_graded_yet(tmp_path):
    db = _make_db(tmp_path)
    _insert_signal(db, "2026-08-27", "BULLISH_CONFIRMED", 7650.0)
    result = grade_signals(db_path=db, today=date(2026, 8, 26))  # today BEFORE expiry
    assert result["graded"] == 0
    assert result["total_pending"] == 0  # query excludes future expiry_date entirely


if __name__ == "__main__":
    import subprocess
    subprocess.run(["python3", "-m", "pytest", __file__, "-v"])
