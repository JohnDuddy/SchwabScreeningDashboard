"""HTML report — produces both a file and the body string for emailing."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List

from ..models import TradeCandidate
from .. import config


_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       margin: 24px; color: #1a1a1a; background: #fafafa; }
h1 { margin-bottom: 4px; }
.meta { color: #666; font-size: 13px; margin-bottom: 18px; }
.stats { display: flex; gap: 14px; margin-bottom: 22px; flex-wrap: wrap; }
.stat { background: white; padding: 10px 16px; border-radius: 8px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
.stat .label { font-size: 10px; text-transform: uppercase;
                letter-spacing: 0.5px; color: #888; }
.stat .value { font-size: 20px; font-weight: 600; margin-top: 2px; }
.legend { background: white; padding: 12px 18px; border-radius: 8px;
          margin-bottom: 18px; font-size: 12.5px;
          box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
.legend h3 { margin: 0 0 6px 0; font-size: 13px; }
.legend ul { margin: 0; padding-left: 20px; }
.legend li { margin-bottom: 3px; }
.card { background: white; border-radius: 10px; padding: 16px 20px; margin-bottom: 14px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
.card-head { display: flex; justify-content: space-between; align-items: baseline;
             border-bottom: 1px solid #eee; padding-bottom: 8px; margin-bottom: 10px; }
.rank-badge { display: inline-block; background: #2c3e50; color: white;
              border-radius: 50%; width: 28px; height: 28px; text-align: center;
              line-height: 28px; font-weight: 700; font-size: 13px; margin-right: 8px; }
.rank-badge.top { background: #d35400; }
.ticker { font-size: 18px; font-weight: 700; }
.company { color: #555; font-weight: 400; font-size: 13px; margin-left: 8px; }
.score-pill { background: #27ae60; color: white; border-radius: 12px;
              padding: 4px 10px; font-weight: 600; font-size: 13px; }
.score-pill.watch { background: #f39c12; }
.score-pill.reject { background: #c0392b; }
.metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
           gap: 10px 18px; font-size: 12.5px; margin: 8px 0; }
.metrics .m { background: #f8f9fa; padding: 6px 10px; border-radius: 6px; }
.metrics .m .l { color: #888; font-size: 10.5px; text-transform: uppercase; }
.metrics .m .v { font-weight: 600; }
.subscores { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap;
             font-size: 11.5px; }
.subscores span { background: #ecf0f1; padding: 3px 8px; border-radius: 4px; }
.explanation { font-style: italic; color: #444; font-size: 13px;
               margin-top: 8px; padding: 8px 12px;
               background: #fffbe6; border-left: 3px solid #f1c40f; }
.warning { color: #c0392b; font-weight: 600; }
.footer { margin-top: 24px; font-size: 12px; color: #888; }
table.summary { border-collapse: collapse; width: 100%; background: white;
                box-shadow: 0 1px 3px rgba(0,0,0,0.08); border-radius: 8px;
                overflow: hidden; font-size: 12px; margin-bottom: 24px; }
table.summary th { background: #2c3e50; color: white; padding: 8px;
                   text-align: left; font-weight: 600; }
table.summary td { padding: 6px 8px; border-bottom: 1px solid #eee; }
table.summary tr:nth-child(-n+5) td:first-child { font-weight: 700; color: #d35400; }
"""


def _action_class(action: str) -> str:
    if action in ("Strong", "Accept"): return ""
    if action == "Watch": return "watch"
    return "reject"


