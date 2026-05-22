"""
Sub-score calculations for each candidate.

Each score returns a 0-100 number. None values are penalized partially rather
than rejecting outright (already past hard filters at this point).
"""

from __future__ import annotations

from typing import Optional

from ..models import StockSnapshot


def _bounded(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _grade(value: Optional[float], thresholds: list[tuple[float, float]]) -> float:
    """
    Linear bucket grading. thresholds = [(value_at_or_below, points), ...] ascending.
    Returns 0 if value is None.
    """
    if value is None:
        return 0.0
    for cutoff, pts in thresholds:
        if value <= cutoff:
            return pts
    return thresholds[-1][1]


def quality_score(s: StockSnapshot) -> float:
    """
    Underlying business quality: profitability, returns on capital, margins.
    Uses ROE, ROA, profit margin, operating margin, FCF positive, F-score.
    """
    parts = []

    # ROE — anchor at 15% = 100 pts
    if s.roe is not None:
        parts.append(_bounded(s.roe / 0.15 * 100))
    # ROA — 8% = 100
    if s.roa is not None:
        parts.append(_bounded(s.roa / 0.08 * 100))
    # Operating margin — 20% = 100
    if s.operating_margin is not None:
        parts.append(_bounded(s.operating_margin / 0.20 * 100))
    # Profit margin — 12% = 100
    if s.profit_margin is not None:
        parts.append(_bounded(s.profit_margin / 0.12 * 100))
    # Positive FCF
    if s.free_cashflow is not None:
        parts.append(100.0 if s.free_cashflow > 0 else 0.0)
    # Positive net income
    if s.net_income is not None:
        parts.append(100.0 if s.net_income > 0 else 0.0)
    # Piotroski F-score 0-9 -> 0-100
    if s.piotroski_f is not None:
        parts.append(s.piotroski_f / 9.0 * 100)

    if not parts:
        return 0.0
    return sum(parts) / len(parts)


def valuation_score(s: StockSnapshot) -> float:
    """
    Cheaper = higher score. Uses P/E, EV/EBITDA, P/B, FCF yield.
    Negative or absurd values get penalized.
    """
    parts = []

    # Trailing P/E — 15 = 100, 30 = 50, 60+ = 0
    if s.pe_trailing is not None and s.pe_trailing > 0:
        parts.append(_grade(s.pe_trailing, [(10, 100), (15, 90), (20, 75), (25, 60),
                                             (30, 45), (40, 25), (60, 10), (1e9, 0)]))
    elif s.pe_trailing is not None and s.pe_trailing <= 0:
        parts.append(20.0)  # money-losing — partial credit at best

    # Forward P/E — same scale
    if s.pe_forward is not None and s.pe_forward > 0:
        parts.append(_grade(s.pe_forward, [(10, 100), (15, 90), (20, 75), (25, 60),
                                            (30, 45), (40, 25), (60, 10), (1e9, 0)]))

    # EV / EBITDA — 8 = 100, 15 = 50
    if s.ev_ebitda is not None and s.ev_ebitda > 0:
        parts.append(_grade(s.ev_ebitda, [(6, 100), (9, 85), (12, 65), (15, 45),
                                           (20, 25), (30, 10), (1e9, 0)]))

    # FCF yield — higher is better; 6%+ = 100
    if s.fcf_yield is not None:
        if s.fcf_yield <= 0:
            parts.append(0.0)
        else:
            parts.append(_bounded(s.fcf_yield / 0.06 * 100))

    # Price / Book — 2 = 100, 5 = 50, 10 = 0
    if s.price_to_book is not None and s.price_to_book > 0:
        parts.append(_grade(s.price_to_book, [(1, 100), (2, 90), (3, 75), (5, 50),
                                               (8, 25), (15, 10), (1e9, 0)]))

    if not parts:
        return 50.0  # no data -> neutral
    return sum(parts) / len(parts)


def balance_sheet_score(s: StockSnapshot) -> float:
    """
    Lower leverage + better liquidity ratios = higher score.
    """
    parts = []

    # Debt-to-equity (yfinance returns this as a percentage, e.g., 50 = 0.5)
    if s.debt_to_equity is not None:
        de = s.debt_to_equity / 100.0 if s.debt_to_equity > 5 else s.debt_to_equity
        parts.append(_grade(de, [(0.3, 100), (0.6, 85), (1.0, 65),
                                  (1.5, 45), (2.5, 25), (5, 10), (1e9, 0)]))

    # Current ratio — 1.5 = 100, <1 = 25
    if s.current_ratio is not None:
        if s.current_ratio < 1.0: parts.append(25)
        elif s.current_ratio < 1.2: parts.append(50)
        elif s.current_ratio < 1.5: parts.append(75)
        elif s.current_ratio < 2.5: parts.append(100)
        else: parts.append(85)  # too high may signal idle assets

    # Quick ratio
    if s.quick_ratio is not None:
        if s.quick_ratio < 0.7: parts.append(25)
        elif s.quick_ratio < 1.0: parts.append(60)
        elif s.quick_ratio < 1.5: parts.append(95)
        else: parts.append(100)

    # Altman Z — > 3 = safe (100), 1.8-3 = grey (60), <1.8 = distressed (20)
    if s.altman_z is not None:
        if s.altman_z >= 3.0: parts.append(100)
        elif s.altman_z >= 2.5: parts.append(85)
        elif s.altman_z >= 1.8: parts.append(60)
        elif s.altman_z >= 1.0: parts.append(35)
        else: parts.append(15)

    # Net debt / EBITDA approximation
    if s.total_debt is not None and s.total_cash is not None and s.ebitda and s.ebitda > 0:
        net_debt_ebitda = (s.total_debt - s.total_cash) / s.ebitda
        if net_debt_ebitda < 0: parts.append(100)
        elif net_debt_ebitda < 1: parts.append(95)
        elif net_debt_ebitda < 2: parts.append(80)
        elif net_debt_ebitda < 3: parts.append(60)
        elif net_debt_ebitda < 4: parts.append(35)
        else: parts.append(15)

    if not parts:
        return 50.0
    return sum(parts) / len(parts)


def earnings_quality_score(s: StockSnapshot) -> float:
    """
    Operating cash flow > net income is a positive signal.
    Beneish M-score available -> use it; otherwise use CFO/NI ratio.
    """
    parts = []

    # CFO vs NI
    if s.operating_cashflow is not None and s.net_income is not None and s.net_income != 0:
        ratio = s.operating_cashflow / s.net_income
        if ratio >= 1.2: parts.append(100)
        elif ratio >= 1.0: parts.append(85)
        elif ratio >= 0.8: parts.append(60)
        elif ratio >= 0.5: parts.append(35)
        elif ratio >= 0: parts.append(15)
        else: parts.append(0)
    elif s.operating_cashflow is not None and s.operating_cashflow > 0:
        parts.append(70)  # no NI to compare but CFO positive

    # Beneish M-score: <-2.22 = unlikely manipulator (100), >-1.78 = likely (0)
    if s.beneish_m is not None:
        if s.beneish_m <= -2.22: parts.append(100)
        elif s.beneish_m <= -1.78: parts.append(50)
        else: parts.append(10)

    # Revenue growth sanity (extreme growth flagged)
    if s.revenue_growth is not None:
        if -0.05 <= s.revenue_growth <= 0.30: parts.append(100)
        elif s.revenue_growth > 0.30: parts.append(70)  # high growth, scrutinize
        elif s.revenue_growth >= -0.15: parts.append(60)
        else: parts.append(20)

    if not parts:
        return 50.0
    return sum(parts) / len(parts)


def technical_score(s: StockSnapshot) -> float:
    """
    Reward stocks above 200dma with positive momentum and reasonable RSI.
    """
    parts = []

    # Above 200 SMA
    if s.sma_200 is not None and s.price > 0:
        ratio = s.price / s.sma_200
        if ratio >= 1.0:
            parts.append(_bounded(100 - max(0, (ratio - 1.0) * 100)))  # closer to 200dma is OK
        else:
            parts.append(_bounded((ratio - 0.7) / 0.3 * 50))  # below 200dma penalized

    # Above 50 SMA
    if s.sma_50 is not None and s.price > 0:
        parts.append(100 if s.price > s.sma_50 else 40)

    # 3-month momentum: positive but not parabolic
    if s.momentum_3m is not None:
        m = s.momentum_3m
        if -0.02 <= m <= 0.10: parts.append(100)
        elif 0.10 < m <= 0.20: parts.append(85)
        elif 0.20 < m <= 0.40: parts.append(60)  # extended
        elif m > 0.40: parts.append(35)
        elif -0.10 <= m < -0.02: parts.append(50)
        else: parts.append(15)

    # 6-month momentum
    if s.momentum_6m is not None:
        parts.append(100 if s.momentum_6m > 0 else 30)

    # RSI sweet spot 40-65
    if s.rsi_14 is not None:
        r = s.rsi_14
        if 40 <= r <= 65: parts.append(100)
        elif 30 <= r < 40 or 65 < r <= 75: parts.append(70)
        elif 25 <= r < 30 or 75 < r <= 80: parts.append(40)
        else: parts.append(15)

    # RS vs SPY
    if s.rs_vs_spy_3m is not None:
        if s.rs_vs_spy_3m >= 0: parts.append(100)
        elif s.rs_vs_spy_3m >= -0.05: parts.append(60)
        else: parts.append(20)

    # Distance from 52w low
    if s.pct_from_52w_low is not None:
        # Closer to lows is risky for puts (catching falling knife)
        if s.pct_from_52w_low > 0.30: parts.append(100)
        elif s.pct_from_52w_low > 0.15: parts.append(75)
        elif s.pct_from_52w_low > 0.05: parts.append(40)
        else: parts.append(15)

    if not parts:
        return 50.0
    return sum(parts) / len(parts)


def event_risk_score(s: StockSnapshot) -> float:
    """
    HIGHER score = MORE risk. Subtracted from composite.
    Earnings-in-window is handled as a hard reject, but if it slips through
    flag it. Add penalty for high short interest, ex-div in window.
    """
    risk = 0.0

    if s.earnings_in_window:
        risk += 60
    if s.ex_div_in_window:
        risk += 10
    if s.short_percent_of_float is not None:
        sp = s.short_percent_of_float
        # yfinance returns this as decimal (0.05 = 5%)
        if sp > 0.30: risk += 50
        elif sp > 0.20: risk += 30
        elif sp > 0.10: risk += 15

    return min(100.0, risk)


def beta_risk_score(s: StockSnapshot) -> float:
    """Lower beta = safer for CSPs. Returns 0-100."""
    if s.beta is None:
        return 50.0
    b = s.beta
    if b < 0:
        return 60.0
    if b <= 0.5:
        return 100.0
    if b <= 0.8:
        return 90.0
    if b <= 1.0:
        return 75.0
    if b <= 1.2:
        return 60.0
    if b <= 1.5:
        return 40.0
    if b <= 2.0:
        return 25.0
    return 10.0
