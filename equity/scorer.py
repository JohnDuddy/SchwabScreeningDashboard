"""
Quantitative scoring engine for the equity ranking system.

All 5 model scores + qualitative overlay are computed from the data dict
produced by data_fetcher.fetch_ticker_data(). No AI required for scoring.

Conventions for input data:
  - Margins (gross, operating, net, ROE, ROA):  decimal  (0.437 = 43.7%)
  - Growth rates (revenue, EPS):                decimal  (0.12 = 12%)
  - FCF yield:                                  percent  (3.5 = 3.5%)
  - Valuation ratios (P/E, PEG, EV/EBITDA):    raw ratio (24.5)
  - Debt/equity, current ratio:                 raw ratio
  - Beta:                                       raw
  - Dividend yield:                             percent  (1.2 = 1.2%)
  - Short % float:                              decimal  (0.0065 = 0.65%)
"""
from __future__ import annotations
import statistics


def _safe(v, default=None):
    """Return v if it's a usable float, else default."""
    if v is None:
        return default
    try:
        f = float(v)
        if f != f:          # NaN
            return default
        return f
    except (TypeError, ValueError):
        return default


def _clamp(v: float, lo: float = 1.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, v))


# ── Model 1: Three-Statement ─────────────────────────────────────────────────

def score_three_statement(d: dict) -> tuple[float, list[str]]:
    """Returns (score 1-10, list of driver strings)."""
    score = 5.0
    drivers = []

    rev = _safe(d.get("revenue_growth"))          # decimal
    if rev is not None:
        if rev > 0.25:   score += 2.0; drivers.append(f"Rev growth {rev*100:.0f}% (strong)")
        elif rev > 0.12: score += 1.2; drivers.append(f"Rev growth {rev*100:.0f}% (solid)")
        elif rev > 0.05: score += 0.5; drivers.append(f"Rev growth {rev*100:.0f}% (modest)")
        elif rev > 0.0:  score += 0.0; drivers.append(f"Rev growth {rev*100:.0f}% (slow)")
        else:            score -= 1.5; drivers.append(f"Rev growth {rev*100:.0f}% (negative)")

    nm = _safe(d.get("net_margin"))               # decimal
    if nm is not None:
        if nm > 0.25:   score += 1.5; drivers.append(f"Net margin {nm*100:.0f}% (excellent)")
        elif nm > 0.15: score += 1.0; drivers.append(f"Net margin {nm*100:.0f}% (good)")
        elif nm > 0.05: score += 0.3; drivers.append(f"Net margin {nm*100:.0f}% (ok)")
        elif nm < 0:    score -= 1.5; drivers.append(f"Net margin {nm*100:.0f}% (losing money)")

    gm = _safe(d.get("gross_margin"))             # decimal
    if gm is not None:
        if gm > 0.65:   score += 1.0; drivers.append(f"Gross margin {gm*100:.0f}% (exceptional)")
        elif gm > 0.45: score += 0.5; drivers.append(f"Gross margin {gm*100:.0f}% (good)")
        elif gm < 0.20: score -= 0.5; drivers.append(f"Gross margin {gm*100:.0f}% (thin)")

    fcf = _safe(d.get("fcf_yield"))               # percent
    if fcf is not None:
        if fcf > 7:   score += 1.5; drivers.append(f"FCF yield {fcf:.1f}% (excellent)")
        elif fcf > 4: score += 0.8; drivers.append(f"FCF yield {fcf:.1f}% (solid)")
        elif fcf > 1: score += 0.2; drivers.append(f"FCF yield {fcf:.1f}% (modest)")
        elif fcf < 0: score -= 1.0; drivers.append(f"FCF yield {fcf:.1f}% (negative)")

    de = _safe(d.get("debt_to_equity"))
    if de is not None:
        if de < 0.3:   score += 0.5; drivers.append(f"D/E {de:.1f} (clean)")
        elif de > 3.0: score -= 1.0; drivers.append(f"D/E {de:.1f} (heavy debt)")
        elif de > 2.0: score -= 0.5

    return _clamp(score), drivers[:3]


# ── Model 2: DCF / Valuation ──────────────────────────────────────────────────

