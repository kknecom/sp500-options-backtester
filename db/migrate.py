"""
Idempotent schema migration: brings an EXISTING database file up to date
with schema.sql, for column/key changes that plain
`CREATE TABLE IF NOT EXISTS` (see init_db.py) can't apply to a table that
already exists with an older shape. Safe to run repeatedly -- every step
checks the current schema before touching it. Also invoked automatically
by init_db.py, so a single `python db/init_db.py` handles both a brand
new database and bringing an existing one up to date.

Two migrations as of the daily chain-snapshot collector (see schema.sql's
comments for why):
  1. option_daily_bar: add bid/ask/gamma/theta/vega columns (populated by
     collect_daily_snapshot.py's moomoo chain snapshots -- older sources
     like Tiger's OHLCV-only bars leave them NULL).
  2. underlying_daily: change PRIMARY KEY from (trade_date) alone to
     (trade_date, symbol), so SPX and VIX rows for the same day can
     coexist. SQLite can't ALTER a PRIMARY KEY in place, so this rebuilds
     the table (copying over any existing rows) if it isn't already right.
"""
from __future__ import annotations
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
    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
