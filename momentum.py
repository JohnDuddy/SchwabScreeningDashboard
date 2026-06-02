"""
momentum.py — Quantitative Momentum Screener
Computes a multi-factor composite momentum score for a stock universe.

Data sources (in priority order):
  1. Schwab Market Data API   (uses existing OAuth tokens)
  2. Yahoo Finance via yfinance (fallback)

All return calculations use adjusted closing prices.
"""

from __future__ import annotations

import os
import json
import math
import time
import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger(__name__)

SCHWAB_MARKET_BASE = "https://api.schwabapi.com/marketdata/v1"
TOKEN_FILE         = os.environ.get("TOKEN_FILE", ".schwab_tokens.json")

# Standard lookback windows in trading days
WIN_1M, WIN_2M, WIN_3M = 21, 42, 63

# Sector → ETF mapping
SECTOR_ETF = {
    "Information Technology": "XLK",
    "Technology":              "XLK",
    "Financials":              "XLF",
    "Energy":                  "XLE",
    "Consumer Discretionary":  "XLY",
    "Health Care":             "XLV",
    "Healthcare":              "XLV",
    "Industrials":             "XLI",
    "Consumer Staples":        "XLP",
    "Utilities":               "XLU",
    "Real Estate":             "XLRE",
    "Materials":               "XLB",
    "Communication Services":  "XLC",
}


# ── Data fetching ───────────────────────────────────────────────────────────

def _load_schwab_token() -> Optional[str]:
    """Load access token from disk."""
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE) as f:
            return json.load(f).get("access_token")
    except Exception:
        return None


def fetch_schwab_history(
    symbol: str,
    days: int = 120,
    token: str | None = None,
    session: requests.Session | None = None,
) -> Optional[pd.DataFrame]:
    """Fetch daily OHLCV from Schwab. Returns DataFrame indexed by date or None on failure."""
    if not token:
        token = _load_schwab_token()
    if not token:
        return None

    end_ms   = int(time.time() * 1000)
    start_ms = end_ms - days * 86400 * 1000
    try:
        http = session or requests
        r = http.get(
            f"{SCHWAB_MARKET_BASE}/pricehistory",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "symbol":             symbol,
                "periodType":         "year",
                "frequencyType":      "daily",
                "frequency":          1,
                "startDate":          start_ms,
                "endDate":            end_ms,
                "needExtendedHoursData": "false",
            },
            timeout=20,
        )
        if r.status_code != 200:
            return None
        candles = r.json().get("candles", [])
        if not candles:
            return None
        df = pd.DataFrame(candles)
        df["date"]  = pd.to_datetime(df["datetime"], unit="ms")
        df          = df.set_index("date").rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume"
        })
        df["Adj Close"] = df["Close"]  # Schwab returns split-adjusted already
        return df[["Open", "High", "Low", "Close", "Adj Close", "Volume"]]
    except Exception as e:
        logger.warning("Schwab fetch failed for %s: %s", symbol, e)
        return None


def fetch_yahoo_history(symbol: str, days: int = 120) -> Optional[pd.DataFrame]:
    """Fallback fetch via yfinance."""
    try:
        import yfinance as yf
        end   = datetime.now()
        start = end - timedelta(days=days * 2)   # extra cushion for non-trading days
        df    = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=False)
        if df is None or df.empty:
            return None
        # Handle yfinance multi-index columns
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        # Ensure consistent columns
        if "Adj Close" not in df.columns and "Close" in df.columns:
            df["Adj Close"] = df["Close"]
        return df
    except Exception as e:
        logger.warning("Yahoo fetch failed for %s: %s", symbol, e)
        return None


def fetch_history(
    symbol: str,
    days: int = 120,
    token: str | None = None,
    session: requests.Session | None = None,
) -> Optional[pd.DataFrame]:
    """Try Schwab first, fall back to Yahoo."""
    df = fetch_schwab_history(symbol, days, token, session=session)
    if df is not None and len(df) >= WIN_3M:
        return df
    return fetch_yahoo_history(symbol, days)


# ── Metric calculations ────────────────────────────────────────────────────