def score_dcf(d: dict, sector_medians: dict | None = None) -> tuple[float, list[str]]:
    score = 5.0
    drivers = []
    sector = d.get("sector", "")

    fwd_pe = _safe(d.get("pe_forward")) or _safe(d.get("pe_trailing"))
    if fwd_pe is not None and fwd_pe > 0:
        # Sector-adjusted threshold
        growth_adj = max(0, (_safe(d.get("revenue_growth"), 0.05)) * 100)  # growth % as integer
        fair_pe = 15 + growth_adj * 1.5   # rough PEG-implied fair P/E
        ratio = fwd_pe / fair_pe
        if ratio < 0.6:   score += 2.5; drivers.append(f"P/E {fwd_pe:.0f} cheap vs growth-implied {fair_pe:.0f}")
        elif ratio < 0.85: score += 1.5; drivers.append(f"P/E {fwd_pe:.0f} reasonable vs {fair_pe:.0f}")
        elif ratio < 1.1:  score += 0.0; drivers.append(f"P/E {fwd_pe:.0f} fairly valued")
        elif ratio < 1.5:  score -= 1.0; drivers.append(f"P/E {fwd_pe:.0f} elevated vs {fair_pe:.0f}")
        else:              score -= 2.0; drivers.append(f"P/E {fwd_pe:.0f} expensive vs growth")

    peg = _safe(d.get("peg_ratio"))
    if peg is not None and peg > 0:
        if peg < 0.8:   score += 1.5; drivers.append(f"PEG {peg:.1f} (undervalued)")
        elif peg < 1.2: score += 0.8; drivers.append(f"PEG {peg:.1f} (fair)")
        elif peg < 2.0: score -= 0.3
        else:           score -= 1.0; drivers.append(f"PEG {peg:.1f} (expensive)")

    # Analyst target upside
    price = _safe(d.get("current_price"))
    target = _safe(d.get("target_mean"))
    if price and target and price > 0:
        upside = (target - price) / price * 100
        d["_analyst_upside"] = round(upside, 1)
        if upside > 30:   score += 1.5; drivers.append(f"Analyst upside {upside:.0f}%")
        elif upside > 15: score += 0.8; drivers.append(f"Analyst upside {upside:.0f}%")
        elif upside > 5:  score += 0.2
        elif upside < -5: score -= 0.8; drivers.append(f"Analyst downside {upside:.0f}%")

    fcf = _safe(d.get("fcf_yield"))    # percent
    if fcf is not None:
        if fcf > 6:   score += 1.0; drivers.append(f"FCF yield {fcf:.1f}% (strong value signal)")
        elif fcf > 3: score += 0.5
        elif fcf < 0: score -= 0.5

    return _clamp(score), drivers[:3]


# ── Model 3: ROIC / Value-Driver ──────────────────────────────────────────────

def score_roic(d: dict) -> tuple[float, list[str]]:
    score = 5.0
    drivers = []

    roe = _safe(d.get("roe"))          # decimal, can be >1
    if roe is not None:
        if roe > 0.30:   score += 2.0; drivers.append(f"ROE {roe*100:.0f}% (exceptional)")
        elif roe > 0.18: score += 1.3; drivers.append(f"ROE {roe*100:.0f}% (strong)")
        elif roe > 0.10: score += 0.5; drivers.append(f"ROE {roe*100:.0f}% (decent)")
        elif roe < 0:    score -= 1.5; drivers.append(f"ROE {roe*100:.0f}% (negative)")

    roa = _safe(d.get("roa"))          # decimal
    if roa is not None:
        if roa > 0.15:   score += 1.5; drivers.append(f"ROA {roa*100:.0f}% (excellent)")
        elif roa > 0.08: score += 0.8; drivers.append(f"ROA {roa*100:.0f}% (good)")
        elif roa > 0.03: score += 0.2
        elif roa < 0:    score -= 1.0; drivers.append(f"ROA {roa*100:.0f}% (negative)")

    om = _safe(d.get("operating_margin"))   # decimal
    if om is not None:
        if om > 0.30:   score += 1.5; drivers.append(f"Op margin {om*100:.0f}% (excellent)")
        elif om > 0.18: score += 0.8; drivers.append(f"Op margin {om*100:.0f}% (solid)")
        elif om > 0.08: score += 0.2
        elif om < 0:    score -= 1.0; drivers.append(f"Op margin {om*100:.0f}% (negative)")

    # Reinvestment efficiency: high growth + high margins = value creation
    rev = _safe(d.get("revenue_growth"), 0.0)
    nm  = _safe(d.get("net_margin"), 0.0)
    if rev > 0.15 and nm > 0.10:
        score += 0.5; drivers.append("High-growth + high-margin compounder")

    return _clamp(score), drivers[:3]


