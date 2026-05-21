"""
universe.py — Stock universe manager for the momentum screener.

Strategy:
  1. A hardcoded dictionary of S&P 500 + Nasdaq 100 (current as of May 2026)
     is always available as the baseline — zero network dependency.
  2. Once a month the app attempts to refresh the list from Wikipedia.
     If that succeeds, the refreshed list is cached to .universe_cache.json.
  3. load_universe() returns the best available list every time.

To manually force a refresh: delete .universe_cache.json
"""

from __future__ import annotations
import json, os, time, logging
from datetime import datetime

logger = logging.getLogger(__name__)
CACHE_FILE    = ".universe_cache.json"
REFRESH_DAYS  = 30          # refresh once a month

# ── Hardcoded baseline — S&P 500 + Nasdaq 100, May 2026 ───────────────────
# Combined & deduplicated. BRK.B → BRK-B for Yahoo/Schwab compatibility.
_BASELINE: list[str] = sorted(set([
    # ── S&P 500 ──────────────────────────────────────────────────────────
    "A","AAPL","ABBV","ABNB","ACGL","ACN","ADBE","ADI","ADM","ADP",
    "ADSK","AEE","AEP","AES","AFL","AIG","AIZ","AJG","AKAM","ALB",
    "ALGN","ALL","ALLE","AMAT","AMCR","AMD","AME","AMGN","AMP","AMT",
    "AMZN","ANET","AON","AOS","APD","APH","APTV","ARE","ATO","AVB",
    "AVGO","AVY","AWK","AXON","AXP","AZO","BA","BAC","BALL","BAX",
    "BBWI","BBY","BDX","BEN","BK","BKNG","BKR","BLK","BMY","BR",
    "BRK-B","BSX","BWA","BX","BXP","C","CAG","CAH","CARR","CAT",
    "CB","CBOE","CBRE","CCI","CCL","CDNS","CDW","CE","CEG","CF",
    "CFG","CHD","CHRW","CHTR","CI","CINF","CL","CLX","CMA","CMCSA",
    "CME","CMG","CMI","CMS","CNC","CNP","COF","COO","COP","COR",
    "COST","CPAY","CPB","CPRT","CPT","CRL","CRM","CSCO","CSGP","CSX",
    "CTAS","CTLT","CTSH","CTVA","CVS","CVX","CZR","D","DAL","DAY",
    "DD","DE","DECK","DG","DGX","DHI","DHR","DIS","DLR","DLTR",
    "DOC","DOV","DOW","DPZ","DRI","DTE","DUK","DVA","DVN","DXCM",
    "EA","EBAY","ECL","ED","EFX","EIX","EL","ELV","EMN","EMR",
    "ENPH","EOG","EPAM","EQIX","EQR","EQT","ES","ESS","ETN","ETR",
    "EVRG","EW","EXC","EXPD","EXPE","EXR","F","FANG","FAST","FCX",
    "FDS","FDX","FI","FICO","FIS","FITB","FMC","FOX","FOXA","FRT",
    "FSLR","FTNT","FTV","GD","GDDY","GE","GEHC","GEN","GEV","GILD",
    "GIS","GL","GLW","GM","GNRC","GOOG","GOOGL","GPC","GPN","GS",
    "GWW","HAL","HAS","HBAN","HCA","HD","HES","HIG","HII","HLT",
    "HOLX","HON","HPE","HPQ","HRL","HSIC","HST","HSY","HUM","HWM",
    "IBM","ICE","IDXX","IEX","IFF","ILMN","INCY","INTC","INTU","INVH",
    "IP","IPG","IQV","IR","IRM","ISRG","IT","ITW","IVZ","J",
    "JBHT","JCI","JKHY","JNJ","JNPR","JPM","K","KDP","KEY","KEYS",
    "KHC","KIM","KLAC","KMB","KMI","KO","KR","KVUE","L","LDOS",
    "LEN","LH","LHX","LIN","LKQ","LLY","LMT","LNT","LOW","LRCX",
    "LULU","LUV","LVS","LW","LYB","LYV","MA","MAA","MAR","MAS",
    "MCD","MCHP","MCK","MCO","MDLZ","MDT","MET","META","MGM","MHK",
    "MKC","MKTX","MLM","MMC","MMM","MO","MOH","MOS","MPC","MPWR",
    "MRK","MRNA","MRO","MS","MSCI","MSFT","MSI","MTB","MTD","MU",
    "NCLH","NEE","NEM","NFLX","NI","NKE","NOC","NOW","NRG","NSC",
    "NTAP","NTRS","NUE","NVDA","NVR","NWS","NWSA","NXPI","O","ODFL",
    "OKE","OMC","ON","ORCL","ORLY","OTIS","OXY","PAYC","PAYX","PCAR",
    "PCG","PEG","PEP","PFE","PFG","PG","PGR","PH","PHM","PKG",
    "PLD","PM","PNC","PNR","PNW","POOL","PPG","PPL","PRU","PSA",
    "PSX","PTC","PWR","PYPL","QCOM","RCL","REG","REGN","RF","RJF",
    "RL","RMD","ROK","ROL","ROP","ROST","RSG","RTX","RVTY","SBAC",
    "SBUX","SCHW","SHW","SJM","SLB","SMCI","SNA","SNPS","SO","SPG",
    "SPGI","SRE","STE","STLD","STT","STX","STZ","SWK","SWKS","SYF",
    "SYK","SYY","T","TAP","TDG","TDY","TECH","TEL","TER","TFC",
    "TFX","TGT","TJX","TMO","TMUS","TPR","TRGP","TRMB","TROW","TRV",
    "TSCO","TSLA","TSN","TT","TTWO","TXN","TYL","UAL","UDR","UHS",
    "ULTA","UNH","UNP","UPS","URI","USB","V","VICI","VLO","VMC",
    "VRSK","VRSN","VRTX","VST","VZ","WAB","WAT","WBA","WBD","WDC",
    "WEC","WELL","WFC","WM","WMB","WMT","WRB","WRK","WST","WTW",
    "WY","WYNN","XEL","XOM","XYL","YUM","ZBH","ZBRA","ZTS",
    # ── Nasdaq 100 additions not already in S&P 500 ──────────────────────
    "ABNB","ADSK","AEP","ALGN","AMAT","AMD","AMGN","AMZN","ANSS","ARM",
    "ASML","AVGO","AZN","BIIB","BKNG","CDNS","CDW","CEG","CHTR","CMCSA",
    "COST","CPRT","CRWD","CSCO","CSGP","CSX","CTAS","CTSH","DDOG","DLTR",
    "DXCM","EA","EBAY","EXC","FANG","FAST","FTNT","GILD","GOOG","GOOGL",
    "HON","IDXX","ILMN","INTC","INTU","ISRG","KDP","KLAC","LRCX","LULU",
    "MAR","MCHP","MDLZ","META","MNST","MRVL","MSFT","MU","NFLX","NVDA",
    "NXPI","ODFL","ON","ORLY","PANW","PAYX","PCAR","PEP","PYPL","QCOM",
    "REGN","ROP","ROST","SBUX","SIRI","SNPS","TEAM","TMUS","TSLA","TTD",
    "TXN","VRSK","VRTX","WBA","WBD","XEL","ZS",
    # ── Recent notable additions ──────────────────────────────────────────
    "APP","COIN","DASH","HOOD","IONQ","PLTR","SMCI","VST","CEG","GEV",
]))

