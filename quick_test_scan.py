"""Quick test scan — run badass screener on 20 representative tickers and cache results."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
os.chdir(os.path.dirname(__file__))

import badass_screener as bs
from momentum import fetch_history
from scan_cache import save_scan

TEST_TICKERS = [
    "AAPL", "MSFT", "NVDA", "META", "GOOGL",
    "AMZN", "TSLA", "PLTR", "AMD", "AVGO",
    "JPM", "V", "UNH", "LLY", "COST",
    "NFLX", "CRM", "NOW", "PANW", "CRWD",
]

print(f"Quick test scan on {len(TEST_TICKERS)} tickers...")

def cb(i, total, ticker):
    print(f"  [{i}/{total}] {ticker}", end="\r", flush=True)

rows, red_flag = bs.run_badass_screen(
    TEST_TICKERS,
    fetch_fn=lambda sym, days: fetch_history(sym, days),
    min_price=1.0,
    min_avg_volume=0,
    progress_cb=cb,
)

print(f"\nDone! {len(rows)} stocks scored.")
print(f"Red flag: {red_flag['regime']} (SPY {red_flag['spy_price']} vs MA200 {red_flag['ma200']}, {red_flag['pct_diff']}%)")
print("\nTop 10 results:")
for r in rows[:10]:
    print(f"  #{r['rank']:2d} {r['ticker']:<6} {r['action']:<12} score={r['composite_score']:.1f}")

save_scan("badass", {"rows": rows, "red_flag": red_flag})
print("\nCache saved to .cache_badass.json")
