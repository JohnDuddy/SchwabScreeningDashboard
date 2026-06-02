from __future__ import annotations

import csv
import io
import math
import os
import sqlite3
import time
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import requests

TARGET_LEVELS = (5.0, 10.0, 15.0)
DEFAULT_DB_PATH = Path("data") / "expiring_options.sqlite"
SCHWAB_MARKET_BASE = os.environ.get("SCHWAB_MARKETDATA_BASE_URL", "https://api.schwabapi.com/marketdata/v1")
REQUEST_DELAY_SECONDS = float(os.environ.get("EXPIRING_OPTIONS_REQUEST_DELAY", "0.35"))
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("EXPIRING_OPTIONS_TIMEOUT", "20"))
EXPECTED_UNIVERSE_COUNT = int(os.environ.get("EXPIRING_OPTIONS_EXPECTED_UNIVERSE", "516"))
MATERIAL_UNIVERSE_DIFFERENCE = int(os.environ.get("EXPIRING_OPTIONS_UNIVERSE_WARNING_DELTA", "35"))

SORT_FIELDS = {
    "midpoint_premium_yield_on_strike": "Mid premium / selected strike %",
    "midpoint_premium_yield_on_underlying": "Midpoint yield on underlying",
    "bid_premium_yield_on_strike": "Bid yield on strike",
    "breakeven_discount": "Breakeven discount",
    "bid_ask_spread_percentage": "Bid-ask spread %",
    "volume": "Volume",
    "open_interest": "Open interest",
    "implied_volatility": "Implied volatility",
    "delta": "Delta",
}

def next_standard_expiration(as_of: date | None = None) -> date:
    """Return the next standard Friday expiration, including today if today is Friday."""
    base = as_of or date.today()
    days_until_friday = (4 - base.weekday()) % 7
    return base + timedelta(days=days_until_friday)

@dataclass
class ExpiringOptionFilters:
    min_bid_price: float = 0.01
    min_ask_price: float = 0.01
    min_midpoint_price: float = 0.01
    min_open_interest: int = 0
    min_volume: int = 0
    max_bid_ask_spread_percentage: float = 100.0
    max_absolute_delta: float | None = None
    min_distance_below_current_stock_price: float = 0.0
    exclude_zero_bid: bool = True
    exclude_missing_bid_ask: bool = True
    exclude_extremely_wide_spreads: bool = True
    extremely_wide_spread_percentage: float = 250.0
    exclude_earnings_today_or_next: bool = False
    exclude_hard_to_borrow: bool = False

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", "").replace("$", "").replace("%", "")
        if not value or value in {"--", "nan", "None", "N/A"}:
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def parse_int(value: Any) -> int | None:
    number = parse_float(value)
    if number is None:
        return None
    return int(number)


def normalize_symbol(symbol: Any) -> str:
    return str(symbol or "").strip().upper().replace(".", "-")


def target_strike(current_stock_price: float, target_percentage: float) -> float:
    return current_stock_price * (1 - target_percentage / 100.0)


def option_midpoint_price(bid_price: float, ask_price: float) -> float:
    return (bid_price + ask_price) / 2.0


def _select_strike_from_sorted(clean: list[float], calculated_target: float) -> tuple[float, bool]:
    if not clean:
        raise ValueError("No listed strikes available")
    idx = bisect_right(clean, calculated_target) - 1
    if idx >= 0:
        return clean[idx], False
    return clean[0], True


def select_strike_at_or_below(strikes: list[float], calculated_target: float) -> tuple[float, bool]:
    clean = sorted({float(strike) for strike in strikes})
    return _select_strike_from_sorted(clean, calculated_target)


def pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator * 100.0


def schwab_timestamp(value: Any) -> str | None:
    number = parse_int(value)
    if number is None or number <= 0:
        return None
    try:
        if number > 10_000_000_000:
            number = number // 1000
        return datetime.fromtimestamp(number).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def normalize_iv(value: Any) -> float | None:
    iv = parse_float(value)
    if iv is None:
        return None
    return iv * 100.0 if 0 < iv < 20 else iv


