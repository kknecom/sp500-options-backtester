"""
CLI: summarize real trades already loaded into the DB (via
collectors/xlsx_trade_loader.py), overall and split by strategy --
using the exact same metrics.summarize() the synthetic backtest uses.

Usage:
    python collectors/xlsx_trade_loader.py data/real/SPX_Backtesting.xlsx
    python run_real_analysis.py
"""
from backtest.replay import load_trades
from backtest.metrics import summarize, print_summary

def main():
    all_trades = load_trades(source='real:xlsx')
    print(f"Loaded {len(all_trades)} real trades from DB (source='real:xlsx')")

    print_summary("ALL REAL TRADES (real:xlsx)", summarize(all_trades))

    mismatches = [t for t in all_trades if 'MISMATCH' in t.notes]
    if mismatches:
        print(f"\n{len(mismatches)} trade(s) where the original sheet's W/L label disagreed with "
              f"the strikes+settlement computation:")
        for t in mismatches:
            print(f"  {t.entry_date} [{t.strategy}] {t.notes}")

    for strat in sorted(set(t.strategy for t in all_trades)):
        subset = [t for t in all_trades if t.strategy == strat]
        print_summary(f"REAL: {strat}", summarize(subset))

if __name__ == "__main__":
    main()
