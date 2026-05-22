"""
Screening pipeline.

For each ticker:
  1. Fetch stock snapshot (with technicals + fundamentals)
  2. Apply Section A hard filters (market cap, price, volume)
  3. Apply Section E earnings filter (reject if earnings in window — strict mode)
  4. Fetch option chain
  5. Filter options per Section B (OI, vol, spread) and ranges (DTE, delta)
  6. Compute per-option metrics (cash, premium, RoC, annualized, stress losses)
  7. Score every option, pick the best one for this ticker
  8. Compute composite score + classification + explanation
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from . import config
from .data.provider import DataProvider
from .models import StockSnapshot, OptionCandidate, TradeCandidate
from .scoring import (
    quality_score, valuation_score, balance_sheet_score,
    earnings_quality_score, technical_score, event_risk_score,
    beta_risk_score,
    option_liquidity_score, premium_attractiveness_score,
    ev_score, iv_rank_score, iv_hv_premium_score,
    composite_score, classify, build_explanation,
)


def _passes_stock_filters(s: StockSnapshot) -> tuple[bool, list[str]]:
    """Section A — strict hard filters."""
    fails = []
    if s.market_cap < config.MIN_MARKET_CAP:
        fails.append(f"market_cap<${config.MIN_MARKET_CAP/1e9:.1f}B")
    if s.price < config.MIN_PRICE:
        fails.append(f"price<${config.MIN_PRICE}")
    if s.avg_share_volume < config.MIN_AVG_SHARE_VOLUME:
        fails.append(f"share_vol<{config.MIN_AVG_SHARE_VOLUME:,}")
    if s.avg_dollar_volume < config.MIN_AVG_DOLLAR_VOLUME:
        fails.append(f"dollar_vol<${config.MIN_AVG_DOLLAR_VOLUME/1e6:.0f}M")
    return (len(fails) == 0, fails)


def _check_earnings_window(s: StockSnapshot, max_dte: int) -> None:
    """Mark earnings_in_window if next earnings date is within max_dte days."""
    if not s.next_earnings_date:
        return
    try:
        edt = datetime.strptime(s.next_earnings_date, "%Y-%m-%d").date()
        today = datetime.now().date()
        days = (edt - today).days
        if 0 <= days <= max_dte:
            s.earnings_in_window = True
    except Exception:
        pass


def _check_ex_div_window(s: StockSnapshot, max_dte: int) -> None:
    if not s.ex_dividend_date:
        return
    try:
        edt = datetime.strptime(s.ex_dividend_date, "%Y-%m-%d").date()
        today = datetime.now().date()
        days = (edt - today).days
        if 0 <= days <= max_dte:
            s.ex_div_in_window = True
    except Exception:
        pass


def _option_passes_filters(o: OptionCandidate, vix_params=None) -> tuple[bool, list[str]]:
    fails = []
    if o.open_interest < config.MIN_OPEN_INTEREST:
        fails.append(f"OI<{config.MIN_OPEN_INTEREST}")
    if o.volume < config.MIN_OPTION_VOLUME:
        fails.append(f"vol<{config.MIN_OPTION_VOLUME}")
    if o.spread_pct > config.MAX_BIDASK_SPREAD_PCT:
        fails.append(f"spread>{config.MAX_BIDASK_SPREAD_PCT*100:.0f}%")

    # Use VIX-adjusted delta/DTE ranges if provided
    delta_min = vix_params["delta_min"] if vix_params else config.DELTA_MIN
    delta_max = vix_params["delta_max"] if vix_params else config.DELTA_MAX
    dte_min = vix_params["dte_min"] if vix_params else config.DTE_MIN
    dte_max = vix_params["dte_max"] if vix_params else config.DTE_MAX

    if o.delta is None:
        fails.append("no_delta")
    elif not (delta_min <= o.delta <= delta_max):
        fails.append(f"delta_out_of_range({o.delta:.2f})")

    if not (dte_min <= o.dte <= dte_max):
        fails.append(f"dte_out_of_range({o.dte})")

    return (len(fails) == 0, fails)


def _compute_option_metrics(o: OptionCandidate, spot: float) -> None:
    """Fill in breakeven, cash, premium, returns, stress losses."""
    o.breakeven = o.strike - o.mid
    o.discount_pct = (spot - o.strike) / spot if spot > 0 else 0.0
    o.cash_required = o.strike * 100
    o.premium_income = o.mid * 100
    o.return_on_cash = o.mid / o.strike if o.strike > 0 else 0.0
    if o.dte > 0:
        o.annualized_return = o.return_on_cash * 365.0 / o.dte
    o.prob_otm = (1.0 - abs(o.delta)) if o.delta is not None else None
    o.prob_assignment = abs(o.delta) if o.delta is not None else None

    # Expected value
    if o.prob_otm is not None and o.prob_assignment is not None:
        o.expected_value = (o.premium_income * o.prob_otm) - (o.loss_at_minus_20 * o.prob_assignment)

    # Stress test losses (shareholder loss if assigned at strike then stock falls)
    # Loss = (assignment_price - mkt_price) * 100 - premium_received
    # We use breakeven as effective cost basis after premium credit.
    cost_basis = o.breakeven  # what we effectively paid per share if assigned
    def loss_at(mkt_price: float) -> float:
        return max(0.0, (cost_basis - mkt_price) * 100)

    o.loss_if_zero = loss_at(0.0)
    o.loss_at_minus_10 = loss_at(spot * 0.90)
    o.loss_at_minus_20 = loss_at(spot * 0.80)
    o.loss_at_minus_30 = loss_at(spot * 0.70)
    o.loss_at_minus_50 = loss_at(spot * 0.50)

    # Recompute EV now that stress losses are available
    if o.prob_otm is not None and o.prob_assignment is not None:
        o.expected_value = (o.premium_income * o.prob_otm) - (o.loss_at_minus_20 * o.prob_assignment)


def _select_best_option(opts: List[OptionCandidate]) -> Optional[OptionCandidate]:
    """
    Among options that passed liquidity + delta filters, pick the one that
    best balances premium and option-level scores.
    """
    if not opts:
        return None
    # Score each, return max
    for o in opts:
        o.score_option_liquidity = option_liquidity_score(o)
        o.score_premium_attract = premium_attractiveness_score(o)
    opts.sort(
        key=lambda x: (x.score_option_liquidity * 0.4
                       + x.score_premium_attract * 0.6),
        reverse=True,
    )
    return opts[0]


def screen_ticker(ticker: str, provider: DataProvider, vix_params=None) -> tuple[Optional[TradeCandidate], str]:
    """
    Returns (TradeCandidate or None, reason).
    Reason is empty on success or describes why ticker was rejected.
    """
    snap = provider.fetch_stock(ticker)
    if snap is None:
        return None, "no_data"

    # Hard stock filters
    ok, fails = _passes_stock_filters(snap)
    if not ok:
        return None, f"stock_filters[{','.join(fails)}]"

    # Earnings window check (using max DTE — use VIX-adjusted if available)
    max_dte = vix_params["dte_max"] if vix_params else config.DTE_MAX
    _check_earnings_window(snap, max_dte)
    _check_ex_div_window(snap, max_dte)
    if config.REJECT_ON_EARNINGS_IN_PERIOD and snap.earnings_in_window:
        return None, f"earnings_in_window({snap.next_earnings_date})"

    # Option chain
    raw_opts = provider.fetch_options(ticker, snap.price)
    if not raw_opts:
        return None, "no_options"

    # Compute per-option metrics first (so later filters can use them)
    for o in raw_opts:
        _compute_option_metrics(o, snap.price)

    # Compute IV rank and IV/HV ratio for each option
    for o in raw_opts:
        if o.iv is not None and snap.hv_52w_low is not None and snap.hv_52w_high is not None:
            iv_range = snap.hv_52w_high - snap.hv_52w_low
            if iv_range > 0.001:
                o.iv_rank = (o.iv - snap.hv_52w_low) / iv_range
                o.iv_rank = max(0.0, min(1.0, o.iv_rank))
        if o.iv is not None and snap.hv_30 is not None and snap.hv_30 > 0.001:
            o.iv_hv_ratio = o.iv / snap.hv_30

    # Filter by Section B + delta range (VIX-aware)
    qualified = []
    for o in raw_opts:
        ok, _ = _option_passes_filters(o, vix_params)
        if ok and o.annualized_return >= config.MIN_ANNUALIZED_RETURN:
            qualified.append(o)

    if not qualified:
        return None, "no_qualified_options"

    best = _select_best_option(qualified)
    if best is None:
        return None, "no_best_option"

    # Score the stock
    snap.score_quality          = quality_score(snap)
    snap.score_valuation        = valuation_score(snap)
    snap.score_balance          = balance_sheet_score(snap)
    snap.score_earnings_quality = earnings_quality_score(snap)
    snap.score_technical        = technical_score(snap)
    snap.score_event_risk       = event_risk_score(snap)
    snap.score_beta_risk        = beta_risk_score(snap)

    # Score the best option (new scores)
    best.score_ev = ev_score(best)
    best.score_iv_rank = iv_rank_score(best)
    best.score_iv_hv_premium = iv_hv_premium_score(best)

    tc = TradeCandidate(stock=snap, option=best)
    tc.composite_score = composite_score(tc)
    tc.action = classify(tc.composite_score)
    tc.explanation = build_explanation(tc)
    return tc, ""
