"""
Schwab Covered Call Dashboard
A secure local web application connecting to Charles Schwab Developer API.
"""

import os
import json
import math
import csv
import io
import logging
import re
from datetime import datetime
from functools import wraps
from urllib.parse import urlencode, urlparse, parse_qs

import base64
import requests
from flask import (
    Flask, render_template, redirect, request,
    session, jsonify, Response, url_for
)
from dotenv import load_dotenv

import threading
import pandas as pd
import momentum as mom

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(32))

@app.errorhandler(500)
def internal_error(e):
    import traceback
    tb = traceback.format_exc()
    logger.error("500 error: %s", tb)
    return render_template("error.html", message=f"Internal error: {e}<br><pre>{tb}</pre>"), 500

# ── Schwab OAuth constants ──────────────────────────────────────────────────
SCHWAB_AUTH_URL   = "https://api.schwabapi.com/v1/oauth/authorize"
SCHWAB_TOKEN_URL  = "https://api.schwabapi.com/v1/oauth/token"
SCHWAB_API_BASE   = "https://api.schwabapi.com/trader/v1"

CLIENT_ID         = os.environ.get("SCHWAB_CLIENT_ID", "")
CLIENT_SECRET     = os.environ.get("SCHWAB_CLIENT_SECRET", "")
REDIRECT_URI      = os.environ.get("SCHWAB_REDIRECT_URI", "http://127.0.0.1:5000/callback")
TOKEN_FILE        = os.environ.get("TOKEN_FILE", ".schwab_tokens.json")


# ── Token helpers ───────────────────────────────────────────────────────────

def save_tokens(tokens: dict):
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)


def load_tokens() -> dict | None:
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            return json.load(f)
    return None


def get_valid_token() -> str | None:
    """Return a valid access token, refreshing if needed."""
    tokens = load_tokens()
    if not tokens:
        return None

    # Try to refresh using refresh token
    try:
        b64 = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        resp = requests.post(
            SCHWAB_TOKEN_URL,
            headers={
                "Authorization": f"Basic {b64}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type":    "refresh_token",
                "refresh_token": tokens.get("refresh_token"),
            },
            timeout=15,
        )
        resp.raise_for_status()
        new_tokens = resp.json()
        save_tokens(new_tokens)
        return new_tokens["access_token"]
    except Exception as e:
        logger.warning("Token refresh failed: %s", e)
        return None


def schwab_get(path: str, token: str, params: dict = None) -> dict:
    """Authenticated GET against the Schwab Trader API."""
    url = f"{SCHWAB_API_BASE}{path}"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()




SCHWAB_MARKET_BASE = "https://api.schwabapi.com/marketdata/v1"


def fetch_underlying_quotes(symbols: list[str], token: str) -> dict[str, float]:
    """
    Fetch CURRENT STOCK PRICES for a list of underlying tickers.
    Uses Schwab Market Data API first, falls back to Yahoo Finance.
    Returns {ticker: last_price}.
    """
    prices: dict[str, float] = {}
    if not symbols:
        return prices

    remaining = list(symbols)

    # ── Schwab Market Data API (quotes endpoint, batches of 100) ──
    try:
        for i in range(0, len(remaining), 100):
            batch = remaining[i:i+100]
            sym_str = ",".join(batch)
            url = f"{SCHWAB_MARKET_BASE}/quotes"
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params={"symbols": sym_str, "fields": "quote"},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                for sym, info in data.items():
                    quote = info.get("quote", {})
                    last = quote.get("lastPrice") or quote.get("mark") or quote.get("closePrice")
                    if last and last > 0:
                        prices[sym.upper()] = round(float(last), 2)
    except Exception as e:
        logger.warning("Schwab quotes failed: %s", e)

    # ── Yahoo fallback for any tickers Schwab didn't return ──
    remaining = [s for s in symbols if s not in prices]
    if remaining:
        try:
            import yfinance as yf
            for sym in remaining:
                try:
                    t = yf.Ticker(sym)
                    info = t.fast_info if hasattr(t, "fast_info") else {}
                    last = getattr(info, "last_price", None) or getattr(info, "previous_close", None)
                    if last and last > 0:
                        prices[sym.upper()] = round(float(last), 2)
                except Exception:
                    pass
        except ImportError:
            logger.warning("yfinance not installed — cannot fetch fallback quotes")

    return prices

# ── Covered-call logic ──────────────────────────────────────────────────────

# ── Custom account labels keyed by last-4 digits ───────────────────────────
ACCOUNT_LABELS = {
    "9680": "BIS, LLC",
    "2634": "Long Term",
    "5649": "SEP",
    "2399": "IRA",
}

