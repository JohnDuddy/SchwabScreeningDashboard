from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from app import get_valid_token, _expiring_universe_rows_and_meta
from expiring_options.scanner import (
    REQUEST_DELAY_SECONDS,
    fetch_schwab_put_chain,
    fetch_schwab_quotes,
    flatten_schwab_put_chain,
    next_standard_expiration,
    option_midpoint_price,
    select_strike_at_or_below,
)


TARGET_PRICE_PERCENT = 90.0


def _empty_row(symbol_row: dict[str, Any], expiration: str, status: str, message: str = "") -> dict[str, Any]:
    return {
        "symbol": symbol_row.get("symbol"),
        "company_name": symbol_row.get("company_name"),
        "expiration_date": expiration,
        "current_stock_price": None,
        "target_percent_of_current_price": TARGET_PRICE_PERCENT,
        "calculated_90_percent_price": None,
        "selected_put_strike": None,
        "bid": None,
        "ask": None,
        "midpoint_premium": None,
        "midpoint_formula": "(bid + ask) / 2",
        "last_price": None,
        "volume": None,
        "open_interest": None,
        "quote_timestamp": None,
        "status": status,
        "message": message,
    }


def _select_90pct_put_row(
    symbol_row: dict[str, Any],
    current_price: float,
    contracts: list[dict[str, Any]],
    expiration: str,
) -> dict[str, Any]:
    target_price = current_price * (TARGET_PRICE_PERCENT / 100.0)
    strikes = [float(c["strike_price"]) for c in contracts if c.get("strike_price") is not None]
    selected_strike, approximate = select_strike_at_or_below(strikes, target_price)
    selected_contract = next(c for c in contracts if float(c["strike_price"]) == float(selected_strike))
    bid = selected_contract.get("bid_price")
    ask = selected_contract.get("ask_price")
    midpoint = option_midpoint_price(bid, ask) if bid is not None and ask is not None else None
    status = "ok" if midpoint is not None else "missing_bid_or_ask"
    message = "selected nearest strike because no strike was at or below 90% target" if approximate else ""
    return {
        "symbol": symbol_row.get("symbol"),
        "company_name": symbol_row.get("company_name"),
        "expiration_date": expiration,
        "current_stock_price": current_price,
        "target_percent_of_current_price": TARGET_PRICE_PERCENT,
        "calculated_90_percent_price": target_price,
        "selected_put_strike": selected_strike,
        "bid": bid,
        "ask": ask,
        "midpoint_premium": midpoint,
        "midpoint_formula": "(bid + ask) / 2",
        "last_price": selected_contract.get("last_price"),
        "volume": selected_contract.get("volume"),
        "open_interest": selected_contract.get("open_interest"),
        "quote_timestamp": selected_contract.get("quote_timestamp"),
        "status": status,
        "message": message,
    }


def build_90pct_midpoint_rows(symbol_rows: list[dict[str, Any]], token: str, scan_date, limit: int | None = None) -> list[dict[str, Any]]:
    selected_symbols = symbol_rows[:limit] if limit else symbol_rows
    symbols = [row["symbol"] for row in selected_symbols]
    expiration = scan_date.isoformat()
    try:
        prices = fetch_schwab_quotes(symbols, token)
    except Exception as exc:
        print(f"Quote batch failed; chain underlying prices will be used where available. {exc}")
        prices = {}

    rows: list[dict[str, Any]] = []
    total = len(selected_symbols)
    for index, symbol_row in enumerate(selected_symbols, start=1):
        symbol = symbol_row["symbol"]
        print(f"[{index}/{total}] {symbol}")
        try:
            chain = fetch_schwab_put_chain(symbol, token, scan_date)
            if not chain:
                rows.append(_empty_row(symbol_row, expiration, "no_option_chain", "No put option chain returned for selected expiration."))
                continue
            chain_price, contracts = flatten_schwab_put_chain(chain, scan_date, prices.get(symbol))
            current_price = chain_price or prices.get(symbol)
            if not current_price:
                rows.append(_empty_row(symbol_row, expiration, "missing_current_price", "No current underlying price returned."))
                continue
            if not contracts:
                row = _empty_row(symbol_row, expiration, "no_put_contracts", "No put contracts returned for selected expiration.")
                row["current_stock_price"] = current_price
                row["calculated_90_percent_price"] = current_price * 0.90
                rows.append(row)
                continue
            rows.append(_select_90pct_put_row(symbol_row, current_price, contracts, expiration))
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else "HTTP"
            rows.append(_empty_row(symbol_row, expiration, "api_error", f"Schwab request failed ({status_code}): {exc}"))
        except Exception as exc:
            rows.append(_empty_row(symbol_row, expiration, "error", str(exc)))
        time.sleep(REQUEST_DELAY_SECONDS)
    return rows


def write_outputs(rows: list[dict[str, Any]], output_dir: Path, scan_date) -> tuple[Path, Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"90pct_put_midpoints_{scan_date.isoformat()}_{timestamp}"
    csv_path = output_dir / f"{stem}.csv"
    xlsx_path = output_dir / f"{stem}.xlsx"
    valid_csv_path = output_dir / f"{stem}_valid_only.csv"
    valid_xlsx_path = output_dir / f"{stem}_valid_only.xlsx"
    df = pd.DataFrame(rows)
    df.to_csv(csv_path, index=False)
    df.to_excel(xlsx_path, index=False)
    valid_df = df[df["status"].eq("ok")].copy() if "status" in df.columns else df
    valid_df.to_csv(valid_csv_path, index=False)
    valid_df.to_excel(valid_xlsx_path, index=False)
    return csv_path, xlsx_path, valid_csv_path, valid_xlsx_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Export 90% current-price put midpoint premiums for the full app universe.")
    parser.add_argument("--expiration", help="Expiration date YYYY-MM-DD. Defaults to the next standard Friday expiration.")
    parser.add_argument("--limit", type=int, help="Optional symbol limit for smoke testing.")
    parser.add_argument("--output-dir", default="exports", help="Folder for CSV/XLSX outputs.")
    args = parser.parse_args()

    scan_date = datetime.strptime(args.expiration, "%Y-%m-%d").date() if args.expiration else next_standard_expiration()
    token = get_valid_token()
    if not token:
        raise SystemExit("Schwab authentication is required. Open the app and connect Schwab first.")

    symbol_rows, meta = _expiring_universe_rows_and_meta()
    print(f"Loaded {len(symbol_rows)} symbols from app universe source: {meta.get('source', 'unknown')}")
    print(f"Expiration: {scan_date.isoformat()}")
    rows = build_90pct_midpoint_rows(symbol_rows, token, scan_date, limit=args.limit)
    csv_path, xlsx_path, valid_csv_path, valid_xlsx_path = write_outputs(rows, Path(args.output_dir), scan_date)

    ok_count = sum(1 for row in rows if row.get("status") == "ok")
    missing_count = len(rows) - ok_count
    print()
    print(f"Rows written: {len(rows)}")
    print(f"OK midpoint rows: {ok_count}")
    print(f"Rows with missing/API status: {missing_count}")
    print(f"CSV:  {csv_path.resolve()}")
    print(f"XLSX: {xlsx_path.resolve()}")
    print(f"Valid-only CSV:  {valid_csv_path.resolve()}")
    print(f"Valid-only XLSX: {valid_xlsx_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