# ── Model 4: Comparable Company ───────────────────────────────────────────────

def score_comp(d: dict, sector_medians: dict) -> tuple[float, list[str]]:
    """Compare multiples to sector median. sector_medians: {sector: {field: median}}."""
    score = 5.0
    drivers = []
    sector = d.get("sector", "")
    sm = sector_medians.get(sector, {})
    if not sm:
        return 5.0, ["No sector peers for comparison"]

    comparisons = 0
    total_adj = 0.0

    def compare_multiple(field, label, invert=False):
        nonlocal comparisons, total_adj
        v = _safe(d.get(field))
        med = _safe(sm.get(field))
        if v is None or med is None or med == 0 or v <= 0:
            return
        ratio = v / med
        # Lower multiple vs sector = cheaper (good for valuation)
        # invert=True means higher is better (e.g., FCF yield)
        if not invert:
            if ratio < 0.65:   adj = +2.0; tag = f"{label} {v:.1f} vs sector {med:.1f} (cheap)"
            elif ratio < 0.85: adj = +1.0; tag = f"{label} {v:.1f} vs sector {med:.1f} (discount)"
            elif ratio < 1.15: adj = 0.0;  tag = f"{label} {v:.1f} ≈ sector {med:.1f}"
            elif ratio < 1.40: adj = -1.0; tag = f"{label} {v:.1f} vs sector {med:.1f} (premium)"
            else:              adj = -2.0; tag = f"{label} {v:.1f} vs sector {med:.1f} (expensive)"
        else:
            if ratio > 1.35:   adj = +2.0; tag = f"{label} {v:.1f}% vs sector {med:.1f}% (better)"
            elif ratio > 1.10: adj = +1.0; tag = f"{label} {v:.1f}% vs sector {med:.1f}%"
            elif ratio < 0.65: adj = -1.0; tag = f"{label} {v:.1f}% below sector {med:.1f}%"
            else:              adj = 0.0;  tag = ""
        comparisons += 1
        total_adj += adj
        if tag:
            drivers.append(tag)

    compare_multiple("pe_trailing", "P/E")
    compare_multiple("ev_ebitda", "EV/EBITDA")
    compare_multiple("price_sales", "P/S")

    # Quality offset: if this stock has much higher margins than peers, a premium is justified
    my_nm  = _safe(d.get("net_margin"), 0.0)
    med_nm = _safe(sm.get("net_margin"), 0.0)
    if med_nm and my_nm > med_nm * 1.5 and my_nm > 0.10:
        total_adj += 1.0
        drivers.append(f"Net margin {my_nm*100:.0f}% justifies premium vs sector {med_nm*100:.0f}%")

    if comparisons > 0:
        score = 5.0 + total_adj / comparisons * 1.5
    return _clamp(score), drivers[:3]


# ── Model 5: Scenario / Sensitivity ───────────────────────────────────────────

def score_scenario(d: dict) -> tuple[float, list[str]]:
    score = 5.0
    drivers = []

    # Position in 52-week range (lower = more margin of safety)
    price  = _safe(d.get("current_price"))
    hi52   = _safe(d.get("wk52_high"))
    lo52   = _safe(d.get("wk52_low"))
    if price and hi52 and lo52 and (hi52 - lo52) > 0:
        pct_range = (price - lo52) / (hi52 - lo52)   # 0 = at 52wk low, 1 = at 52wk high
        if pct_range < 0.25:   score += 2.0; drivers.append(f"Near 52wk low ({pct_range*100:.0f}% of range)")
        elif pct_range < 0.45: score += 1.0; drivers.append(f"Lower half of 52wk range")
        elif pct_range > 0.85: score -= 0.5; drivers.append(f"Near 52wk high ({pct_range*100:.0f}% of range)")
        d["_pct_52wk"] = round(pct_range * 100, 1)

    # Analyst upside (computed in DCF step, stored in d["_analyst_upside"])
    upside = _safe(d.get("_analyst_upside"))
    if upside is not None:
        if upside > 25:   score += 2.0; drivers.append(f"Analyst target: +{upside:.0f}% upside")
        elif upside > 12: score += 1.0; drivers.append(f"Analyst target: +{upside:.0f}%")
        elif upside > 4:  score += 0.3
        elif upside < -5: score -= 1.0; drivers.append(f"Analyst target: {upside:.0f}% (below market)")

    # Beta: lower beta = better downside protection (base case preference)
    beta = _safe(d.get("beta"))
    if beta is not None:
        if beta < 0.7:   score += 1.0; drivers.append(f"Beta {beta:.2f} (low risk)")
        elif beta < 1.0: score += 0.5
        elif beta > 1.8: score -= 0.8; drivers.append(f"Beta {beta:.2f} (high volatility)")

    # Revenue growth provides upside in bull case
    rev = _safe(d.get("revenue_growth"))
    if rev and rev > 0.18:
        score += 0.5; drivers.append(f"Strong revenue growth supports bull case")

    return _clamp(score), drivers[:3]


