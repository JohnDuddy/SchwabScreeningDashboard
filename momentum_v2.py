"""
momentum_v2.py — Advanced Multi-Factor Momentum Screener (v2)

9-factor composite score (0-100) with market regime Red Flag filter.

Factor weights:
  52-Week High Proximity        22%
  3-Month Price Momentum        18%
  New 20/50-Day Highs           12%
  Volume Surge + Price Confirm  12%
  Above Rising 50-Day MA        10%
  Sector Relative Strength       8%
  ROC Acceleration               8%
  Residual Momentum              5%
  Consistent Momentum            5%

Red Flag: SPY close < 200-day MA  →  final score × 0.65

Data: Schwab Market Data API (primary), yfinance (fallback).
Reuses fetch_history() and related helpers from momentum.py.
"""

from __future__ import annotations

import time
import logging
import numpy as np
import pandas as pd
from typing import Optional

from momentum import fetch_history, percentile_rank, _load_schwab_token

logger = logging.getLogger(__name__)

HISTORY_DAYS = 280  # 252 for 52w high + buffer for non-trading days
RED_FLAG_MULTIPLIER = 0.65


# ── Market regime ────────────────────────────────────────────────────────────

def check_market_regime(spy_df: pd.DataFrame) -> dict:
    """Return Red Flag status: SPY below 200-day MA triggers the flag."""
    px = spy_df["Adj Close"].dropna()
    if len(px) < 200:
        return {
            "red_flag": False,
            "spy_price": float(px.iloc[-1]) if len(px) else None,
            "ma200": None,
            "pct_from_ma200": None,
            "insufficient_data": True,
        }
    ma200 = float(px.rolling(200).mean().iloc[-1])
    spy_price = float(px.iloc[-1])
    return {
        "red_flag": spy_price < ma200,
        "spy_price": round(spy_price, 2),
        "ma200": round(ma200, 2),
        "pct_from_ma200": round((spy_price / ma200 - 1) * 100, 2),
        "insufficient_data": False,
    }


# ── Per-stock factor calculations ────────────────────────────────────────────

