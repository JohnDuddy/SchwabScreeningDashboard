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
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
from urllib.parse import urlencode, urlparse, parse_qs

import base64
import requests
from flask import (
    Flask, render_template, redirect, request,
    session, jsonify, Response, url_for
)
from dotenv import load_dotenv

import time
import threading
import pandas as pd
import momentum as mom
import momentum_v2 as momv2

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

_token_cache: dict = {"access_token": None, "expires_at": 0.0}
_token_lock = threading.Lock()


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
    """Return a valid access token, refreshing only when the cached one is near expiry."""
    with _token_lock:
        if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 60:
            return _token_cache["access_token"]

    tokens = load_tokens()
    if not tokens:
        return None

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
        with _token_lock:
            _token_cache["access_token"] = new_tokens["access_token"]
            _token_cache["expires_at"] = time.time() + new_tokens.get("expires_in", 1800)
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


def _fetch_all_accounts(token: str) -> tuple[list, list]:
    """Fetch all account positions in parallel. Returns (accounts_data, errors)."""
    acct_nums_resp = schwab_get("/accounts/accountNumbers", token)
    hashes = [a.get("hashValue") for a in acct_nums_resp if a.get("hashValue")]

    accounts_data = []
    errors = []

    def _fetch_one(h):
        return schwab_get(f"/accounts/{h}", token, params={"fields": "positions"})

    with ThreadPoolExecutor(max_workers=min(len(hashes), 4)) as pool:
        futures = {pool.submit(_fetch_one, h): h for h in hashes}
        for future in as_completed(futures):
            h = futures[future]
            try:
                accounts_data.append(future.result())
            except Exception as e:
                errors.append(f"Account {h[:8]}…: {e}")

    return accounts_data, errors




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
            raw = yf.download(remaining, period="5d", progress=False, auto_adjust=False)
            if not raw.empty:
                if isinstance(raw.columns, pd.MultiIndex):
                    close_df = raw["Close"] if "Close" in raw.columns.get_level_values(0) else pd.DataFrame()
                    for sym in remaining:
                        col = sym.upper()
                        if col in close_df.columns:
                            series = close_df[col].dropna()
                            if not series.empty:
                                last = float(series.iloc[-1])
                                if last > 0:
                                    prices[col] = round(last, 2)
                else:
                    # single-ticker download returns flat columns
                    close_series = raw["Close"].dropna() if "Close" in raw.columns else pd.Series(dtype=float)
                    if not close_series.empty:
                        last = float(close_series.iloc[-1])
                        if last > 0:
                            prices[remaining[0].upper()] = round(last, 2)
        except ImportError:
            logger.warning("yfinance not installed — cannot fetch fallback quotes")
        except Exception as e:
            logger.warning("yfinance batch fallback failed: %s", e)

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
                "current_price":   None,
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
    show_full = request.args.get("show_full", "false").lower() == "true"
    filter_mode = request.args.get("filter", "sell")   # all | gte100 | sell

    with _dash_lock:
        running   = _dash_state["running"]
        completed = _dash_state["completed"]
        rows      = _dash_state.get("results")
        summary   = _dash_state.get("summary")
        errors    = _dash_state.get("errors") or []

    has_token = get_valid_token() is not None

    if rows is None:
        # No data yet
        return render_template(
            "dashboard.html",
            rows=[], summary={}, errors=errors,
            show_full=show_full, filter_mode=filter_mode,
            running=running, has_token=has_token, completed=completed,
        )

    # Apply filter
    if filter_mode == "gte100":
        display_rows = [r for r in rows if r["abs_shares"] >= 100]
    elif filter_mode == "sell":
        display_rows = [r for r in rows if r["to_be_sold"] > 0]
    else:
        display_rows = rows

    return render_template(
        "dashboard.html",
        rows=display_rows,
        summary=summary or {},
        errors=errors,
        show_full=show_full,
        filter_mode=filter_mode,
        running=running, has_token=has_token, completed=completed,
    )