# ── Qualitative Overlay ────────────────────────────────────────────────────────

def score_qualitative(d: dict) -> tuple[float, list[str]]:
    score = 5.0
    drivers = []

    # Analyst recommendation
    rec = (d.get("recommendation") or "").upper()
    if "STRONG_BUY" in rec or rec == "STRONGBUY":
        score += 2.0; drivers.append("Analyst: Strong Buy")
    elif rec in ("BUY",):
        score += 1.2; drivers.append("Analyst: Buy")
    elif rec in ("HOLD", "NEUTRAL"):
        score += 0.0
    elif "SELL" in rec:
        score -= 1.5; drivers.append("Analyst: Sell")

    # Number of analysts (coverage)
    n_analysts = _safe(d.get("num_analysts"), 0)
    if n_analysts and n_analysts >= 15:
        score += 0.3   # well-covered

    # Insider ownership
    insider = _safe(d.get("insider_pct"))     # decimal
    if insider is not None:
        if insider > 0.15:   score += 1.0; drivers.append(f"Insiders own {insider*100:.0f}%")
        elif insider > 0.05: score += 0.5; drivers.append(f"Insiders own {insider*100:.0f}%")

    # Balance sheet quality
    cr = _safe(d.get("current_ratio"))
    if cr is not None:
        if cr > 2.0:   score += 0.5; drivers.append(f"Current ratio {cr:.1f} (strong)")
        elif cr < 0.8: score -= 0.8; drivers.append(f"Current ratio {cr:.1f} (weak)")

    de = _safe(d.get("debt_to_equity"))
    if de is not None:
        if de < 0.5:   score += 0.5
        elif de > 3.0: score -= 0.8; drivers.append(f"High leverage D/E {de:.1f}")

    # Dividend (quality signal for established companies)
    div = _safe(d.get("dividend_yield"), 0.0)
    if div and div > 1.0:
        score += 0.3   # steady dividend payer

    return _clamp(score), drivers[:3]


# ── Master scorer ─────────────────────────────────────────────────────────────

WEIGHTS = {
    "three_stmt":  0.20,
    "dcf":         0.20,
    "roic":        0.20,
    "comp":        0.15,
    "scenario":    0.15,
    "qualitative": 0.10,
}

CLASSIFICATION = [
    (9.0, "Strong Buy"),
    (8.0, "Buy"),
    (7.0, "Watchlist"),
    (6.0, "Hold"),
    (5.0, "Weak"),
    (0.0, "Avoid"),
]

CONVICTION = [
    (8.5, "High"),
    (7.0, "Moderate"),
    (5.5, "Low"),
    (0.0, "Speculative"),
]


def _classify(score: float, table) -> str:
    for threshold, label in table:
        if score >= threshold:
            return label
    return table[-1][1]


