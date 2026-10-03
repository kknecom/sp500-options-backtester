import sys, os, sqlite3
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors.tiger_trade_history_collector import (
    trade_from_combo_order, group_single_leg_orders, normalize_orders,
    write_trades, NormalizedLeg,
)


class FakeContract:
    def __init__(self, put_call, strike, expiry, action=None, avg_fill_price=None):
        self.put_call = put_call
        self.strike = strike
        self.expiry = expiry
        self.action = action
        self.avg_fill_price = avg_fill_price


class FakeOrder:
    def __init__(self, id, action, quantity, filled, avg_fill_price, commission,
                 trade_time, order_time=None, contract=None, contract_legs=None):
        self.id = id
        self.action = action
        self.quantity = quantity
        self.filled = filled
        self.avg_fill_price = avg_fill_price
        self.commission = commission
        self.trade_time = trade_time
        self.order_time = order_time or trade_time
        self.contract = contract
        self.contract_legs = contract_legs


def test_combo_order_becomes_one_trade():
    legs = [
        FakeContract("PUT", 7700, "20260827", action="SELL", avg_fill_price=2.10),
        FakeContract("PUT", 7695, "20260827", action="BUY", avg_fill_price=1.25),
    ]
    order = FakeOrder(id=1, action="SELL", quantity=1, filled=1, avg_fill_price=0.85,
                       commission=2.50, trade_time=1756296600000, contract_legs=legs)
    t = trade_from_combo_order(order)
    assert t is not None
    assert t.strategy == "bull_put_spread"
    assert len(t.legs) == 2
    # credit = (2.10 - 1.25) * 100 = 85, width = 5 -> max_loss = 500 - 85 = 415
    assert abs(t.entry_credit - 85.0) < 1e-6
    assert abs(t.max_loss - 415.0) < 1e-6
    assert t.commission == 2.50


def test_single_leg_orders_grouped_within_window():
    c1 = FakeContract("PUT", 7700, "20260827")
    c2 = FakeContract("PUT", 7695, "20260827")
    o1 = FakeOrder(id=10, action="SELL", quantity=1, filled=1, avg_fill_price=2.10,
                    commission=1.25, trade_time=1756296600000, contract=c1)
    o2 = FakeOrder(id=11, action="BUY", quantity=1, filled=1, avg_fill_price=1.25,
                    commission=1.25, trade_time=1756296601500, contract=c2)  # 1.5s later
    trades, ungrouped = group_single_leg_orders([o1, o2])
    assert len(ungrouped) == 0
    assert len(trades) == 1
    assert trades[0].strategy == "bull_put_spread"
    assert abs(trades[0].entry_credit - 85.0) < 1e-6


def test_single_leg_orders_outside_window_left_ungrouped():
    c1 = FakeContract("PUT", 7700, "20260827")
    c2 = FakeContract("PUT", 7695, "20260827")
    o1 = FakeOrder(id=20, action="SELL", quantity=1, filled=1, avg_fill_price=2.10,
                    commission=1.25, trade_time=1756296600000, contract=c1)
    o2 = FakeOrder(id=21, action="BUY", quantity=1, filled=1, avg_fill_price=1.25,
                    commission=1.25, trade_time=1756296600000 + 60_000, contract=c2)  # 60s later
    trades, ungrouped = group_single_leg_orders([o1, o2])
    assert len(trades) == 0
    assert len(ungrouped) == 2


def test_naked_single_leg_left_ungrouped():
    c1 = FakeContract("CALL", 7800, "20260827")
    o1 = FakeOrder(id=30, action="SELL", quantity=1, filled=1, avg_fill_price=1.50,
                    commission=1.25, trade_time=1756296600000, contract=c1)
    trades, ungrouped = group_single_leg_orders([o1])
    assert len(trades) == 0
    assert len(ungrouped) == 1


def test_write_trades_roundtrip(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    schema = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db", "schema.sql")).read()
    conn.executescript(schema)
    conn.close()

    legs = [
        FakeContract("CALL", 7780, "20260827", action="SELL", avg_fill_price=1.80),
        FakeContract("CALL", 7785, "20260827", action="BUY", avg_fill_price=1.10),
    ]
    order = FakeOrder(id=2, action="SELL", quantity=1, filled=1, avg_fill_price=0.70,
                       commission=2.50, trade_time=1756296600000, contract_legs=legs)
    trades, _ = normalize_orders([order])
    written, skipped = write_trades(trades, db_path=db_path)
    assert written == 1
    assert skipped == 0

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT strategy, source, entry_credit, source_order_ids FROM trades").fetchone()
    conn.close()
    assert row[0] == "bear_call_spread"
    assert row[1] == "live:tiger"
    assert abs(row[2] - 70.0) < 1e-6
    assert row[3] == "[2]"


def test_write_trades_is_idempotent_on_resync(tmp_path):
    """Re-running write_trades with the same (or an overlapping-window) order
    set must not duplicate rows -- this is what makes run_sync safe to call
    repeatedly, e.g. the README's default full-history (2010 -> today) sync."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    schema = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db", "schema.sql")).read()
    conn.executescript(schema)
    conn.close()

    legs = [
        FakeContract("CALL", 7780, "20260827", action="SELL", avg_fill_price=1.80),
        FakeContract("CALL", 7785, "20260827", action="BUY", avg_fill_price=1.10),
    ]
    order = FakeOrder(id=99, action="SELL", quantity=1, filled=1, avg_fill_price=0.70,
                       commission=2.50, trade_time=1756296600000, contract_legs=legs)
    trades, _ = normalize_orders([order])

    written1, skipped1 = write_trades(trades, db_path=db_path)
    assert (written1, skipped1) == (1, 0)

    # Simulate re-running the sync over an overlapping/wider date range --
    # normalize_orders is called fresh each time in real usage, but the
    # resulting NormalizedTrade for the same order id should be recognized
    # as already-synced and skipped, not duplicated.
    trades_again, _ = normalize_orders([order])
    written2, skipped2 = write_trades(trades_again, db_path=db_path)
    assert (written2, skipped2) == (0, 1)

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    conn.close()
    assert count == 1


if __name__ == "__main__":
    import subprocess
    subprocess.run(["python3", "-m", "pytest", __file__, "-v"])