def flatten_schwab_put_chain(chain: dict[str, Any], scan_date: date, fallback_price: float | None = None) -> tuple[float | None, list[dict[str, Any]]]:
    date_str = scan_date.strftime("%Y-%m-%d")
    underlying_price = parse_float(chain.get("underlyingPrice")) or fallback_price
    contracts: list[dict[str, Any]] = []
    put_map = chain.get("putExpDateMap") or {}
    for exp_key, strikes_map in put_map.items():
        expiration = str(exp_key).split(":", 1)[0]
        if expiration != date_str or not isinstance(strikes_map, dict):
            continue
        for strike_key, option_list in strikes_map.items():
            strike = parse_float(strike_key)
            if strike is None or not isinstance(option_list, list):
                continue
            for opt in option_list:
                if not isinstance(opt, dict):
                    continue
                quote_time = schwab_timestamp(opt.get("quoteTimeInLong") or opt.get("tradeTimeInLong"))
                contracts.append({
                    "symbol": normalize_symbol(chain.get("symbol") or opt.get("underlyingSymbol")),
                    "option_symbol": opt.get("symbol"),
                    "expiration_date": expiration,
                    "strike_price": strike,
                    "bid_price": parse_float(opt.get("bid")),
                    "ask_price": parse_float(opt.get("ask")),
                    "last_price": parse_float(opt.get("last")),
                    "volume": parse_int(opt.get("totalVolume") or opt.get("volume")),
                    "open_interest": parse_int(opt.get("openInterest")),
                    "implied_volatility": normalize_iv(opt.get("volatility") or opt.get("impliedVolatility")),
                    "delta": parse_float(opt.get("delta")),
                    "theta": parse_float(opt.get("theta")),
                    "gamma": parse_float(opt.get("gamma")),
                    "vega": parse_float(opt.get("vega")),
                    "quote_timestamp": quote_time,
                    "market_data_status": "Schwab Market Data API; real-time/delayed depends on account entitlements",
                })
    return underlying_price, contracts