def score_ticker(data: dict, sector_medians: dict) -> dict:
    """
    Compute all model scores and return a flat result dict with scores,
    weighted total, classification, and key drivers.
    """
    d = dict(data)  # work on a copy so we can stash intermediate values

    s1, d1 = score_three_statement(d)
    s2, d2 = score_dcf(d, sector_medians)         # also writes d["_analyst_upside"]
    s3, d3 = score_roic(d)
    s4, d4 = score_comp(d, sector_medians)
    s5, d5 = score_scenario(d)                    # also writes d["_pct_52wk"]
    s6, d6 = score_qualitative(d)

    weighted = (
        s1 * WEIGHTS["three_stmt"] +
        s2 * WEIGHTS["dcf"] +
        s3 * WEIGHTS["roic"] +
        s4 * WEIGHTS["comp"] +
        s5 * WEIGHTS["scenario"] +
        s6 * WEIGHTS["qualitative"]
    )
    weighted = round(weighted, 2)

    return {
        "ticker":           data.get("ticker", ""),
        "company_name":     data.get("company_name", ""),
        "sector":           data.get("sector", "N/A"),
        "industry":         data.get("industry", "N/A"),
        "source":           data.get("source", "yfinance"),
        # prices
        "current_price":    _safe(data.get("current_price")),
        "market_cap":       _safe(data.get("market_cap")),
        "wk52_high":        _safe(data.get("wk52_high")),
        "wk52_low":         _safe(data.get("wk52_low")),
        "pct_52wk":         d.get("_pct_52wk"),
        # scores
        "score_3stmt":      round(s1, 1),
        "score_dcf":        round(s2, 1),
        "score_roic":       round(s3, 1),
        "score_comp":       round(s4, 1),
        "score_scenario":   round(s5, 1),
        "score_qual":       round(s6, 1),
        "score_total":      weighted,
        # classification
        "classification":   _classify(weighted, CLASSIFICATION),
        "conviction":       _classify(weighted, CONVICTION),
        # key metrics for display
        "pe_forward":       _safe(data.get("pe_forward")) or _safe(data.get("pe_trailing")),
        "peg_ratio":        _safe(data.get("peg_ratio")),
        "gross_margin":     _safe(data.get("gross_margin")),
        "net_margin":       _safe(data.get("net_margin")),
        "roe":              _safe(data.get("roe")),
        "revenue_growth":   _safe(data.get("revenue_growth")),
        "fcf_yield":        _safe(data.get("fcf_yield")),
        "debt_to_equity":   _safe(data.get("debt_to_equity")),
        "beta":             _safe(data.get("beta")),
        "dividend_yield":   _safe(data.get("dividend_yield")),
        "analyst_upside":   d.get("_analyst_upside"),
        "recommendation":   data.get("recommendation", "N/A"),
        "target_mean":      _safe(data.get("target_mean")),
        # drivers
        "drivers_3stmt":    d1,
        "drivers_dcf":      d2,
        "drivers_roic":     d3,
        "drivers_comp":     d4,
        "drivers_scenario": d5,
        "drivers_qual":     d6,
    }


def compute_sector_medians(ticker_data_list: list[dict]) -> dict:
    """
    Build sector-level medians for the comp model.
    Returns {sector: {field: median_value}}.
    """
    from collections import defaultdict
    buckets: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))

    fields = ["pe_trailing", "pe_forward", "ev_ebitda", "price_sales",
              "net_margin", "gross_margin", "operating_margin", "roe"]

    for d in ticker_data_list:
        sector = d.get("sector") or "Unknown"
        for f in fields:
            v = _safe(d.get(f))
            if v is not None and v > 0:
                buckets[sector][f].append(v)

    result = {}
    for sector, field_vals in buckets.items():
        result[sector] = {}
        for f, vals in field_vals.items():
            if vals:
                result[sector][f] = statistics.median(vals)

    return result


def rank_universe(ticker_data_list: list[dict]) -> list[dict]:
    """
    Score + rank all tickers. Returns list sorted by score_total descending.
    Tickers with errors or no data are placed at the bottom.
    """
    sector_medians = compute_sector_medians(ticker_data_list)

    scored = []
    for item in ticker_data_list:
        if item.get("error"):
            scored.append({
                "ticker": item["ticker"], "company_name": item["ticker"],
                "sector": "N/A", "score_total": 0.0,
                "classification": "N/A", "conviction": "N/A",
                "error": item["error"],
            })
            continue
        try:
            result = score_ticker(item.get("data", item), sector_medians)
            scored.append(result)
        except Exception as e:
            scored.append({
                "ticker": item.get("ticker", "?"), "company_name": item.get("ticker", "?"),
                "sector": "N/A", "score_total": 0.0,
                "classification": "N/A", "conviction": "N/A",
                "error": str(e),
            })

    scored.sort(key=lambda x: (x.get("score_total") or 0), reverse=True)
    for i, row in enumerate(scored, 1):
        row["rank"] = i

    return scored