def total_return(prices: pd.Series, window: int) -> float:
    if len(prices) < window + 1:
        return np.nan
    return float(prices.iloc[-1] / prices.iloc[-1 - window] - 1)


def annualized_log_return(prices: pd.Series, window: int) -> float:
    if len(prices) < window + 1:
        return np.nan
    log_ret = math.log(prices.iloc[-1] / prices.iloc[-1 - window])
    return float(log_ret * 252 / window)


def regression_metrics(prices: pd.Series, window: int) -> dict:
    """Linear regression of log price vs time over `window` days."""
    if len(prices) < window:
        return {"slope": np.nan, "slope_ann": np.nan, "r2": np.nan, "tstat": np.nan, "stderr": np.nan}
    y = np.log(prices.iloc[-window:].values)
    x = np.arange(window, dtype=float)
    x_mean, y_mean = x.mean(), y.mean()
    ss_xx = ((x - x_mean) ** 2).sum()
    ss_yy = ((y - y_mean) ** 2).sum()
    ss_xy = ((x - x_mean) * (y - y_mean)).sum()

    if ss_xx == 0:
        return {"slope": np.nan, "slope_ann": np.nan, "r2": np.nan, "tstat": np.nan, "stderr": np.nan}

    slope     = ss_xy / ss_xx
    intercept = y_mean - slope * x_mean
    y_pred    = intercept + slope * x
    residuals = y - y_pred
    sse       = (residuals ** 2).sum()
    r2        = 1 - (sse / ss_yy) if ss_yy > 0 else np.nan
    stderr    = math.sqrt(sse / (window - 2) / ss_xx) if window > 2 else np.nan
    tstat     = slope / stderr if stderr and stderr > 0 else np.nan

    return {
        "slope":     float(slope),
        "slope_ann": float(slope * 252),
        "r2":        float(r2),
        "tstat":     float(tstat),
        "stderr":    float(stderr) if stderr else np.nan,
    }


def realized_vol(returns: pd.Series, window: int) -> float:
    if len(returns) < window:
        return np.nan
    return float(returns.iloc[-window:].std() * math.sqrt(252))


def sortino_score(returns: pd.Series, window: int) -> float:
    if len(returns) < window:
        return np.nan
    r        = returns.iloc[-window:]
    downside = r[r < 0]
    if len(downside) == 0:
        return np.nan
    dd_vol = downside.std() * math.sqrt(252)
    if dd_vol == 0 or np.isnan(dd_vol):
        return np.nan
    return float(r.mean() * 252 / dd_vol)


def max_drawdown(prices: pd.Series, window: int) -> float:
    if len(prices) < window:
        return np.nan
    p     = prices.iloc[-window:]
    peak  = p.cummax()
    dd    = (p / peak - 1).min()
    return float(dd)


def rsi(prices: pd.Series, period: int = 14) -> float:
    if len(prices) < period + 1:
        return np.nan
    delta = prices.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = -delta.clip(upper=0).rolling(period).mean()
    rs    = gain / loss
    return float((100 - (100 / (1 + rs))).iloc[-1])


def macd_signal(prices: pd.Series) -> str:
    if len(prices) < 35:
        return "n/a"
    ema12  = prices.ewm(span=12, adjust=False).mean()
    ema26  = prices.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    if macd.iloc[-1] > signal.iloc[-1]:
        return "bullish"
    return "bearish"


def atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return np.nan
    high_low   = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift()).abs()
    low_close  = (df["Low"]  - df["Close"].shift()).abs()
    tr         = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def percent_up_days(returns: pd.Series, window: int) -> float:
    if len(returns) < window:
        return np.nan
    r = returns.iloc[-window:]
    return float((r > 0).sum() / len(r))


def single_day_concentration(returns: pd.Series, window: int) -> float:
    """Fraction of total log-return that comes from the single largest day."""
    if len(returns) < window:
        return np.nan
    r        = returns.iloc[-window:]
    total    = r.sum()
    if abs(total) < 1e-9:
        return np.nan
    biggest  = r.max()
    return float(biggest / total) if total > 0 else np.nan


