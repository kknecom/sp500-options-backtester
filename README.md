# SPX Credit Spread Backtester (starter scaffold)

Backtests 3 SPX options strategies -- Bull Put Spread, Bear Call Spread,
Iron Condor -- and is structured so daily data collected via the Tiger
Open API can gradually replace the synthetic pricer with real market
data. Telegram bot I/O is a planned phase 2 (not built here).

## Quick start

```bash
pip install -r requirements.txt
python db/init_db.py                 # creates db/backtester.db
python run_backtest.py --csv data/sample/spx_proxy_sample.csv
```

## What's real vs. synthetic right now

- `data/sample/spx_proxy_sample.csv` is **~50 trading days of real SPY
  closes** (pulled Aug 2026, scaled x10 as a rough SPX-level proxy) --
  just enough to prove the engine runs on real numbers. It is **too
  short and too calm a window to mean anything as a strategy result**
  (every trade in the demo run wins -- classic "unrepresentative time
  period" pitfall, see below). Replace it with a real multi-year SPX
  daily-close CSV (`date,close` columns) before trusting any output.
- Every option price/greek in a backtest run comes from
  `pricing/black_scholes.py`, a **synthetic** Black-Scholes chain built
  from the underlying close + trailing realized volatility as a flat IV
  proxy. No skew, no bid/ask, no real quotes. This is deliberate: it
  lets the strategy logic and P&L accounting get built and tested before
  real chain data is available.

## Two-track data plan

1. **Historical backtesting (deep, approximate):** feed
   `run_backtest.py` a real multi-year SPX daily-close series (plus
   ideally VIX as a better IV proxy than realized vol -- swap into
   `backtest/engine.py::_trailing_realized_vol`). Good for testing
   strategy rules across multiple volatility regimes (2018, 2020, 2022)
   without needing real historical option chains, which are expensive
   and hard to source in bulk.
2. **Forward daily collection (shallow, exact):** `collectors/
   tiger_daily_collector.py` is a stub for pulling **today's** relevant
   SPX strikes via the Tiger Open API and writing them to
   `option_daily_bar` in the DB every day. Tiger's historical options
   quota (10-200 unique symbols, see the module docstring) is far too
   small to backtest years of history in bulk, but it's plenty for
   collecting a handful of strikes once a day. After a year of daily
   collection you'll have a real (if narrow) proprietary chain history
   to backtest against -- far more trustworthy than the synthetic
   pricer.

## Project layout

```
config.py                    tunable strategy/pricing parameters
pricing/black_scholes.py     synthetic option pricer + delta-targeted strike picker
strategies/                  Trade/Leg data model + the 3 strategy rule sets
backtest/engine.py           daily-loop backtest runner (open/manage/close trades)
backtest/metrics.py          win rate, Sharpe, max drawdown, etc.
db/schema.sql                SQLite schema (trades, legs, option bars, backtest runs)
collectors/tiger_daily_collector.py   Tiger Open API daily collector stub
data/sample/                 demo CSV (see caveat above)
run_backtest.py              CLI entry point
```

## Backtesting pitfalls this scaffold tries to respect

(from the BetterTrader "Backtesting 101" guide reviewed alongside this project)

- **In-sample testing:** don't tune `SHORT_LEG_TARGET_DELTA` / profit
  targets against the same window you're evaluating on. Split any real
  historical run into a training period and a held-out test period.
- **Unrepresentative time period:** the bundled demo is a perfect
  negative example -- 50 calm days produced a 100% win rate. Real
  validation needs multiple full volatility cycles (2018 vol spike, 2020
  crash, 2022 bear market), not just a recent bull run.
- **Overfitting:** resist the urge to hand-tune strike deltas per
  strategy until the backtest looks perfect on one dataset -- that's
  data mining, not a validated edge.
- **Unexpected risk:** `STOP_LOSS_MULT` in `config.py` caps loss per
  trade, but a synthetic-vol backtest cannot see real gap risk (e.g. an
  overnight move through both strikes) -- treat backtested max
  drawdown as a floor, not a ceiling, on real risk.

## Phase 2 (not built yet): Telegram bot

Planned I/O split:
- **Output:** push daily trade signals / open-position status / backtest
  summaries to a Telegram channel via `python-telegram-bot`.
- **Input:** commands like `/backtest iron_condor 2y`, `/status`,
  `/positions` that trigger the same `backtest/engine.py` and
  `collectors/tiger_daily_collector.py` functions already built here --
  the bot is a thin front-end over this existing engine, not a rewrite.


## Real trade data (new)

`collectors/xlsx_trade_loader.py` loads a real trade log (matching the
"Sell X / Buy Y" strike format from a signals-service export) into the
same `trades` table the synthetic engine writes to, tagged
`source='real:xlsx'`. It recomputes exact settlement P&L from strikes +
close price rather than trusting a W/L label -- if the label disagrees
with the computed outcome, the trade is flagged `MISMATCH vs sheet
label` in its `notes` column.

```bash
python db/init_db.py
python collectors/xlsx_trade_loader.py data/real/SPX_Backtesting.xlsx
python run_real_analysis.py
```

`run_real_analysis.py` prints the same win-rate/Sharpe/drawdown summary
`backtest/metrics.py` produces for synthetic backtests, so real and
synthetic results are directly comparable -- there's exactly one metrics
implementation in the codebase, not two. Loading the bundled 36-trade
sample reproduces the standalone SPX_Trade_Log_Analysis.xlsx numbers
exactly (total P&L $10,100; Iron Condor -$840 despite an 84.6% win rate
because average losses vastly outweigh average wins; Vertical +$10,940;
2 rows flagged as label/settlement mismatches).

## Seasonality module (new)

`seasonality/` tests calendar effects (month-of-year, day-of-week,
turn-of-month, OPEX week, Sell-in-May, Santa Claus rally) against a
daily price series, following the BetterTrader Ch.4 approach: a t-test
per effect against the null of zero mean return, not just an eyeballed
average. `run_seasonality.py` also Bonferroni-corrects for the fact that
testing ~20 effects at once will produce roughly one false positive by
chance at an uncorrected 0.05 cutoff.

```bash
python run_seasonality.py --csv data/sample/spx_proxy_sample.csv
```

**This needs a long history to mean anything** -- 20-30+ years, not the
~50-day bundled sample (which exists only to prove the code runs; it
correctly finds nothing "significant," which is what noise should look
like). For a real run, pull SPX or SPY's full daily history via Tiger:
unlike options, Tiger's Day Bar data for Stocks/ETFs is stored
"Complete" (full available history) and sits under the much larger
stock/ETF quota tier, not the tight options quota that constrains the
credit-spread side of this project. Save that history as a `date,close`
CSV and point `--csv` at it. Once a long series is loaded, cross-check
any effect that looks promising against the credit-spread strategies --
e.g. does the OPEX week or Sell-in-May window correlate with lower
realized volatility, which would favor iron condors specifically over
verticals.
