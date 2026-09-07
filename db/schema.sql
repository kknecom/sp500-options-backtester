-- SQLite schema for the SPX credit-spread platform.
-- Start with SQLite for local development; the schema is plain enough to
-- port to Postgres later (swap AUTOINCREMENT -> SERIAL/IDENTITY) once the
-- Telegram bot phase needs concurrent read/write access.

CREATE TABLE IF NOT EXISTS underlying_daily (
    -- PRIMARY KEY is (trade_date, symbol) so SPX and VIX rows for the same
    -- day can coexist -- trade_date alone was under-specified for that.
    trade_date      TEXT NOT NULL,      -- ISO date
    symbol          TEXT NOT NULL,      -- e.g. 'SPX', 'VIX'
    close           REAL NOT NULL,
    open            REAL,
    high            REAL,
    low             REAL,
    volume          INTEGER,
    PRIMARY KEY (trade_date, symbol)
);

CREATE TABLE IF NOT EXISTS option_daily_bar (
    -- One row per option contract per day. This is what the Tiger/moomoo
    -- daily collectors write to, and what a bulk historical vendor import
    -- would also populate if you later add one.
    --
    -- bid/ask/gamma/theta/vega added for the moomoo daily chain-snapshot
    -- collector (collect_daily_snapshot.py) -- a live snapshot has real
    -- bid/ask and full greeks, not just a single close+delta. Nullable so
    -- older sources (e.g. Tiger's OHLCV-only bars) that don't populate
    -- them keep working unchanged. See db/init_db.py for the migration
    -- that adds these columns to an existing database file.
    trade_date      TEXT NOT NULL,
    underlying      TEXT NOT NULL,      -- 'SPX'
    expiration      TEXT NOT NULL,      -- ISO date
    strike          REAL NOT NULL,
    right           TEXT NOT NULL,      -- 'P' or 'C'
    open            REAL,
    high            REAL,
    low             REAL,
    close           REAL,
    volume          INTEGER,
    open_interest   INTEGER,
    iv              REAL,               -- implied vol if available from source
    delta           REAL,
    bid             REAL,
    ask             REAL,
    gamma           REAL,
    theta           REAL,
    vega            REAL,
    source          TEXT NOT NULL,      -- 'tiger_api' | 'moomoo_api' | 'synthetic' | 'vendor:<name>'
    PRIMARY KEY (trade_date, underlying, expiration, strike, right, source)
);

CREATE TABLE IF NOT EXISTS trades (
    trade_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy        TEXT NOT NULL,       -- bull_put_spread | bear_call_spread | iron_condor
    entry_date      TEXT NOT NULL,
    expiration_date TEXT NOT NULL,
    entry_credit    REAL NOT NULL,
    max_loss        REAL NOT NULL,
    contracts       INTEGER NOT NULL DEFAULT 1,
    underlying_entry REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'OPEN',
    exit_date       TEXT,
    exit_debit      REAL,
    underlying_exit REAL,
    realized_pnl    REAL,
    source          TEXT NOT NULL DEFAULT 'backtest',  -- 'backtest' | 'live'
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS trade_legs (
    leg_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        INTEGER NOT NULL REFERENCES trades(trade_id),
    right           TEXT NOT NULL,      -- 'P' or 'C'
    strike          REAL NOT NULL,
    side            TEXT NOT NULL,      -- 'SHORT' or 'LONG'
    entry_price     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    -- Every time run_backtest.py executes, log the config used so results
    -- are reproducible and comparable across parameter tweaks (avoids the
    -- "which config produced this number" problem when iterating).
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at          TEXT NOT NULL,
    strategy        TEXT NOT NULL,
    config_json     TEXT NOT NULL,
    trades          INTEGER,
    win_rate        REAL,
    total_pnl       REAL,
    max_drawdown    REAL,
    sharpe_per_trade REAL
);
