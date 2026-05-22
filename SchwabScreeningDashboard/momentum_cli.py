#!/usr/bin/env python3
"""
momentum_cli.py — Command-line momentum screener for the S&P 500.

Outputs:
  - momentum_YYYYMMDD_HHMMSS.csv  (full ranked table)
  - momentum_YYYYMMDD_HHMMSS.txt  (human-readable summary report)

Usage:
  python momentum_cli.py                    # Full S&P 500 scan
  python momentum_cli.py AAPL MSFT NVDA     # Custom ticker list
  python momentum_cli.py --top 25           # Show top 25 in console (default 10)
"""

import argparse
import sys
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

import momentum as mom


def progress(i, total, ticker):
    pct = i / total * 100
    bar_len = 30
    filled = int(bar_len * i / total)
    bar = "█" * filled + "░" * (bar_len - filled)
    sys.stdout.write(f"\r[{bar}] {pct:5.1f}%  {i}/{total}  {ticker:<8s}")
    sys.stdout.flush()


def fmt_pct(x):
    return f"{x*100:+.1f}%" if x is not None and x == x else "  n/a"


def write_summary(df, path):
    """Write a human-readable summary report."""
    with open(path, "w", encoding="utf-8") as f:
        f.write("="*78 + "\n")
        f.write(f"  MOMENTUM SCREEN — {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        f.write(f"  Universe: {len(df)} stocks analyzed\n")
        f.write("="*78 + "\n\n")

        # Top 10
        f.write("─── TOP 10 BY COMPOSITE MOMENTUM SCORE ────────────────────────────────\n\n")
        top10 = df.head(10)
        for _, r in top10.iterrows():
            f.write(f"  #{r['rank']:<3} {r['ticker']:<6}  Score: {r['composite_score']:5.1f}  ({r['classification']})\n")
            f.write(f"        21d:{fmt_pct(r['ret_21'])}  42d:{fmt_pct(r['ret_42'])}  63d:{fmt_pct(r['ret_63'])}  vs SPY:{fmt_pct(r['vs_spy_63'])}\n")
            f.write(f"        Slope(ann):{r['reg_slope']:+.2f}  R²:{r['reg_r2']:.2f}  t-stat:{r['reg_tstat']:.1f}  Sharpe:{r['sharpe_63']:.2f}\n")
            f.write(f"        RSI:{r['rsi_14']:.0f}  MACD:{r['macd']}  MaxDD:{fmt_pct(r['max_dd_63'])}  Flags:{r['flags'] or '—'}\n\n")

        # Top 5 risk-adjusted
        f.write("─── TOP 5 RISK-ADJUSTED QUALITY (Sharpe-style) ────────────────────────\n")
        rq = df.sort_values("sharpe_63", ascending=False).head(5)
        for _, r in rq.iterrows():
            f.write(f"  {r['ticker']:<6}  Sharpe:{r['sharpe_63']:.2f}  Sortino:{r['sortino_63']:.2f}  Vol:{r['vol_63']*100:.0f}%  Score:{r['composite_score']:.1f}\n")
        f.write("\n")

        # Top 5 vs SPY
        f.write("─── TOP 5 RELATIVE STRENGTH vs SPY (63d) ──────────────────────────────\n")
        rs = df.sort_values("vs_spy_63", ascending=False).head(5)
        for _, r in rs.iterrows():
            f.write(f"  {r['ticker']:<6}  vs SPY:{fmt_pct(r['vs_spy_63'])}  RS slope:{r['rs_slope_63']:+.4f}  Score:{r['composite_score']:.1f}\n")
        f.write("\n")

        # Top 5 trend persistence
        f.write("─── TOP 5 TREND PERSISTENCE (highest R² with positive slope) ──────────\n")
        tp = df[df["reg_slope"] > 0].sort_values("reg_r2", ascending=False).head(5)
        for _, r in tp.iterrows():
            f.write(f"  {r['ticker']:<6}  R²:{r['reg_r2']:.2f}  t-stat:{r['reg_tstat']:.1f}  63d:{fmt_pct(r['ret_63'])}\n")
        f.write("\n")

        # Overextended
        oe = df[df["overextended"]]
        f.write(f"─── POSSIBLY OVEREXTENDED ({len(oe)} flagged) ────────────────────────────\n")
        for _, r in oe.head(10).iterrows():
            f.write(f"  {r['ticker']:<6}  RSI:{r['rsi_14']:.0f}  Flags:{r['flags']}\n")
        if oe.empty:
            f.write("  (none)\n")
        f.write("\n")

        # Gap-driven
        gd = df[df["single_day_pct"] > 0.5]
        f.write(f"─── GAP-DRIVEN / STATISTICALLY WEAK ({len(gd)}) ───────────────────────\n")
        for _, r in gd.head(10).iterrows():
            f.write(f"  {r['ticker']:<6}  Single-day share of 63d log-return: {r['single_day_pct']*100:.0f}%\n")
        if gd.empty:
            f.write("  (none)\n")
        f.write("\n")

        # Detailed top 10 recommendations
        f.write("="*78 + "\n")
        f.write("  TOP 10 — DETAILED ANALYST NOTES\n")
        f.write("="*78 + "\n\n")
        for _, r in top10.iterrows():
            why = []
            if r["ret_63"] > 0.15: why.append(f"strong 63d return of {r['ret_63']*100:.1f}%")
            if r["vs_spy_63"] > 0.05: why.append(f"outperforming SPY by {r['vs_spy_63']*100:.1f}pp")
            if r["reg_r2"] > 0.7: why.append(f"persistent trend (R²={r['reg_r2']:.2f})")
            if r["reg_tstat"] > 3: why.append(f"statistically meaningful slope (t={r['reg_tstat']:.1f})")
            if r["sharpe_63"] > 1.5: why.append(f"strong risk-adjusted return (Sharpe={r['sharpe_63']:.2f})")

            risks = []
            if r["rsi_14"] > 75: risks.append("RSI elevated above 75")
            if r["max_dd_63"] < -0.15: risks.append(f"recent drawdown of {r['max_dd_63']*100:.1f}%")
            if r["overextended"]: risks.append(f"flagged overextended ({r['flags']})")
            if r["vol_63"] > 0.5: risks.append(f"high volatility ({r['vol_63']*100:.0f}%)")

            persistence = "persistent"
            if r["ret_21"] < r["ret_63"] / 3 * 0.6:
                persistence = "fading"
            elif r["ret_21"] > r["ret_63"] / 3 * 1.4:
                persistence = "accelerating"
            if r["overextended"]:
                persistence = "overextended"

            f.write(f"#{r['rank']} {r['ticker']}  (Score {r['composite_score']:.1f}, {r['classification']})\n")
            f.write(f"  Why it ranks: {'; '.join(why) if why else 'composite of multiple weaker positive factors'}\n")
            f.write(f"  Stats: 63d {fmt_pct(r['ret_63'])}, vs SPY {fmt_pct(r['vs_spy_63'])}, "
                    f"slope(ann) {r['reg_slope']:+.2f}, R² {r['reg_r2']:.2f}, t {r['reg_tstat']:.1f}\n")
            f.write(f"  Technicals: RSI {r['rsi_14']:.0f}, MACD {r['macd']}, "
                    f"MA20:{'✓' if r['above_ma20'] else '✗'} MA50:{'✓' if r['above_ma50'] else '✗'}, "
                    f"vol/20d {r['vol_vs_20']:.1f}x\n")
            f.write(f"  Risks: {'; '.join(risks) if risks else 'no major red flags identified'}\n")
            f.write(f"  Status: {persistence}\n\n")

        f.write("="*78 + "\n")
        f.write("  DISCLAIMER: Output is statistical analysis only. Not investment advice.\n")
        f.write("              Verify all data and conduct independent due diligence.\n")
        f.write("="*78 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Momentum screener CLI")
    parser.add_argument("tickers", nargs="*", help="Optional ticker list (default: S&P 500)")
    parser.add_argument("--top", type=int, default=10, help="Number of top stocks to print")
    parser.add_argument("--days", type=int, default=130, help="History days to fetch")
    args = parser.parse_args()

    if args.tickers:
        symbols = [t.upper() for t in args.tickers]
        print(f"Scanning {len(symbols)} custom tickers...")
    else:
        symbols = mom.load_sp500_tickers()
        print(f"Loaded {len(symbols)} S&P 500 tickers")

    print(f"Fetching data (this may take 5-15 min for full S&P 500)...\n")
    df = mom.run_screen(symbols, days=args.days, progress_cb=progress)
    print()  # newline after progress bar

    if df.empty:
        print("\nNo results — check data sources and OAuth token.")
        sys.exit(1)

    ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path  = f"momentum_{ts}.csv"
    txt_path  = f"momentum_{ts}.txt"

    df.to_csv(csv_path, index=False)
    write_summary(df, txt_path)

    # Append to persistent Excel workbook
    try:
        import excel_export
        xl_path = excel_export.append_scan(df)
        print(f"✓ Excel updated:   {xl_path}")
    except Exception as e:
        print(f"⚠ Excel export failed: {e}")

    print(f"\n✓ Analyzed {len(df)} stocks")
    print(f"✓ CSV written:     {csv_path}")
    print(f"✓ Summary written: {txt_path}\n")

    # Print top N to console
    print(f"─── TOP {args.top} ───────────────────────────────────────────────────")
    print(f"{'#':<4}{'Ticker':<8}{'Score':<8}{'21d':<9}{'63d':<9}{'vs SPY':<10}{'Class'}")
    for _, r in df.head(args.top).iterrows():
        print(f"{r['rank']:<4}{r['ticker']:<8}{r['composite_score']:<8.1f}"
              f"{fmt_pct(r['ret_21']):<9}{fmt_pct(r['ret_63']):<9}"
              f"{fmt_pct(r['vs_spy_63']):<10}{r['classification']}")


if __name__ == "__main__":
    main()
