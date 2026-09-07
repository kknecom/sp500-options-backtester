"""
Central configuration for the SPX credit-spread backtester.
Tune these values before running a backtest or the live collector.
"""

# --- Underlying / market assumptions ---
RISK_FREE_RATE = 0.045       # annualized, used in Black-Scholes
DIVIDEND_YIELD = 0.013       # SPX approx dividend yield drag (index options are cash-settled, European)
TRADING_DAYS_PER_YEAR = 252

# --- Strategy parameters (shared defaults, override per-strategy if needed) ---
TARGET_DTE = 45              # days to expiration at entry
MIN_DTE_EXIT = 21            # force-close/roll when DTE drops below this
SHORT_LEG_TARGET_DELTA = 0.30   # magnitude of delta for the short strike (e.g. sell ~30-delta put/call)
LONG_LEG_TARGET_DELTA = 0.10    # magnitude of delta for the protective long strike
STRIKE_INCREMENT = 5            # SPX strikes traded in $5 increments (index points)

PROFIT_TARGET_PCT = 0.50     # close when 50% of max credit is captured
STOP_LOSS_MULT = 2.0         # close when loss reaches 2x the credit received

CONTRACTS_PER_TRADE = 1
ENTRY_FREQUENCY_DAYS = 7     # open a new trade every N trading days (only one open position per strategy at a time in this scaffold)

# --- Synthetic pricer (used when no real option chain is available) ---
# Realized volatility (annualized stdev of log returns) is computed on a
# trailing window and used as a flat IV proxy across all strikes. This has
# no skew/smile and will NOT match real SPX option prices. It exists only
# to let the backtest engine run end-to-end before real chain data (from
# Tiger Open API or a paid vendor) is wired in.
REALIZED_VOL_WINDOW = 20
IV_SKEW_ADJUSTMENT = 0.0     # placeholder: add a constant vol bump for OTM puts to crudely mimic skew

# --- Database ---
DB_PATH = "db/backtester.db"

# --- Go/No-Go event gate (strategy_logic/gate.py) ---
EVENT_BLACKOUT_LOOKAHEAD_DAYS = 1   # block today's trade if a high-impact event lands today or tomorrow
EVENT_BLACKOUT_LOOKBACK_DAYS = 0    # also block for N days AFTER an event has already passed (0 = off)
VIX_MAX_FOR_ENTRY = 35.0         # crisis-regime vol -- gate flags it, does not hard-block by itself
VIX_MIN_FOR_ENTRY = 10.0         # complacency floor -- premium likely too thin to be worth the risk

# --- Real intraday direction inputs (strategy_logic/direction_matrix.py via
# collectors/moomoo_daily_collector.fetch_real_direction_inputs) ---
DIRECTION_PROXY_SYMBOL = "SPY"   # SPX/VIX reject moomoo's intraday kline the same way they reject
                                  # get_market_snapshot/request_history_kline (see moomoo_daily_collector.py) --
                                  # SPY is the same practical proxy already used for daily bars/GEX-spot elsewhere
                                  # in this project. Gap direction and VWAP-position are scale-invariant, so a
                                  # 1/10th-scale, near-perfectly-correlated proxy gives the same signal for free.
DIRECTION_INTRADAY_BARS = 390    # ~1 full regular session of 1-minute bars