# ── Per-stock analysis ─────────────────────────────────────────────────────

def analyze_stock(symbol: str, df: pd.DataFrame, spy: pd.DataFrame,
                  sector_etf_df: Optional[pd.DataFrame] = None) -> dict:
    """Compute all momentum metrics for one stock."""
    px      = df["Adj Close"].dropna()
    rets    = px.pct_change().dropna()
    log_ret = np.log(px / px.shift(1)).dropna()

    spy_px  = spy["Adj Close"].dropna()

    result = {"ticker": symbol, "current_price": float(px.iloc[-1]) if len(px) else np.nan}

    # 1. Absolute momentum
    result["ret_21"] = total_return(px, WIN_1M)
    result["ret_42"] = total_return(px, WIN_2M)
    result["ret_63"] = total_return(px, WIN_3M)
    result["ann_log_21"] = annualized_log_return(px, WIN_1M)
    result["ann_log_42"] = annualized_log_return(px, WIN_2M)
    result["ann_log_63"] = annualized_log_return(px, WIN_3M)

    # 2. Relative momentum
    spy_ret_21 = total_return(spy_px, WIN_1M)
    spy_ret_42 = total_return(spy_px, WIN_2M)
    spy_ret_63 = total_return(spy_px, WIN_3M)
    result["vs_spy_21"] = result["ret_21"] - spy_ret_21 if not np.isnan(result["ret_21"]) else np.nan
    result["vs_spy_42"] = result["ret_42"] - spy_ret_42 if not np.isnan(result["ret_42"]) else np.nan
    result["vs_spy_63"] = result["ret_63"] - spy_ret_63 if not np.isnan(result["ret_63"]) else np.nan

    if sector_etf_df is not None and not sector_etf_df.empty:
        sec_px = sector_etf_df["Adj Close"].dropna()
        result["vs_sector_63"] = result["ret_63"] - total_return(sec_px, WIN_3M)
    else:
        result["vs_sector_63"] = np.nan

    # Relative strength ratio slope
    try:
        joined = pd.concat([px, spy_px], axis=1, keys=["s", "b"]).dropna()
        if len(joined) >= WIN_3M:
            rs_ratio = joined["s"] / joined["b"]
            rs_slope = regression_metrics(rs_ratio, WIN_3M)["slope"]
            result["rs_slope_63"] = rs_slope
        else:
            result["rs_slope_63"] = np.nan
    except Exception:
        result["rs_slope_63"] = np.nan

    # 3. Regression metrics (use 63-day for primary)
    reg63 = regression_metrics(px, WIN_3M)
    result["reg_slope"]   = reg63["slope_ann"]
    result["reg_r2"]      = reg63["r2"]
    result["reg_tstat"]   = reg63["tstat"]

    # 4. Risk-adjusted
    vol_63              = realized_vol(rets, WIN_3M)
    result["vol_63"]    = vol_63
    result["sharpe_63"] = (result["ret_63"] * 252 / WIN_3M) / vol_63 if vol_63 and not np.isnan(vol_63) and vol_63 > 0 else np.nan
    result["sortino_63"] = sortino_score(rets, WIN_3M)
    result["max_dd_63"]  = max_drawdown(px, WIN_3M)
    result["calmar_63"]  = (result["ret_63"] / abs(result["max_dd_63"])) if result["max_dd_63"] and abs(result["max_dd_63"]) > 0 else np.nan

    # 5. Trend confirmation
    ma20 = px.rolling(20).mean().iloc[-1]
    ma50 = px.rolling(50).mean().iloc[-1]
    result["above_ma20"]   = bool(px.iloc[-1] > ma20) if not np.isnan(ma20) else False
    result["above_ma50"]   = bool(px.iloc[-1] > ma50) if not np.isnan(ma50) else False
    result["ma20_gt_ma50"] = bool(ma20 > ma50) if not (np.isnan(ma20) or np.isnan(ma50)) else False
    result["pct_up_21"]    = percent_up_days(rets, WIN_1M)
    result["pct_up_63"]    = percent_up_days(rets, WIN_3M)

    high_63 = px.iloc[-WIN_3M:].max() if len(px) >= WIN_3M else np.nan
    result["dist_from_high_63"] = float(px.iloc[-1] / high_63 - 1) if high_63 and not np.isnan(high_63) else np.nan

    # 6. Volume confirmation
    vol_series = df["Volume"].dropna()
    if len(vol_series) >= 20:
        avg20         = vol_series.iloc[-20:].mean()
        result["vol_vs_20"] = float(vol_series.iloc[-1] / avg20) if avg20 > 0 else np.nan
        avg5          = vol_series.iloc[-5:].mean()
        result["vol5_vs_20"] = float(avg5 / avg20) if avg20 > 0 else np.nan
    else:
        result["vol_vs_20"]  = np.nan
        result["vol5_vs_20"] = np.nan

    # Technicals
    result["rsi_14"]     = rsi(px, 14)
    result["macd"]       = macd_signal(px)
    result["atr_14"]     = atr(df, 14)

    # 7/8. Penalties & overextension flags
    result["single_day_pct"] = single_day_concentration(log_ret, WIN_3M)

    overextended = False
    flags        = []
    if result["rsi_14"] and result["rsi_14"] > 75:
        overextended = True
        flags.append("RSI>75")
    if result["atr_14"] and not np.isnan(result["atr_14"]) and not np.isnan(ma20):
        dist_atr = (px.iloc[-1] - ma20) / result["atr_14"]
        if dist_atr > 2.5:
            overextended = True
            flags.append(f"{dist_atr:.1f}xATR above MA20")
    if not np.isnan(ma50) and px.iloc[-1] / ma50 - 1 > 0.15:
        overextended = True
        flags.append("15%+ above MA50")
    if result["single_day_pct"] and result["single_day_pct"] > 0.5:
        flags.append("Gap-driven")

    result["overextended"] = overextended
    result["flags"]        = "; ".join(flags)

    return result


