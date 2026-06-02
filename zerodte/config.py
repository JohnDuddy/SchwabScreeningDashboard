"""
Configuration for the 0DTE Options Anomaly Scanner.
All thresholds and settings are in one place for easy tuning.
"""

# ── Anomaly Detection Thresholds ──────────────────────────────────────────────

# Option must have at least this volume OR open interest to be evaluated
MIN_VOLUME = 5
MIN_OPEN_INTEREST = 25

# Parity Violation: flag if bid < intrinsic_value - PARITY_THRESHOLD
# Intrinsic for call = max(S - K, 0); for put = max(K - S, 0)
# A $0.10+ violation means you could buy the option and exercise/sell for a profit
PARITY_THRESHOLD = 0.10  # $0.10 below intrinsic → flag; stronger below $0.50

# Stale Pricing: flag if |last_trade - current_mid| / mid > STALE_THRESHOLD
# Option last traded far from where it's currently quoted
STALE_THRESHOLD = 0.30   # 30% deviation from current mid

# Wide Bid-Ask: flag if spread_pct > WIDE_SPREAD_THRESHOLD AND volume >= MIN_VOLUME
# spread_pct = (ask - bid) / mid
WIDE_SPREAD_THRESHOLD = 0.60  # 60% of mid — very wide for a liquid name

# Abnormal IV: flag if IV (as %) is outside these bounds AND volume is meaningful
# On expiration day, ATM options naturally have high IV; we flag extreme cases
IV_HIGH_THRESHOLD_PCT = 500.0  # > 500% annualized IV with volume = suspicious
IV_LOW_THRESHOLD_PCT  = 1.0    # < 1% IV on options with volume = suspiciously cheap
IV_MIN_VOLUME_FOR_FLAG = 50    # only flag IV anomaly if volume >= this

# Unusual Strike Volume: flag if abs(delta) < threshold AND volume >= unusual min
# Far OTM (delta < 0.05) with lots of volume often means informed buying or mis-pricing
UNUSUAL_DELTA_THRESHOLD = 0.05
UNUSUAL_VOLUME_MIN      = 200   # meaningful volume for a near-worthless option

# ── Scan Universe ─────────────────────────────────────────────────────────────
# Liquid underlyings with active 0DTE option markets.
# Index ETFs have 0DTE every trading day; large caps have weekly options (Fri DTE).
# Ordered by typical 0DTE activity (most liquid first).
SCAN_UNIVERSE = [
    # Index ETFs — always have 0DTE
    "SPY", "QQQ", "IWM", "DIA",
    # Mega-cap tech — daily or near-daily options
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD",
    "AVGO", "ORCL", "NFLX", "ADBE", "CRM", "QCOM", "INTC",
    # Financial
    "JPM", "BAC", "GS", "MS", "V", "MA", "C", "WFC",
    # Healthcare & consumer
    "UNH", "LLY", "JNJ", "MRK", "ABBV", "PFE",
    "COST", "HD", "WMT", "MCD", "NKE", "AMGN",
    # Energy & industrials
    "XOM", "CVX", "BA", "CAT",
    # High-vol names with active options
    "PLTR", "COIN", "MSTR", "RIVN", "GME",
]

# Remove any duplicates while preserving order
_seen: set = set()
_deduped = []
for _t in SCAN_UNIVERSE:
    if _t not in _seen:
        _seen.add(_t)
        _deduped.append(_t)
SCAN_UNIVERSE = _deduped

# ── API Settings ──────────────────────────────────────────────────────────────

# Number of strikes to request per side (call/put) per ticker
STRIKES_PER_SIDE = 30

# Seconds to sleep between ticker API calls to avoid rate-limiting
REQUEST_DELAY_SECONDS = 0.4

# API request timeout in seconds
REQUEST_TIMEOUT_SECONDS = 20

# ── Scheduling ────────────────────────────────────────────────────────────────

# Weekday numbers for auto-scan: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri
SCAN_DAYS = [0, 2, 4]  # Monday, Wednesday, Friday

# Target scan time in Central Time (24-hour format)
# 9:45 AM CT = ~15 min after market open, when 0DTE pricing stabilizes
SCAN_HOUR_CT   = 9
SCAN_MINUTE_CT = 45

# Tolerance window (±minutes) for the scheduled scan trigger
SCAN_WINDOW_MINUTES = 5

# ── Module Toggle ─────────────────────────────────────────────────────────────
ENABLED = True
