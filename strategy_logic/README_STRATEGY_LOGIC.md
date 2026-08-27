# strategy_logic — course-derived formulas

Codifies the GEXOptionsTrading Premium Academy course (Classes #01–#05)
into the only pieces that are actually computable. Everything else the
course describes stays qualitative on purpose — faking a formula for it
would be worse than leaving it as a manual checklist.

## What's implemented (and where it comes from)

| Module | Rule | Source | Status |
|---|---|---|---|
| `direction_matrix.py` | Gap direction × VWAP position → BULLISH_CONFIRMED / BEARISH_CONFIRMED / WAIT | Class #02, reaffirmed #04/#05 | Fully specified by the course, no free parameters |
| `gex_walls.py` | Net GEX per strike, Put Wall / Call Wall detection | **Not from the course.** Standard retail dealer-gamma-exposure formula (`OI × gamma × S² × 0.01 × 100`), included because the course names "GEX," "Put Wall," "Call Wall" repeatedly but never defines how to compute them | Needs real open interest to be meaningful — currently only has a placeholder OI proxy (`estimate_oi_proxy`) |
| `strike_selector.py` | Short strike must sit at/beyond the wall (`short_put ≤ put_wall`, `short_call ≥ call_wall`); ties broken by credit/max-loss ratio | Placement rule: Class #05 §9/10/17. Tie-break: **not from the course** — the course never says how to choose among multiple wall-protected strikes | Placement rule is course-sourced; tie-break is a reasonable default, swap it out if you want |
| `strike_selector.py` (P&L) | `max_profit = credit`, `max_loss = width×100 − credit` | Classes #03 §8, #04 §8, #05 §14 — same numbers restated three times | Matches `strategies/base.py` exactly, verified against the course's own $500/$150/$350 example |
| `checklist.py` | Explicit fields for Market Structure, Price Action, Squeeze Risk, Timing, Premium-worth-it, Event risk | Named throughout every class, never quantified | Deliberately **not** computed — exposed as manual booleans so the dashboard doesn't pretend to automate a chart read |

## What's still missing before this is trade-ready

- **Real open interest.** `gex_walls.py` needs a live chain (strike, call OI, put OI) to produce real walls. Wire `collectors/tiger_daily_collector.py` up with credentials and feed its output in instead of `estimate_oi_proxy`.
- **Real intraday price/volume bars** for `compute_vwap()` — the demo (`run_strike_selector.py`) uses hardcoded synthetic ticks.
- **The checklist stays manual.** Market Structure, Price Action quality, and Squeeze Risk are not formulas in the course and aren't faked as ones here.

## Try it

```bash
python run_strike_selector.py
python -m pytest tests/test_strategy_logic.py -v
```