# ── Composite scoring ──────────────────────────────────────────────────────

def winsorize(s: pd.Series, lower: float = 0.05, upper: float = 0.95) -> pd.Series:
    lo = s.quantile(lower)
    hi = s.quantile(upper)
    return s.clip(lower=lo, upper=hi)


def z_score(s: pd.Series) -> pd.Series:
    s = winsorize(s)
    mu = s.mean()
    sd = s.std()
    if sd == 0 or np.isnan(sd):
        return pd.Series(0, index=s.index)
    return (s - mu) / sd


def percentile_rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True) * 100


def build_composite(df: pd.DataFrame) -> pd.DataFrame:
    """Add composite_score and classification columns. Expects analyze_stock rows."""
    df = df.copy()

    # All metrics where "higher = better"
    higher_better = [
        "ret_21", "ret_42", "ret_63",
        "vs_spy_21", "vs_spy_42", "vs_spy_63",
        "vs_sector_63", "rs_slope_63",
        "reg_slope", "reg_r2", "reg_tstat",
        "sharpe_63", "sortino_63", "calmar_63",
        "pct_up_21", "pct_up_63",
        "vol_vs_20", "vol5_vs_20",
    ]

    # Ensure columns exist and percentile-rank in a single pass
    pct = pd.DataFrame(index=df.index)
    for m in higher_better:
        if m not in df.columns:
            df[m] = np.nan
        pct[m] = percentile_rank(df[m])

    # Weighted composite (boolean trend confirmations contribute fixed pts)
    score = (
        # A. Absolute momentum (25%)
        pct["ret_21"]      * 0.08 +
        pct["ret_42"]      * 0.08 +
        pct["ret_63"]      * 0.09 +
        # B. Relative momentum (20%)
        pct["vs_spy_63"]   * 0.10 +
        pct["vs_sector_63"].fillna(50) * 0.05 +
        pct["rs_slope_63"] * 0.05 +
        # C. Regression trend quality (20%)
        pct["reg_slope"]   * 0.07 +
        pct["reg_r2"]      * 0.06 +
        pct["reg_tstat"]   * 0.07 +
        # D. Risk-adjusted (15%)
        pct["sharpe_63"]   * 0.05 +
        pct["sortino_63"]  * 0.05 +
        pct["calmar_63"]   * 0.05 +
        # F. Volume confirmation (5%)
        pct["vol_vs_20"]   * 0.025 +
        pct["vol5_vs_20"]  * 0.025
    )

    # E. Trend confirmation flat bonus (10%)
    trend_bonus = (
        df["above_ma20"].astype(float)   * 2.5 +
        df["above_ma50"].astype(float)   * 3.5 +
        df["ma20_gt_ma50"].astype(float) * 2.0 +
        (df["pct_up_63"] > 0.5).astype(float) * 2.0
    )
    score = score + trend_bonus

    # G. Penalties
    penalty = pd.Series(0.0, index=df.index)
    penalty += (df["single_day_pct"] > 0.5).fillna(False).astype(float) * 10
    penalty += df["overextended"].astype(float) * 5
    penalty += (df["ret_21"] < 0).fillna(False).astype(float) * 5

    score = (score - penalty).clip(0, 100)
    df["composite_score"] = score.round(1)

    # Classification (vectorized)
    clear = (
        (df["ret_63"] > 0) &
        (df["ret_42"] > 0) &
        (df["vs_spy_63"].fillna(0) > 0) &
        (df["reg_slope"] > 0) &
        (df["reg_tstat"].fillna(0) > 1.0) &
        df["above_ma50"]
    )
    s = df["composite_score"]
    df["classification"] = np.where(
        (s >= 80) & clear, "Strong",
        np.where(
            (s >= 65) & clear, "Moderate",
            np.where(s >= 50, "Weak/Unconfirmed", "No Clear Momentum")
        )
    )
    df["rank"]           = df["composite_score"].rank(ascending=False, method="min").astype("Int64")

    return df.sort_values("composite_score", ascending=False).reset_index(drop=True)


