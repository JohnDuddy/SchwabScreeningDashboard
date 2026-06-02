"""
Financial data fetcher for equity ranking.

Two fetch modes:
  fetch_universe_bulk() — fast, for screening all 516 tickers
    • Schwab batch API: real-time price + ~20 fundamental ratios (6 API calls total)
    • yfinance .info fallback for any ticker Schwab doesn't cover
    • Does NOT fetch multi-year financial statements (too slow for 516 stocks)

  fetch_ticker_detail() — thorough, for AI write-up of 1-10 stocks
    • Schwab fundamentals + yfinance multi-year income stmt/cash flow/balance sheet
"""
from __future__ import annotations
import requests
import yfinance as yf
import pandas as pd
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

SCHWAB_MARKET_BASE = "https://api.schwabapi.com/marketdata/v1"


# ─────────────────────────── Schwab helpers ───────────────────────────────────

def _schwab_batch(symbols: list[str], token: str) -> dict:
    """One Schwab quotes call for up to 100 symbols. Returns raw JSON dict."""
    try:
        resp = requests.get(
            f"{SCHWAB_MARKET_BASE}/quotes",
            headers={"Authorization": f"Bearer {token}"},
            params={"symbols": ",".join(symbols), "fields": "quote,fundamental"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("Schwab batch failed for %d symbols: %s", len(symbols), e)
        return {}


def _parse_schwab(raw: dict) -> dict:
    """Pull out the fields we care about from one Schwab symbol blob."""
    q = raw.get("quote", {})
    f = raw.get("fundamental", {})

    def g(*keys):
        for d in (f, q):
            for k in keys:
                v = d.get(k)
                if v not in (None, "", 0):
                    try:
                        fv = float(v)
                        if fv == fv:   # not NaN
                            return fv
                    except (TypeError, ValueError):
                        return v
        return None

    return {
        "_source":         "schwab",
        "current_price":   g("lastPrice", "mark", "closePrice"),
        "wk52_high":       g("52WeekHigh", "high52"),
        "wk52_low":        g("52WeekLow",  "low52"),
        "market_cap":      g("marketCap"),
        "beta":            g("beta"),
        "avg_volume":      g("vol10DayAvg", "vol1DayAvg"),
        "shares_out":      g("sharesOutstanding"),
        "ma50":            g("day50MovAvg"),
        "ma200":           g("day200MovAvg"),
        # valuation ratios
        "pe_trailing":     g("peRatio"),
        "peg_ratio":       g("pegRatio"),
        "price_book":      g("pbRatio"),
        "price_sales":     g("prRatio"),
        "price_cf":        g("pcfRatio"),
        "eps_trailing":    g("eps"),
        "eps_growth":      g("epsChangePercentTTM"),
        # margins (decimal format: 0.437 = 43.7%)
        "gross_margin":    g("grossMarginTTM"),
        "operating_margin": g("operatingMarginTTM"),
        "net_margin":      g("netProfitMarginTTM"),
        # returns (decimal format)
        "roe":             g("returnOnEquity"),
        "roa":             g("returnOnAssets"),
        "roi":             g("returnOnInvestment"),
        # revenue
        "revenue_ttm":     g("revenueTTM"),
        "revenue_growth":  g("revenueChangeIn1Year"),
        # balance sheet ratios
        "book_value_ps":   g("bookValuePerShare"),
        "current_ratio":   g("currentRatio"),
        "quick_ratio":     g("quickRatio"),
        "debt_to_equity":  g("totalDebtToEquity"),
        "lt_debt_equity":  g("ltDebtToEquity"),
        "debt_to_capital": g("totalDebtToCapital"),
        # dividends — Schwab returns yield as a % (e.g. 0.47 = 0.47%)
        "dividend_amount": g("dividendAmount"),
        "dividend_yield":  g("dividendYield"),
        # short interest (decimal)
        "short_pct_float": g("shortIntToFloat"),
    }


def fetch_schwab_bulk(tickers: list[str], token: str) -> dict[str, dict]:
    """
    Batch-fetch Schwab fundamentals for all tickers.
    Returns {TICKER_UPPER: parsed_dict}.
    """
    result = {}
    batch_size = 100
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        raw = _schwab_batch(batch, token)
        for sym, blob in raw.items():
            result[sym.upper()] = _parse_schwab(blob)
    logger.info("Schwab bulk: returned data for %d / %d tickers", len(result), len(tickers))
    return result


# ─────────────────────────── yfinance helpers ─────────────────────────────────

def _yf_info(ticker: str) -> dict:
    try:
        return yf.Ticker(ticker).info or {}
    except Exception:
        return {}


def _series_to_annual(series) -> dict:
    out = {}
    if series is None:
        return out
    for idx, val in series.items():
        if pd.notna(val):
            try:
                out[str(pd.Timestamp(idx).year)] = float(val)
            except Exception:
                pass
    return out


def _yf_statements(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    stmts = {}
    try:
        fin = t.financials
        if fin is not None and not fin.empty:
            for row in ["Total Revenue", "Gross Profit", "Operating Income", "Net Income", "EBITDA"]:
                if row in fin.index:
                    stmts[row] = _series_to_annual(fin.loc[row])
    except Exception:
        pass
    try:
        cf = t.cashflow
        if cf is not None and not cf.empty:
            for row in ["Operating Cash Flow", "Capital Expenditure", "Free Cash Flow"]:
                if row in cf.index:
                    stmts[row] = _series_to_annual(cf.loc[row])
    except Exception:
        pass
    try:
        bs = t.balance_sheet
        if bs is not None and not bs.empty:
            for row in ["Total Assets", "Stockholders Equity", "Total Debt",
                        "Cash And Cash Equivalents"]:
                if row in bs.index:
                    stmts[row] = _series_to_annual(bs.loc[row])
    except Exception:
        pass
    return stmts


# ─────────────────────────── Build data dict ──────────────────────────────────

def _merge(ticker: str, sd: dict | None, yfi: dict) -> dict:
    """
    Merge Schwab data (sd) and yfinance info (yfi) into a unified data dict.
    Schwab wins for market/fundamental data; yfinance fills gaps + adds EV, targets.
    """
    sd = sd or {}
    src = sd.get("_source", "yfinance")

    def pick_sd_yf(sd_key, yf_key, yf_mult=1.0):
        v = sd.get(sd_key)
        if v not in (None, 0, ""):
            try:
                fv = float(v)
                if fv == fv:
                    return fv
            except (TypeError, ValueError):
                pass
        yv = yfi.get(yf_key)
        if yv not in (None, 0, ""):
            try:
                fv = float(yv) * yf_mult
                if fv == fv:
                    return fv
            except (TypeError, ValueError):
                pass
        return None

    data = {
        "ticker":       ticker,
        "source":       src,
        "company_name": yfi.get("longName") or yfi.get("shortName") or ticker,
        "sector":       yfi.get("sector", "N/A"),
        "industry":     yfi.get("industry", "N/A"),
        "exchange":     sd.get("exchange") or yfi.get("exchange", "N/A"),
        "business_summary": (yfi.get("longBusinessSummary") or "")[:600],
        "employees":    yfi.get("fullTimeEmployees"),

        # Market data
        "current_price": pick_sd_yf("current_price", "currentPrice") or yfi.get("regularMarketPrice"),
        "wk52_high":     pick_sd_yf("wk52_high", "fiftyTwoWeekHigh"),
        "wk52_low":      pick_sd_yf("wk52_low",  "fiftyTwoWeekLow"),
        "market_cap":    pick_sd_yf("market_cap", "marketCap"),
        "enterprise_value": yfi.get("enterpriseValue"),
        "avg_volume":    pick_sd_yf("avg_volume", "averageVolume"),
        "beta":          pick_sd_yf("beta", "beta"),
        "shares_out":    pick_sd_yf("shares_out", "sharesOutstanding"),
        "ma50":          pick_sd_yf("ma50",  "fiftyDayAverage"),
        "ma200":         pick_sd_yf("ma200", "twoHundredDayAverage"),

        # Valuation
        "pe_trailing":   pick_sd_yf("pe_trailing", "trailingPE"),
        "pe_forward":    yfi.get("forwardPE"),
        "peg_ratio":     pick_sd_yf("peg_ratio", "pegRatio"),
        "price_book":    pick_sd_yf("price_book", "priceToBook"),
        "price_sales":   pick_sd_yf("price_sales", "priceToSalesTrailing12Months"),
        "price_cf":      sd.get("price_cf"),
        "ev_revenue":    yfi.get("enterpriseToRevenue"),
        "ev_ebitda":     yfi.get("enterpriseToEbitda"),
        "eps_trailing":  pick_sd_yf("eps_trailing", "trailingEps"),
        "eps_forward":   yfi.get("forwardEps"),

        # Margins (decimal: 0.437 = 43.7%)
        "gross_margin":    pick_sd_yf("gross_margin",    "grossMargins"),
        "operating_margin": pick_sd_yf("operating_margin", "operatingMargins"),
        "net_margin":      pick_sd_yf("net_margin",      "profitMargins"),
        "roe":             pick_sd_yf("roe", "returnOnEquity"),
        "roa":             pick_sd_yf("roa", "returnOnAssets"),
        "roi":             sd.get("roi"),
        "revenue_ttm":    sd.get("revenue_ttm"),
        "revenue_growth": pick_sd_yf("revenue_growth", "revenueGrowth"),
        "eps_growth":     pick_sd_yf("eps_growth", "earningsGrowth"),

        # Balance sheet
        "total_cash":    yfi.get("totalCash"),
        "total_debt":    yfi.get("totalDebt"),
        "free_cashflow": yfi.get("freeCashflow"),
        "operating_cf":  yfi.get("operatingCashflow"),
        "book_value_ps": pick_sd_yf("book_value_ps", "bookValue"),
        "current_ratio": pick_sd_yf("current_ratio", "currentRatio"),
        "quick_ratio":   pick_sd_yf("quick_ratio",   "quickRatio"),
        "debt_to_equity": pick_sd_yf("debt_to_equity", "debtToEquity"),
        "debt_to_capital": sd.get("debt_to_capital"),

        # Dividends — normalize to PERCENT (Schwab gives %, yfinance gives decimal)
        "dividend_yield":  sd.get("dividend_yield") or ((yfi.get("dividendYield") or 0) * 100),
        "dividend_amount": pick_sd_yf("dividend_amount", "dividendRate"),

        # Short interest (decimal)
        "short_pct_float": pick_sd_yf("short_pct_float", "shortPercentOfFloat"),

        # Ownership
        "insider_pct": yfi.get("heldPercentInsiders"),
        "inst_pct":    yfi.get("heldPercentInstitutions"),

        # Analyst
        "target_mean":    yfi.get("targetMeanPrice"),
        "target_high":    yfi.get("targetHighPrice"),
        "target_low":     yfi.get("targetLowPrice"),
        "recommendation": (yfi.get("recommendationKey") or "N/A").upper(),
        "num_analysts":   yfi.get("numberOfAnalystOpinions"),
    }

    # FCF yield (as percent)
    fc = data.get("free_cashflow")
    mc = data.get("market_cap")
    data["fcf_yield"] = (fc / mc * 100) if fc and mc and mc > 0 else None

    return data


# ─────────────────────────── Public API ───────────────────────────────────────

def fetch_universe_bulk(
    tickers: list[str],
    token: str | None = None,
    progress_cb=None,
    max_workers: int = 20,
) -> list[dict]:
    """
    Fast bulk fetch for all tickers. Uses Schwab batch for market data, then
    parallel yfinance .info calls for gaps + sector/name/analyst data.
    Does NOT fetch multi-year financial statements.
    """
    # 1. Schwab batch (instant — a few HTTP calls regardless of universe size)
    schwab_map: dict[str, dict] = {}
    if token:
        schwab_map = fetch_schwab_bulk(tickers, token)

    # 2. Parallel yfinance .info for all tickers (name, sector, analyst targets, etc.)
    yf_map: dict[str, dict] = {}
    lock = threading.Lock()
    done = [0]

    def _fetch_one_yf(ticker):
        info = _yf_info(ticker)
        with lock:
            yf_map[ticker.upper()] = info
            done[0] += 1
            if progress_cb:
                progress_cb(done[0], len(tickers), ticker)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        list(pool.map(_fetch_one_yf, tickers))

    # 3. Merge and return
    results = []
    for ticker in tickers:
        t = ticker.upper()
        try:
            data = _merge(ticker, schwab_map.get(t), yf_map.get(t, {}))
            results.append({"ticker": ticker, "error": None, "data": data})
        except Exception as e:
            results.append({"ticker": ticker, "error": str(e), "data": {}})

    return results


def fetch_ticker_detail(ticker: str, token: str | None = None) -> dict:
    """
    Detailed fetch for a single ticker — includes multi-year financial statements.
    Used for AI write-up generation.
    """
    sd = {}
    if token:
        batch = fetch_schwab_bulk([ticker], token)
        sd = batch.get(ticker.upper(), {})
    yfi = _yf_info(ticker)
    stmts = _yf_statements(ticker)
    data = _merge(ticker, sd, yfi)
    data["income_stmt"]  = {k: stmts[k] for k in
        ["Total Revenue", "Gross Profit", "Operating Income", "EBITDA", "Net Income"] if k in stmts}
    data["cash_flow"]    = {k: stmts[k] for k in
        ["Operating Cash Flow", "Capital Expenditure", "Free Cash Flow"] if k in stmts}
    data["balance_sheet"] = {k: stmts[k] for k in
        ["Total Assets", "Stockholders Equity", "Total Debt", "Cash And Cash Equivalents"] if k in stmts}
    return {"ticker": ticker, "error": None, "data": data}


# ─────────────────────────── Prompt formatting ────────────────────────────────

def _fmt(v, prefix="", billions=False, millions=False, pct_decimal=False, pct_direct=False, dec=2):
    if v is None:
        return "N/A"
    try:
        v = float(v)
        if v != v:
            return "N/A"
    except (TypeError, ValueError):
        return str(v) if v else "N/A"
    if pct_decimal:           # input is 0.437, display as 43.7%
        return f"{v * 100:.1f}%"
    if pct_direct:            # input is already %, display as X.X%
        return f"{v:.1f}%"
    if billions:
        return f"{prefix}{v / 1e9:.{dec}f}B"
    if millions:
        return f"{prefix}{v / 1e6:.{dec}f}M"
    return f"{prefix}{v:.{dec}f}"


def format_for_prompt(ticker_result: dict) -> str:
    """Format a detailed ticker result into a structured text block for the AI prompt."""
    ticker = ticker_result["ticker"]
    if ticker_result.get("error"):
        return f"\n--- {ticker} ---\nDATA ERROR: {ticker_result['error']}\n"

    d = ticker_result["data"]
    src = "Schwab API (real-time)" if d.get("source") == "schwab" else "Yahoo Finance"

    def annual_row(label, row_dict):
        if not row_dict:
            return f"  {label}: N/A"
        years = sorted(row_dict.keys(), reverse=True)[:5]
        vals = "  |  ".join(f"{y}: {_fmt(row_dict[y], '$', billions=True)}" for y in years)
        return f"  {label}: {vals}"

    lines = [
        f"\n{'='*70}",
        f"TICKER: {ticker}   [Source: {src}]",
        f"Company: {d.get('company_name','N/A')}",
        f"Sector: {d.get('sector','N/A')} | Industry: {d.get('industry','N/A')}",
        "",
        "MARKET DATA:",
        f"  Price:              {_fmt(d.get('current_price'),'$')}",
        f"  52-Week High/Low:   {_fmt(d.get('wk52_high'),'$')} / {_fmt(d.get('wk52_low'),'$')}",
        f"  50-Day MA:          {_fmt(d.get('ma50'),'$')}",
        f"  200-Day MA:         {_fmt(d.get('ma200'),'$')}",
        f"  Market Cap:         {_fmt(d.get('market_cap'),'$',billions=True)}",
        f"  Enterprise Value:   {_fmt(d.get('enterprise_value'),'$',billions=True)}",
        f"  Beta:               {_fmt(d.get('beta'))}",
        f"  Short % Float:      {_fmt(d.get('short_pct_float'),pct_decimal=True)}",
        "",
        "VALUATION:",
        f"  P/E Trailing:       {_fmt(d.get('pe_trailing'))}",
        f"  P/E Forward:        {_fmt(d.get('pe_forward'))}",
        f"  EV/EBITDA:          {_fmt(d.get('ev_ebitda'))}",
        f"  EV/Revenue:         {_fmt(d.get('ev_revenue'))}",
        f"  Price/Book:         {_fmt(d.get('price_book'))}",
        f"  Price/Sales:        {_fmt(d.get('price_sales'))}",
        f"  PEG Ratio:          {_fmt(d.get('peg_ratio'))}",
        f"  FCF Yield:          {_fmt(d.get('fcf_yield'),pct_direct=True)}",
        f"  EPS Trailing:       {_fmt(d.get('eps_trailing'),'$')}",
        f"  EPS Forward:        {_fmt(d.get('eps_forward'),'$')}",
        "",
        "PROFITABILITY:",
        f"  Gross Margin:       {_fmt(d.get('gross_margin'),pct_decimal=True)}",
        f"  Operating Margin:   {_fmt(d.get('operating_margin'),pct_decimal=True)}",
        f"  Net Margin:         {_fmt(d.get('net_margin'),pct_decimal=True)}",
        f"  ROE:                {_fmt(d.get('roe'),pct_decimal=True)}",
        f"  ROA:                {_fmt(d.get('roa'),pct_decimal=True)}",
        f"  Revenue Growth YoY: {_fmt(d.get('revenue_growth'),pct_decimal=True)}",
        f"  EPS Growth:         {_fmt(d.get('eps_growth'),pct_decimal=True)}",
        "",
        "BALANCE SHEET:",
        f"  Cash:               {_fmt(d.get('total_cash'),'$',billions=True)}",
        f"  Total Debt:         {_fmt(d.get('total_debt'),'$',billions=True)}",
        f"  FCF (TTM):          {_fmt(d.get('free_cashflow'),'$',billions=True)}",
        f"  Operating CF (TTM): {_fmt(d.get('operating_cf'),'$',billions=True)}",
        f"  Current Ratio:      {_fmt(d.get('current_ratio'))}",
        f"  Debt/Equity:        {_fmt(d.get('debt_to_equity'))}",
        "",
        "ANALYST CONSENSUS:",
        f"  Recommendation:     {d.get('recommendation','N/A')}",
        f"  # Analysts:         {d.get('num_analysts','N/A')}",
        f"  Target (Mean):      {_fmt(d.get('target_mean'),'$')}",
        f"  Target (High/Low):  {_fmt(d.get('target_high'),'$')} / {_fmt(d.get('target_low'),'$')}",
    ]

    for section, keys in [
        ("ANNUAL INCOME STATEMENT (USD):",
         ["Total Revenue","Gross Profit","Operating Income","EBITDA","Net Income"]),
        ("ANNUAL CASH FLOW (USD):",
         ["Operating Cash Flow","Capital Expenditure","Free Cash Flow"]),
        ("ANNUAL BALANCE SHEET (USD):",
         ["Total Assets","Stockholders Equity","Total Debt","Cash And Cash Equivalents"]),
    ]:
        stmts = d.get(section.split()[1].lower().replace(" ","_"), {})
        if not stmts:
            # try alternate key names
            stmts = (d.get("income_stmt") or d.get("cash_flow") or d.get("balance_sheet") or {})
            stmts = {k: v for k, v in stmts.items() if k in keys}
        if stmts:
            lines.append(""); lines.append(section)
            for k in keys:
                if k in stmts:
                    lines.append(annual_row(k, stmts[k]))

    # Proper statement access
    for section_label, attr in [
        ("ANNUAL INCOME STATEMENT (USD):", "income_stmt"),
        ("ANNUAL CASH FLOW (USD):", "cash_flow"),
        ("ANNUAL BALANCE SHEET (USD):", "balance_sheet"),
    ]:
        stmts = d.get(attr, {})
        if stmts:
            lines.append(""); lines.append(section_label)
            for k, v in stmts.items():
                lines.append(annual_row(k, v))
            break   # avoid duplicates if any matched above

    summary = d.get("business_summary", "")
    if summary:
        lines += ["", f"BUSINESS: {summary}"]

    return "\n".join(lines)
