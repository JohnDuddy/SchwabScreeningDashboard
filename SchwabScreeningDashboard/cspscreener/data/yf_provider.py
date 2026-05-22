"""
yfinance-based DataProvider.

Pulls stock fundamentals, price history, and option chain from Yahoo Finance.
Falls back gracefully when fields are missing.
"""

from __future__ import annotations

import math
import warnings
from datetime import datetime, timedelta
from typing import List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

from .. import config
from ..models import StockSnapshot, OptionCandidate
from .provider import DataProvider

warnings.filterwarnings("ignore")

# Cache SPY history for relative-strength calc
_SPY_HISTORY: Optional[pd.DataFrame] = None


def _spy_history() -> Optional[pd.DataFrame]:
    global _SPY_HISTORY
    if _SPY_HISTORY is None:
        try:
            _SPY_HISTORY = yf.Ticker("SPY").history(
                period=f"{config.PRICE_HISTORY_DAYS}d", auto_adjust=True
            )
        except Exception:
            _SPY_HISTORY = pd.DataFrame()
    return _SPY_HISTORY if _SPY_HISTORY is not None and not _SPY_HISTORY.empty else None


def fetch_vix_level() -> Optional[float]:
    """Fetch current VIX level. Called once at scan start."""
    try:
        vix = yf.Ticker("^VIX")
        h = vix.history(period="5d", auto_adjust=True)
        if h is not None and not h.empty:
            return float(h["Close"].iloc[-1])
    except Exception:
        pass
    return None


def _safe(d: dict, key: str, default=None):
    v = d.get(key, default)
    if v is None:
        return default
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return default
    return v


def _rsi(close: pd.Series, period: int = 14) -> Optional[float]:
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return float(rsi.iloc[-1]) if not math.isnan(rsi.iloc[-1]) else None


