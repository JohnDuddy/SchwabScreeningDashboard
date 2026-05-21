"""Composite scoring and per-candidate explanation."""

from __future__ import annotations

from ..models import TradeCandidate
from .. import config


def composite_score(tc: TradeCandidate) -> float:
    """
    Weighted average of sub-scores, then subtract event-risk penalty.
    """
    s = tc.stock
    o = tc.option
    w = config.WEIGHTS

    base = (
        w["underlying_quality"] * s.score_quality
        + w["valuation"]          * s.score_valuation
        + w["balance_sheet"]      * s.score_balance
        + w["earnings_quality"]   * s.score_earnings_quality
        + w["technical_trend"]    * s.score_technical
        + w["option_liquidity"]   * o.score_option_liquidity
        + w["premium_attract"]    * o.score_premium_attract
    )

    penalty = (s.score_event_risk / 100.0) * config.EVENT_RISK_PENALTY_MAX
    return max(0.0, base - penalty)


def classify(score: float) -> str:
    if score >= config.SCORE_ACCEPT_MIN:
        return "Strong"
    if score >= config.SCORE_OK_MIN:
        return "Accept"
    if score >= config.SCORE_WATCH_MIN:
        return "Watch"
    return "Reject"


def build_explanation(tc: TradeCandidate) -> str:
    s = tc.stock
    o = tc.option
    bullets = []

    # Quality
    if s.score_quality >= 70:
        bullets.append("strong profitability and returns on capital")
    elif s.score_quality >= 50:
        bullets.append("decent profitability")
    else:
        bullets.append("weak profitability metrics")

    # Balance sheet
    if s.score_balance >= 70:
        bullets.append("solid balance sheet")
    elif s.score_balance < 40:
        bullets.append("stretched balance sheet")

    # Valuation
    if s.score_valuation >= 70:
        bullets.append("reasonable valuation")
    elif s.score_valuation < 35:
        bullets.append("rich valuation")

    # Technicals
    if s.score_technical >= 70:
        bullets.append("constructive trend")
    elif s.score_technical < 40:
        bullets.append("weak technical setup")

    # Option liquidity
    if o.score_option_liquidity >= 70:
        bullets.append("liquid options chain")
    elif o.score_option_liquidity < 40:
        bullets.append("thin option liquidity")

    # Earnings warning
    if s.earnings_in_window:
        bullets.append("earnings during option period (HARD RISK)")
    elif s.next_earnings_date:
        bullets.append(f"next earnings {s.next_earnings_date} (outside window)")

    # The trade
    trade_line = (
        f"The {o.dte}-day ${o.strike:.0f} put offers "
        f"{o.annualized_return*100:.1f}% annualized "
        f"with a breakeven {o.discount_pct*100:.1f}% below current price"
    )

    qual = ", ".join(bullets)
    if tc.action in ("Strong", "Accept"):
        return f"{s.ticker} passes: {qual}. {trade_line}."
    elif tc.action == "Watch":
        return f"{s.ticker} watchlist: {qual}. {trade_line}."
    else:
        return f"{s.ticker} rejected: {qual}. {trade_line}."