def fetch_schwab_quotes(symbols: list[str], token: str, session: requests.Session | None = None) -> dict[str, float]:
    prices: dict[str, float] = {}
    http = session or requests
    for i in range(0, len(symbols), 100):
        batch = symbols[i:i + 100]
        resp = http.get(
            f"{SCHWAB_MARKET_BASE}/quotes",
            headers={"Authorization": f"Bearer {token}"},
            params={"symbols": ",".join(batch), "fields": "quote"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()
        for symbol, info in payload.items():
            quote = info.get("quote", {}) if isinstance(info, dict) else {}
            price = parse_float(quote.get("lastPrice") or quote.get("mark") or quote.get("closePrice"))
            if price and price > 0:
                prices[normalize_symbol(symbol)] = price
    return prices


def fetch_schwab_put_chain(
    symbol: str,
    token: str,
    scan_date: date,
    session: requests.Session | None = None,
) -> dict[str, Any] | None:
    date_str = scan_date.strftime("%Y-%m-%d")
    http = session or requests
    resp = http.get(
        f"{SCHWAB_MARKET_BASE}/chains",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "symbol": symbol,
            "contractType": "PUT",
            "strategy": "SINGLE",
            "includeUnderlyingQuote": "true",
            "fromDate": date_str,
            "toDate": date_str,
            "range": "ALL",
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    return data if data.get("putExpDateMap") else None


def read_csv_dicts(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_test_data(root: Path, scan_date: date) -> tuple[list[dict[str, Any]], dict[str, float], dict[str, list[dict[str, Any]]]]:
    symbol_rows = read_csv_dicts(root / "samples" / "expiring_symbol_universe.csv")
    price_rows = read_csv_dicts(root / "samples" / "expiring_current_prices.csv")
    chain_rows = read_csv_dicts(root / "samples" / "expiring_option_chains.csv")
    symbols = []
    for row in symbol_rows:
        symbol = normalize_symbol(row.get("symbol"))
        if symbol:
            symbols.append({"symbol": symbol, "company_name": row.get("company_name") or symbol, "index_membership": row.get("index_membership") or "Sample"})
    prices = {normalize_symbol(row.get("symbol")): parse_float(row.get("current_stock_price")) for row in price_rows if normalize_symbol(row.get("symbol")) and parse_float(row.get("current_stock_price"))}
    chains: dict[str, list[dict[str, Any]]] = {}
    date_str = scan_date.strftime("%Y-%m-%d")
    for row in chain_rows:
        symbol = normalize_symbol(row.get("symbol"))
        expiration = row.get("expiration_date") or date_str
        put_call = str(row.get("put_call") or "PUT").upper()
        if not symbol or put_call != "PUT" or expiration != date_str:
            continue
        chains.setdefault(symbol, []).append({
            "symbol": symbol,
            "option_symbol": row.get("option_symbol"),
            "expiration_date": expiration,
            "strike_price": parse_float(row.get("strike_price")),
            "bid_price": parse_float(row.get("bid_price")),
            "ask_price": parse_float(row.get("ask_price")),
            "last_price": parse_float(row.get("last_price")),
            "volume": parse_int(row.get("volume")),
            "open_interest": parse_int(row.get("open_interest")),
            "implied_volatility": parse_float(row.get("implied_volatility")),
            "delta": parse_float(row.get("delta")),
            "theta": parse_float(row.get("theta")),
            "gamma": parse_float(row.get("gamma")),
            "vega": parse_float(row.get("vega")),
            "quote_timestamp": row.get("quote_timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "market_data_status": row.get("market_data_status") or "Test CSV data",
        })
    return symbols, prices, chains

def calculate_candidate(symbol_row: dict[str, Any], current_price: float, target_percentage: float, contract: dict[str, Any], calculated_target: float, selected_strike: float, approximate: bool, scan_timestamp: str) -> dict[str, Any]:
    bid = contract.get("bid_price")
    ask = contract.get("ask_price")
    midpoint = option_midpoint_price(bid, ask) if bid is not None and ask is not None else None
    spread = (ask - bid) if bid is not None and ask is not None else None
    spread_pct = pct(spread, midpoint) if midpoint else None
    breakeven = selected_strike - midpoint if midpoint is not None else None
    return {
        "rank": None,
        "symbol": symbol_row["symbol"],
        "company_name": symbol_row.get("company_name") or symbol_row["symbol"],
        "current_stock_price": current_price,
        "target_percentage": target_percentage,
        "calculated_target_strike": calculated_target,
        "actual_selected_strike": selected_strike,
        "expiration_date": contract.get("expiration_date"),
        "bid_price": bid,
        "ask_price": ask,
        "option_midpoint_price": midpoint,
        "last_price": contract.get("last_price"),
        "volume": contract.get("volume"),
        "open_interest": contract.get("open_interest"),
        "implied_volatility": contract.get("implied_volatility"),
        "delta": contract.get("delta"),
        "theta": contract.get("theta"),
        "gamma": contract.get("gamma"),
        "vega": contract.get("vega"),
        "bid_ask_spread": spread,
        "bid_ask_spread_percentage": spread_pct,
        "midpoint_premium_yield_on_strike": pct(midpoint, selected_strike),
        "midpoint_premium_percent_of_selected_strike": pct(midpoint, selected_strike),
        "midpoint_premium_yield_on_underlying": pct(midpoint, current_price),
        "bid_premium_yield_on_strike": pct(bid, selected_strike),
        "distance_below_current_stock_price": pct(current_price - selected_strike, current_price),
        "breakeven_price": breakeven,
        "breakeven_discount": pct(current_price - breakeven, current_price) if breakeven is not None else None,
        "is_approximate_strike": approximate,
        "market_data_status": contract.get("market_data_status"),
        "quote_timestamp": contract.get("quote_timestamp") or scan_timestamp,
        "scan_timestamp": scan_timestamp,
    }


def filter_reason(row: dict[str, Any], filters: ExpiringOptionFilters) -> str | None:
    bid = row.get("bid_price")
    ask = row.get("ask_price")
    midpoint = row.get("option_midpoint_price")
    spread_pct = row.get("bid_ask_spread_percentage")
    delta = row.get("delta")
    if filters.exclude_missing_bid_ask and (bid is None or ask is None):
        return "missing bid or ask"
    if filters.exclude_zero_bid and bid == 0:
        return "zero bid"
    if bid is not None and bid < filters.min_bid_price:
        return "bid below minimum"
    if ask is not None and ask < filters.min_ask_price:
        return "ask below minimum"
    if midpoint is not None and midpoint < filters.min_midpoint_price:
        return "midpoint below minimum"
    if (row.get("open_interest") or 0) < filters.min_open_interest:
        return "open interest below minimum"
    if (row.get("volume") or 0) < filters.min_volume:
        return "volume below minimum"
    if spread_pct is not None and spread_pct > filters.max_bid_ask_spread_percentage:
        return "spread percentage above maximum"
    if filters.exclude_extremely_wide_spreads and spread_pct is not None and spread_pct > filters.extremely_wide_spread_percentage:
        return "extremely wide spread"
    if filters.max_absolute_delta is not None and delta is not None and abs(delta) > filters.max_absolute_delta:
        return "absolute delta above maximum"
    if (row.get("distance_below_current_stock_price") or 0) < filters.min_distance_below_current_stock_price:
        return "distance below current price under minimum"
    if filters.exclude_earnings_today_or_next and row.get("has_near_term_earnings"):
        return "earnings today or next trading day"
    if filters.exclude_hard_to_borrow and row.get("is_hard_to_borrow"):
        return "hard-to-borrow symbol"
    return None


def rank_rows(rows: list[dict[str, Any]], sort_by: str) -> list[dict[str, Any]]:
    sort_field = sort_by if sort_by in SORT_FIELDS else "midpoint_premium_yield_on_strike"
    ranked = sorted(rows, key=lambda row: row.get(sort_field) if row.get(sort_field) is not None else -1e18, reverse=True)
    for i, row in enumerate(ranked, start=1):
        row["rank"] = i
    return ranked


def build_candidates(symbol_rows: list[dict[str, Any]], prices: dict[str, float], chains: dict[str, list[dict[str, Any]]], filters: ExpiringOptionFilters, sort_by: str, scan_date: date, errors: list[dict[str, str]]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], int]:
    scan_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows_by_level = {"5": [], "10": [], "15": []}
    full_chain_rows: list[dict[str, Any]] = []
    excluded = 0
    for symbol_row in symbol_rows:
        symbol = symbol_row["symbol"]
        current_price = prices.get(symbol)
        if not current_price or current_price <= 0:
            errors.append({"symbol": symbol, "error_type": "missing_price", "error_message": "Missing current stock price"})
            continue
        contracts = [c for c in chains.get(symbol, []) if c.get("strike_price") is not None]
        if not contracts:
            errors.append({"symbol": symbol, "error_type": "no_expiration_puts", "error_message": "No put option chain for the selected expiration date"})
            continue
        for contract in contracts:
            chain_row = dict(contract)
            chain_row["current_stock_price"] = current_price
            full_chain_rows.append(chain_row)
        by_strike: dict[float, dict[str, Any]] = {}
        for contract in contracts:
            by_strike.setdefault(float(contract["strike_price"]), contract)
        strikes = sorted(by_strike)
        for level in TARGET_LEVELS:
            calculated = target_strike(current_price, level)
            try:
                selected, approximate = _select_strike_from_sorted(strikes, calculated)
            except ValueError as exc:
                errors.append({"symbol": symbol, "error_type": "no_strikes", "error_message": str(exc)})
                continue
            row = calculate_candidate(symbol_row, current_price, level, by_strike[selected], calculated, selected, approximate, scan_timestamp)
            reason = filter_reason(row, filters)
            if reason:
                excluded += 1
                errors.append({"symbol": symbol, "error_type": "filtered", "error_message": f"{level:.0f}% candidate excluded: {reason}"})
                continue
            rows_by_level[str(int(level))].append(row)
    for key in rows_by_level:
        rows_by_level[key] = rank_rows(rows_by_level[key], sort_by)
    return rows_by_level, full_chain_rows, excluded


def init_db(path: Path = DEFAULT_DB_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS symbols (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL UNIQUE, company_name TEXT, index_membership TEXT, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS scan_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, scan_timestamp TEXT NOT NULL, total_symbols_loaded INTEGER NOT NULL DEFAULT 0, total_symbols_scanned INTEGER NOT NULL DEFAULT 0, total_candidates_found INTEGER NOT NULL DEFAULT 0, total_errors INTEGER NOT NULL DEFAULT 0, notes TEXT);
        CREATE TABLE IF NOT EXISTS option_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scan_run_id INTEGER NOT NULL, symbol TEXT NOT NULL, company_name TEXT, current_stock_price REAL NOT NULL,
            target_percentage REAL NOT NULL, calculated_target_strike REAL NOT NULL, actual_selected_strike REAL NOT NULL, expiration_date TEXT NOT NULL,
            bid_price REAL, ask_price REAL, option_midpoint_price REAL, last_price REAL, volume INTEGER, open_interest INTEGER, implied_volatility REAL,
            delta REAL, theta REAL, gamma REAL, vega REAL, bid_ask_spread REAL, bid_ask_spread_percentage REAL,
            midpoint_premium_yield_on_strike REAL, midpoint_premium_yield_on_underlying REAL, bid_premium_yield_on_strike REAL,
            distance_below_current_stock_price REAL, breakeven_price REAL, breakeven_discount REAL, is_approximate_strike INTEGER NOT NULL DEFAULT 0,
            market_data_status TEXT, quote_timestamp TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS errors (id INTEGER PRIMARY KEY AUTOINCREMENT, scan_run_id INTEGER NOT NULL, symbol TEXT, error_type TEXT NOT NULL, error_message TEXT NOT NULL, created_at TEXT NOT NULL);
        """)


def save_to_sqlite(payload: dict[str, Any], db_path: Path = DEFAULT_DB_PATH) -> int:
    init_db(db_path)
    summary = payload["summary"]
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO scan_runs (scan_timestamp,total_symbols_loaded,total_symbols_scanned,total_candidates_found,total_errors,notes) VALUES (?,?,?,?,?,?)", (summary["timestamp"], summary["total_symbols_loaded"], summary["total_symbols_scanned"], summary["total_candidates_found"], len(payload.get("errors", [])), summary.get("notes", "")))
        scan_run_id = int(cur.lastrowid)
        for row in payload.get("all_rows", []):
            cur.execute("""INSERT INTO option_candidates (scan_run_id,symbol,company_name,current_stock_price,target_percentage,calculated_target_strike,actual_selected_strike,expiration_date,bid_price,ask_price,option_midpoint_price,last_price,volume,open_interest,implied_volatility,delta,theta,gamma,vega,bid_ask_spread,bid_ask_spread_percentage,midpoint_premium_yield_on_strike,midpoint_premium_yield_on_underlying,bid_premium_yield_on_strike,distance_below_current_stock_price,breakeven_price,breakeven_discount,is_approximate_strike,market_data_status,quote_timestamp,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (scan_run_id,row["symbol"],row.get("company_name"),row["current_stock_price"],row["target_percentage"],row["calculated_target_strike"],row["actual_selected_strike"],row["expiration_date"],row.get("bid_price"),row.get("ask_price"),row.get("option_midpoint_price"),row.get("last_price"),row.get("volume"),row.get("open_interest"),row.get("implied_volatility"),row.get("delta"),row.get("theta"),row.get("gamma"),row.get("vega"),row.get("bid_ask_spread"),row.get("bid_ask_spread_percentage"),row.get("midpoint_premium_yield_on_strike"),row.get("midpoint_premium_yield_on_underlying"),row.get("bid_premium_yield_on_strike"),row.get("distance_below_current_stock_price"),row.get("breakeven_price"),row.get("breakeven_discount"),1 if row.get("is_approximate_strike") else 0,row.get("market_data_status"),row.get("quote_timestamp"),created_at))
        for error in payload.get("errors", []):
            cur.execute("INSERT INTO errors (scan_run_id,symbol,error_type,error_message,created_at) VALUES (?,?,?,?,?)", (scan_run_id, error.get("symbol"), error.get("error_type", "error"), error.get("error_message", ""), created_at))
    return scan_run_id


def to_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def export_csv(rows: list[dict[str, Any]]) -> str:
    return to_dataframe(rows).to_csv(index=False)


def export_excel_bytes(rows_by_level: dict[str, list[dict[str, Any]]], errors: list[dict[str, Any]]) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for level in ("5", "10", "15"):
            to_dataframe(rows_by_level.get(level, [])).to_excel(writer, sheet_name=f"{level} pct", index=False)
        pd.DataFrame(errors).to_excel(writer, sheet_name="Errors", index=False)
    return output.getvalue()


def run_test_scan(root: Path, filters: ExpiringOptionFilters, sort_by: str, progress: Callable[[int, int, str], None] | None = None, scan_date: date | None = None) -> dict[str, Any]:
    scan_date = scan_date or next_standard_expiration()
    symbols, prices, chains = load_test_data(root, scan_date)
    errors: list[dict[str, str]] = []
    for i, row in enumerate(symbols, start=1):
        if progress:
            progress(i, len(symbols), row["symbol"])
    rows_by_level, chain_rows, excluded = build_candidates(symbols, prices, chains, filters, sort_by, scan_date, errors)
    return finalize_payload("test", symbols, rows_by_level, chain_rows, errors, excluded, filters, sort_by, scan_date)


def run_live_scan(token: str, symbol_rows: list[dict[str, Any]], filters: ExpiringOptionFilters, sort_by: str, progress: Callable[[int, int, str], None] | None = None, scan_date: date | None = None) -> dict[str, Any]:
    scan_date = scan_date or next_standard_expiration()
    symbols = [row["symbol"] for row in symbol_rows]
    errors: list[dict[str, str]] = []
    with requests.Session() as session:
        try:
            prices = fetch_schwab_quotes(symbols, token, session=session)
        except Exception as exc:
            prices = {}
            errors.append({"symbol": "*", "error_type": "quote_batch_error", "error_message": str(exc)})
        chains: dict[str, list[dict[str, Any]]] = {}
        total = len(symbol_rows)
        for i, row in enumerate(symbol_rows, start=1):
            symbol = row["symbol"]
            if progress:
                progress(i, total, symbol)
            try:
                chain = fetch_schwab_put_chain(symbol, token, scan_date, session=session)
                if not chain:
                    errors.append({"symbol": symbol, "error_type": "no_option_chain", "error_message": f"No put option chain returned for selected expiration {scan_date.isoformat()}"})
                    continue
                price, contracts = flatten_schwab_put_chain(chain, scan_date, prices.get(symbol))
                if price and price > 0:
                    prices[symbol] = price
                chains[symbol] = contracts
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else "HTTP"
                errors.append({"symbol": symbol, "error_type": "api_error", "error_message": f"Schwab chain request failed ({status}): {exc}"})
            except requests.Timeout:
                errors.append({"symbol": symbol, "error_type": "api_timeout", "error_message": "Schwab chain request timed out"})
            except Exception as exc:
                errors.append({"symbol": symbol, "error_type": "api_error", "error_message": str(exc)})
            time.sleep(REQUEST_DELAY_SECONDS)
    rows_by_level, chain_rows, excluded = build_candidates(symbol_rows, prices, chains, filters, sort_by, scan_date, errors)
    return finalize_payload("live", symbol_rows, rows_by_level, chain_rows, errors, excluded, filters, sort_by, scan_date)


def finalize_payload(mode: str, symbols: list[dict[str, Any]], rows_by_level: dict[str, list[dict[str, Any]]], chain_rows: list[dict[str, Any]], errors: list[dict[str, str]], excluded: int, filters: ExpiringOptionFilters, sort_by: str, scan_date: date) -> dict[str, Any]:
    all_rows = rows_by_level["5"] + rows_by_level["10"] + rows_by_level["15"]
    count = len(symbols)
    warnings: list[str] = []
    missing_symbols = len({e.get("symbol") for e in errors if e.get("error_type") in {"missing_price", "no_same_day_puts", "no_expiration_puts", "no_option_chain"}})
    api_errors = len([e for e in errors if "api" in e.get("error_type", "")])
    if scan_date.weekday() != 4:
        warnings.append(f"Selected expiration {scan_date.isoformat()} is not a standard Friday expiration; use Custom date only for holiday-adjusted or special expirations.")
    if mode == "live" and count and not all_rows and missing_symbols == count:
        warnings.append(f"No put chains were returned for expiration {scan_date.isoformat()}. Confirm this is the next listed expiration date for the symbols being scanned, or choose a custom expiration date.")
    if abs(count - EXPECTED_UNIVERSE_COUNT) > MATERIAL_UNIVERSE_DIFFERENCE and mode == "live":
        warnings.append(f"Universe count is {count}, materially different from expected ~{EXPECTED_UNIVERSE_COUNT}.")
    if filters.exclude_earnings_today_or_next:
        warnings.append("Earnings exclusion requested, but no earnings data source is configured; no symbols were excluded for earnings.")
    if filters.exclude_hard_to_borrow:
        warnings.append("Hard-to-borrow exclusion requested, but no borrow data source is configured; no symbols were excluded for borrow status.")
    summary = {
        "mode": mode,
        "scan_date": scan_date.strftime("%Y-%m-%d"),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_symbols_loaded": count,
        "total_symbols_scanned": count,
        "total_candidates_found": len(all_rows),
        "missing_data_symbols": missing_symbols,
        "api_errors": api_errors,
        "excluded_by_filters": excluded,
        "rows_5": len(rows_by_level["5"]),
        "rows_10": len(rows_by_level["10"]),
        "rows_15": len(rows_by_level["15"]),
        "sort_by": sort_by,
        "market_data_status": "Test CSV data" if mode == "test" else "Schwab Market Data API; real-time/delayed depends on account entitlements",
        "warnings": warnings,
        "notes": "; ".join(warnings),
    }
    payload = {"rows_by_level": rows_by_level, "all_rows": all_rows, "chain_rows": chain_rows, "errors": errors, "summary": summary, "filters": filters.to_dict(), "sort_by": sort_by}
    payload["scan_run_id"] = save_to_sqlite(payload)
    return payload