SYMBOL_NAME_OVERRIDES = {
    "ACHR": "Archer Aviation Inc. Class A",
    "GDXY": "YieldMax Gold Miners Option Income Strategy ETF",
    "GDX": "VanEck Gold Miners ETF",
    "GDXJ": "VanEck Junior Gold Miners ETF",
    "FAST": "Fastenal Company",
    "IONQ": "IonQ Inc.",
    "SLV": "iShares Silver Trust",
    "INTC": "Intel Corporation",
    "KEYS": "Keysight Technologies Inc.",
    "LRCX": "Lam Research Corporation",
    "MFC": "Manulife Financial Corporation",
    "NVDA": "NVIDIA Corporation",
    "PLTR": "Palantir Technologies Inc. Class A",
    "RGTI": "Rigetti Computing Inc.",
    "TGB": "Taseko Mines Limited",
    "TSLA": "Tesla Inc.",
    "WELL": "Welltower Inc.",
}
COMPANY_NAME_CACHE = os.environ.get("COMPANY_NAME_CACHE", ".company_name_cache.json")

LONG_UNDERLYING_ASSET_TYPES = {
    "EQUITY",
    "COLLECTIVE_INVESTMENT",
    "ETF",
    "EXCHANGE_TRADED_FUND",
}

def mask_account(acct_number: str) -> str:
    if len(acct_number) <= 4:
        return "****"
    return "*" * (len(acct_number) - 4) + acct_number[-4:]


def account_label(acct_number: str, acct_type: str) -> str:
    """Return a friendly account name based on last-4 digits, or fall back to type-last4."""
    last4 = acct_number[-4:] if len(acct_number) >= 4 else acct_number
    return ACCOUNT_LABELS.get(last4, f"{acct_type}-{last4}")


def to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clean_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def display_company_name(ticker: str, description: str) -> str:
    ticker = clean_symbol(ticker)
    if ticker in SYMBOL_NAME_OVERRIDES:
        return SYMBOL_NAME_OVERRIDES[ticker]
    description = (description or "").strip()
    return description or ticker


def load_company_name_cache() -> dict[str, str]:
    try:
        if os.path.exists(COMPANY_NAME_CACHE):
            with open(COMPANY_NAME_CACHE, "r") as f:
                data = json.load(f)
            return {clean_symbol(k): str(v) for k, v in data.items() if v}
    except Exception as e:
        logger.debug("Company-name cache read failed: %s", e)
    return {}


def save_company_name_cache(cache: dict[str, str]) -> None:
    try:
        with open(COMPANY_NAME_CACHE, "w") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
    except Exception as e:
        logger.debug("Company-name cache write failed: %s", e)


def fetch_company_names(tickers: list[str]) -> dict[str, str]:
    """Resolve missing company names through static overrides and local cache only."""
    resolved: dict[str, str] = {}
    cache = load_company_name_cache()

    for ticker in tickers:
        ticker = clean_symbol(ticker)
        if ticker in SYMBOL_NAME_OVERRIDES:
            resolved[ticker] = SYMBOL_NAME_OVERRIDES[ticker]
        elif ticker in cache:
            resolved[ticker] = cache[ticker]

    return resolved


def position_purchase_price(pos: dict) -> float | None:
    """Return Schwab's best available stock average/purchase price."""
    for key in ("averagePrice", "averageLongPrice", "averageCost", "costPerShare"):
        value = pos.get(key)
        if value in (None, ""):
            continue
        price = to_float(value)
        if price > 0:
            return price
    return None


def option_is_call(instrument: dict) -> bool:
    put_call = str(instrument.get("putCall", "")).upper()
    if put_call:
        return put_call == "CALL"

    symbol = clean_symbol(str(instrument.get("symbol", ""))).replace(" ", "")
    description = clean_symbol(str(instrument.get("description", "")))
    return bool(re.search(r"\d{6}C\d+", symbol) or re.search(r"\sCALL\b", description))


def option_underlying(instrument: dict) -> str:
    """Extract the underlying ticker even when Schwab omits underlyingSymbol."""
    for key in ("underlyingSymbol", "underlying", "rootSymbol"):
        value = clean_symbol(str(instrument.get(key, "")))
        if value:
            return value

    symbol = clean_symbol(str(instrument.get("symbol", "")))
    match = re.match(r"^([A-Z]{1,6})\s+\d{6}[CP]\d+", symbol)
    if match:
        return match.group(1)

    compact = symbol.replace(" ", "")
    match = re.match(r"^([A-Z]{1,6})\d{6}[CP]\d+", compact)
    if match:
        return match.group(1)

    description = clean_symbol(str(instrument.get("description", "")))
    match = re.match(r"^([A-Z]{1,6})\s+\d{2}/\d{2}/\d{4}\s+\d+(\.\d+)?\s+CALL", description)
    if match:
        return match.group(1)

    return ""