def analyze_stock_v2(
    symbol: str,
    df: pd.DataFrame,
    spy_df: pd.DataFrame,
    sector_etf_df: Optional[pd.DataFrame] = None,
) -> dict:
    """Compute all 9 raw factor values for one stock."""
    px = df["Adj Close"].dropna()
    vol = df["Volume"].dropna()
    spy_px = spy_df["Adj Close"].dropna()

    result: dict = {
        "ticker": symbol,
        "current_price": float(px.iloc[-1]) if len(px) else np.nan,
    }

    # ── Factor 1: 52-Week High Proximity (22%) ────────────────────────────
    # (current_price / 52_week_high) * 100
    if len(px) >= 252:
        high_52w = float(px.iloc[-252:].max())
    elif len(px) >= 20:
        high_52w = float(px.max())
    else:
        high_52w = np.nan
    result["high_52w"] = high_52w
    result["prox_52w"] = float(px.iloc[-1] / high_52w * 100) if high_52w and not np.isnan(high_52w) else np.nan

    # ── Factor 2: 3-Month Momentum (18%) ─────────────────────────────────
    # ((current / price_63_days_ago) - 1) * 100
    result["ret_63"] = float((px.iloc[-1] / px.iloc[-64] - 1) * 100) if len(px) >= 64 else np.nan

    # ── Factor 3: New 20/50-Day Highs (12%) ──────────────────────────────
    # Is today's close at/above the prior 20-day or 50-day high?
    new_20d = bool(px.iloc[-1] >= px.iloc[-20:-1].max()) if len(px) >= 21 else False
    new_50d = bool(px.iloc[-1] >= px.iloc[-50:-1].max()) if len(px) >= 51 else False
    result["new_20d_high"] = new_20d
    result["new_50d_high"] = new_50d
    result["new_highs_score"] = (0.4 * float(new_20d) + 0.6 * float(new_50d)) * 100  # 0/40/60/100

    # ── Factor 4: Volume Surge + Price Confirmation (12%) ─────────────────
    # vol_surge = today_vol / 20-day_avg_vol; only counts when price is up
    if len(vol) >= 21 and len(px) >= 2:
        avg_vol_20 = float(vol.iloc[-21:-1].mean())
        today_vol = float(vol.iloc[-1])
        price_up = bool(px.iloc[-1] > px.iloc[-2])
        vol_surge_raw = today_vol / avg_vol_20 if avg_vol_20 > 0 else 0.0
        result["vol_surge"] = vol_surge_raw if price_up else 0.0
        result["vol_confirmed"] = price_up
    else:
        result["vol_surge"] = np.nan
        result["vol_confirmed"] = False

    # ── Factor 5: Above Rising 50-Day MA (10%) ────────────────────────────
    # 100 = above AND slope positive; 50 = above only; 0 = below
    if len(px) >= 52:
        ma50_series = px.rolling(50).mean()
        ma50_today = float(ma50_series.iloc[-1])
        ma50_prev = float(ma50_series.iloc[-2])
        above = bool(px.iloc[-1] > ma50_today)
        rising = bool(ma50_today > ma50_prev)
        result["ma50_score"] = 100.0 if (above and rising) else (50.0 if above else 0.0)
        result["above_ma50"] = above
        result["ma50_rising"] = rising
    else:
        result["ma50_score"] = np.nan
        result["above_ma50"] = False
        result["ma50_rising"] = False

    # ── Factor 6: Sector Relative Strength (8%) ───────────────────────────
    # stock 3-month return minus sector 3-month return
    benchmark_px = sector_etf_df["Adj Close"].dropna() if sector_etf_df is not None and not sector_etf_df.empty else spy_px
    if len(benchmark_px) >= 64 and len(px) >= 64 and not np.isnan(result["ret_63"]):
        bench_ret_63 = float((benchmark_px.iloc[-1] / benchmark_px.iloc[-64] - 1) * 100)
        result["vs_sector_63"] = result["ret_63"] - bench_ret_63
    else:
        result["vs_sector_63"] = np.nan

    # ── Factor 7: ROC Acceleration (8%) ───────────────────────────────────
    # Compares annualized 1-month return vs annualized 3-month return.
    # Positive = stock is accelerating (short-term momentum > long-term).
    if len(px) >= 64:
        roc_21 = float((px.iloc[-1] / px.iloc[-22] - 1) * 100) if len(px) >= 22 else np.nan
        roc_63 = result["ret_63"]
        if not np.isnan(roc_21) and roc_63 is not None and not np.isnan(roc_63):
            # Annualize both to make them comparable across time windows
            roc_21_ann = ((1 + roc_21 / 100) ** (252 / 21) - 1) * 100
            roc_63_ann = ((1 + roc_63 / 100) ** (252 / 63) - 1) * 100
            result["roc_accel"] = roc_21_ann - roc_63_ann
        else:
            result["roc_accel"] = np.nan
        result["roc_21"] = roc_21
    else:
        result["roc_accel"] = np.nan
        result["roc_21"] = np.nan

    # ── Factor 8: Residual Momentum (5%) ─────────────────────────────────
    # Stock excess return after adjusting for market beta (simplified CAPM residual).
    if len(px) >= 64 and len(spy_px) >= 64:
        try:
            s_rets = px.pct_change().dropna()
            m_rets = spy_px.pct_change().dropna()
            joined = pd.concat([s_rets, m_rets], axis=1, keys=["s", "m"]).dropna().iloc[-63:]
            if len(joined) >= 20:
                cov_mat = np.cov(joined["s"].values, joined["m"].values)
                var_m = cov_mat[1, 1]
                beta = cov_mat[0, 1] / var_m if var_m > 0 else 1.0
                spy_ret_63 = float((spy_px.iloc[-1] / spy_px.iloc[-64] - 1) * 100)
                result["beta"] = round(beta, 2)
                result["residual_momentum"] = result["ret_63"] - beta * spy_ret_63
            else:
                result["beta"] = np.nan
                result["residual_momentum"] = np.nan
        except Exception:
            result["beta"] = np.nan
            result["residual_momentum"] = np.nan
    else:
        result["beta"] = np.nan
        result["residual_momentum"] = np.nan

    # ── Factor 9: Consistent Momentum (5%) ───────────────────────────────
    # Fraction of the past 12 weekly periods (5-day intervals) with a positive return.
    if len(px) >= 13:
        pts = []
        for k in range(13):
            idx = -(1 + k * 5)
            if abs(idx) <= len(px):
                pts.append(float(px.iloc[idx]))
            else:
                break
        if len(pts) >= 2:
            weekly_rets = [pts[k] / pts[k + 1] - 1 for k in range(len(pts) - 1)]
            result["consistent_momentum"] = round(sum(1 for r in weekly_rets if r > 0) / len(weekly_rets) * 100, 1)
        else:
            result["consistent_momentum"] = np.nan
    else:
        result["consistent_momentum"] = np.nan

    return result


