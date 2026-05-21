"""Data models for stock, option contract, and trade candidate."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any


@dataclass
class StockSnapshot:
    """Underlying stock data + computed technicals + fundamentals."""
    ticker: str
    company_name: str = ""
    sector: str = ""
    industry: str = ""
    price: float = 0.0
    market_cap: float = 0.0
    avg_share_volume: float = 0.0
    avg_dollar_volume: float = 0.0

    # Fundamentals (raw)
    pe_trailing: Optional[float] = None
    pe_forward: Optional[float] = None
    ev_ebitda: Optional[float] = None
    ev_sales: Optional[float] = None
    price_to_book: Optional[float] = None
    fcf_yield: Optional[float] = None
    dividend_yield: Optional[float] = None
    payout_ratio: Optional[float] = None
    profit_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    gross_margin: Optional[float] = None
    roe: Optional[float] = None
    roa: Optional[float] = None
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    quick_ratio: Optional[float] = None
    revenue_growth: Optional[float] = None
    earnings_growth: Optional[float] = None
    free_cashflow: Optional[float] = None
    operating_cashflow: Optional[float] = None
    net_income: Optional[float] = None
    total_debt: Optional[float] = None
    total_cash: Optional[float] = None
    ebitda: Optional[float] = None
    short_percent_of_float: Optional[float] = None

    # Composite fundamental scores
    piotroski_f: Optional[int] = None     # 0-9
    altman_z: Optional[float] = None
    beneish_m: Optional[float] = None     # may be None if data missing

    # Technicals
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    rsi_14: Optional[float] = None
    atr_14: Optional[float] = None
    momentum_3m: Optional[float] = None
    momentum_6m: Optional[float] = None
    momentum_12m: Optional[float] = None
    rs_vs_spy_3m: Optional[float] = None  # relative strength vs SPY
    pct_from_52w_high: Optional[float] = None
    pct_from_52w_low: Optional[float] = None

    # Event risk
    next_earnings_date: Optional[str] = None  # YYYY-MM-DD
    earnings_in_window: bool = False
    ex_dividend_date: Optional[str] = None
    ex_div_in_window: bool = False

    # Sub-scores (0-100)
    score_quality: float = 0.0
    score_valuation: float = 0.0
    score_balance: float = 0.0
    score_earnings_quality: float = 0.0
    score_technical: float = 0.0
    score_event_risk: float = 0.0  # higher = more risk

    # Reasoning trail
    notes: list = field(default_factory=list)
    reject_reasons: list = field(default_factory=list)


@dataclass
class OptionCandidate:
    """A single put contract being considered for the trade."""
    ticker: str
    expiration: str
    dte: int
    strike: float
    bid: float
    ask: float
    mid: float
    last: float
    open_interest: int
    volume: int
    spread_pct: float
    delta: Optional[float]
    iv: Optional[float]
    iv_rank: Optional[float] = None
    iv_percentile: Optional[float] = None

    # Computed
    breakeven: float = 0.0
    discount_pct: float = 0.0
    cash_required: float = 0.0          # strike * 100
    premium_income: float = 0.0         # mid * 100
    return_on_cash: float = 0.0
    annualized_return: float = 0.0
    prob_otm: Optional[float] = None    # ~ 1 - |delta|
    prob_assignment: Optional[float] = None
    expected_value: Optional[float] = None
    loss_if_zero: float = 0.0
    loss_at_minus_10: float = 0.0
    loss_at_minus_20: float = 0.0
    loss_at_minus_30: float = 0.0
    loss_at_minus_50: float = 0.0

    # Sub-scores
    score_option_liquidity: float = 0.0
    score_premium_attract: float = 0.0


@dataclass
class TradeCandidate:
    """Combined stock + best option contract + final composite scoring."""
    stock: StockSnapshot
    option: OptionCandidate

    composite_score: float = 0.0
    action: str = "Reject"   # Accept / Watch / Reject
    explanation: str = ""

    def to_flat_dict(self) -> Dict[str, Any]:
        """Flatten for CSV output."""
        s = self.stock
        o = self.option
        return {
            "rank": "",
            "ticker": s.ticker,
            "company_name": s.company_name,
            "sector": s.sector,
            "industry": s.industry,
            "price": round(s.price, 2),
            "market_cap": round(s.market_cap, 0),
            "avg_share_volume": round(s.avg_share_volume, 0),
            "avg_dollar_volume": round(s.avg_dollar_volume, 0),
            "expiration": o.expiration,
            "dte": o.dte,
            "strike": round(o.strike, 2),
            "bid": round(o.bid, 2),
            "ask": round(o.ask, 2),
            "mid": round(o.mid, 2),
            "last": round(o.last, 2),
            "open_interest": o.open_interest,
            "option_volume": o.volume,
            "spread_pct": round(o.spread_pct * 100, 2),
            "delta": round(o.delta, 3) if o.delta is not None else None,
            "iv": round(o.iv * 100, 1) if o.iv is not None else None,
            "iv_percentile": round(o.iv_percentile, 1) if o.iv_percentile is not None else None,
            "breakeven": round(o.breakeven, 2),
            "discount_pct": round(o.discount_pct * 100, 2),
            "cash_required": round(o.cash_required, 0),
            "premium_income": round(o.premium_income, 2),
            "return_on_cash_pct": round(o.return_on_cash * 100, 2),
            "annualized_return_pct": round(o.annualized_return * 100, 2),
            "prob_otm": round(o.prob_otm * 100, 1) if o.prob_otm is not None else None,
            "prob_assignment": round(o.prob_assignment * 100, 1) if o.prob_assignment is not None else None,
            "loss_if_zero": round(o.loss_if_zero, 0),
            "loss_at_-10pct": round(o.loss_at_minus_10, 0),
            "loss_at_-20pct": round(o.loss_at_minus_20, 0),
            "loss_at_-30pct": round(o.loss_at_minus_30, 0),
            "loss_at_-50pct": round(o.loss_at_minus_50, 0),
            "earnings_date": s.next_earnings_date,
            "earnings_in_window": s.earnings_in_window,
            "ex_dividend_date": s.ex_dividend_date,
            "ex_div_in_window": s.ex_div_in_window,
            "score_quality": round(s.score_quality, 1),
            "score_valuation": round(s.score_valuation, 1),
            "score_balance": round(s.score_balance, 1),
            "score_earnings_quality": round(s.score_earnings_quality, 1),
            "score_technical": round(s.score_technical, 1),
            "score_option_liquidity": round(o.score_option_liquidity, 1),
            "score_premium_attract": round(o.score_premium_attract, 1),
            "score_event_risk": round(s.score_event_risk, 1),
            "composite_score": round(self.composite_score, 1),
            "action": self.action,
            "explanation": self.explanation,
            "piotroski_f": s.piotroski_f,
            "altman_z": round(s.altman_z, 2) if s.altman_z is not None else None,
        }
