"""
badass_screener.py — Duddy's Badass Stock Screener
Short-term momentum screener designed for <4 week holding periods.

Scoring factors (each 0-100, combined into composite 0-100):
  1. 52-Week High Proximity   (20%) — near the high = strong momentum
  2. 20-day Momentum          (15%) — short-term price thrust
  3. 60-day Momentum          (15%) — medium-term trend confirmation
  4. Volume Surge             (15%) — breakout volume confirmation
  5. New Highs                (10%) — recently set 20d / 50d / 52w highs
  6. Industry Relative Str.   (15%) — outperforming the sector ETF
  7. Price Acceleration       (10%) — momentum speeding up recently

Red Flag system: when SPY trades below its 200-day MA, all composite
scores are penalised by 15% to reflect unfavourable market regime.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Callable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Factor weights (must sum to 1.0) ─────────────────────────────────────────
FACTOR_WEIGHTS: dict[str, float] = {
    "score_52wh": 0.20,
    "score_20d":  0.15,
    "score_60d":  0.15,
    "score_vol":  0.15,
    "score_nh":   0.10,
    "score_rs":   0.15,
    "score_acc":  0.10,
}

assert abs(sum(FACTOR_WEIGHTS.values()) - 1.0) < 1e-9, "Weights must sum to 1.0"

RED_FLAG_PENALTY = 0.85   # multiply composite by this when red flag active


# ── Individual factor scorers (each returns float 0-100) ─────────────────────

def score_52w_proximity(px: pd.Series) -> float:
    """Distance from 52-week high. At the high = 100; 20%+ below = 0."""
    if len(px) < 10:
        return 50.0
    window = min(len(px), 252)
    high = float(px.iloc[-window:].max())
    if high <= 0:
        return 0.0
    pct_below = (float(px.iloc[-1]) / high - 1.0) * 100  # ≤ 0
    # 0% below → 100; -20% → 0 (linear)
    return float(max(0.0, min(100.0, 100.0 + pct_below * 5.0)))


def score_momentum_20d(px: pd.Series) -> float:
    """20-day price momentum. +15% → 100; 0% → 50; -15% → 0."""
    if len(px) < 21:
        return 50.0
    ret = (float(px.iloc[-1]) / float(px.iloc[-21]) - 1.0) * 100
    return float(max(0.0, min(100.0, 50.0 + ret * 3.33)))


def score_momentum_60d(px: pd.Series) -> float:
    """60-day price momentum. +30% → 100; 0% → 50; -30% → 0."""
    if len(px) < 61:
        return 50.0
    ret = (float(px.iloc[-1]) / float(px.iloc[-61]) - 1.0) * 100
    return float(max(0.0, min(100.0, 50.0 + ret * 1.67)))


def score_volume_surge(vol: pd.Series) -> float:
    """5-day avg volume vs 20-day avg. 2× → 100; 1× → 50; 0.5× → 25."""
    if len(vol) < 20:
        return 50.0
    avg20 = float(vol.iloc[-20:].mean())
    avg5  = float(vol.iloc[-5:].mean())
    if avg20 <= 0:
        return 50.0
    ratio = avg5 / avg20
    return float(max(0.0, min(100.0, ratio * 50.0)))


def score_new_highs(px: pd.Series) -> float:
    """
    Bonus for setting recent new highs.
    New 20d high: +25 | New 50d high: +35 | New 52w high: +40
    Partial when history is short.
    """
    if len(px) < 21:
        return 50.0
    current = float(px.iloc[-1])
    score = 0.0

    # 20-day high (exclude today)
    h20 = float(px.iloc[-21:-1].max()) if len(px) >= 21 else current
    if current >= h20:
        score += 25.0

    # 50-day high
    if len(px) >= 51:
        h50 = float(px.iloc[-51:-1].max())
        if current >= h50:
            score += 35.0
    else:
        score += 15.0  # partial credit

    # 52-week high
    if len(px) >= 252:
        h252 = float(px.iloc[-252:-1].max())
        if current >= h252:
            score += 40.0
    elif len(px) >= 100:
        h_all = float(px.iloc[:-1].max())
        if current >= h_all:
            score += 30.0
    else:
        score += 15.0  # partial credit

    return float(min(100.0, score))


def score_industry_rs(px: pd.Series, sector_px: Optional[pd.Series]) -> float:
    """
    60-day return relative to sector ETF.
    +15% alpha → 100; 0% → 50; -15% → 0.
    Falls back to 50 when sector data is unavailable.
    """
    if sector_px is None or len(sector_px) < 61 or len(px) < 61:
        return 50.0
    stock_ret  = (float(px.iloc[-1])       / float(px.iloc[-61])       - 1.0) * 100
    sector_ret = (float(sector_px.iloc[-1]) / float(sector_px.iloc[-61]) - 1.0) * 100
    alpha = stock_ret - sector_ret
    return float(max(0.0, min(100.0, 50.0 + alpha * 3.33)))


def score_price_acceleration(px: pd.Series) -> float:
    """
    Is momentum speeding up? Compares annualised 10-day return to
    annualised 60-day return. Positive acceleration → above 50.
    """
    if len(px) < 61:
        return 50.0
    ret10 = (float(px.iloc[-1]) / float(px.iloc[-11]) - 1.0) * (252 / 10) * 100 if len(px) >= 11 else 0.0
    ret60 = (float(px.iloc[-1]) / float(px.iloc[-61]) - 1.0) * (252 / 60) * 100
    accel = ret10 - ret60          # positive = accelerating
    return float(max(0.0, min(100.0, 50.0 + accel * 0.25)))


# ── Red flag detection ────────────────────────────────────────────────────────

def compute_red_flag(spy_px: pd.Series) -> dict:
    """
    Returns a dict describing whether the market red flag is active.
    Active when SPY is trading below its 200-day moving average.
    """
    if len(spy_px) < 50:
        return {"active": False, "spy_price": 0.0, "ma200": None, "pct_diff": None, "regime": "unknown"}

    current = float(spy_px.iloc[-1])

    # Sanity check: SPY below $350 almost certainly means bad/stale data
    # (SPY has been above $350 since 2020 and has never retraced that far).
    # Return unknown rather than trigger a false red flag.
    if current < 350:
        logger.warning("SPY price %.2f looks like bad data — skipping red flag", current)
        return {"active": False, "spy_price": round(current, 2), "ma200": None, "pct_diff": None, "regime": "unknown"}

    lookback = min(len(spy_px), 200)
    ma200 = float(spy_px.rolling(lookback).mean().iloc[-1])
    pct_diff = round((current / ma200 - 1.0) * 100, 2)
    active = current < ma200

    if active:
        regime = "bearish"
    elif pct_diff < 3:
        regime = "caution"
    else:
        regime = "healthy"

    return {
        "active":    active,
        "spy_price": round(current, 2),
        "ma200":     round(ma200, 2),
        "pct_diff":  pct_diff,
        "regime":    regime,
    }


# ── Per-stock scoring ─────────────────────────────────────────────────────────

def compute_stock_factors(
    symbol: str,
    df: pd.DataFrame,
    sector_df: Optional[pd.DataFrame] = None,
    red_flag_active: bool = False,
) -> dict:
    """
    Compute all factor scores for one stock and build the composite.
    Returns a flat dict suitable for display / export.
    """
    px  = df["Adj Close"].dropna()
    vol = df["Volume"].dropna()
    sector_px = sector_df["Adj Close"].dropna() if (sector_df is not None and not sector_df.empty) else None

    if len(px) < 21:
        return {}

    # Factor scores
    f52wh = score_52w_proximity(px)
    f20d  = score_momentum_20d(px)
    f60d  = score_momentum_60d(px)
    fvol  = score_volume_surge(vol)
    fnh   = score_new_highs(px)
    frs   = score_industry_rs(px, sector_px)
    facc  = score_price_acceleration(px)

    # Composite
    raw_composite = (
        f52wh * FACTOR_WEIGHTS["score_52wh"] +
        f20d  * FACTOR_WEIGHTS["score_20d"]  +
        f60d  * FACTOR_WEIGHTS["score_60d"]  +
        fvol  * FACTOR_WEIGHTS["score_vol"]  +
        fnh   * FACTOR_WEIGHTS["score_nh"]   +
        frs   * FACTOR_WEIGHTS["score_rs"]   +
        facc  * FACTOR_WEIGHTS["score_acc"]
    )
    composite = raw_composite * RED_FLAG_PENALTY if red_flag_active else raw_composite

    # Action label
    if composite >= 72:
        action = "Strong Buy"
    elif composite >= 58:
        action = "Buy"
    elif composite >= 44:
        action = "Watch"
    else:
        action = "Pass"

    # Display helpers
    window = min(len(px), 252)
    high52w = float(px.iloc[-window:].max())
    pct_from_high = round((float(px.iloc[-1]) / high52w - 1.0) * 100, 1) if high52w > 0 else 0.0
    ret20 = round((float(px.iloc[-1]) / float(px.iloc[-21]) - 1.0) * 100, 1) if len(px) >= 21 else 0.0
    ret60 = round((float(px.iloc[-1]) / float(px.iloc[-61]) - 1.0) * 100, 1) if len(px) >= 61 else 0.0

    avg20_vol = float(vol.iloc[-20:].mean()) if len(vol) >= 20 else 0.0
    avg5_vol  = float(vol.iloc[-5:].mean())  if len(vol) >= 5  else 0.0
    vol_ratio = round(avg5_vol / avg20_vol, 2) if avg20_vol > 0 else 1.0

    return {
        "ticker":          symbol,
        "current_price":   round(float(px.iloc[-1]), 2),
        "high_52w":        round(high52w, 2),
        "pct_from_high":   pct_from_high,
        "ret_20d":         ret20,
        "ret_60d":         ret60,
        "vol_ratio":       vol_ratio,
        # Factor scores (0-100)
        "score_52wh":      round(f52wh, 1),
        "score_20d":       round(f20d,  1),
        "score_60d":       round(f60d,  1),
        "score_vol":       round(fvol,  1),
        "score_nh":        round(fnh,   1),
        "score_rs":        round(frs,   1),
        "score_acc":       round(facc,  1),
        # Composite
        "composite_score": round(composite, 1),
        "rf_adjusted":     red_flag_active,
        "action":          action,
    }


# ── Main screener pipeline ────────────────────────────────────────────────────

FetchFn = Callable[[str, int], Optional[pd.DataFrame]]


def run_badass_screen(
    symbols: list[str],
    fetch_fn: FetchFn,
    min_price: float = 10.0,
    min_avg_volume: int = 500_000,
    apply_rf_penalty: bool = True,
    progress_cb=None,
) -> tuple[list[dict], dict]:
    """
    Screen the full symbol universe and return (ranked_rows, red_flag_info).

    Args:
        symbols:          List of tickers to screen.
        fetch_fn:         fetch_fn(symbol, days) → DataFrame | None
        min_price:        Minimum stock price filter.
        min_avg_volume:   Minimum 20-day average volume filter.
        apply_rf_penalty: Whether to apply the 15% score penalty when red flag is active.
        progress_cb:      Optional callback(i, total, ticker) for progress updates.

    Returns:
        (rows, red_flag)  where rows are sorted by composite_score descending.
    """
    import universe as univ

    # Step 1: fetch SPY for red flag detection
    spy_df = fetch_fn("SPY", 252)
    if spy_df is not None and not spy_df.empty:
        spy_px = spy_df["Adj Close"].dropna()
    else:
        spy_px = pd.Series(dtype=float)
    red_flag = compute_red_flag(spy_px)
    rf_active = red_flag["active"] and apply_rf_penalty

    # Step 2: pre-load sector ETFs (cached per ETF to avoid redundant fetches)
    sector_cache: dict[str, Optional[pd.DataFrame]] = {}

    rows: list[dict] = []
    total = len(symbols)

    for i, sym in enumerate(symbols, 1):
        if progress_cb:
            progress_cb(i, total, sym)

        try:
            df = fetch_fn(sym, 252)
            if df is None or len(df) < 60:
                continue

            px  = df["Adj Close"].dropna()
            vol = df["Volume"].dropna()

            # Apply filters
            if len(px) < 1:
                continue
            if float(px.iloc[-1]) < min_price:
                continue
            if len(vol) >= 20:
                avg_vol = float(vol.iloc[-20:].mean())
                if avg_vol < min_avg_volume:
                    continue

            # Sector ETF (cached)
            etf = univ.get_sector_etf(sym)
            if etf and etf not in sector_cache:
                sector_cache[etf] = fetch_fn(etf, 252)
            sector_df = sector_cache.get(etf) if etf else None

            row = compute_stock_factors(sym, df, sector_df, rf_active)
            if row:
                rows.append(row)

        except Exception as exc:
            logger.warning("badass_screen: %s failed — %s", sym, exc)
            continue

        time.sleep(0.05)  # polite rate limit — prevents yfinance throttling

    # Sort by composite score descending
    rows.sort(key=lambda r: r["composite_score"], reverse=True)
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank

    return rows, red_flag
