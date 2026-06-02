# Schwab Covered Call Dashboard

A secure, local web application that connects to your Charles Schwab trading accounts via the official Schwab Developer API and displays covered-call opportunities across all your accounts.

---

## Features

- OAuth 2.0 authentication — no username or password stored
- Reads all authorised accounts and their stock/option positions
- Calculates covered-call capacity and identifies how many calls you can still sell
- Sortable, filterable HTML dashboard with summary cards
- CSV export
- Account numbers masked by default

---

## Prerequisites

| Requirement | Version |
|-------------|---------|
| Python | 3.11 + |
| pip | any recent |
| A Schwab Developer App | see below |

---

## 1 — Create Schwab Developer Credentials

1. Go to **https://developer.schwab.com** and sign in with your Schwab credentials.
2. Click **Create App**.
3. Fill in:
   - **App Name**: anything (e.g. "Covered Call Dashboard")
   - **Redirect URI**: `http://127.0.0.1:5000/callback`
   - **API Products**: select **Accounts and Trading Production**
4. Submit and wait for approval (usually instant or a few minutes).
5. Copy your **Client ID** and **Client Secret**.

---

## 2 — Set Environment Variables

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```
SCHWAB_CLIENT_ID=your_client_id
SCHWAB_CLIENT_SECRET=your_client_secret
SCHWAB_REDIRECT_URI=http://127.0.0.1:5000/callback
FLASK_SECRET_KEY=some_long_random_string
```

---

## 3 — Install Dependencies

```bash
python -m venv venv

# macOS / Linux
source venv/bin/activate

# Windows
venv\Scripts\activate

pip install -r requirements.txt
```

---

## 4 — Run the Application

```bash
python app.py
```

Open your browser to **http://127.0.0.1:5000**

---

## 5 — Authenticate with Schwab

1. Click **Connect Schwab Account**.
2. You will be redirected to Schwab's login page.
3. Log in and authorise the application.
4. You will be redirected back to the dashboard automatically.

Tokens are saved to `.schwab_tokens.json` (readable only by your user). The app automatically refreshes the access token when it expires.

---

## How the Covered-Call Calculation Works

For each **account** and each **stock ticker**:

| Variable | Formula |
|----------|---------|
| `abs_shares` | `abs(shares_owned)` |
| `covered_call_capacity_exact` | `abs_shares / 100` |
| `covered_call_capacity_whole` | `floor(abs_shares / 100)` |
| `covered_calls_present` | count of *short* CALL option contracts for that ticker in that account |
| `calls_to_be_sold` | `max(covered_call_capacity_whole − covered_calls_present, 0)` |

**Example:**

> Account IRA-1234 holds 550 shares of NVDA and has 3 short NVDA calls open.
>
> - Capacity exact = 550 / 100 = **5.5**
> - Capacity whole = floor(5.5) = **5**
> - Calls present = **3**
> - Calls to be sold = 5 − 3 = **2**

**Rules applied:**
- Only *long* (positive) stock positions are considered.
- Only *short* call contracts count as existing covered calls (long calls are ignored).
- Each account is evaluated independently.
- Positions with fewer than 100 shares are hidden by default (use "All positions" filter to show them).

---

## Dashboard Controls

| Control | Description |
|---------|-------------|
| Filter dropdown | Switch between ≥100 shares / calls to sell / all positions |
| Account numbers | Toggle between masked and full display |
| Search box | Live filter by ticker or account name |
| Column headers | Click any header to sort ascending/descending |
| CSV Export | Downloads all positions (masked account numbers) |
| Refresh | Re-fetches live data from Schwab |

---

## Security Notes

- `.env` and `.schwab_tokens.json` are listed in `.gitignore` and should **never** be committed.
- The token file is created with `chmod 600` (owner read/write only) on Unix systems.
- This application **never places trades**. It is read-only.
- No data is sent to any third party.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "SCHWAB_CLIENT_ID not set" | Make sure `.env` exists and is filled in |
| Redirect URI mismatch | Ensure the URI in `.env` exactly matches what's registered in the Schwab Developer Portal |
| Token refresh fails | Click **Logout** and re-authenticate |
| Account shows error | The account may not be authorised for API access; check the Schwab Developer Portal |
| No positions shown | Confirm the account has holdings and the API product includes "Accounts and Trading" |