def _atr(hist: pd.DataFrame, period: int = 14) -> Optional[float]:
    if len(hist) < period + 1:
        return None
    h, l, c = hist["High"], hist["Low"], hist["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def _piotroski_f_score(info: dict, fin: pd.DataFrame, bs: pd.DataFrame, cf: pd.DataFrame) -> Optional[int]:
    """
    Compute Piotroski F-score (0-9) from yfinance financial statements.
    Returns None if too much data is missing.
    """
    try:
        if fin is None or fin.empty or len(fin.columns) < 2:
            return None
        cur, prv = fin.columns[0], fin.columns[1]

        def get(df, key, col):
            if df is None or df.empty or key not in df.index or col not in df.columns:
                return None
            v = df.loc[key, col]
            if pd.isna(v):
                return None
            return float(v)

        score = 0
        ni_cur  = get(fin, "Net Income", cur)
        ni_prv  = get(fin, "Net Income", prv)
        rev_cur = get(fin, "Total Revenue", cur)
        rev_prv = get(fin, "Total Revenue", prv)
        gp_cur  = get(fin, "Gross Profit", cur)
        gp_prv  = get(fin, "Gross Profit", prv)

        ta_cur = get(bs, "Total Assets", cur) if bs is not None else None
        ta_prv = get(bs, "Total Assets", prv) if bs is not None else None
        ltd_cur = get(bs, "Long Term Debt", cur) if bs is not None else None
        ltd_prv = get(bs, "Long Term Debt", prv) if bs is not None else None
        ca_cur = get(bs, "Current Assets", cur) if bs is not None else None
        cl_cur = get(bs, "Current Liabilities", cur) if bs is not None else None
        ca_prv = get(bs, "Current Assets", prv) if bs is not None else None
        cl_prv = get(bs, "Current Liabilities", prv) if bs is not None else None
        shares_cur = get(bs, "Share Issued", cur) if bs is not None else None
        shares_prv = get(bs, "Share Issued", prv) if bs is not None else None

        cfo_cur = get(cf, "Operating Cash Flow", cur) if cf is not None else None

        # 1. Positive net income
        if ni_cur is not None and ni_cur > 0: score += 1
        # 2. Positive operating cash flow
        if cfo_cur is not None and cfo_cur > 0: score += 1
        # 3. ROA improving
        if ni_cur and ni_prv and ta_cur and ta_prv:
            if (ni_cur / ta_cur) > (ni_prv / ta_prv): score += 1
        # 4. CFO > NI (earnings quality)
        if cfo_cur is not None and ni_cur is not None and cfo_cur > ni_cur: score += 1
        # 5. Long-term debt declining
        if ltd_cur is not None and ltd_prv is not None and ltd_cur < ltd_prv: score += 1
        # 6. Current ratio improving
        if ca_cur and cl_cur and ca_prv and cl_prv and cl_cur > 0 and cl_prv > 0:
            if (ca_cur / cl_cur) > (ca_prv / cl_prv): score += 1
        # 7. No dilution (shares not increasing materially)
        if shares_cur is not None and shares_prv is not None:
            if shares_cur <= shares_prv * 1.005: score += 1
        # 8. Gross margin improving
        if gp_cur and gp_prv and rev_cur and rev_prv and rev_cur > 0 and rev_prv > 0:
            if (gp_cur / rev_cur) > (gp_prv / rev_prv): score += 1
        # 9. Asset turnover improving
        if rev_cur and rev_prv and ta_cur and ta_prv:
            if (rev_cur / ta_cur) > (rev_prv / ta_prv): score += 1

        return score
    except Exception:
        return None


def _altman_z(info: dict, bs: pd.DataFrame, fin: pd.DataFrame) -> Optional[float]:
    """
    Altman Z-score for public manufacturers:
      Z = 1.2 A + 1.4 B + 3.3 C + 0.6 D + 1.0 E
    where:
      A = working_capital / total_assets
      B = retained_earnings / total_assets
      C = EBIT / total_assets
      D = market_cap / total_liabilities
      E = sales / total_assets
    """
    try:
        if bs is None or bs.empty or len(bs.columns) < 1:
            return None
        col = bs.columns[0]

        def g(df, key):
            if df is None or df.empty or key not in df.index or col not in df.columns:
                return None
            v = df.loc[key, col]
            return float(v) if not pd.isna(v) else None

        ca = g(bs, "Current Assets")
        cl = g(bs, "Current Liabilities")
        ta = g(bs, "Total Assets")
        re = g(bs, "Retained Earnings")
        tl = g(bs, "Total Liabilities Net Minority Interest") or g(bs, "Total Liab")
        sales = g(fin, "Total Revenue")
        ebit = g(fin, "EBIT") or g(fin, "Operating Income")
        mc = info.get("marketCap")

        if not all(v is not None and v > 0 for v in [ta, tl, mc, sales, ca, cl]):
            return None
        if ebit is None or re is None:
            return None

        wc = ca - cl
        a = wc / ta
        b = re / ta
        c = ebit / ta
        d = mc / tl
        e = sales / ta
        z = 1.2*a + 1.4*b + 3.3*c + 0.6*d + 1.0*e
        return z
    except Exception:
        return None


class YFinanceProvider(DataProvider):
    """Wraps yfinance — implements DataProvider for stock + options."""

    def fetch_stock(self, ticker: str) -> Optional[StockSnapshot]:
        try:
            tk = yf.Ticker(ticker)
            hist = tk.history(period=f"{config.PRICE_HISTORY_DAYS}d", auto_adjust=True)
            if hist is None or hist.empty or len(hist) < 30:
                return None

            try:
                info = tk.info or {}
            except Exception:
                info = {}

            close = hist["Close"]
            volume = hist["Volume"]
            spot = float(close.iloc[-1])
            avg_vol = float(volume.tail(21).mean())
            avg_dollar_vol = avg_vol * spot

            snap = StockSnapshot(
                ticker=ticker,
                company_name=str(_safe(info, "longName", "") or _safe(info, "shortName", "") or ticker),
                sector=str(_safe(info, "sector", "") or ""),
                industry=str(_safe(info, "industry", "") or ""),
                price=spot,
                market_cap=float(_safe(info, "marketCap", 0) or 0),
                avg_share_volume=avg_vol,
                avg_dollar_volume=avg_dollar_vol,
            )

            # Fundamentals from info
            snap.pe_trailing      = _safe(info, "trailingPE")
            snap.pe_forward       = _safe(info, "forwardPE")
            snap.ev_ebitda        = _safe(info, "enterpriseToEbitda")
            snap.ev_sales         = _safe(info, "enterpriseToRevenue")
            snap.price_to_book    = _safe(info, "priceToBook")
            snap.dividend_yield   = _safe(info, "dividendYield")
            snap.payout_ratio     = _safe(info, "payoutRatio")
            snap.profit_margin    = _safe(info, "profitMargins")
            snap.operating_margin = _safe(info, "operatingMargins")
            snap.gross_margin     = _safe(info, "grossMargins")
            snap.roe              = _safe(info, "returnOnEquity")
            snap.roa              = _safe(info, "returnOnAssets")
            snap.debt_to_equity   = _safe(info, "debtToEquity")
            snap.current_ratio    = _safe(info, "currentRatio")
            snap.quick_ratio      = _safe(info, "quickRatio")
            snap.revenue_growth   = _safe(info, "revenueGrowth")
            snap.earnings_growth  = _safe(info, "earningsGrowth")
            snap.free_cashflow    = _safe(info, "freeCashflow")
            snap.operating_cashflow = _safe(info, "operatingCashflow")
            snap.total_debt       = _safe(info, "totalDebt")
            snap.total_cash       = _safe(info, "totalCash")
            snap.ebitda           = _safe(info, "ebitda")
            snap.short_percent_of_float = _safe(info, "shortPercentOfFloat")

            # FCF yield = FCF / market_cap
            if snap.free_cashflow and snap.market_cap and snap.market_cap > 0:
                snap.fcf_yield = snap.free_cashflow / snap.market_cap

            # Net income (for Beneish/accruals later)
            try:
                fin = tk.financials
                bs  = tk.balance_sheet
                cf  = tk.cashflow
            except Exception:
                fin, bs, cf = None, None, None

            if fin is not None and not fin.empty and "Net Income" in fin.index:
                snap.net_income = float(fin.loc["Net Income"].iloc[0]) if not pd.isna(fin.loc["Net Income"].iloc[0]) else None

            # F-score and Z-score
            snap.piotroski_f = _piotroski_f_score(info, fin, bs, cf)
            snap.altman_z = _altman_z(info, bs, fin)

            # Technicals
            if len(close) >= 20:  snap.sma_20  = float(close.tail(20).mean())
            if len(close) >= 50:  snap.sma_50  = float(close.tail(50).mean())
            if len(close) >= 200: snap.sma_200 = float(close.tail(200).mean())

            snap.rsi_14 = _rsi(close, 14)
            snap.atr_14 = _atr(hist, 14)

            if len(close) >= 63:  snap.momentum_3m  = float(close.iloc[-1] / close.iloc[-63]  - 1)
            if len(close) >= 126: snap.momentum_6m  = float(close.iloc[-1] / close.iloc[-126] - 1)
            if len(close) >= 252: snap.momentum_12m = float(close.iloc[-1] / close.iloc[-252] - 1)

            # Relative strength vs SPY (3-month)
            spy = _spy_history()
            if spy is not None and len(spy) >= 63 and snap.momentum_3m is not None:
                try:
                    spy_3m = float(spy["Close"].iloc[-1] / spy["Close"].iloc[-63] - 1)
                    snap.rs_vs_spy_3m = snap.momentum_3m - spy_3m
                except Exception:
                    pass

            # 52-week extremes
            year = close.tail(252) if len(close) >= 252 else close
            hi52 = float(year.max())
            lo52 = float(year.min())
            if hi52 > 0: snap.pct_from_52w_high = (spot - hi52) / hi52
            if lo52 > 0: snap.pct_from_52w_low  = (spot - lo52) / lo52

            # Beta
            snap.beta = _safe(info, "beta")

            # Historical volatility (30-day, annualized)
            if len(close) >= 31:
                log_ret = np.log(close / close.shift(1)).dropna().tail(30)
                if len(log_ret) >= 20:
                    snap.hv_30 = float(log_ret.std() * np.sqrt(252))

            # 52-week rolling HV range (for IV rank computation)
            if len(close) >= 252:
                all_log_ret = np.log(close / close.shift(1)).dropna()
                rolling_hv = all_log_ret.rolling(30).std() * np.sqrt(252)
                rolling_hv = rolling_hv.dropna()
                if len(rolling_hv) >= 100:
                    snap.hv_52w_low = float(rolling_hv.min())
                    snap.hv_52w_high = float(rolling_hv.max())

            # Earnings date
            try:
                cal = tk.calendar
                if isinstance(cal, dict):
                    earn = cal.get("Earnings Date")
                    if earn:
                        if isinstance(earn, list) and earn:
                            d = earn[0]
                        else:
                            d = earn
                        if hasattr(d, "strftime"):
                            snap.next_earnings_date = d.strftime("%Y-%m-%d")
                        else:
                            snap.next_earnings_date = str(d)[:10]
                elif isinstance(cal, pd.DataFrame) and not cal.empty:
                    if "Earnings Date" in cal.index:
                        d = cal.loc["Earnings Date"].iloc[0]
                        if hasattr(d, "strftime"):
                            snap.next_earnings_date = d.strftime("%Y-%m-%d")
            except Exception:
                pass

            # Ex-dividend date
            ed = _safe(info, "exDividendDate")
            if ed:
                try:
                    snap.ex_dividend_date = datetime.utcfromtimestamp(int(ed)).strftime("%Y-%m-%d")
                except Exception:
                    pass

            return snap
        except Exception as e:
            return None

    def fetch_options(self, ticker: str, spot: float) -> List[OptionCandidate]:
        """Pull all expirations within DTE_MIN..DTE_MAX, return raw put rows."""
        try:
            tk = yf.Ticker(ticker)
            try:
                exps = tk.options
            except Exception:
                return []
            if not exps:
                return []

            today = datetime.now().date()
            out: List[OptionCandidate] = []

            for exp_str in exps:
                try:
                    d = datetime.strptime(exp_str, "%Y-%m-%d").date()
                except Exception:
                    continue
                dte = (d - today).days
                if not (config.DTE_MIN <= dte <= config.DTE_MAX):
                    continue

                try:
                    chain = tk.option_chain(exp_str)
                    puts = chain.puts
                except Exception:
                    continue
                if puts is None or puts.empty:
                    continue

                # Only consider strikes BELOW spot (OTM puts) for cash-secured puts
                otm = puts[puts["strike"] < spot].copy()
                if otm.empty:
                    continue

                for _, row in otm.iterrows():
                    bid = float(row.get("bid") or 0)
                    ask = float(row.get("ask") or 0)
                    last = float(row.get("lastPrice") or 0)
                    if bid <= 0 or ask <= 0:
                        continue
                    mid = (bid + ask) / 2
                    if mid <= 0:
                        continue
                    spread_pct = (ask - bid) / mid if mid > 0 else 1.0
                    iv = row.get("impliedVolatility")
                    iv = float(iv) if iv is not None and not pd.isna(iv) else None
                    oi = int(row.get("openInterest") or 0)
                    vol = int(row.get("volume") or 0)
                    strike = float(row["strike"])

                    # Approximate delta from IV (Black-Scholes) — yfinance doesn't expose Greeks
                    delta = self._approx_put_delta(spot, strike, dte, iv) if iv else None

                    out.append(OptionCandidate(
                        ticker=ticker,
                        expiration=exp_str,
                        dte=dte,
                        strike=strike,
                        bid=bid, ask=ask, mid=mid, last=last,
                        open_interest=oi,
                        volume=vol,
                        spread_pct=spread_pct,
                        delta=delta,
                        iv=iv,
                    ))
            return out
        except Exception:
            return []

    @staticmethod
    def _approx_put_delta(spot: float, strike: float, dte: int, iv: float) -> Optional[float]:
        """Black-Scholes put delta approximation."""
        try:
            if iv is None or iv <= 0 or dte <= 0 or spot <= 0 or strike <= 0:
                return None
            from math import log, sqrt, exp, erf
            T = dte / 365.0
            r = config.RISK_FREE_RATE
            sigma = iv
            d1 = (log(spot / strike) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt(T))
            # N(d1)
            N_d1 = 0.5 * (1 + erf(d1 / sqrt(2)))
            put_delta = N_d1 - 1.0  # negative
            return put_delta
        except Exception:
            return None