# Sector map (ticker → sector ETF) for relative momentum calc
SECTOR_MAP: dict[str, str] = {
    # Technology
    **{t: "XLK" for t in ["AAPL","MSFT","NVDA","AVGO","AMD","INTC","QCOM","TXN","AMAT",
       "LRCX","KLAC","ADI","MCHP","NXPI","ON","CDNS","SNPS","ORCL","CRM","NOW",
       "ADBE","INTU","ANSS","CTSH","EPAM","IBM","CSCO","ANET","JNPR","HPQ","HPE",
       "STX","WDC","NTAP","CDW","GPN","GDDY","PYPL","FISERV","FICO","IT","GLW",
       "TRMB","PTC","KEYS","MPWR","TER","SWKS","QRVO","SMCI","ARM","CRWD"]},
    # Communication
    **{t: "XLC" for t in ["GOOGL","GOOG","META","NFLX","DIS","CMCSA","T","VZ","TMUS",
       "CHTR","WBD","FOX","FOXA","NWS","NWSA","TTWO","EA","MTCH","OMC","IPG",
       "LYV","SIRI","TTD","ZS","DASH"]},
    # Consumer Discretionary
    **{t: "XLY" for t in ["AMZN","TSLA","HD","MCD","NKE","LOW","SBUX","BKNG","TJX",
       "MAR","HLT","GM","F","ORLY","AZO","CMG","YUM","DPZ","LVS","WYNN","MGM",
       "CCL","RCL","NCLH","PHM","DHI","LEN","NVR","POOL","DECK","LULU","TPR",
       "RL","PVH","HAS","APTV","BWA","LKQ","ULTA","EBAY","EXPE","ABNB","BBY"]},
    # Consumer Staples
    **{t: "XLP" for t in ["WMT","PG","KO","PEP","COST","MDLZ","PM","MO","ABBV","CL",
       "KMB","GIS","HSY","K","KHC","KDP","MNST","STZ","ADM","SJM","MKC","CPB",
       "CAG","HRL","TSN","LW","TAP","WBA","CVS","KR","SYY","KVUE","DLTR","DG",
       "ROST","TGT"]},
    # Health Care
    **{t: "XLV" for t in ["LLY","UNH","JNJ","ABBV","MRK","TMO","ABT","DHR","AMGN",
       "ISRG","BSX","SYK","MDT","BMY","GILD","REGN","VRTX","BIIB","MRNA","ZTS",
       "EW","IDXX","MTD","A","DXCM","HUM","ELV","CI","CNC","MOH","HCA","RMD",
       "HOLX","BDX","BAX","IQV","ILMN","INCY","RVTY","TECH","COO","GEHC","ALGN",
       "HSIC","PKG","WST","ZBH","MKTX","CTLT","WAT","CRL","DGX","LH","MCK",
       "CAH","COR","AMP","UHS","DVA","ACGL"]},
    # Financials
    **{t: "XLF" for t in ["JPM","BAC","WFC","GS","MS","BLK","SCHW","AXP","CB","C",
       "MMC","AON","MET","PRU","AFL","AIG","ALL","TRV","PNC","USB","TFC","KEY",
       "CFG","HBAN","RF","FITB","MTB","STT","BK","NTRS","ICE","CME","SPGI","MCO",
       "MSCI","FDS","BR","AMP","RJF","IVZ","TROW","BEN","FI","FIS","PYPL","COF",
       "DFS","SYF","CBOE","NDAQ","MKTX","CINF","GL","WRB","AIZ","PFG","ACGL",
       "COIN","HOOD","BX","APO","KKR","BX"]},
    # Industrials
    **{t: "XLI" for t in ["GE","HON","CAT","UPS","BA","RTX","LMT","DE","ADP","ETN",
       "EMR","PH","ITW","GD","NOC","LHX","TDG","TT","CARR","OTIS","SWK","ROK",
       "CMI","PCAR","URI","CTAS","RSG","WM","FDX","UNP","NSC","CSX","DAL","UAL",
       "LUV","AAL","EXPD","CHRW","JBHT","ODFL","OLD","XPO","GXO","SAIA","WERN",
       "KNX","JBLU","ALK","FLR","AGCO","DOV","XYLEM","AME","ROP","IEX","LDOS",
       "HII","NOC","GD","L3H","TDY","BWXT","AXON","PWR","WAB","MTZ","HUBB",
       "NVT","GNRC","TRMB","FTV","RRX","XYL","NDSN","MAS","SNA","ALLE","IR",
       "INGR","FAST","GWW","MSC","AWI","TREX","BLDR","MHK","SWK","FBHS"]},
    # Energy
    **{t: "XLE" for t in ["XOM","CVX","COP","SLB","EOG","PXD","MPC","PSX","VLO",
       "OXY","HAL","DVN","BKR","FANG","OKE","WMB","KMI","ET","EPD","MMP",
       "TRGP","AM","CQP","LNG","NFG","SW","APA","HES","MRO","RRC","EQT",
       "AR","CNX","SWN","CIVI","MTDR","PDCE","SM","REI"]},
    # Utilities
    **{t: "XLU" for t in ["NEE","SO","DUK","AEP","D","SRE","EXC","XEL","ED","PEG",
       "EIX","WEC","ES","ETR","EVRG","AES","NI","CMS","CNP","LNT","AWK",
       "ATO","PNW","NRG","PCG","PPL","FE","DTE","VST","CEG"]},
    # Real Estate
    **{t: "XLRE" for t in ["PLD","AMT","EQIX","CCI","PSA","SPG","O","DLR","WELL","AVB",
       "EQR","EXR","INVH","VICI","ARE","BXP","SBA","AMH","UDR","CPT",
       "REG","KIM","FRT","HST","PEAK","VTR","NNN","NHI","SBAC","CSGP",
       "CBRE","JLL","WY"]},
    # Materials
    **{t: "XLB" for t in ["LIN","APD","SHW","FCX","NEM","NUE","ECL","PPG","VMC","MLM",
       "DD","DOW","LYB","EMN","CE","ALB","MOS","FMC","CF","IFF","BALL",
       "AMCR","PKG","IP","SEE","SON","GEF","OLN","ATR","AVNT","HWKN"]},
}


