"""Option-level sub-scores: liquidity and premium attractiveness."""

from __future__ import annotations

import math

from ..models import OptionCandidate
from .. import config


def option_liquidity_score(o: OptionCandidate) -> float:
    """OI, volume, and tightness of spread."""
    parts = []

    # Open interest — log scale, 250 = 50, 1000 = 80, 5000 = 95
    if o.open_interest >= 5000: parts.append(95)
    elif o.open_interest >= 2000: parts.append(85)
    elif o.open_interest >= 1000: parts.append(75)
    elif o.open_interest >= 500: parts.append(60)
    elif o.open_interest >= 250: parts.append(45)
    else: parts.append(20)

    # Daily volume
    if o.volume >= 500: parts.append(95)
    elif o.volume >= 200: parts.append(80)
    elif o.volume >= 100: parts.append(65)
    elif o.volume >= 50: parts.append(45)
    else: parts.append(15)

    # Spread tightness — 2% = 100, 10% = 0
    if o.spread_pct <= 0.02: parts.append(100)
    elif o.spread_pct <= 0.04: parts.append(85)
    elif o.spread_pct <= 0.06: parts.append(65)
    elif o.spread_pct <= 0.08: parts.append(40)
    elif o.spread_pct <= 0.10: parts.append(20)
    else: parts.append(0)

    return sum(parts) / len(parts)


def premium_attractiveness_score(o: OptionCandidate) -> float:
    """
    Risk-adjusted premium. Reward annualized return, penalize unrealistic
    premium (which usually means event risk or thin chains).
    """
    parts = []

    ar = o.annualized_return
    # 8% = 50, 15% = 80, 25% = 100, 60%+ = 30 (suspicious)
    if ar < 0.05: parts.append(10)
    elif ar < 0.08: parts.append(35)
    elif ar < 0.12: parts.append(60)
    elif ar < 0.18: parts.append(80)
    elif ar < 0.30: parts.append(100)
    elif ar < 0.50: parts.append(70)
    else: parts.append(35)  # too good to be true

    # Discount to spot — deeper OTM = safer
    d = o.discount_pct
    if d >= 0.10: parts.append(100)
    elif d >= 0.06: parts.append(80)
    elif d >= 0.04: parts.append(60)
    elif d >= 0.02: parts.append(40)
    else: parts.append(20)

    # Delta sweet spot -0.20 to -0.30
    if o.delta is not None:
        ad = abs(o.delta)
        if 0.18 <= ad <= 0.30: parts.append(100)
        elif 0.15 <= ad < 0.18 or 0.30 < ad <= 0.35: parts.append(80)
        elif 0.10 <= ad < 0.15 or 0.35 < ad <= 0.45: parts.append(50)
        else: parts.append(25)

    return sum(parts) / len(parts)
