"""Portfolio-level risk analysis for CSP candidates."""

from __future__ import annotations

from typing import Dict, List, Any, Optional


def analyze_portfolio_risk(rows: List[Dict[str, Any]], total_capital: float) -> Dict[str, Any]:
    """
    Compute portfolio-level risk metrics from flat CSP result dicts.

    Returns dict with:
      - sector_concentration: {sector: pct}
      - total_capital_required, capital_deployed, utilization
      - stress losses at -10%, -20%, -30%
      - weighted delta, avg beta
      - total premium income, portfolio return %
      - warnings list
    """
    if not rows or total_capital <= 0:
        return {"empty": True}

    # Sector concentration
    sector_capital: Dict[str, float] = {}
    total_cash_req = 0.0
    total_premium = 0.0
    total_loss_10 = 0.0
    total_loss_20 = 0.0
    total_loss_30 = 0.0
    weighted_delta_sum = 0.0
    beta_sum = 0.0
    beta_count = 0
    position_count = 0

    for r in rows:
        cash_req = r.get("cash_required", 0) or 0
        sector = r.get("sector", "Unknown") or "Unknown"
        sector_capital[sector] = sector_capital.get(sector, 0) + cash_req
        total_cash_req += cash_req
        total_premium += r.get("premium_income", 0) or 0
        total_loss_10 += r.get("loss_at_-10pct", 0) or 0
        total_loss_20 += r.get("loss_at_-20pct", 0) or 0
        total_loss_30 += r.get("loss_at_-30pct", 0) or 0

        delta = r.get("delta")
        if delta is not None and cash_req > 0:
            weighted_delta_sum += delta * cash_req

        beta = r.get("beta")
        if beta is not None:
            beta_sum += beta
            beta_count += 1

        position_count += 1

    # How many positions can you afford?
    affordable = 0
    running_capital = 0.0
    sorted_rows = sorted(rows, key=lambda x: x.get("composite_score", 0), reverse=True)
    for r in sorted_rows:
        cost = r.get("cash_required", 0) or 0
        if running_capital + cost <= total_capital:
            running_capital += cost
            affordable += 1

    utilization = running_capital / total_capital if total_capital > 0 else 0.0

    # Sector concentration as % of deployed capital
    sector_pct: Dict[str, float] = {}
    warnings: List[str] = []
    if total_cash_req > 0:
        for sector, cap in sorted(sector_capital.items(), key=lambda x: -x[1]):
            pct = cap / total_cash_req
            sector_pct[sector] = round(pct * 100, 1)
            if pct > 0.20:
                warnings.append(f"{sector} concentration {pct*100:.0f}% exceeds 20% threshold")

    weighted_delta = weighted_delta_sum / total_cash_req if total_cash_req > 0 else 0.0
    avg_beta = beta_sum / beta_count if beta_count > 0 else None
    portfolio_return = total_premium / total_capital if total_capital > 0 else 0.0

    return {
        "empty": False,
        "position_count": position_count,
        "affordable": affordable,
        "total_capital": total_capital,
        "total_cash_required": round(total_cash_req, 0),
        "capital_deployed": round(running_capital, 0),
        "utilization": round(utilization * 100, 1),
        "total_premium": round(total_premium, 2),
        "portfolio_return_pct": round(portfolio_return * 100, 2),
        "stress_loss_10": round(total_loss_10, 0),
        "stress_loss_20": round(total_loss_20, 0),
        "stress_loss_30": round(total_loss_30, 0),
        "weighted_delta": round(weighted_delta, 3),
        "avg_beta": round(avg_beta, 2) if avg_beta is not None else None,
        "sector_concentration": sector_pct,
        "warnings": warnings,
    }


def kelly_allocation(rows: List[Dict[str, Any]], total_capital: float) -> List[Dict[str, Any]]:
    """
    Kelly-criterion position sizing for CSP candidates.

    Uses half-Kelly with a 5% per-position cap.
    f* = (p*b - q) / b  where:
      p = prob of profit (prob_otm)
      q = 1 - p
      b = ratio of profit to loss
    """
    if not rows or total_capital <= 0:
        return []

    allocations = []
    for r in rows:
        prob_otm = r.get("prob_otm")
        premium = r.get("premium_income", 0) or 0
        loss_20 = r.get("loss_at_-20pct", 0) or 0
        cash_req = r.get("cash_required", 0) or 0

        if prob_otm is None or cash_req <= 0:
            continue

        p = prob_otm / 100.0  # stored as percentage in flat dict
        q = 1.0 - p

        # b = win / loss ratio
        if loss_20 > 0:
            b = premium / loss_20
        elif premium > 0:
            b = 10.0  # no loss scenario — cap ratio
        else:
            continue

        # Kelly fraction
        kelly_f = (p * b - q) / b if b > 0 else 0.0
        # Half-Kelly, capped at 5%
        half_kelly = max(0.0, min(0.05, kelly_f / 2.0))

        capital_alloc = total_capital * half_kelly
        contracts = int(capital_alloc / cash_req) if cash_req > 0 else 0

        if contracts >= 1:
            allocations.append({
                "ticker": r.get("ticker", ""),
                "score": r.get("composite_score", 0),
                "kelly_fraction": round(half_kelly * 100, 2),
                "contracts": contracts,
                "capital_required": round(contracts * cash_req, 0),
                "premium_income": round(contracts * premium, 0),
            })

    allocations.sort(key=lambda x: x["score"], reverse=True)
    return allocations