def _fetch_wikipedia(url: str, user_agent: str = "Mozilla/5.0 (compatible; MomentumScreener/1.0)") -> list[str]:
    """Try to scrape a ticker list from Wikipedia."""
    import urllib.request, io, pandas as pd
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=20) as r:
        html = r.read()
    tables = pd.read_html(io.BytesIO(html))
    return tables


def refresh_universe() -> list[str]:
    """
    Attempt to fetch the latest S&P 500 + Nasdaq 100 from Wikipedia.
    Falls back to _BASELINE on any error.
    Saves result to CACHE_FILE with a timestamp.
    """
    tickers: list[str] = []
    errors: list[str] = []

    # S&P 500
    try:
        tables = _fetch_wikipedia("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        sp500  = tables[0]["Symbol"].str.replace(".", "-", regex=False).dropna().tolist()
        tickers.extend(sp500)
        logger.info("Wikipedia S&P 500 refresh: %d tickers", len(sp500))
    except Exception as e:
        errors.append(f"S&P 500 Wikipedia fetch failed: {e}")
        logger.warning(errors[-1])

    # Nasdaq 100
    try:
        tables = _fetch_wikipedia("https://en.wikipedia.org/wiki/Nasdaq-100")
        for t in tables:
            if "Ticker" in t.columns:
                ndx = t["Ticker"].str.replace(".", "-", regex=False).dropna().tolist()
                tickers.extend(ndx)
                logger.info("Wikipedia Nasdaq 100 refresh: %d tickers", len(ndx))
                break
    except Exception as e:
        errors.append(f"Nasdaq 100 Wikipedia fetch failed: {e}")
        logger.warning(errors[-1])

    if not tickers:
        logger.warning("Both Wikipedia refreshes failed — using hardcoded baseline (%d tickers)", len(_BASELINE))
        tickers = list(_BASELINE)

    combined = sorted(set(t.strip().upper() for t in tickers if t and len(t) <= 6))

    # Save cache
    try:
        payload = {
            "updated":    datetime.now().isoformat(),
            "source":     "wikipedia" if not errors else "partial+baseline",
            "errors":     errors,
            "count":      len(combined),
            "tickers":    combined,
        }
        with open(CACHE_FILE, "w") as f:
            json.dump(payload, f, indent=2)
        logger.info("Universe cache saved: %d tickers → %s", len(combined), CACHE_FILE)
    except Exception as e:
        logger.warning("Could not save universe cache: %s", e)

    return combined


def load_universe(force_refresh: bool = False) -> tuple[list[str], dict]:
    """
    Return (tickers, meta) where meta has 'source', 'count', 'updated', 'next_refresh'.

    Refresh logic:
      - If cache exists and is < REFRESH_DAYS old → use cache
      - If cache is stale or missing → try Wikipedia refresh
      - If refresh fails → fall back to hardcoded baseline
    """
    meta: dict = {}

    if not force_refresh and os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                cache = json.load(f)
            age_days = (time.time() - os.path.getmtime(CACHE_FILE)) / 86400
            if age_days < REFRESH_DAYS:
                tickers = cache["tickers"]
                meta = {
                    "source":       cache.get("source", "cache"),
                    "count":        len(tickers),
                    "updated":      cache.get("updated", "unknown"),
                    "age_days":     round(age_days, 1),
                    "next_refresh": f"in ~{REFRESH_DAYS - int(age_days)} days",
                    "errors":       cache.get("errors", []),
                }
                logger.info("Using cached universe: %d tickers (%.1f days old)", len(tickers), age_days)
                return tickers, meta
            else:
                logger.info("Cache is %.1f days old — refreshing from Wikipedia", age_days)
        except Exception as e:
            logger.warning("Cache read failed: %s", e)

    # Attempt refresh
    tickers = refresh_universe()
    if not tickers:
        tickers = list(_BASELINE)

    meta = {
        "source":       "refreshed" if os.path.exists(CACHE_FILE) else "baseline",
        "count":        len(tickers),
        "updated":      datetime.now().isoformat(),
        "age_days":     0,
        "next_refresh": f"in ~{REFRESH_DAYS} days",
        "errors":       [],
    }

    # If we ended up with very few tickers, blend with baseline
    if len(tickers) < 200:
        logger.warning("Refresh returned only %d tickers — merging with baseline", len(tickers))
        tickers = sorted(set(tickers) | set(_BASELINE))
        meta["source"] = "baseline+partial"
        meta["count"]  = len(tickers)

    return tickers, meta


def get_sector_etf(ticker: str) -> str:
    """Return the sector ETF for a ticker, or 'SPY' as fallback."""
    return SECTOR_MAP.get(ticker, "SPY")