# ── Composite scoring ─────────────────────────────────────────────────────────

def build_composite_v2(df: pd.DataFrame, red_flag_active: bool = False) -> pd.DataFrame:
    """
    Percentile-rank each factor, apply weights, apply Red Flag multiplier.
    Returns df sorted by composite_score desc with 'rank' column added.
    """
    df = df.copy()

    def _pct(col, fill_median=True):
        s = df[col].copy()
        if fill_median:
            s = s.fillna(s.median())
        else:
            s = s.fillna(0)
        return percentile_rank(s)

    df["pct_prox_52w"]   = _pct("prox_52w").round(1)
    df["pct_ret_63"]     = _pct("ret_63").round(1)
    df["pct_new_highs"]  = _pct("new_highs_score", fill_median=False).round(1)
    df["pct_vol_surge"]  = _pct("vol_surge", fill_median=False).round(1)
    df["pct_ma50"]       = _pct("ma50_score", fill_median=False).round(1)
    df["pct_sector_rs"]  = _pct("vs_sector_63").round(1)
    df["pct_roc_accel"]  = _pct("roc_accel").round(1)
    df["pct_residual"]   = _pct("residual_momentum").round(1)
    df["pct_consistent"] = _pct("consistent_momentum", fill_median=False).round(1)

    score = (
        df["pct_prox_52w"]   * 0.22 +
        df["pct_ret_63"]     * 0.18 +
        df["pct_new_highs"]  * 0.12 +
        df["pct_vol_surge"]  * 0.12 +
        df["pct_ma50"]       * 0.10 +
        df["pct_sector_rs"]  * 0.08 +
        df["pct_roc_accel"]  * 0.08 +
        df["pct_residual"]   * 0.05 +
        df["pct_consistent"] * 0.05
    )

    if red_flag_active:
        score = score * RED_FLAG_MULTIPLIER

    df["composite_score"] = score.clip(0, 100).round(1)
    df["rank"] = df["composite_score"].rank(ascending=False, method="min").astype("Int64")

    return df.sort_values("composite_score", ascending=False).reset_index(drop=True)


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_screen_v2(
    symbols: list[str],
    days: int = HISTORY_DAYS,
    progress_cb=None,
) -> tuple[pd.DataFrame, dict]:
    """
    Run the full v2 screen across symbols.
    Returns (results_df, regime_dict).
    progress_cb: optional callback(i, total, ticker)
    """
    import universe as univ

    token = _load_schwab_token()
    spy = fetch_history("SPY", max(days, 260), token)
    if spy is None or spy.empty:
        raise RuntimeError("Could not fetch SPY benchmark data")

    regime = check_market_regime(spy)

    rows = []
    total = len(symbols)
    for i, sym in enumerate(symbols, 1):
        if progress_cb:
            progress_cb(i, total, sym)
        df = fetch_history(sym, days, token)
        if df is None or len(df) < 63:
            continue
        try:
            sector_etf = univ.get_sector_etf(sym)
            sec_df = None
            if sector_etf and sector_etf != "SPY":
                sec_df = fetch_history(sector_etf, days, token)
            row = analyze_stock_v2(sym, df, spy, sector_etf_df=sec_df)
            rows.append(row)
        except Exception as e:
            logger.warning("analyze_stock_v2 failed for %s: %s", sym, e)
        time.sleep(0.05)

    if not rows:
        return pd.DataFrame(), regime

    result_df = pd.DataFrame(rows)
    result_df = build_composite_v2(result_df, red_flag_active=regime["red_flag"])
    return result_df, regime