def parse_positions(accounts_data: list, show_full_account: bool = False) -> list:
    """
    Walk accounts → positions, compute covered-call metrics.
    Returns a list of row dicts.
    """
    rows = []

    for acct in accounts_data:
        acct_info   = acct.get("securitiesAccount", {})
        acct_type   = acct_info.get("type", "")
        acct_number = acct_info.get("accountNumber", "UNKNOWN")
        acct_name   = account_label(acct_number, acct_type) if acct_number != "UNKNOWN" else "UNKNOWN"
        display_num = acct_number if show_full_account else mask_account(acct_number)

        positions   = acct_info.get("positions", [])

        # Collect stock positions  {ticker: shares}
        stock_shares: dict[str, float] = {}
        stock_cost_basis: dict[str, float] = {}
        # Collect existing short call contracts  {ticker: count}
        short_calls:  dict[str, int]   = {}

        company_names: dict[str, str] = {}

        for pos in positions:
            instrument = pos.get("instrument", {})
            asset_type = clean_symbol(instrument.get("assetType", ""))
            long_qty   = to_float(pos.get("longQuantity", 0))
            short_qty_position = to_float(pos.get("shortQuantity", 0))
            qty        = long_qty - short_qty_position

            if asset_type in LONG_UNDERLYING_ASSET_TYPES:
                ticker = clean_symbol(instrument.get("symbol", ""))
                if ticker:
                    stock_shares[ticker] = stock_shares.get(ticker, 0) + qty
                    purchase_price = position_purchase_price(pos)
                    if purchase_price is not None and qty > 0:
                        stock_cost_basis[ticker] = stock_cost_basis.get(ticker, 0.0) + (purchase_price * qty)
                    # Store company name from equity instrument only
                    if ticker not in company_names:
                        company_names[ticker] = display_company_name(ticker, instrument.get("description", ""))

            elif asset_type == "OPTION":
                underlying = option_underlying(instrument)
                short_qty  = to_float(pos.get("shortQuantity", 0))

                if option_is_call(instrument) and short_qty > 0 and underlying:
                    short_calls[underlying] = short_calls.get(underlying, 0) + int(short_qty)

        missing_company_names = [
            ticker for ticker in stock_shares
            if not company_names.get(ticker) or company_names.get(ticker) == ticker
        ]
        if missing_company_names:
            company_names.update(fetch_company_names(missing_company_names))

        # Build one row per stock ticker
        for ticker, shares in stock_shares.items():
            # Only long (positive) stock holdings support covered calls
            if shares <= 0:
                continue

            abs_shares   = abs(shares)
            cap_exact    = abs_shares / 100
            cap_whole    = math.floor(abs_shares / 100)
            cc_present   = short_calls.get(ticker, 0)
            to_be_sold   = max(cap_whole - cc_present, 0)
            purchase_price = None
            if shares > 0 and ticker in stock_cost_basis:
                purchase_price = stock_cost_basis[ticker] / shares

            notes = []
            if abs_shares < 100:
                notes.append("< 100 shares")
            if cc_present > cap_whole:
                notes.append("⚠ More calls than capacity")

            rows.append({
                "account_name":    acct_name,
                "account_number":  display_num,
                "ticker":          ticker,
                "company_name":    display_company_name(ticker, company_names.get(ticker, "")),
                "purchase_price":  purchase_price,
                "shares_owned":    shares,
                "abs_shares":      abs_shares,
                "cap_exact":       round(cap_exact, 4),
                "cap_whole":       cap_whole,
                "cc_present":      cc_present,
                "to_be_sold":      to_be_sold,
                "notes":           "; ".join(notes),
            })

    # Sort: account_name → ticker
    rows.sort(key=lambda r: (r["account_name"], r["ticker"]))
    return rows


# ── Flask routes ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    tokens = load_tokens()
    authenticated = tokens is not None
    return render_template("index.html", authenticated=authenticated)


@app.route("/login")
def login():
    if not CLIENT_ID:
        return "ERROR: SCHWAB_CLIENT_ID not set in .env", 500
    logger.info("LOGIN: CLIENT_ID=%s REDIRECT_URI=%s", CLIENT_ID[:8]+"...", REDIRECT_URI)
    params = {
        "response_type": "code",
        "client_id":     CLIENT_ID,
        "redirect_uri":  REDIRECT_URI,
    }
    auth_url = f"{SCHWAB_AUTH_URL}?{urlencode(params)}"
    return redirect(auth_url)


@app.route("/callback")
def callback():
    error = request.args.get("error")
    if error:
        return render_template("error.html", message=f"OAuth error: {error}")

    code = request.args.get("code")
    if not code:
        return render_template("error.html", message="No authorization code received.")

    try:
        b64 = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        resp = requests.post(
            SCHWAB_TOKEN_URL,
            headers={
                "Authorization": f"Basic {b64}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type":   "authorization_code",
                "code":         code,
                "redirect_uri": REDIRECT_URI,
            },
            timeout=15,
        )
        resp.raise_for_status()
        save_tokens(resp.json())
        return redirect(url_for("dashboard"))
    except Exception as e:
        logger.error("Token exchange failed: %s", e)
        return render_template("error.html", message=f"Token exchange failed: {e}")


