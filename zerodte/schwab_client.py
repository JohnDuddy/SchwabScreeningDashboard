"""
Schwab Market Data API client for 0DTE options chains.
Uses the same token/auth pattern as the main app.
"""

import logging
import requests
from datetime import date

from . import config

logger = logging.getLogger(__name__)

SCHWAB_MARKET_BASE = "https://api.schwabapi.com/marketdata/v1"


def fetch_options_chain(symbol: str, token: str, exp_date: date) -> dict | None:
    """
    Fetch the 0DTE options chain for `symbol` expiring on `exp_date`.

    Returns the raw Schwab API response dict, or None on failure.
    Only returns data if the chain actually has contracts expiring on `exp_date`.
    """
    date_str = exp_date.strftime("%Y-%m-%d")
    url = f"{SCHWAB_MARKET_BASE}/chains"
    params = {
        "symbol":                 symbol,
        "contractType":           "ALL",
        "strikeCount":            config.STRIKES_PER_SIDE,
        "includeUnderlyingQuote": "true",
        "strategy":               "SINGLE",
        "fromDate":               date_str,
        "toDate":                 date_str,
    }
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        if resp.status_code == 200:
            data = resp.json()
            # Schwab returns an empty callExpDateMap/putExpDateMap if no contracts
            # exist for the requested date — filter those out
            has_calls = bool(data.get("callExpDateMap"))
            has_puts  = bool(data.get("putExpDateMap"))
            if has_calls or has_puts:
                return data
            logger.debug("%s: no 0DTE contracts found for %s", symbol, date_str)
            return None
        if resp.status_code == 404:
            logger.debug("%s: no options chain (404)", symbol)
        else:
            logger.debug(
                "%s: chains returned %s — %s",
                symbol, resp.status_code, resp.text[:120],
            )
        return None
    except requests.Timeout:
        logger.warning("%s: options chain request timed out", symbol)
        return None
    except requests.RequestException as e:
        logger.warning("%s: options chain fetch failed: %s", symbol, e)
        return None


def fetch_quote(symbol: str, token: str) -> float | None:
    """Fetch the current price of a single ticker via the Schwab quotes endpoint."""
    url = f"{SCHWAB_MARKET_BASE}/quotes"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params={"symbols": symbol, "fields": "quote"},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            info  = data.get(symbol, {})
            quote = info.get("quote", {})
            price = (
                quote.get("lastPrice")
                or quote.get("mark")
                or quote.get("closePrice")
            )
            if price and float(price) > 0:
                return float(price)
    except Exception as e:
        logger.warning("Quote fetch failed for %s: %s", symbol, e)
    return None