---

## Momentum Screener (S&P 500)

A quantitative momentum scanner is included as both a web tab and a CLI tool.

### Web tab

After authenticating with Schwab, click **Momentum** in the top nav, then **▶ Run Scan**. The scan analyzes the full S&P 500 (~500 stocks) and takes 8–15 minutes. Progress is shown live. Results are sortable and exportable as CSV.

### CLI

```bash
# Activate venv first
source venv/bin/activate                 # Mac/Linux
venv\Scripts\activate                    # Windows

# Full S&P 500 scan
python momentum_cli.py

# Custom ticker list
python momentum_cli.py AAPL MSFT NVDA GOOGL TSLA

# Show top 25 in console
python momentum_cli.py --top 25
```

Outputs `momentum_YYYYMMDD_HHMMSS.csv` (full ranked table) and `momentum_YYYYMMDD_HHMMSS.txt` (analyst-style summary report).

### Data sources

The screener tries **Schwab Market Data API first** (uses your existing OAuth token), then falls back to **Yahoo Finance** via `yfinance` for any tickers Schwab can't return.

### What it measures

Each stock receives a 0–100 **composite momentum score** combining:

| Component | Weight |
|-----------|--------|
| Absolute price momentum (21d / 42d / 63d) | 25% |
| Relative momentum vs SPY and sector | 20% |
| Regression trend quality (slope, R², t-stat) | 20% |
| Risk-adjusted return (Sharpe, Sortino, Calmar) | 15% |
| Trend confirmation (MA20/MA50, % up days) | 10% |
| Volume confirmation | 5% |
| **Penalties** (gap-driven, overextended, weak short-term) | up to −20% |

Stocks are classified **Strong / Moderate / Weak / No Clear Momentum**, requiring positive 63d return, positive vs-SPY, positive slope, t-stat > 1.0, and price above MA50 to qualify as "clear momentum".


## Expiring Options

The `Expiring Options` page scans the next expiration date for put options at 5%, 10%, and 15% below the current stock price.

Open it from the navigation bar or use the desktop shortcut named `Expiring Options`.

The scanner is read-only. It does not place trades, create order tickets, or automate Schwab Desktop.

Universe:

- `Live Schwab API` scans the same deduplicated S&P 500 + Nasdaq 100 list shown on the app's `Universe` / `All tickers in universe` page.
- `Test CSV data` is only a small sample dataset for smoke testing calculations and exports.

Modes:

- `Live Schwab API`: uses the existing Schwab OAuth token and the Schwab market-data quotes/chains endpoints for the full app universe.
- `Test CSV data`: uses `samples/expiring_symbol_universe.csv`, `samples/expiring_current_prices.csv`, and `samples/expiring_option_chains.csv`.

Results are cached locally, exported to CSV/Excel, and stored in `data/expiring_options.sqlite`.

Simple 90% midpoint export:

- Double-click `generate_90pct_put_midpoints.bat` to create `exports/90pct_put_midpoints_*.csv` and `.xlsx`.
- Each run also writes `_valid_only.csv` and `_valid_only.xlsx` copies containing only rows where Schwab returned bid and ask values.
- The export uses the full app universe, the next expiration by default, selects the closest listed put strike at or below 90% of the current stock price, and calculates `midpoint_premium = (bid + ask) / 2`.
- To specify an expiration manually, run `python export_90pct_put_midpoints.py --expiration YYYY-MM-DD`.

Expiration-date behavior:

- `Next expiration` scans today if today is a standard Friday expiration, otherwise the next Friday.
- `Custom expiration` lets you scan a specific expiration date supported by Schwab market data, including holiday-adjusted expirations.