@app.route("/logout")
def logout():
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
    return redirect(url_for("index"))


@app.route("/dashboard")
def dashboard():
    token = get_valid_token()
    if not token:
        return redirect(url_for("login"))

    show_full = request.args.get("show_full", "false").lower() == "true"
    filter_mode = request.args.get("filter", "sell")   # all | gte100 | sell

    errors = []
    accounts_data = []

    # 1. Fetch account numbers
    try:
        acct_nums_resp = schwab_get("/accounts/accountNumbers", token)
        hashes = [a.get("hashValue") for a in acct_nums_resp if a.get("hashValue")]
    except Exception as e:
        return render_template("error.html", message=f"Failed to retrieve accounts: {e}")

    # 2. Fetch positions for each account
    for h in hashes:
        try:
            data = schwab_get(f"/accounts/{h}", token, params={"fields": "positions"})
            accounts_data.append(data)
        except Exception as e:
            errors.append(f"Account {h[:8]}…: {e}")

    # 3. Parse positions
    rows = parse_positions(accounts_data, show_full_account=show_full)

    # 4. Apply filter
    if filter_mode == "gte100":
        display_rows = [r for r in rows if r["abs_shares"] >= 100]
    elif filter_mode == "sell":
        display_rows = [r for r in rows if r["to_be_sold"] > 0]
    else:
        display_rows = rows

    # 5. Summary stats
    summary = {
        "accounts":       len(accounts_data),
        "total_positions": len(rows),
        "gte100":         sum(1 for r in rows if r["abs_shares"] >= 100),
        "total_cc":       sum(r["cc_present"] for r in rows),
        "total_to_sell":  sum(r["to_be_sold"] for r in rows),
        "timestamp":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    return render_template(
        "dashboard.html",
        rows=display_rows,
        summary=summary,
        errors=errors,
        show_full=show_full,
        filter_mode=filter_mode,
    )


@app.route("/export/csv")
def export_csv():
    token = get_valid_token()
    if not token:
        return redirect(url_for("login"))

    try:
        acct_nums_resp = schwab_get("/accounts/accountNumbers", token)
        hashes = [a.get("hashValue") for a in acct_nums_resp if a.get("hashValue")]
        accounts_data = []
        for h in hashes:
            try:
                data = schwab_get(f"/accounts/{h}", token, params={"fields": "positions"})
                accounts_data.append(data)
            except Exception:
                pass
        rows = parse_positions(accounts_data, show_full_account=False)
    except Exception as e:
        return f"Export failed: {e}", 500

    output = io.StringIO()
    fieldnames = [
        "account_name", "account_number", "ticker", "company_name",
        "purchase_price", "shares_owned", "abs_shares", "cap_exact", "cap_whole",
        "cc_present", "to_be_sold", "notes",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=covered_calls_{ts}.csv"},
    )


@app.route("/api/data")
def api_data():
    """JSON endpoint for in-page refresh."""
    token = get_valid_token()
    if not token:
        return jsonify({"error": "not_authenticated"}), 401
    try:
        acct_nums_resp = schwab_get("/accounts/accountNumbers", token)
        hashes = [a.get("hashValue") for a in acct_nums_resp if a.get("hashValue")]
        accounts_data = []
        for h in hashes:
            try:
                data = schwab_get(f"/accounts/{h}", token, params={"fields": "positions"})
                accounts_data.append(data)
            except Exception:
                pass
        rows = parse_positions(accounts_data)
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def generate_self_signed_cert(cert_file, key_file):
    """Generate a self-signed certificate using the cryptography library — no openssl binary needed."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    import ipaddress, datetime

    print("Generating self-signed SSL certificate (pure Python)...")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256(), default_backend())
    )
    with open(key_file, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    with open(cert_file, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    print("Certificate generated successfully.")






# ── Options positions logic ────────────────────────────────────────────────

def parse_option_positions(accounts_data: list, show_full_account: bool = False) -> list:
    """
    Extract all active option positions (puts and calls) across all accounts.
    Returns a list of row dicts sorted by account_name, then underlying symbol.
    """
    rows = []

    for acct in accounts_data:
        acct_info   = acct.get("securitiesAccount", {})
        acct_type   = acct_info.get("type", "")
        acct_number = acct_info.get("accountNumber", "UNKNOWN")
        acct_name   = account_label(acct_number, acct_type) if acct_number != "UNKNOWN" else "UNKNOWN"
        display_num = acct_number if show_full_account else mask_account(acct_number)

        positions = acct_info.get("positions", [])

        for pos in positions:
            instrument = pos.get("instrument", {})
            asset_type = instrument.get("assetType", "")

            if asset_type != "OPTION":
                continue

            put_call    = instrument.get("putCall", "UNKNOWN")
            underlying  = instrument.get("underlyingSymbol", "")
            description = instrument.get("description", "")
            symbol      = instrument.get("symbol", "")

            # Parse expiration and strike from description or symbol
            # Description format: "NVDA 05/16/2026 $135.00 Call"
            expiration  = ""
            strike      = ""
            try:
                parts = description.split()
                for p in parts:
                    if "/" in p and len(p) >= 8:  # date like 05/16/2026
                        expiration = p
                    elif p.startswith("$"):
                        strike = p.replace("$", "")
            except Exception:
                pass

            long_qty  = pos.get("longQuantity", 0)
            short_qty = pos.get("shortQuantity", 0)
            quantity  = int(long_qty - short_qty)
            side      = "LONG" if quantity > 0 else "SHORT"

            # Prices — Schwab returns these per position
            avg_price    = pos.get("averagePrice", 0) or 0
            market_value = pos.get("marketValue", 0) or 0
            day_pl       = pos.get("currentDayProfitLoss", 0) or 0

            # Mark price: derive from marketValue / (abs_qty * 100)
            abs_qty = abs(quantity)
            if abs_qty > 0:
                mark_price = abs(market_value) / (abs_qty * 100)
            else:
                mark_price = 0

            # P&L since opening
            # For long: P&L = marketValue - (avg_price * abs_qty * 100)
            # For short: P&L = (avg_price * abs_qty * 100) - abs(marketValue)
            # Schwab often provides this directly in various fields
            cost_basis = avg_price * abs_qty * 100
            if quantity > 0:  # long
                pnl = market_value - cost_basis
            else:  # short
                pnl = cost_basis - abs(market_value)

            # Additional fields from Schwab if available
            maint_req = pos.get("maintenanceRequirement", 0) or 0

            notes = []
            if abs_qty == 0:
                continue  # skip zero-quantity (fully closed)

            rows.append({
                "account_name":   acct_name,
                "account_number": display_num,
                "type":           put_call,
                "side":           side,
                "underlying":     underlying,
                "description":    description,
                "symbol":         symbol,
                "expiration":     expiration,
                "strike":         strike,
                "quantity":       quantity,
                "avg_price":      round(avg_price, 4),
                "mark_price":     round(mark_price, 4),
                "market_value":   round(market_value, 2),
                "pnl":            round(pnl, 2),
                "day_pl":         round(day_pl, 2),
            })

    # Sort by account_name, then underlying, then expiration
    rows.sort(key=lambda r: (r["account_name"], r["underlying"], r["expiration"], r["type"]))
    return rows


@app.route("/options")
def options_page():
    token = get_valid_token()
    if not token:
        return redirect(url_for("login"))

    show_full = request.args.get("show_full", "false").lower() == "true"
    filter_type = request.args.get("type", "all")  # all | call | put

    errors = []
    accounts_data = []

    try:
        acct_nums_resp = schwab_get("/accounts/accountNumbers", token)
        hashes = [a.get("hashValue") for a in acct_nums_resp if a.get("hashValue")]
    except Exception as e:
        return render_template("error.html", message=f"Failed to retrieve accounts: {e}")

    for h in hashes:
        try:
            data = schwab_get(f"/accounts/{h}", token, params={"fields": "positions"})
            accounts_data.append(data)
        except Exception as e:
            errors.append(f"Account {h[:8]}…: {e}")

    rows = parse_option_positions(accounts_data, show_full_account=show_full)

    # Fetch live underlying stock prices for ALL unique tickers in options
    underlying_tickers = list(set(r["underlying"] for r in rows if r["underlying"]))
    logger.info("OPTIONS: Fetching live quotes for %d underlying tickers: %s", len(underlying_tickers), underlying_tickers)
    live_prices = fetch_underlying_quotes(underlying_tickers, token)
    logger.info("OPTIONS: Got %d prices back: %s", len(live_prices), live_prices)
    for r in rows:
        r["current_price"] = live_prices.get(r["underlying"], 0)

    # Apply type filter
    if filter_type == "call":
        display_rows = [r for r in rows if r["type"] == "CALL"]
    elif filter_type == "put":
        display_rows = [r for r in rows if r["type"] == "PUT"]
    else:
        display_rows = rows

    # Summary
    total_calls = sum(1 for r in rows if r["type"] == "CALL")
    total_puts  = sum(1 for r in rows if r["type"] == "PUT")
    total_pnl   = sum(r["pnl"] for r in rows)
    total_mv    = sum(r["market_value"] for r in rows)

    summary = {
        "accounts":     len(accounts_data),
        "total":        len(rows),
        "calls":        total_calls,
        "puts":         total_puts,
        "total_pnl":    total_pnl,
        "total_mv":     total_mv,
        "timestamp":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    return render_template(
        "options.html",
        rows=display_rows,
        summary=summary,
        errors=errors,
        show_full=show_full,
        filter_type=filter_type,
    )


@app.route("/options/export/csv")
def options_export_csv():
    token = get_valid_token()
    if not token:
        return redirect(url_for("login"))
    try:
        acct_nums_resp = schwab_get("/accounts/accountNumbers", token)
        hashes = [a.get("hashValue") for a in acct_nums_resp if a.get("hashValue")]
        accounts_data = []
        for h in hashes:
            try:
                data = schwab_get(f"/accounts/{h}", token, params={"fields": "positions"})
                accounts_data.append(data)
            except Exception:
                pass
        rows = parse_option_positions(accounts_data)
        underlying_tickers = list(set(r["underlying"] for r in rows if r["underlying"]))
        live_prices = fetch_underlying_quotes(underlying_tickers, token)
        for r in rows:
            r["current_price"] = live_prices.get(r["underlying"], 0)
    except Exception as e:
        return f"Export failed: {e}", 500

    output = io.StringIO()
    fieldnames = [
        "account_name", "account_number", "type", "side", "underlying",
        "description", "expiration", "strike", "current_price", "quantity",
        "avg_price", "mark_price", "market_value", "pnl", "day_pl",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        output.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=options_{ts}.csv"},
    )




# ── Cash-Secured Put screener routes ───────────────────────────────────────

_csp_state = {
    "running":   False,
    "started":   None,
    "completed": None,
    "progress":  0,
    "total":     0,
    "current":   "",
    "results":   None,
    "error":     None,
}
_csp_lock = threading.Lock()


def _run_csp_background():
    """Background thread for CSP screening."""
    try:
        import universe as univ
        from cspscreener.data import build_universe as csp_build_universe, YFinanceProvider
        from cspscreener.data.yf_provider import fetch_vix_level
        from cspscreener.screener import screen_ticker
        from cspscreener import config as csp_config

        # Fetch VIX level and determine regime
        vix_level = fetch_vix_level()
        vix_regime = "normal"
        vix_params = None
        if vix_level is not None:
            for regime_name, params in csp_config.VIX_REGIMES.items():
                if vix_level <= params["vix_max"]:
                    vix_regime = regime_name
                    vix_params = params
                    break

        with _csp_lock:
            _csp_state["vix_level"] = vix_level
            _csp_state["vix_regime"] = vix_regime

        # Use our universe
        tickers, _ = univ.load_universe()
        with _csp_lock:
            _csp_state["total"] = len(tickers)

        provider = YFinanceProvider()
        candidates = []
        rejections = {}

        for i, ticker in enumerate(tickers, 1):
            with _csp_lock:
                _csp_state["progress"] = i
                _csp_state["current"]  = ticker

            tc, reason = screen_ticker(ticker, provider, vix_params=vix_params)
            if tc is not None:
                candidates.append(tc)
            elif reason:
                tag = reason.split("[")[0]
                rejections[tag] = rejections.get(tag, 0) + 1

            import time
            time.sleep(0.02)

        # Rank
        candidates.sort(key=lambda c: c.composite_score, reverse=True)

        # Convert to flat dicts for template
        rows = []
        for rank, tc in enumerate(candidates, 1):
            d = tc.to_flat_dict()
            d["rank"] = rank
            rows.append(d)

        with _csp_lock:
            _csp_state["results"]   = rows
            _csp_state["completed"] = datetime.now()
            _csp_state["rejections"] = rejections

    except Exception as e:
        logger.exception("CSP scan failed: %s", e)
        with _csp_lock:
            _csp_state["error"] = str(e)
    finally:
        with _csp_lock:
            _csp_state["running"] = False


@app.route("/csp")
def csp_page():
    """Cash-Secured Put screener page."""
    with _csp_lock:
        running   = _csp_state["running"]
        completed = _csp_state["completed"]
        rows      = _csp_state.get("results")
        error     = _csp_state.get("error")

    filter_action = request.args.get("action", "all")
    top_n = int(request.args.get("top", "50"))

    display_rows = rows or []
    if filter_action == "strong":
        display_rows = [r for r in display_rows if r.get("action") == "Strong"]
    elif filter_action == "accept":
        display_rows = [r for r in display_rows if r.get("action") in ("Strong", "Accept")]
    elif filter_action == "watch":
        display_rows = [r for r in display_rows if r.get("action") in ("Strong", "Accept", "Watch")]

    display_rows = display_rows[:top_n]

    summary = {}
    vix_level = _csp_state.get("vix_level")
    vix_regime = _csp_state.get("vix_regime")
    if rows:
        summary = {
            "total_scanned": _csp_state.get("total", 0),
            "qualified":     len(rows),
            "strong":        sum(1 for r in rows if r.get("action") == "Strong"),
            "accept":        sum(1 for r in rows if r.get("action") == "Accept"),
            "watch":         sum(1 for r in rows if r.get("action") == "Watch"),
            "completed":     completed.strftime("%Y-%m-%d %H:%M:%S") if completed else None,
        }

    return render_template(
        "csp.html",
        running=running, rows=display_rows, summary=summary, error=error,
        filter_action=filter_action, top_n=top_n,
        vix_level=vix_level, vix_regime=vix_regime,
    )


@app.route("/csp/start", methods=["POST"])
def csp_start():
    with _csp_lock:
        if _csp_state["running"]:
            return jsonify({"status": "already_running"})
        _csp_state.update({
            "running": True, "started": datetime.now(),
            "completed": None, "progress": 0, "total": 0,
            "current": "", "results": None, "error": None,
        })
    threading.Thread(target=_run_csp_background, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/csp/status")
def csp_status():
    with _csp_lock:
        return jsonify({
            "running":   _csp_state["running"],
            "progress":  _csp_state["progress"],
            "total":     _csp_state["total"],
            "current":   _csp_state["current"],
            "completed": _csp_state["completed"].isoformat() if _csp_state["completed"] else None,
            "error":     _csp_state["error"],
        })


@app.route("/csp/export/csv")
def csp_export_csv():
    with _csp_lock:
        rows = _csp_state.get("results")
    if not rows:
        return "No results to export", 404
    import pandas as pd
    df = pd.DataFrame(rows)
    csv_data = df.to_csv(index=False)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        csv_data, mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=csp_candidates_{ts}.csv"},
    )


# ── CSP Compare route ────────────────────────────────────────────────────

@app.route("/csp/compare")
def csp_compare():
    """Side-by-side comparison of CSP candidates."""
    tickers_param = request.args.get("tickers", "")
    tickers = [t.strip().upper() for t in tickers_param.split(",") if t.strip()]

    with _csp_lock:
        all_rows = _csp_state.get("results") or []

    matching = [r for r in all_rows if r.get("ticker", "").upper() in tickers]
    # Preserve requested order
    ticker_order = {t: i for i, t in enumerate(tickers)}
    matching.sort(key=lambda r: ticker_order.get(r.get("ticker", "").upper(), 999))

    return render_template("csp_compare.html", rows=matching)


# ── Trade Journal routes ──────────────────────────────────────────────────

@app.route("/journal")
def journal_page():
    """Display trade journal."""
    import journal
    trades = journal.get_all_trades()
    stats = journal.get_stats()
    return render_template("journal.html", trades=trades, stats=stats)


@app.route("/journal/add", methods=["POST"])
def journal_add():
    """Add a new trade to the journal."""
    import journal
    journal.add_trade({
        "ticker": request.form.get("ticker", ""),
        "strike": request.form.get("strike", 0),
        "expiration": request.form.get("expiration", ""),
        "premium": request.form.get("premium", 0),
        "contracts": request.form.get("contracts", 1),
        "notes": request.form.get("notes", ""),
    })
    return redirect(url_for("journal_page"))


@app.route("/journal/close/<trade_id>", methods=["POST"])
def journal_close(trade_id):
    """Close an open trade."""
    import journal
    journal.close_trade(trade_id, {
        "close_premium": request.form.get("close_premium", 0),
        "close_reason": request.form.get("close_reason", "expired"),
    })
    return redirect(url_for("journal_page"))


# ── Portfolio Risk Dashboard ───────────────────────────────────────────────

@app.route("/risk")
def risk_page():
    """Portfolio risk analysis for CSP candidates."""
    from cspscreener.risk import analyze_portfolio_risk, kelly_allocation

    capital = request.args.get("capital", "100000")
    try:
        total_capital = float(capital.replace(",", ""))
    except (ValueError, TypeError):
        total_capital = 100000.0

    with _csp_lock:
        rows = _csp_state.get("results") or []
        vix_level = _csp_state.get("vix_level")
        vix_regime = _csp_state.get("vix_regime")

    risk = analyze_portfolio_risk(rows, total_capital)
    kelly = kelly_allocation(rows, total_capital) if rows else []

    return render_template(
        "risk.html",
        risk=risk, kelly=kelly,
        capital=int(total_capital),
        vix_level=vix_level, vix_regime=vix_regime,
    )


# ── Momentum screener routes ───────────────────────────────────────────────

_scan_state = {
    "running":   False,
    "started":   None,
    "completed": None,
    "progress":  0,
    "total":     0,
    "current":   "",
    "results":   None,
    "error":     None,
}
_scan_lock = threading.Lock()


def _run_scan_background():
    """Background thread target for the momentum scan."""
    try:
        symbols = mom.load_sp500_tickers()
        with _scan_lock:
            _scan_state["total"] = len(symbols)

        def cb(i, total, ticker):
            with _scan_lock:
                _scan_state["progress"] = i
                _scan_state["current"]  = ticker

        df = mom.run_screen(symbols, days=130, progress_cb=cb)
        # Save to Excel history workbook
        try:
            import excel_export
            xl_path = excel_export.append_scan(df)
            logger.info("Excel export saved: %s", xl_path)
        except Exception as xe:
            logger.warning("Excel export failed: %s", xe)

        with _scan_lock:
            _scan_state["results"]   = df
            _scan_state["completed"] = datetime.now()
    except Exception as e:
        logger.exception("Scan failed: %s", e)
        with _scan_lock:
            _scan_state["error"] = str(e)
    finally:
        with _scan_lock:
            _scan_state["running"] = False


@app.route("/momentum")
def momentum_page():
    """Main momentum screener page."""
    with _scan_lock:
        running   = _scan_state["running"]
        completed = _scan_state["completed"]
        df        = _scan_state["results"]
        error     = _scan_state["error"]

    rows         = []
    summary_data = {}
    if df is not None and not df.empty:
        rows = df.head(100).to_dict("records")  # Show top 100
        # Build summary buckets
        df_clear = df[df["classification"].isin(["Strong", "Moderate"])]
        summary_data = {
            "top10":         df.head(10).to_dict("records"),
            "top_quality":   df.sort_values("sharpe_63", ascending=False).head(5).to_dict("records"),
            "top_vs_spy":    df.sort_values("vs_spy_63", ascending=False).head(5).to_dict("records"),
            "top_trend":     df.sort_values("reg_r2", ascending=False).head(5).to_dict("records"),
            "overextended":  df[df["overextended"]].head(10).to_dict("records"),
            "gap_driven":    df[df["single_day_pct"] > 0.5].head(10).to_dict("records"),
            "completed":     completed.strftime("%Y-%m-%d %H:%M:%S") if completed else None,
            "total_scanned": len(df),
        }

    return render_template(
        "momentum.html",
        running=running, rows=rows, summary=summary_data, error=error,
    )



@app.route("/universe")
def universe_status():
    """Show universe status and allow manual refresh."""
    import universe as univ
    tickers, meta = univ.load_universe()
    return render_template("universe.html", tickers=tickers, meta=meta)


@app.route("/universe/refresh", methods=["POST"])
def universe_refresh():
    """Force a universe refresh from Wikipedia."""
    import universe as univ
    tickers = univ.refresh_universe()
    meta = {"count": len(tickers), "source": "just refreshed", "updated": datetime.now().isoformat(),
            "next_refresh": f"in ~{univ.REFRESH_DAYS} days", "errors": []}
    return render_template("universe.html", tickers=tickers, meta=meta, just_refreshed=True)

@app.route("/momentum/start", methods=["POST"])
def momentum_start():
    with _scan_lock:
        if _scan_state["running"]:
            return jsonify({"status": "already_running"})
        _scan_state.update({
            "running": True, "started": datetime.now(),
            "completed": None, "progress": 0, "total": 0,
            "current": "", "results": None, "error": None,
        })
    threading.Thread(target=_run_scan_background, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/momentum/status")
def momentum_status():
    with _scan_lock:
        return jsonify({
            "running":   _scan_state["running"],
            "progress":  _scan_state["progress"],
            "total":     _scan_state["total"],
            "current":   _scan_state["current"],
            "completed": _scan_state["completed"].isoformat() if _scan_state["completed"] else None,
            "error":     _scan_state["error"],
        })


@app.route("/momentum/export/csv")
def momentum_export_csv():
    with _scan_lock:
        df = _scan_state["results"]
    if df is None or df.empty:
        return "No results to export", 404
    csv_data = df.to_csv(index=False)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        csv_data, mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=momentum_{ts}.csv"},
    )


if __name__ == "__main__":
    import ssl, sys

    CERT_FILE = "cert.pem"
    KEY_FILE  = "key.pem"

    if not (os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE)):
        try:
            generate_self_signed_cert(CERT_FILE, KEY_FILE)
        except ImportError:
            print("\nERROR: 'cryptography' package not found. Run:  pip install cryptography")
            sys.exit(1)
        except Exception as e:
            print(f"ERROR generating certificate: {e}")
            sys.exit(1)

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(CERT_FILE, KEY_FILE)

    print("\n" + "="*60)
    print("  Schwab Covered Call Dashboard")
    print("  Open https://127.0.0.1 in your browser")
    print("  (Accept the self-signed certificate warning)")
    print("="*60 + "\n")

    port = int(os.environ.get("PORT", "443"))
    try:
        app.run(debug=False, host="127.0.0.1", port=port, ssl_context=context)
    except PermissionError:
        print(f"\nERROR: Cannot bind to port {port} — permission denied.")
        print("On Windows: right-click run.bat → 'Run as Administrator'")
        print("On Mac/Linux: sudo python app.py")
        sys.exit(1)
