"""Build the screening universe — S&P 500 + NASDAQ-100."""

from __future__ import annotations

from io import StringIO
from typing import List

import pandas as pd
import requests

from .. import config
from .tickers_fallback import SP500_FALLBACK, NASDAQ100_FALLBACK


def _fetch(url: str) -> str | None:
    try:
        r = requests.get(url, headers=config.BROWSER_HEADERS, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  [warn] fetch {url}: {e}")
        return None


def get_sp500() -> List[str]:
    html = _fetch("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    if html:
        try:
            tables = pd.read_html(StringIO(html))
            df = tables[0]
            tickers = df["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist()
            if len(tickers) >= 400:
                return tickers
        except Exception as e:
            print(f"  [warn] S&P parse: {e}")
    print(f"  [info] using S&P 500 fallback ({len(SP500_FALLBACK)} tickers)")
    return list(SP500_FALLBACK)


def get_nasdaq100() -> List[str]:
    html = _fetch("https://en.wikipedia.org/wiki/Nasdaq-100")
    if html:
        try:
            tables = pd.read_html(StringIO(html))
            for t in tables:
                cols = [str(c).lower() for c in t.columns.astype(str)]
                if any("ticker" in c or "symbol" in c for c in cols):
                    col = next(c for c in t.columns if str(c).lower() in ("ticker", "symbol"))
                    tickers = t[col].astype(str).str.replace(".", "-", regex=False).tolist()
                    if len(tickers) >= 80:
                        return tickers
        except Exception as e:
            print(f"  [warn] NASDAQ parse: {e}")
    print(f"  [info] using NASDAQ-100 fallback ({len(NASDAQ100_FALLBACK)} tickers)")
    return list(NASDAQ100_FALLBACK)


def build_universe() -> List[str]:
    sp = get_sp500()
    ndx = get_nasdaq100()
    universe = sorted({t for t in (sp + ndx) if t and t.replace("-", "").isalnum()})
    print(f"  Universe: {len(sp)} S&P 500 + {len(ndx)} NASDAQ-100 = {len(universe)} unique")
    return universe