@app.route("/export/csv")
def export_csv():
    with _dash_lock:
        rows = _dash_state.get("results")

    if rows is None:
        token = get_valid_token()
        if not token:
            return redirect(url_for("login"))
        try:
            accounts_data, _ = _fetch_all_accounts(token)
            rows = parse_positions(accounts_data, show_full_account=False)
            tickers = list({r["ticker"] for r in rows if r["ticker"]})
            live_prices = fetch_underlying_quotes(tickers, token)
            for r in rows:
                r["current_price"] = live_prices.get(r["ticker"])
        except Exception as e:
            return f"Export failed: {e}", 500

    output = io.StringIO()
    fieldnames = [
        "account_name", "account_number", "ticker", "company_name",
        "purchase_price", "current_price", "shares_owned", "abs_shares", "cap_exact", "cap_whole",
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
    with _dash_lock:
        rows = _dash_state.get("results")

    if rows is not None:
        return jsonify(rows)

    token = get_valid_token()
    if not token:
        return jsonify({"error": "not_authenticated"}), 401
    try:
        accounts_data, _ = _fetch_all_accounts(token)
        return jsonify(parse_positions(accounts_data))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/dashboard/start", methods=["POST"])
def dashboard_start():
    with _dash_lock:
        if _dash_state["running"]:
            return jsonify({"status": "already_running"})
        _dash_state["running"] = True
    threading.Thread(target=_run_dashboard_background, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/dashboard/status")
def dashboard_status():
    with _dash_lock:
        return jsonify({
            "running":   _dash_state["running"],
            "completed": _dash_state["completed"].isoformat() if _dash_state["completed"] else None,
        })


@app.route("/options/start", methods=["POST"])
def options_start():
    with _opts_lock:
        if _opts_state["running"]:
            return jsonify({"status": "already_running"})
        _opts_state["running"] = True
    threading.Thread(target=_run_options_background, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/options/status")
def options_status():
    with _opts_lock:
        return jsonify({
            "running":   _opts_state["running"],
            "completed": _opts_state["completed"].isoformat() if _opts_state["completed"] else None,
        })


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






# Pre-compiled pattern for option description: "NVDA 05/16/2026 $135.00 Call"
_OPT_DESC_RE = re.compile(r'(\d{2}/\d{2}/\d{4}).*?\$(\d+\.?\d*)')

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

            # Parse expiration and strike from description
            # Description format: "NVDA 05/16/2026 $135.00 Call"
            expiration = strike = ""
            m = _OPT_DESC_RE.search(description)
            if m:
                expiration, strike = m.group(1), m.group(2)

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
    show_full = request.args.get("show_full", "false").lower() == "true"
    filter_type = request.args.get("type", "all")  # all | call | put

    with _opts_lock:
        running   = _opts_state["running"]
        completed = _opts_state["completed"]
        rows      = _opts_state.get("results")
        summary   = _opts_state.get("summary")
        errors    = _opts_state.get("errors") or []

    has_token = get_valid_token() is not None

    if rows is None:
        return render_template(
            "options.html",
            rows=[], summary={}, errors=errors,
            show_full=show_full, filter_type=filter_type,
            running=running, has_token=has_token, completed=completed,
        )

    # Apply type filter
    if filter_type == "call":
        display_rows = [r for r in rows if r["type"] == "CALL"]
    elif filter_type == "put":
        display_rows = [r for r in rows if r["type"] == "PUT"]
    else:
        display_rows = rows

    return render_template(
        "options.html",
        rows=display_rows,
        summary=summary or {},
        errors=errors,
        show_full=show_full,
        filter_type=filter_type,
        running=running, has_token=has_token, completed=completed,
    )


@app.route("/options/export/csv")
def options_export_csv():
    with _opts_lock:
        rows = list(_opts_state.get("results") or [])

    if not rows:
        token = get_valid_token()
        if not token:
            return redirect(url_for("login"))
        try:
            accounts_data, _ = _fetch_all_accounts(token)
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

_dash_state = {
    "running": False, "completed": None,
    "results": None, "summary": None, "errors": None,
}
_dash_lock = threading.Lock()

_opts_state = {
    "running": False, "completed": None,
    "results": None, "summary": None, "errors": None,
}
_opts_lock = threading.Lock()

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

_risk_state = {
    "results": None,   # output of analyze_portfolio_risk()
    "kelly":   None,   # output of kelly_allocation()
    "capital": 100000, # default capital
    "completed": None,
}
_risk_lock = threading.Lock()

_journal_state = {
    "trades":    None,
    "stats":     None,
    "completed": None,
}
_journal_lock = threading.Lock()


def _compute_risk(capital=None):
    """Compute risk metrics from current CSP results and cache them."""
    from cspscreener.risk import analyze_portfolio_risk, kelly_allocation

    with _csp_lock:
        rows = _csp_state.get("results") or []

    with _risk_lock:
        if capital is not None:
            _risk_state["capital"] = capital
        total_capital = _risk_state["capital"]

    risk = analyze_portfolio_risk(rows, total_capital)
    kelly = kelly_allocation(rows, total_capital) if rows else []

    with _risk_lock:
        _risk_state["results"]   = risk
        _risk_state["kelly"]     = kelly
        _risk_state["completed"] = datetime.now()

    from scan_cache import save_scan
    save_scan("risk", {"risk": risk, "kelly": kelly, "capital": total_capital})


def _refresh_journal():
    """Load journal data from disk into the in-memory state dict."""
    import journal
    trades = journal.get_all_trades()
    stats = journal.get_stats()
    with _journal_lock:
        _journal_state["trades"]    = trades
        _journal_state["stats"]     = stats
        _journal_state["completed"] = datetime.now()


def _run_dashboard_background():
    """Background thread to fetch covered-call positions."""
    try:
        token = get_valid_token()
        if not token:
            logger.info("Dashboard scan skipped — no valid Schwab token")
            return

        accounts_data, errors = _fetch_all_accounts(token)

        rows = parse_positions(accounts_data, show_full_account=False)

        # Fetch live underlying prices and attach to each row
        tickers = list({r["ticker"] for r in rows if r["ticker"]})
        live_prices = fetch_underlying_quotes(tickers, token)
        for r in rows:
            r["current_price"] = live_prices.get(r["ticker"])

        summary = {
            "accounts":       len(accounts_data),
            "total_positions": len(rows),
            "gte100":         sum(1 for r in rows if r["abs_shares"] >= 100),
            "total_cc":       sum(r["cc_present"] for r in rows),
            "total_to_sell":  sum(r["to_be_sold"] for r in rows),
            "timestamp":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        with _dash_lock:
            _dash_state["results"]   = rows
            _dash_state["summary"]   = summary
            _dash_state["errors"]    = errors
            _dash_state["completed"] = datetime.now()

        from scan_cache import save_scan
        save_scan("dashboard", {"rows": rows, "summary": summary, "errors": errors})

    except Exception as e:
        logger.exception("Dashboard scan failed: %s", e)
        with _dash_lock:
            _dash_state["errors"] = [str(e)]
    finally:
        with _dash_lock:
            _dash_state["running"] = False


def _run_options_background():
    """Background thread to fetch option positions."""
    try:
        token = get_valid_token()
        if not token:
            logger.info("Options scan skipped — no valid Schwab token")
            return

        accounts_data, errors = _fetch_all_accounts(token)

        rows = parse_option_positions(accounts_data, show_full_account=False)

        # Fetch live underlying prices
        underlying_tickers = list(set(r["underlying"] for r in rows if r["underlying"]))
        live_prices = fetch_underlying_quotes(underlying_tickers, token)
        for r in rows:
            r["current_price"] = live_prices.get(r["underlying"], 0)

        total_calls = total_puts = 0
        total_pnl = total_mv = 0.0
        for r in rows:
            if r["type"] == "CALL":
                total_calls += 1
            else:
                total_puts += 1
            total_pnl += r["pnl"]
            total_mv  += r["market_value"]

        summary = {
            "accounts":  len(accounts_data),
            "total":     len(rows),
            "calls":     total_calls,
            "puts":      total_puts,
            "total_pnl": total_pnl,
            "total_mv":  total_mv,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        with _opts_lock:
            _opts_state["results"]   = rows
            _opts_state["summary"]   = summary
            _opts_state["errors"]    = errors
            _opts_state["completed"] = datetime.now()

        from scan_cache import save_scan
        save_scan("options", {"rows": rows, "summary": summary, "errors": errors})

    except Exception as e:
        logger.exception("Options scan failed: %s", e)
        with _opts_lock:
            _opts_state["errors"] = [str(e)]
    finally:
        with _opts_lock:
            _opts_state["running"] = False


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

        # Persist to disk
        from scan_cache import save_scan
        save_scan("csp", {
            "rows": rows,
            "rejections": rejections,
            "vix_level": vix_level,
            "vix_regime": vix_regime,
        })

        # Recompute risk from fresh CSP data
        try:
            _compute_risk()
        except Exception as re:
            logger.warning("Risk recompute after CSP scan failed: %s", re)

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
        action_counts: dict[str, int] = {}
        for r in rows:
            a = r.get("action", "")
            action_counts[a] = action_counts.get(a, 0) + 1
        summary = {
            "total_scanned": _csp_state.get("total", 0),
            "qualified":     len(rows),
            "strong":        action_counts.get("Strong", 0),
            "accept":        action_counts.get("Accept", 0),
            "watch":         action_counts.get("Watch", 0),
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
    with _journal_lock:
        trades = _journal_state["trades"]
        stats = _journal_state["stats"]

    # Fallback if not yet pre-loaded
    if trades is None:
        import journal
        trades = journal.get_all_trades()
        stats = journal.get_stats()

    return render_template("journal.html", trades=trades, stats=stats or {})


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
    _refresh_journal()
    return redirect(url_for("journal_page"))


@app.route("/journal/close/<trade_id>", methods=["POST"])
def journal_close(trade_id):
    """Close an open trade."""
    import journal
    journal.close_trade(trade_id, {
        "close_premium": request.form.get("close_premium", 0),
        "close_reason": request.form.get("close_reason", "expired"),
    })
    _refresh_journal()
    return redirect(url_for("journal_page"))


# ── Portfolio Risk Dashboard ───────────────────────────────────────────────

@app.route("/risk")
def risk_page():
    """Portfolio risk analysis for CSP candidates."""
    capital = request.args.get("capital", None)

    if capital is not None:
        # User changed capital — recompute on the fly
        try:
            total_capital = float(capital.replace(",", ""))
        except (ValueError, TypeError):
            total_capital = 100000.0
        _compute_risk(capital=total_capital)
    else:
        with _risk_lock:
            total_capital = _risk_state["capital"]

    with _risk_lock:
        risk = _risk_state["results"]
        kelly = _risk_state["kelly"]

    with _csp_lock:
        vix_level = _csp_state.get("vix_level")
        vix_regime = _csp_state.get("vix_regime")

    # If no cached risk yet, compute fresh
    if risk is None:
        from cspscreener.risk import analyze_portfolio_risk, kelly_allocation
        with _csp_lock:
            rows = _csp_state.get("results") or []
        risk = analyze_portfolio_risk(rows, total_capital)
        kelly = kelly_allocation(rows, total_capital) if rows else []

    return render_template(
        "risk.html",
        risk=risk, kelly=kelly or [],
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

_momv2_state = {
    "running":   False,
    "started":   None,
    "completed": None,
    "progress":  0,
    "total":     0,
    "current":   "",
    "results":   None,
    "regime":    None,
    "error":     None,
}
_momv2_lock = threading.Lock()

_zerodte_state = {
    "running":   False,
    "started":   None,
    "completed": None,
    "progress":  0,
    "total":     0,
    "current":   "",
    "results":   None,
    "summary":   None,
    "error":     None,
    "enabled":   True,
}
_zerodte_lock = threading.Lock()

_expiring_state = {
    "running": False,
    "started": None,
    "completed": None,
    "progress": 0,
    "total": 0,
    "current": "",
    "rows_by_level": {"5": [], "10": [], "15": []},
    "all_rows": [],
    "chain_rows": [],
    "summary": {},
    "errors": [],
    "error": None,
    "filters": {},
    "sort_by": "midpoint_premium_yield_on_strike",
    "mode": "live",
    "expiration_mode": "next",
    "selected_scan_date": None,
}
_expiring_lock = threading.Lock()


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

        # Persist to disk
        from scan_cache import save_scan
        save_scan("momentum", {
            "records": df.to_dict("records"),
            "columns": list(df.columns),
        })

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


def _run_momv2_background():
    """Background thread for the Momentum Pro (v2) scan."""
    try:
        symbols = mom.load_sp500_tickers()
        with _momv2_lock:
            _momv2_state["total"] = len(symbols)

        def cb(i, total, ticker):
            with _momv2_lock:
                _momv2_state["progress"] = i
                _momv2_state["current"]  = ticker

        df, regime = momv2.run_screen_v2(symbols, progress_cb=cb)

        with _momv2_lock:
            _momv2_state["results"]   = df
            _momv2_state["regime"]    = regime
            _momv2_state["completed"] = datetime.now()

        from scan_cache import save_scan
        save_scan("momentum2", {
            "records": df.to_dict("records"),
            "columns": list(df.columns),
            "regime":  regime,
        })

    except Exception as e:
        logger.exception("Momentum Pro scan failed: %s", e)
        with _momv2_lock:
            _momv2_state["error"] = str(e)
    finally:
        with _momv2_lock:
            _momv2_state["running"] = False


def _run_zerodte_background():
    """Background thread: fetch 0DTE options chains and detect pricing anomalies."""
    try:
        from zerodte import config as zdt_cfg, schwab_client, anomaly_engine
        import time
        from datetime import date as _date

        token = get_valid_token()
        if not token:
            logger.info("0DTE scan skipped — no valid Schwab token")
            return

        today    = _date.today()
        universe = zdt_cfg.SCAN_UNIVERSE

        with _zerodte_lock:
            _zerodte_state["total"] = len(universe)

        all_anomalies: list[dict] = []

        for i, ticker in enumerate(universe, 1):
            with _zerodte_lock:
                _zerodte_state["progress"] = i
                _zerodte_state["current"]  = ticker

            chain = schwab_client.fetch_options_chain(ticker, token, today)
            if chain:
                anomalies = anomaly_engine.detect_anomalies(chain)
                all_anomalies.extend(anomalies)

            time.sleep(zdt_cfg.REQUEST_DELAY_SECONDS)

        # Sort by score and add rank
        all_anomalies.sort(key=lambda x: x["anomaly_score"], reverse=True)
        for rank, a in enumerate(all_anomalies, 1):
            a["rank"] = rank

        tickers_with_anomalies = len(set(a["underlying"] for a in all_anomalies))
        summary = {
            "tickers_scanned":          len(universe),
            "tickers_with_anomalies":   tickers_with_anomalies,
            "total_anomalies":          len(all_anomalies),
            "parity_count":  sum(1 for a in all_anomalies if "PARITY" in a["flags"]),
            "stale_count":   sum(1 for a in all_anomalies if "STALE" in a["flags"]),
            "iv_count":      sum(1 for a in all_anomalies if "HIGH_IV" in a["flags"] or "LOW_IV" in a["flags"]),
            "spread_count":  sum(1 for a in all_anomalies if "WIDE_SPREAD" in a["flags"]),
            "volume_count":  sum(1 for a in all_anomalies if "UNUSUAL_VOLUME" in a["flags"]),
            "scan_date":     today.strftime("%Y-%m-%d"),
            "timestamp":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        with _zerodte_lock:
            _zerodte_state["results"]   = all_anomalies
            _zerodte_state["summary"]   = summary
            _zerodte_state["completed"] = datetime.now()
            _zerodte_state["error"]     = None

        from scan_cache import save_scan
        save_scan("zerodte", {"rows": all_anomalies, "summary": summary})
        logger.info("0DTE scan complete: %d anomalies across %d tickers", len(all_anomalies), tickers_with_anomalies)

    except Exception as e:
        logger.exception("0DTE scan failed: %s", e)
        with _zerodte_lock:
            _zerodte_state["error"] = str(e)
    finally:
        with _zerodte_lock:
            _zerodte_state["running"] = False


@app.route("/momentum2")
def momentum2_page():
    """Momentum Pro screener page."""
    with _momv2_lock:
        running   = _momv2_state["running"]
        completed = _momv2_state["completed"]
        df        = _momv2_state["results"]
        regime    = _momv2_state["regime"]
        error     = _momv2_state["error"]

    rows = []
    summary = {}
    if df is not None and not df.empty:
        rows = df.head(100).to_dict("records")
        summary = {
            "total_scanned":  len(df),
            "strong_count":   int((df["composite_score"] >= 75).sum()),
            "moderate_count": int(((df["composite_score"] >= 55) & (df["composite_score"] < 75)).sum()),
            "new_highs_count": int(df["new_20d_high"].sum()) if "new_20d_high" in df.columns else 0,
            "red_flag_str":   "🚩 Red Flag" if (regime and regime.get("red_flag")) else "✅ Uptrend",
            "completed":      completed.strftime("%Y-%m-%d %H:%M:%S") if completed else None,
        }

    return render_template(
        "momentum_v2.html",
        running=running, rows=rows, summary=summary,
        regime=regime or {}, error=error,
    )


@app.route("/momentum2/start", methods=["POST"])
def momentum2_start():
    with _momv2_lock:
        if _momv2_state["running"]:
            return jsonify({"status": "already_running"})
        _momv2_state.update({
            "running": True, "started": datetime.now(),
            "completed": None, "progress": 0, "total": 0,
            "current": "", "results": None, "error": None,
        })
    threading.Thread(target=_run_momv2_background, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/momentum2/status")
def momentum2_status():
    with _momv2_lock:
        return jsonify({
            "running":   _momv2_state["running"],
            "progress":  _momv2_state["progress"],
            "total":     _momv2_state["total"],
            "current":   _momv2_state["current"],
            "completed": _momv2_state["completed"].isoformat() if _momv2_state["completed"] else None,
            "error":     _momv2_state["error"],
        })


@app.route("/momentum2/export/csv")
def momentum2_export_csv():
    with _momv2_lock:
        df = _momv2_state["results"]
    if df is None or df.empty:
        return "No results to export", 404
    csv_data = df.to_csv(index=False)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        csv_data, mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=momentum_pro_{ts}.csv"},
    )



# ---- Expiration-day put scanner routes ----

def _expiring_bool_arg(args, name: str, default: bool) -> bool:
    if name in args:
        values = args.getlist(name) if hasattr(args, "getlist") else [args.get(name)]
        return str(values[-1]).lower() in {"1", "true", "yes", "on"}
    return default


def _expiring_float_arg(args, name: str, default=None):
    value = args.get(name)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _expiring_int_arg(args, name: str, default: int = 0) -> int:
    value = _expiring_float_arg(args, name, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default



def _expiring_scan_date_from_args(args):
    from datetime import date as _date, datetime as _dt
    from expiring_options.scanner import next_standard_expiration
    expiration_mode = args.get("expiration_mode", "next")
    custom_date = args.get("expiration_date", "")
    if expiration_mode == "today":
        return _date.today()
    if expiration_mode == "custom" and custom_date:
        try:
            return _dt.strptime(custom_date, "%Y-%m-%d").date()
        except ValueError:
            return next_standard_expiration()
    return next_standard_expiration()

def _expiring_filters_from_args(args):
    from expiring_options.scanner import ExpiringOptionFilters
    return ExpiringOptionFilters(
        min_bid_price=_expiring_float_arg(args, "min_bid_price", 0.01),
        min_ask_price=_expiring_float_arg(args, "min_ask_price", 0.01),
        min_midpoint_price=_expiring_float_arg(args, "min_midpoint_price", 0.01),
        min_open_interest=_expiring_int_arg(args, "min_open_interest", 0),
        min_volume=_expiring_int_arg(args, "min_volume", 0),
        max_bid_ask_spread_percentage=_expiring_float_arg(args, "max_bid_ask_spread_percentage", 100.0),
        max_absolute_delta=_expiring_float_arg(args, "max_absolute_delta", None),
        min_distance_below_current_stock_price=_expiring_float_arg(args, "min_distance_below_current_stock_price", 0.0),
        exclude_zero_bid=_expiring_bool_arg(args, "exclude_zero_bid", True),
        exclude_missing_bid_ask=_expiring_bool_arg(args, "exclude_missing_bid_ask", True),
        exclude_extremely_wide_spreads=_expiring_bool_arg(args, "exclude_extremely_wide_spreads", True),
        exclude_earnings_today_or_next=_expiring_bool_arg(args, "exclude_earnings_today_or_next", False),
        exclude_hard_to_borrow=_expiring_bool_arg(args, "exclude_hard_to_borrow", False),
    )


_universe_rows_cache: tuple | None = None
_universe_rows_cache_lock = threading.Lock()


def _expiring_universe_rows_and_meta() -> tuple[list[dict], dict]:
    global _universe_rows_cache
    with _universe_rows_cache_lock:
        if _universe_rows_cache is not None:
            return _universe_rows_cache

    import universe as univ
    tickers, meta = univ.load_universe()
    names = fetch_company_names(tickers)
    rows = []
    seen = set()
    for ticker in tickers:
        symbol = clean_symbol(ticker).replace(".", "-")
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        rows.append({
            "symbol": symbol,
            "company_name": names.get(symbol, symbol),
            "index_membership": "S&P 500 / Nasdaq 100",
        })
    meta = dict(meta or {})
    meta["count"] = len(rows)
    logger.info("Expiring Options All tickers universe count: %d", len(rows))
    result = (rows, meta)
    with _universe_rows_cache_lock:
        _universe_rows_cache = result
    return result


def _expiring_symbol_rows() -> list[dict]:
    rows, _meta = _expiring_universe_rows_and_meta()
    return rows


def _run_expiring_options_background(filters, sort_by: str, mode: str, scan_date):
    try:
        from pathlib import Path
        from expiring_options import scanner
        from scan_cache import save_scan

        root = Path(__file__).resolve().parent

        def progress(i: int, total: int, current: str) -> None:
            with _expiring_lock:
                _expiring_state["progress"] = i
                _expiring_state["total"] = total
                _expiring_state["current"] = current

        if mode == "test":
            payload = scanner.run_test_scan(root, filters, sort_by, progress=progress, scan_date=scan_date)
        else:
            token = get_valid_token()
            if not token:
                raise RuntimeError("Schwab authentication is required for live mode.")
            symbol_rows, universe_meta = _expiring_universe_rows_and_meta()
            with _expiring_lock:
                _expiring_state["total"] = len(symbol_rows)
            payload = scanner.run_live_scan(token, symbol_rows, filters, sort_by, progress=progress, scan_date=scan_date)
            payload.setdefault("summary", {})["universe_source"] = universe_meta.get("source", "unknown")
            payload.setdefault("summary", {})["universe_updated"] = universe_meta.get("updated", "")
            payload.setdefault("summary", {})["universe_count"] = universe_meta.get("count", len(symbol_rows))

        with _expiring_lock:
            _expiring_state["rows_by_level"] = payload.get("rows_by_level", {"5": [], "10": [], "15": []})
            _expiring_state["all_rows"] = payload.get("all_rows", [])
            _expiring_state["chain_rows"] = payload.get("chain_rows", [])
            _expiring_state["summary"] = payload.get("summary", {})
            _expiring_state["errors"] = payload.get("errors", [])
            _expiring_state["filters"] = payload.get("filters", filters.to_dict())
            _expiring_state["sort_by"] = sort_by
            _expiring_state["mode"] = mode
            _expiring_state["selected_scan_date"] = payload.get("summary", {}).get("scan_date")
            _expiring_state["completed"] = datetime.now()
            _expiring_state["error"] = None
        save_scan("expiring_options", payload)
        logger.info("Expiring Options scan complete: %d candidates", len(payload.get("all_rows", [])))
    except Exception as e:
        logger.exception("Expiring Options scan failed: %s", e)
        with _expiring_lock:
            _expiring_state["error"] = str(e)
    finally:
        with _expiring_lock:
            _expiring_state["running"] = False
            _expiring_state["current"] = ""


@app.route("/expiring-options")
def expiring_options_page():
    from expiring_options import scanner
    filters = _expiring_filters_from_args(request.args)
    scan_date = _expiring_scan_date_from_args(request.args)
    expiration_mode = request.args.get("expiration_mode", "next")
    with _expiring_lock:
        running = _expiring_state["running"]
        completed = _expiring_state["completed"]
        rows_by_level = _expiring_state.get("rows_by_level") or {"5": [], "10": [], "15": []}
        all_rows = list(_expiring_state.get("all_rows") or [])
        chain_rows = list(_expiring_state.get("chain_rows") or [])
        summary = dict(_expiring_state.get("summary") or {})
        errors = list(_expiring_state.get("errors") or [])
        error = _expiring_state.get("error")
        last_sort = _expiring_state.get("sort_by", "midpoint_premium_yield_on_strike")

    mode = request.args.get("mode") or "live"
    sort_by = request.args.get("sort_by") or last_sort
    expiration_mode = request.args.get("expiration_mode") or "next"
    scan_date_value = request.args.get("expiration_date") or scanner.next_standard_expiration().isoformat()
    _universe_rows, universe_meta = _expiring_universe_rows_and_meta()
    if summary.get("mode") and summary.get("mode") != mode:
        rows_by_level = {"5": [], "10": [], "15": []}
        all_rows = []
        chain_rows = []
        errors = []
        summary = {
            "mode": mode,
            "total_symbols_loaded": universe_meta.get("count", 0),
            "total_symbols_scanned": 0,
            "total_candidates_found": 0,
            "missing_data_symbols": 0,
            "api_errors": 0,
            "excluded_by_filters": 0,
            "rows_5": 0,
            "rows_10": 0,
            "rows_15": 0,
            "market_data_status": "Schwab Market Data API; real-time/delayed depends on account entitlements" if mode == "live" else "Test CSV data",
            "warnings": [],
        }
    summary.setdefault("universe_count", universe_meta.get("count", 0))
    summary.setdefault("universe_source", universe_meta.get("source", "unknown"))
    summary.setdefault("universe_updated", universe_meta.get("updated", ""))
    selected_symbol = clean_symbol(request.args.get("symbol", ""))
    detail_rows = []
    if selected_symbol:
        selected_by_strike: dict[float, list[str]] = {}
        for row in all_rows:
            if row.get("symbol") == selected_symbol:
                selected_by_strike.setdefault(float(row.get("actual_selected_strike")), []).append(f"{int(row.get('target_percentage'))}%")
        for row in chain_rows:
            if row.get("symbol") == selected_symbol:
                item = dict(row)
                bid = item.get("bid_price")
                ask = item.get("ask_price")
                item["midpoint"] = scanner.option_midpoint_price(bid, ask) if bid is not None and ask is not None else None
                item["selected_levels"] = selected_by_strike.get(float(item.get("strike_price")), []) if item.get("strike_price") is not None else []
                detail_rows.append(item)
        detail_rows.sort(key=lambda r: r.get("strike_price") or 0, reverse=True)

    return render_template(
        "expiring_options.html",
        running=running,
        completed=completed,
        rows_by_level=rows_by_level,
        summary=summary,
        errors=errors,
        error=error,
        filters=filters,
        sort_by=sort_by,
        sort_fields=scanner.SORT_FIELDS,
        mode=mode,
        expiration_mode=expiration_mode,
        scan_date_value=scan_date_value,
        has_token=(True if mode == "test" else get_valid_token() is not None),
        selected_symbol=selected_symbol,
        detail_rows=detail_rows,
    )


@app.route("/expiring-options/start", methods=["POST"])
def expiring_options_start():
    from expiring_options import scanner
    mode = request.args.get("mode", "live")
    sort_by = request.args.get("sort_by", "midpoint_premium_yield_on_strike")
    if sort_by not in scanner.SORT_FIELDS:
        sort_by = "midpoint_premium_yield_on_strike"
    filters = _expiring_filters_from_args(request.args)
    scan_date = _expiring_scan_date_from_args(request.args)
    expiration_mode = request.args.get("expiration_mode", "next")
    with _expiring_lock:
        if _expiring_state["running"]:
            return jsonify({"status": "already_running"})
        _expiring_state.update({
            "running": True,
            "started": datetime.now(),
            "completed": None,
            "progress": 0,
            "total": 0,
            "current": "",
            "error": None,
            "filters": filters.to_dict(),
            "sort_by": sort_by,
            "mode": mode,
            "expiration_mode": expiration_mode,
            "selected_scan_date": scan_date.isoformat(),
        })
    threading.Thread(target=_run_expiring_options_background, args=(filters, sort_by, mode, scan_date), daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/expiring-options/status")
def expiring_options_status():
    with _expiring_lock:
        return jsonify({
            "running": _expiring_state["running"],
            "progress": _expiring_state["progress"],
            "total": _expiring_state["total"],
            "current": _expiring_state["current"],
            "completed": _expiring_state["completed"].isoformat() if _expiring_state["completed"] else None,
            "error": _expiring_state["error"],
        })


@app.route("/expiring-options/export/csv")
def expiring_options_export_csv():
    from expiring_options import scanner
    level = request.args.get("level", "all")
    with _expiring_lock:
        if level in {"5", "10", "15"}:
            rows = list((_expiring_state.get("rows_by_level") or {}).get(level, []))
        else:
            rows = list(_expiring_state.get("all_rows") or [])
    if not rows:
        return "No Expiring Options results to export", 404
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(scanner.export_csv(rows), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=expiring_options_{level}_{ts}.csv"})


@app.route("/expiring-options/export/xlsx")
def expiring_options_export_xlsx():
    from expiring_options import scanner
    with _expiring_lock:
        rows_by_level = dict(_expiring_state.get("rows_by_level") or {"5": [], "10": [], "15": []})
        errors = list(_expiring_state.get("errors") or [])
    if not any(rows_by_level.values()):
        return "No Expiring Options results to export", 404
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    data = scanner.export_excel_bytes(rows_by_level, errors)
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=expiring_options_{ts}.xlsx"},
    )


@app.route("/zerodte")
def zerodte_page():
    """0DTE Options Anomaly Scanner page."""
    with _zerodte_lock:
        running   = _zerodte_state["running"]
        completed = _zerodte_state["completed"]
        rows      = _zerodte_state.get("results")
        summary   = _zerodte_state.get("summary")
        error     = _zerodte_state.get("error")

    has_token    = get_valid_token() is not None
    filter_flag  = request.args.get("flag", "all")
    top_n        = int(request.args.get("top", "100"))

    display_rows = list(rows or [])
    if filter_flag != "all" and display_rows:
        display_rows = [r for r in display_rows if filter_flag in r.get("flags", [])]
    display_rows = display_rows[:top_n]

    return render_template(
        "zerodte.html",
        running=running, rows=display_rows, summary=summary or {},
        error=error, filter_flag=filter_flag, top_n=top_n,
        has_token=has_token, completed=completed,
    )


@app.route("/zerodte/start", methods=["POST"])
def zerodte_start():
    with _zerodte_lock:
        if not _zerodte_state.get("enabled", True):
            return jsonify({"status": "disabled"})
        if _zerodte_state["running"]:
            return jsonify({"status": "already_running"})
        _zerodte_state.update({
            "running": True, "started": datetime.now(),
            "completed": None, "progress": 0, "total": 0,
            "current": "", "error": None,
        })
    threading.Thread(target=_run_zerodte_background, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/zerodte/status")
def zerodte_status():
    with _zerodte_lock:
        return jsonify({
            "running":   _zerodte_state["running"],
            "progress":  _zerodte_state["progress"],
            "total":     _zerodte_state["total"],
            "current":   _zerodte_state["current"],
            "completed": _zerodte_state["completed"].isoformat() if _zerodte_state["completed"] else None,
            "error":     _zerodte_state["error"],
        })


@app.route("/zerodte/toggle", methods=["POST"])
def zerodte_toggle():
    with _zerodte_lock:
        _zerodte_state["enabled"] = not _zerodte_state.get("enabled", True)
        enabled = _zerodte_state["enabled"]
    return jsonify({"enabled": enabled})


@app.route("/zerodte/export/csv")
def zerodte_export_csv():
    with _zerodte_lock:
        rows = _zerodte_state.get("results")
    if not rows:
        return "No results to export", 404
    df = pd.DataFrame(rows)
    csv_data = df.to_csv(index=False)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        csv_data, mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=zerodte_anomalies_{ts}.csv"},
    )


# ── Equity Ranking routes ──────────────────────────────────────────────────

# Scan state: quantitative scoring of the full universe
_equity_scan_state = {
    "running": False, "started": None, "completed": None,
    "phase": None, "progress": 0, "total": 0, "current": "",
    "rows": None, "error": None,
}
_equity_scan_lock = threading.Lock()

# AI write-up state: OpenAI analysis of selected tickers
_equity_ai_state = {
    "running": False, "started": None, "completed": None,
    "report": "", "tickers": [], "error": None,
}
_equity_ai_lock = threading.Lock()


def _run_equity_scan_background():
    """Background: fetch all universe tickers, score quantitatively."""
    from equity.data_fetcher import fetch_universe_bulk
    from equity.scorer import rank_universe
    import universe as univ

    try:
        tickers, _ = univ.load_universe()
        with _equity_scan_lock:
            _equity_scan_state.update({
                "phase": "fetching", "progress": 0,
                "total": len(tickers), "current": "", "rows": None, "error": None,
            })

        token = get_valid_token()

        def progress_cb(i, total, ticker):
            with _equity_scan_lock:
                _equity_scan_state["progress"] = i
                _equity_scan_state["current"]  = ticker

        results = fetch_universe_bulk(tickers, token=token, progress_cb=progress_cb)

        with _equity_scan_lock:
            _equity_scan_state["phase"] = "scoring"

        raw_data = [r.get("data", r) if not r.get("error") else r for r in results]
        ranked   = rank_universe(results)

        with _equity_scan_lock:
            _equity_scan_state["rows"]      = ranked
            _equity_scan_state["phase"]     = "done"
            _equity_scan_state["completed"] = datetime.now()

        from scan_cache import save_scan
        save_scan("equity_scan", {"rows": ranked})
        logger.info("Equity scan complete: %d stocks ranked", len(ranked))

    except Exception as e:
        logger.exception("Equity scan failed: %s", e)
        with _equity_scan_lock:
            _equity_scan_state["error"] = str(e)
            _equity_scan_state["phase"] = "error"
    finally:
        with _equity_scan_lock:
            _equity_scan_state["running"] = False


def _run_equity_ai_background(tickers, objective, horizon, style, benchmark):
    """Background: fetch detailed data for selected tickers, call OpenAI."""
    from equity.data_fetcher import fetch_ticker_detail, format_for_prompt
    from equity.claude_analyst import run_analysis

    try:
        with _equity_ai_lock:
            _equity_ai_state.update({"report": "", "error": None})

        token = get_valid_token()
        detailed = [fetch_ticker_detail(t, token=token) for t in tickers]
        data_str = "\n".join(format_for_prompt(r) for r in detailed)

        def stream_cb(chunk):
            with _equity_ai_lock:
                _equity_ai_state["report"] += chunk

        report = run_analysis(data_str, tickers, objective, horizon, style, benchmark,
                              stream_cb=stream_cb)

        with _equity_ai_lock:
            _equity_ai_state["report"]    = report
            _equity_ai_state["completed"] = datetime.now()

        from scan_cache import save_scan
        save_scan("equity_ai", {"report": report, "tickers": tickers,
                                "objective": objective, "horizon": horizon,
                                "style": style, "benchmark": benchmark})

    except Exception as e:
        logger.exception("Equity AI analysis failed: %s", e)
        with _equity_ai_lock:
            _equity_ai_state["error"] = str(e)
    finally:
        with _equity_ai_lock:
            _equity_ai_state["running"] = False


@app.route("/equity")
def equity_page():
    with _equity_scan_lock:
        scan  = dict(_equity_scan_state)
    with _equity_ai_lock:
        ai    = dict(_equity_ai_state)
    has_api_key = bool(os.environ.get("OPENAI_API_KEY"))
    return render_template("equity.html", scan=scan, ai=ai, has_api_key=has_api_key)


@app.route("/equity/scan", methods=["POST"])
def equity_scan_start():
    with _equity_scan_lock:
        if _equity_scan_state["running"]:
            return jsonify({"status": "already_running"})
        _equity_scan_state.update({
            "running": True, "started": datetime.now(),
            "completed": None, "phase": "fetching",
            "progress": 0, "total": 0, "current": "",
            "rows": None, "error": None,
        })
    threading.Thread(target=_run_equity_scan_background, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/equity/scan/status")
def equity_scan_status():
    with _equity_scan_lock:
        return jsonify({
            "running":   _equity_scan_state["running"],
            "phase":     _equity_scan_state["phase"],
            "progress":  _equity_scan_state["progress"],
            "total":     _equity_scan_state["total"],
            "current":   _equity_scan_state["current"],
            "completed": _equity_scan_state["completed"].isoformat() if _equity_scan_state["completed"] else None,
            "error":     _equity_scan_state["error"],
            "row_count": len(_equity_scan_state["rows"] or []),
        })


@app.route("/equity/scan/results")
def equity_scan_results():
    """Return ranked rows as JSON (used for table rendering)."""
    with _equity_scan_lock:
        rows = list(_equity_scan_state.get("rows") or [])
    return jsonify(rows)


@app.route("/equity/scan/export/csv")
def equity_scan_export_csv():
    with _equity_scan_lock:
        rows = list(_equity_scan_state.get("rows") or [])
    if not rows:
        return "No scan results to export", 404
    df = pd.DataFrame(rows)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        df.to_csv(index=False),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=equity_ranking_{ts}.csv"},
    )


@app.route("/equity/analyze", methods=["POST"])
def equity_analyze():
    """Start OpenAI write-up for selected tickers."""
    data = request.get_json() or {}
    tickers = [t.strip().upper() for t in data.get("tickers", []) if t.strip()]
    if not tickers:
        return jsonify({"status": "error", "error": "No tickers provided"}), 400
    if len(tickers) > 10:
        return jsonify({"status": "error", "error": "Max 10 tickers for AI write-up"}), 400
    if not os.environ.get("OPENAI_API_KEY"):
        return jsonify({"status": "error", "error": "OPENAI_API_KEY not set in .env"}), 400

    objective = data.get("objective", "long-term compounders")
    horizon   = data.get("horizon", "3 years")
    style     = data.get("style", "quality-focused")
    benchmark = data.get("benchmark", "S&P 500")

    with _equity_ai_lock:
        if _equity_ai_state["running"]:
            return jsonify({"status": "already_running"})
        _equity_ai_state.update({
            "running": True, "started": datetime.now(),
            "completed": None, "report": "", "tickers": tickers, "error": None,
        })

    threading.Thread(
        target=_run_equity_ai_background,
        args=(tickers, objective, horizon, style, benchmark),
        daemon=True,
    ).start()
    return jsonify({"status": "started"})


@app.route("/equity/analyze/status")
def equity_analyze_status():
    with _equity_ai_lock:
        return jsonify({
            "running":    _equity_ai_state["running"],
            "completed":  _equity_ai_state["completed"].isoformat() if _equity_ai_state["completed"] else None,
            "error":      _equity_ai_state["error"],
            "tickers":    _equity_ai_state["tickers"],
            "has_report": bool(_equity_ai_state.get("report")),
        })


@app.route("/equity/analyze/report")
def equity_analyze_report():
    with _equity_ai_lock:
        return jsonify({
            "report": _equity_ai_state.get("report") or "",
            "running": _equity_ai_state["running"],
        })


@app.route("/equity/analyze/export")
def equity_analyze_export():
    with _equity_ai_lock:
        report  = _equity_ai_state.get("report") or ""
        tickers = _equity_ai_state.get("tickers", [])
    if not report:
        return "No report to export", 404
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"equity_analysis_{'_'.join(tickers)}_{ts}.md"
    return Response(report, mimetype="text/markdown",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


def _startup_scans():
    """Load cached results, then start fresh background scans."""
    from scan_cache import load_scan

    # 1. Load persisted results into state dicts immediately
    cached_csp = load_scan("csp")
    if cached_csp:
        with _csp_lock:
            _csp_state["results"]    = cached_csp["data"]["rows"]
            _csp_state["completed"]  = datetime.fromisoformat(cached_csp["timestamp"])
            _csp_state["vix_level"]  = cached_csp["data"].get("vix_level")
            _csp_state["vix_regime"] = cached_csp["data"].get("vix_regime")
            _csp_state["rejections"] = cached_csp["data"].get("rejections", {})
        logger.info("Loaded cached CSP results (%d rows)", len(cached_csp["data"]["rows"]))

    cached_momentum = load_scan("momentum")
    if cached_momentum:
        try:
            records = cached_momentum["data"]["records"]
            df = pd.DataFrame(records)
            with _scan_lock:
                _scan_state["results"]   = df
                _scan_state["completed"] = datetime.fromisoformat(cached_momentum["timestamp"])
            logger.info("Loaded cached momentum results (%d rows)", len(records))
        except Exception as e:
            logger.warning("Failed to restore momentum cache: %s", e)

    cached_momv2 = load_scan("momentum2")
    if cached_momv2:
        try:
            records = cached_momv2["data"]["records"]
            df = pd.DataFrame(records)
            with _momv2_lock:
                _momv2_state["results"]   = df
                _momv2_state["regime"]    = cached_momv2["data"].get("regime")
                _momv2_state["completed"] = datetime.fromisoformat(cached_momv2["timestamp"])
            logger.info("Loaded cached Momentum Pro results (%d rows)", len(records))
        except Exception as e:
            logger.warning("Failed to restore momentum2 cache: %s", e)

    cached_dash = load_scan("dashboard")
    if cached_dash:
        with _dash_lock:
            _dash_state["results"]   = cached_dash["data"]["rows"]
            _dash_state["summary"]   = cached_dash["data"]["summary"]
            _dash_state["errors"]    = cached_dash["data"].get("errors", [])
            _dash_state["completed"] = datetime.fromisoformat(cached_dash["timestamp"])
        logger.info("Loaded cached dashboard results (%d rows)", len(cached_dash["data"]["rows"]))

    cached_opts = load_scan("options")
    if cached_opts:
        with _opts_lock:
            _opts_state["results"]   = cached_opts["data"]["rows"]
            _opts_state["summary"]   = cached_opts["data"]["summary"]
            _opts_state["errors"]    = cached_opts["data"].get("errors", [])
            _opts_state["completed"] = datetime.fromisoformat(cached_opts["timestamp"])
        logger.info("Loaded cached options results (%d rows)", len(cached_opts["data"]["rows"]))

    cached_zerodte = load_scan("zerodte")
    if cached_zerodte:
        with _zerodte_lock:
            _zerodte_state["results"]   = cached_zerodte["data"]["rows"]
            _zerodte_state["summary"]   = cached_zerodte["data"]["summary"]
            _zerodte_state["completed"] = datetime.fromisoformat(cached_zerodte["timestamp"])
        logger.info("Loaded cached 0DTE results (%d anomalies)", len(cached_zerodte["data"]["rows"]))


    cached_expiring = load_scan("expiring_options")
    if cached_expiring:
        try:
            data = cached_expiring["data"]
            with _expiring_lock:
                _expiring_state["rows_by_level"] = data.get("rows_by_level", {"5": [], "10": [], "15": []})
                _expiring_state["all_rows"] = data.get("all_rows", [])
                _expiring_state["chain_rows"] = data.get("chain_rows", [])
                _expiring_state["summary"] = data.get("summary", {})
                _expiring_state["errors"] = data.get("errors", [])
                _expiring_state["filters"] = data.get("filters", {})
                _expiring_state["sort_by"] = data.get("sort_by", "midpoint_premium_yield_on_strike")
                _expiring_state["mode"] = data.get("summary", {}).get("mode", "live")
                _expiring_state["selected_scan_date"] = data.get("summary", {}).get("scan_date")
                _expiring_state["completed"] = datetime.fromisoformat(cached_expiring["timestamp"])
            logger.info("Loaded cached Expiring Options results (%d rows)", len(data.get("all_rows", [])))
        except Exception as e:
            logger.warning("Failed to restore Expiring Options cache: %s", e)

    cached_equity_scan = load_scan("equity_scan")
    if cached_equity_scan:
        try:
            with _equity_scan_lock:
                _equity_scan_state["rows"]      = cached_equity_scan["data"]["rows"]
                _equity_scan_state["completed"] = datetime.fromisoformat(cached_equity_scan["timestamp"])
                _equity_scan_state["phase"]     = "done"
            logger.info("Loaded cached equity scan (%d rows)", len(cached_equity_scan["data"]["rows"]))
        except Exception as e:
            logger.warning("Failed to restore equity scan cache: %s", e)

    cached_equity_ai = load_scan("equity_ai")
    if cached_equity_ai:
        try:
            with _equity_ai_lock:
                _equity_ai_state["report"]    = cached_equity_ai["data"]["report"]
                _equity_ai_state["tickers"]   = cached_equity_ai["data"].get("tickers", [])
                _equity_ai_state["completed"] = datetime.fromisoformat(cached_equity_ai["timestamp"])
            logger.info("Loaded cached equity AI report")
        except Exception as e:
            logger.warning("Failed to restore equity AI cache: %s", e)

    # Pre-compute risk from cached CSP data
    if cached_csp:
        try:
            cached_risk = load_scan("risk")
            if cached_risk:
                with _risk_lock:
                    _risk_state["results"]   = cached_risk["data"]["risk"]
                    _risk_state["kelly"]     = cached_risk["data"]["kelly"]
                    _risk_state["capital"]   = cached_risk["data"].get("capital", 100000)
                    _risk_state["completed"] = datetime.fromisoformat(cached_risk["timestamp"])
                logger.info("Loaded cached risk results")
            else:
                _compute_risk()
                logger.info("Computed risk from cached CSP data")
        except Exception as e:
            logger.warning("Risk pre-compute failed: %s", e)

    # Pre-load journal from disk
    try:
        _refresh_journal()
        with _journal_lock:
            count = len(_journal_state["trades"]) if _journal_state["trades"] else 0
        logger.info("Pre-loaded journal (%d trades)", count)
    except Exception as e:
        logger.warning("Journal pre-load failed: %s", e)

    # 2. Start fresh scans in background threads
    # Dashboard + Options: only if Schwab token available
    if get_valid_token():
        with _dash_lock:
            _dash_state["running"] = True
        threading.Thread(target=_run_dashboard_background, daemon=True).start()

        with _opts_lock:
            _opts_state["running"] = True
        threading.Thread(target=_run_options_background, daemon=True).start()
        logger.info("Auto-started dashboard + options scans (Schwab token available)")
    else:
        logger.info("Skipping dashboard/options auto-scan (no Schwab token)")

    # CSP + Momentum: run sequentially in one thread to avoid yfinance rate contention.
    # Cached results are already loaded above, so the UI is responsive immediately.
    def _run_yf_scans_sequentially():
        for state, lock, target, name in [
            (_csp_state,   _csp_lock,   _run_csp_background,   "CSP"),
            (_scan_state,  _scan_lock,  _run_scan_background,  "momentum"),
            (_momv2_state, _momv2_lock, _run_momv2_background, "momentum2"),
        ]:
            with lock:
                state.update({"running": True, "started": datetime.now(),
                              "progress": 0, "total": 0, "current": "", "error": None})
            logger.info("Auto-starting %s scan", name)
            target()

    threading.Thread(target=_run_yf_scans_sequentially, daemon=True).start()
    logger.info("Auto-started CSP + momentum + momentum2 scans (sequential)")


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

    # Load cached results + start fresh background scans
    _startup_scans()

    # Start the 0DTE Mon/Wed/Fri auto-scheduler
    def _zerodte_schedule_trigger():
        with _zerodte_lock:
            if _zerodte_state["running"] or not _zerodte_state.get("enabled", True):
                return
            _zerodte_state.update({
                "running": True, "started": datetime.now(),
                "completed": None, "progress": 0, "total": 0,
                "current": "", "error": None,
            })
        threading.Thread(target=_run_zerodte_background, daemon=True).start()

    try:
        from zerodte.scheduler import start_scheduler as _start_zerodte_scheduler
        _start_zerodte_scheduler(_zerodte_schedule_trigger)
    except Exception as _sched_err:
        logger.warning("0DTE scheduler failed to start: %s", _sched_err)

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