def _summary_table(top: List[TradeCandidate]) -> str:
    rows = []
    for i, tc in enumerate(top, 1):
        s, o = tc.stock, tc.option
        ann = o.annualized_return * 100
        roc = o.return_on_cash * 100
        disc = o.discount_pct * 100
        action_class = _action_class(tc.action)
        action_html = f'<span class="score-pill {action_class}">{tc.action}</span>'
        rows.append(
            f"<tr><td>{i}</td><td><b>{s.ticker}</b></td>"
            f"<td>{s.company_name[:30]}</td>"
            f"<td>{s.sector[:18]}</td>"
            f"<td>${s.price:.2f}</td>"
            f"<td>{o.expiration}</td>"
            f"<td>${o.strike:.0f}</td>"
            f"<td>${o.mid:.2f}</td>"
            f"<td>{disc:.1f}%</td>"
            f"<td>{ann:.1f}%</td>"
            f"<td>{tc.composite_score:.1f}</td>"
            f"<td>{action_html}</td></tr>"
        )
    return (
        '<table class="summary"><thead><tr>'
        "<th>#</th><th>Ticker</th><th>Company</th><th>Sector</th>"
        "<th>Price</th><th>Expiration</th><th>Strike</th><th>Mid</th>"
        "<th>Disc</th><th>Annual</th><th>Score</th><th>Action</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _candidate_card(rank: int, tc: TradeCandidate) -> str:
    s, o = tc.stock, tc.option
    badge_class = "rank-badge top" if rank <= 5 else "rank-badge"
    action_class = _action_class(tc.action)

    earnings_warn = ""
    if s.earnings_in_window:
        earnings_warn = f' <span class="warning">⚠ EARNINGS IN WINDOW ({s.next_earnings_date})</span>'
    elif s.next_earnings_date:
        earnings_warn = f' <span style="color:#888">earnings: {s.next_earnings_date}</span>'

    metrics_html_unused = ""  # placeholder removed; correct version below

    # Hand-fix the f-string conditionals (cleaner)
    delta_str = f"{o.delta:.2f}" if o.delta is not None else "n/a"
    iv_str = f"{o.iv*100:.1f}%" if o.iv is not None else "n/a"
    prob_str = f"{o.prob_otm*100:.0f}%" if o.prob_otm is not None else "n/a"

    metrics_html = f"""
    <div class="metrics">
      <div class="m"><div class="l">Strike</div><div class="v">${o.strike:.2f}</div></div>
      <div class="m"><div class="l">DTE</div><div class="v">{o.dte}</div></div>
      <div class="m"><div class="l">Mid Premium</div><div class="v">${o.mid:.2f}</div></div>
      <div class="m"><div class="l">Bid / Ask</div><div class="v">${o.bid:.2f} / ${o.ask:.2f}</div></div>
      <div class="m"><div class="l">Spread %</div><div class="v">{o.spread_pct*100:.1f}%</div></div>
      <div class="m"><div class="l">OI / Vol</div><div class="v">{o.open_interest:,} / {o.volume:,}</div></div>
      <div class="m"><div class="l">Delta</div><div class="v">{delta_str}</div></div>
      <div class="m"><div class="l">IV</div><div class="v">{iv_str}</div></div>
      <div class="m"><div class="l">Breakeven</div><div class="v">${o.breakeven:.2f}</div></div>
      <div class="m"><div class="l">Discount</div><div class="v">{o.discount_pct*100:.1f}%</div></div>
      <div class="m"><div class="l">Cash Required</div><div class="v">${o.cash_required:,.0f}</div></div>
      <div class="m"><div class="l">Premium Income</div><div class="v">${o.premium_income:,.0f}</div></div>
      <div class="m"><div class="l">Return on Cash</div><div class="v">{o.return_on_cash*100:.2f}%</div></div>
      <div class="m"><div class="l">Annualized</div><div class="v"><b>{o.annualized_return*100:.1f}%</b></div></div>
      <div class="m"><div class="l">Prob OTM</div><div class="v">{prob_str}</div></div>
      <div class="m"><div class="l">Loss @ -20%</div><div class="v">${o.loss_at_minus_20:,.0f}</div></div>
    </div>
    """

    fscore = f"F={s.piotroski_f}" if s.piotroski_f is not None else "F=n/a"
    zscore = f"Z={s.altman_z:.1f}" if s.altman_z is not None else "Z=n/a"

    return f"""
    <div class="card">
      <div class="card-head">
        <div>
          <span class="{badge_class}">{rank}</span>
          <span class="ticker">{s.ticker}</span>
          <span class="company">{s.company_name} — {s.sector}</span>
        </div>
        <div>
          <span class="score-pill {action_class}">{tc.action} • {tc.composite_score:.1f}</span>
        </div>
      </div>
      <div style="font-size: 13px; color:#555; margin-bottom: 6px;">
        Price ${s.price:.2f} • Mkt Cap ${s.market_cap/1e9:.1f}B
        • Avg Vol {s.avg_share_volume/1e6:.1f}M{earnings_warn}
      </div>
      {metrics_html}
      <div class="subscores">
        <span>Quality {s.score_quality:.0f}</span>
        <span>Valuation {s.score_valuation:.0f}</span>
        <span>Balance {s.score_balance:.0f}</span>
        <span>Earnings Q {s.score_earnings_quality:.0f}</span>
        <span>Technical {s.score_technical:.0f}</span>
        <span>Opt Liquid {o.score_option_liquidity:.0f}</span>
        <span>Premium {o.score_premium_attract:.0f}</span>
        <span>Risk {s.score_event_risk:.0f}</span>
        <span>{fscore}</span>
        <span>{zscore}</span>
      </div>
      <div class="explanation">{tc.explanation}</div>
    </div>
    """


def render_html(top: List[TradeCandidate], total_scanned: int, qualified: int,
                rejected_breakdown: dict[str, int]) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    summary = _summary_table(top) if top else "<p>No candidates passed all filters.</p>"
    cards = "\n".join(_candidate_card(i + 1, tc) for i, tc in enumerate(top))

    rej_items = "".join(
        f"<li>{reason}: {count}</li>"
        for reason, count in sorted(rejected_breakdown.items(), key=lambda x: -x[1])[:8]
    )
    rej_block = f"<div class='legend'><h3>Top rejection reasons</h3><ul>{rej_items}</ul></div>" if rej_items else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Cash-Secured Put Screener — Top {len(top)}</title>
<style>{_CSS}</style>
</head>
<body>
  <h1>Cash-Secured Put Screener</h1>
  <div class="meta">Disciplined underwriting-first ranking. Generated {timestamp}.</div>

  <div class="stats">
    <div class="stat"><div class="label">Universe</div><div class="value">{total_scanned}</div></div>
    <div class="stat"><div class="label">Qualified</div><div class="value">{qualified}</div></div>
    <div class="stat"><div class="label">Top Picks</div><div class="value">{len(top)}</div></div>
    <div class="stat"><div class="label">Min Annualized Return</div><div class="value">{config.MIN_ANNUALIZED_RETURN*100:.0f}%</div></div>
    <div class="stat"><div class="label">DTE Range</div><div class="value">{config.DTE_MIN}–{config.DTE_MAX}</div></div>
    <div class="stat"><div class="label">Delta Range</div><div class="value">{config.DELTA_MIN} to {config.DELTA_MAX}</div></div>
  </div>

  <div class="legend">
    <h3>Methodology</h3>
    <ul>
      <li>Hard filters: market cap ≥ $2B, price ≥ $10, $50M+ daily dollar volume, OI ≥ 250, vol ≥ 50, spread ≤ 10%, DTE 21–60, delta -0.15 to -0.35</li>
      <li><b>Strict mode:</b> stocks with earnings during the option period are rejected outright</li>
      <li>Composite score weights: Quality 25% • Valuation 15% • Balance 15% • Earnings Q 10% • Technical 15% • Option Liquidity 10% • Premium 10% (event risk subtracts up to 25 pts)</li>
      <li>Action: Strong ≥85, Accept 70–85, Watch 55–70, Reject &lt;55</li>
      <li>Underwriting principle: rank first by "would I own this at the breakeven price" — premium chasing comes second</li>
    </ul>
  </div>

  {rej_block}

  <h2 style="margin-top:24px;">Top {len(top)} — quick view</h2>
  {summary}

  <h2>Detailed cards</h2>
  {cards}

  <div class="footer">
    Data: Yahoo Finance via yfinance. Option bid/ask figures may be stale —
    verify in your broker before trading. This is screening output, not investment advice.
  </div>
</body>
</html>
"""


def write_html(top: List[TradeCandidate], total_scanned: int, qualified: int,
               rejected_breakdown: dict[str, int], out_dir: Path) -> tuple[Path, str]:
    html = render_html(top, total_scanned, qualified, rejected_breakdown)
    path = out_dir / "top15_report.html"
    path.write_text(html, encoding="utf-8")
    return path, html
