"""
Idempotent schema migration: brings an EXISTING database file up to date
with schema.sql, for column/key changes that plain
`CREATE TABLE IF NOT EXISTS` (see init_db.py) can't apply to a table that
already exists with an older shape. Safe to run repeatedly -- every step
checks the current schema before touching it. Also invoked automatically
by init_db.py, so a single `python db/init_db.py` handles both a brand
new database and bringing an existing one up to date.

Three migrations as of the Tiger trade-history sync:
  1. option_daily_bar: add bid/ask/gamma/theta/vega columns (populated by
     collect_daily_snapshot.py's moomoo chain snapshots -- older sources
     like Tiger's OHLCV-only bars leave them NULL).
  2. underlying_daily: change PRIMARY KEY from (trade_date) alone to
     (trade_date, symbol), so SPX and VIX rows for the same day can
     coexist. SQLite can't ALTER a PRIMARY KEY in place, so this rebuilds
     the table (copying over any existing rows) if it isn't already right.
  3. trades: add source_order_ids column -- the dedup key
     run_trade_history_sync.py needs so a repeated/overlapping sync window
     never inserts the same closed Tiger trade twice (see
     tiger_trade_history_collector.py and schema.sql's comment on the
     column). Existing live:tiger rows (synced before this column existed)
     have their order ids backfilled from the notes text written at the
     time (every row's notes already end in "order_ids=[...]" -- see
     write_trades), so dedup covers trades synced before this fix too.
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))
import config


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_missing_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = _columns(conn, table)
    for name, coltype in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}")
            print(f"  added {table}.{name} ({coltype})")


def _underlying_daily_needs_rebuild(conn: sqlite3.Connection) -> bool:
    pk_cols = [row[1] for row in conn.execute("PRAGMA table_info(underlying_daily)").fetchall() if row[5] > 0]
    return pk_cols != ["trade_date", "symbol"]


def _rebuild_underlying_daily(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE underlying_daily_new (
            trade_date TEXT NOT NULL, symbol TEXT NOT NULL, close REAL NOT NULL,
            open REAL, high REAL, low REAL, volume INTEGER,
            PRIMARY KEY (trade_date, symbol)
        )
    """)
    cur = conn.execute(
        "INSERT INTO underlying_daily_new SELECT trade_date, symbol, close, open, high, low, volume "
        "FROM underlying_daily"
    )
    n = cur.rowcount
    conn.execute("DROP TABLE underlying_daily")
    conn.execute("ALTER TABLE underlying_daily_new RENAME TO underlying_daily")
    print(f"  rebuilt underlying_daily with (trade_date, symbol) primary key, carried over {n} row(s)")


def _backfill_trade_order_ids(conn: sqlite3.Connection) -> None:
    """For live:tiger rows synced before source_order_ids existed, recover the
    order ids from the notes text (every row written by write_trades ends in
    "order_ids=[123, 456]") rather than leaving them NULL and un-dedupable."""
    import re
    rows = conn.execute(
        "SELECT trade_id, notes FROM trades WHERE source='live:tiger' AND source_order_ids IS NULL"
    ).fetchall()
    n = 0
    for trade_id, notes in rows:
        m = re.search(r"order_ids=(\[[^\]]*\])", notes or "")
        if not m:
            continue
        ids = [int(x) for x in re.findall(r"\d+", m.group(1))]
        if not ids:
            continue
        conn.execute("UPDATE trades SET source_order_ids = ? WHERE trade_id = ?",
                     (json.dumps(ids), trade_id))
        n += 1
    if n:
        print(f"  backfilled source_order_ids for {n} pre-existing live:tiger trade(s) from notes")


def migrate(db_path: str = config.DB_PATH) -> None:
    if not Path(db_path).exists():
        return  # nothing to migrate -- init_db.py's executescript will create it fresh
    conn = sqlite3.connect(db_path)
    print(f"Migrating {db_path}...")
    _add_missing_columns(conn, "option_daily_bar", {
        "bid": "REAL", "ask": "REAL", "gamma": "REAL", "theta": "REAL", "vega": "REAL",
    })
    if _underlying_daily_needs_rebuild(conn):
        _rebuild_underlying_daily(conn)
    else:
        print("  underlying_daily already has the right primary key")
    _add_missing_columns(conn, "trades", {"source_order_ids": "TEXT"})
    _backfill_trade_order_ids(conn)
    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