# ── Universe loaders ───────────────────────────────────────────────────────

def load_sp500_tickers() -> list[str]:
    """Load S&P 500 + Nasdaq 100 universe via universe.py (hardcoded baseline + monthly refresh)."""
    import universe as univ
    tickers, meta = univ.load_universe()
    logger.info("Universe loaded: %d tickers from %s", meta["count"], meta["source"])
    return tickers


# ── Main pipeline ──────────────────────────────────────────────────────────

def run_screen(symbols: list[str], days: int = 120, progress_cb=None) -> pd.DataFrame:
    """
    Run the full momentum screen across `symbols`.
    progress_cb: optional callback(i, total, ticker) for UI updates.
    """
    token = _load_schwab_token()
    logger.info("Schwab token available: %s", bool(token))

    import universe as univ
    with requests.Session() as session:
        spy = fetch_history("SPY", days, token, session=session)
        if spy is None or spy.empty:
            raise RuntimeError("Could not fetch SPY benchmark data")

        # Pre-fetch each unique sector ETF once rather than once-per-stock
        sector_etf_map = {sym: univ.get_sector_etf(sym) for sym in symbols}
        unique_etfs = {etf for etf in sector_etf_map.values() if etf and etf != "SPY"}
        sector_df_cache: dict[str, pd.DataFrame] = {}
        for etf in unique_etfs:
            etf_df = fetch_history(etf, days, token, session=session)
            if etf_df is not None:
                sector_df_cache[etf] = etf_df
        logger.info("Pre-fetched %d sector ETFs", len(sector_df_cache))

        rows = []
        total = len(symbols)
        for i, sym in enumerate(symbols, 1):
            if progress_cb:
                progress_cb(i, total, sym)
            df = fetch_history(sym, days, token, session=session)
            if df is None or len(df) < WIN_3M:
                continue
            try:
                sector_etf = sector_etf_map.get(sym)
                sec_df = sector_df_cache.get(sector_etf) if sector_etf else None
                row = analyze_stock(sym, df, spy, sector_etf_df=sec_df)
                rows.append(row)
            except Exception as e:
                logger.warning("analyze_stock failed for %s: %s", sym, e)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = build_composite(df)
    return df
