"""
Configuration for the CSP Screener.

All thresholds default to the spec values. Edit constants here to override.
Email/SMTP settings live in config.ini (see config.ini.example).
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent  # one level up from cspscreener/
OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Output sizing
# ---------------------------------------------------------------------------
TOP_N = 15

# ---------------------------------------------------------------------------
# Section A — Stock liquidity filters (HARD)
# ---------------------------------------------------------------------------
MIN_MARKET_CAP        = 2_000_000_000      # $2B
MIN_PRICE             = 10.00
MIN_AVG_DOLLAR_VOLUME = 50_000_000         # $50M/day
MIN_AVG_SHARE_VOLUME  = 1_000_000          # 1M shares/day

# ---------------------------------------------------------------------------
# Section B — Option liquidity filters (HARD)
# ---------------------------------------------------------------------------
MIN_OPEN_INTEREST     = 250
MIN_OPTION_VOLUME     = 50
MAX_BIDASK_SPREAD_PCT = 0.10               # 10% of midpoint

# ---------------------------------------------------------------------------
# Option preferred ranges
# ---------------------------------------------------------------------------
DTE_MIN = 21
DTE_MAX = 60

DELTA_MIN = -0.35   # more negative = deeper ITM put
DELTA_MAX = -0.15

MIN_ANNUALIZED_RETURN = 0.08  # 8%

# ---------------------------------------------------------------------------
# Earnings handling — strict per user choice
# ---------------------------------------------------------------------------
REJECT_ON_EARNINGS_IN_PERIOD = True

# ---------------------------------------------------------------------------
# Section G — Composite score weights (sum to 1.0; event risk is subtracted)
# ---------------------------------------------------------------------------
WEIGHTS = {
    "underlying_quality":  0.18,
    "valuation":           0.12,
    "balance_sheet":       0.10,
    "earnings_quality":    0.08,
    "technical_trend":     0.10,
    "option_liquidity":    0.07,
    "premium_attract":     0.07,
    "ev_score":            0.10,
    "iv_rank":             0.07,
    "iv_hv_premium":       0.06,
    "beta_risk":           0.05,
}
EVENT_RISK_PENALTY_MAX = 25  # max points subtracted

VIX_REGIMES = {
    "low":      {"vix_max": 15,  "delta_min": -0.35, "delta_max": -0.20, "dte_min": 30, "dte_max": 60},
    "normal":   {"vix_max": 25,  "delta_min": -0.35, "delta_max": -0.15, "dte_min": 21, "dte_max": 60},
    "elevated": {"vix_max": 35,  "delta_min": -0.25, "delta_max": -0.15, "dte_min": 21, "dte_max": 45},
    "crisis":   {"vix_max": 999, "delta_min": -0.20, "delta_max": -0.10, "dte_min": 14, "dte_max": 35},
}

# Action thresholds (Section G)
SCORE_ACCEPT_MIN = 85
SCORE_OK_MIN     = 70
SCORE_WATCH_MIN  = 55
# Below 55 -> Reject

# ---------------------------------------------------------------------------
# Section H — Portfolio risk controls (informational, surfaced in report)
# ---------------------------------------------------------------------------
MAX_CAPITAL_PER_STOCK_PCT  = 0.05
MAX_CAPITAL_PER_SECTOR_PCT = 0.20

# ---------------------------------------------------------------------------
# Network / scraping
# ---------------------------------------------------------------------------
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
PRICE_HISTORY_DAYS = 260   # ~1 trading year for technicals + RS-vs-SPY
RISK_FREE_RATE     = 0.045 # for Black-Scholes if needed; informational
