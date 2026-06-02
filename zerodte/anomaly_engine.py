"""
0DTE Options Anomaly Detection Engine.

Parses Schwab options chain data and flags pricing anomalies.
Each check is independent; a single contract can trigger multiple flags.
The composite Anomaly Score (0-100) drives the ranking.
"""

import logging
from typing import Any

from . import config

logger = logging.getLogger(__name__)


# ── IV normalization ───────────────────────────────────────────────────────────

def _normalize_iv(raw_iv: float) -> float:
    """
    Convert Schwab's volatility field to percentage (e.g. 150.0 = 150% IV).

    Schwab's chain API is inconsistent: some responses use decimal fraction
    (1.50 = 150%), others use the percentage directly (150.0 = 150%).
    Heuristic: values < 20.0 are assumed to be fractional.
    """
    if raw_iv is None or raw_iv != raw_iv:  # None or NaN
        return 0.0
    raw_iv = float(raw_iv)
    return raw_iv * 100.0 if 0.0 < raw_iv < 20.0 else raw_iv


# ── Chain parsing ─────────────────────────────────────────────────────────────

def _parse_contracts(chain_data: dict) -> list[dict]:
    """
    Flatten a Schwab chain response into a list of normalized contract dicts.
    Only returns contracts with daysToExpiration == 0 (true 0DTE).
    """
    symbol          = chain_data.get("symbol", "")
    underlying_price = float(chain_data.get("underlyingPrice") or 0.0)
    contracts: list[dict] = []

    for side_key in ("callExpDateMap", "putExpDateMap"):
        option_type = "CALL" if side_key == "callExpDateMap" else "PUT"
        for exp_key, strikes_map in chain_data.get(side_key, {}).items():
            # exp_key format: "2024-01-05:0"  (date:daysToExpiration)
            parts = exp_key.split(":")
            exp_date_str = parts[0]
            try:
                dte = int(parts[1])
            except (IndexError, ValueError):
                dte = -1

            if dte != 0:
                continue  # skip non-0DTE expirations

            for strike_str, option_list in strikes_map.items():
                strike = float(strike_str)
                for opt in option_list:
                    bid    = float(opt.get("bid",         0) or 0)
                    ask    = float(opt.get("ask",         0) or 0)
                    last   = float(opt.get("last",        0) or 0)
                    mark   = float(opt.get("mark",        0) or 0)
                    volume = int(opt.get("totalVolume",   0) or 0)
                    oi     = int(opt.get("openInterest",  0) or 0)
                    delta  = float(opt.get("delta",       0) or 0)
                    theo   = float(opt.get("theoreticalOptionValue", 0) or 0)
                    intrinsic_raw = float(opt.get("intrinsicValue", 0) or 0)
                    iv_raw = float(opt.get("volatility",  0) or 0)
                    iv_pct = _normalize_iv(iv_raw)

                    # Prefer mark for mid; fall back to bid/ask average
                    if mark > 0:
                        mid = mark
                    elif bid > 0 or ask > 0:
                        mid = (bid + ask) / 2.0
                    else:
                        mid = last

                    contracts.append({
                        "underlying":       symbol,
                        "underlying_price": underlying_price,
                        "option_type":      option_type,
                        "symbol":           opt.get("symbol", ""),
                        "strike":           strike,
                        "expiration":       exp_date_str,
                        "bid":              bid,
                        "ask":              ask,
                        "last":             last,
                        "mid":              mid,
                        "volume":           volume,
                        "open_interest":    oi,
                        "iv_pct":           iv_pct,
                        "delta":            delta,
                        "theoretical_value": theo,
                        "intrinsic_value":  intrinsic_raw,
                        "in_the_money":     bool(opt.get("inTheMoney", False)),
                    })
    return contracts


def _calc_intrinsic(option_type: str, strike: float, underlying_price: float) -> float:
    """True intrinsic value: the amount in-the-money."""
    if option_type == "CALL":
        return max(underlying_price - strike, 0.0)
    return max(strike - underlying_price, 0.0)


# ── Anomaly checks ─────────────────────────────────────────────────────────────

def _check_parity(c: dict) -> tuple[list[str], int, dict]:
    """
    Parity Violation: ITM option trading below intrinsic value.
    A bid < intrinsic - threshold means you could theoretically buy and
    immediately exercise/sell for a risk-free profit.
    """
    flags, score, details = [], 0, {}
    intrinsic = _calc_intrinsic(c["option_type"], c["strike"], c["underlying_price"])
    if intrinsic <= 0:
        return flags, score, details

    bid = c["bid"]
    if bid <= 0:
        return flags, score, details

    parity_gap = intrinsic - bid
    if parity_gap > config.PARITY_THRESHOLD:
        # Score scales with size of violation: 10¢ gap → ~25 pts, 50¢ → ~60 pts
        raw = parity_gap / config.PARITY_THRESHOLD
        score = min(65, int(raw * 22))
        flags.append("PARITY")
        details["parity_gap"]  = round(parity_gap, 4)
        details["intrinsic"]   = round(intrinsic, 4)

    return flags, score, details


def _check_stale(c: dict) -> tuple[list[str], int, dict]:
    """
    Stale Pricing: last trade price far from current bid/ask midpoint.
    Indicates the option traded at a stale price before quotes moved.
    """
    flags, score, details = [], 0, {}
    mid  = c["mid"]
    last = c["last"]
    if mid <= 0 or last <= 0 or c["volume"] == 0:
        return flags, score, details

    stale_gap = abs(last - mid) / mid
    if stale_gap > config.STALE_THRESHOLD:
        score = min(40, int(stale_gap * 40))
        flags.append("STALE")
        details["stale_gap_pct"] = round(stale_gap * 100, 1)
        details["last_vs_mid"]   = round(last - mid, 4)

    return flags, score, details


def _check_iv(c: dict) -> tuple[list[str], int, dict]:
    """
    Abnormal Implied Volatility.
    Extremely high IV with real volume suggests mispricing or unusual demand.
    Near-zero IV on a traded option suggests it's almost free — maybe undervalued.
    """
    flags, score, details = [], 0, {}
    iv_pct = c["iv_pct"]
    volume = c["volume"]

    if volume < config.IV_MIN_VOLUME_FOR_FLAG:
        return flags, score, details

    if iv_pct > config.IV_HIGH_THRESHOLD_PCT:
        # Excess above threshold contributes, capped
        excess = iv_pct - config.IV_HIGH_THRESHOLD_PCT
        score  = min(45, int(excess / 50))
        flags.append("HIGH_IV")
        details["iv_pct"] = round(iv_pct, 1)

    elif 0 < iv_pct < config.IV_LOW_THRESHOLD_PCT and c["mid"] > 0.01:
        score = 30
        flags.append("LOW_IV")
        details["iv_pct"] = round(iv_pct, 2)

    return flags, score, details


def _check_spread(c: dict) -> tuple[list[str], int, dict]:
    """
    Wide Bid-Ask Spread with Volume.
    A wide spread on a liquid underlying with real volume can indicate pricing
    confusion or a market maker pulling quotes.
    """
    flags, score, details = [], 0, {}
    bid, ask, mid = c["bid"], c["ask"], c["mid"]
    if bid <= 0 or ask <= 0 or mid <= 0 or c["volume"] < config.MIN_VOLUME:
        return flags, score, details

    spread     = ask - bid
    spread_pct = spread / mid
    if spread_pct > config.WIDE_SPREAD_THRESHOLD:
        score = min(30, int(spread_pct * 20))
        flags.append("WIDE_SPREAD")
        details["spread"]     = round(spread, 4)
        details["spread_pct"] = round(spread_pct * 100, 1)

    return flags, score, details


def _check_unusual_volume(c: dict) -> tuple[list[str], int, dict]:
    """
    Unusual Volume on Far-OTM / Near-Worthless Strikes.
    High volume on options with delta < 0.05 (almost certainly expiring worthless)
    often indicates informed positioning or a mis-priced lottery ticket.
    """
    flags, score, details = [], 0, {}
    volume = c["volume"]
    delta  = abs(c["delta"])
    if volume < config.UNUSUAL_VOLUME_MIN:
        return flags, score, details

    if delta < config.UNUSUAL_DELTA_THRESHOLD:
        ratio = volume / config.UNUSUAL_VOLUME_MIN
        score = min(45, int(ratio * 10))
        flags.append("UNUSUAL_VOLUME")
        details["delta"]  = round(c["delta"], 4)

    return flags, score, details


# ── Main entry point ──────────────────────────────────────────────────────────

def detect_anomalies(chain_data: dict) -> list[dict]:
    """
    Run all anomaly checks against a single ticker's 0DTE options chain.

    Returns a list of anomaly dicts (one per flagged contract), sorted by
    Anomaly Score descending. Returns an empty list if no anomalies are found
    or if the chain has no 0DTE contracts.
    """
    contracts = _parse_contracts(chain_data)
    if not contracts:
        return []

    anomalies: list[dict] = []

    for c in contracts:
        # Gate: skip thin options with no volume and no OI
        if c["volume"] < config.MIN_VOLUME and c["open_interest"] < config.MIN_OPEN_INTEREST:
            continue

        all_flags:   list[str] = []
        total_score: int       = 0
        all_details: dict      = {}

        for check_fn in (_check_parity, _check_stale, _check_iv, _check_spread, _check_unusual_volume):
            flags, score, details = check_fn(c)
            all_flags.extend(flags)
            total_score += score
            all_details.update(details)

        if not all_flags:
            continue

        total_score = min(100, total_score)

        anomalies.append({
            # Identity
            "underlying":       c["underlying"],
            "underlying_price": round(c["underlying_price"], 2),
            "option_type":      c["option_type"],
            "symbol":           c["symbol"],
            "strike":           c["strike"],
            "expiration":       c["expiration"],
            # Current quotes
            "bid":              c["bid"],
            "ask":              c["ask"],
            "last":             c["last"],
            "mid":              round(c["mid"], 4),
            # Activity
            "volume":           c["volume"],
            "open_interest":    c["open_interest"],
            # Greeks / theo
            "iv_pct":           round(c["iv_pct"], 1),
            "delta":            round(c["delta"], 4),
            "intrinsic_value":  round(c["intrinsic_value"], 4),
            "theoretical_value": round(c["theoretical_value"], 4),
            # Anomaly result
            "flags":            all_flags,
            "flag_str":         ", ".join(all_flags),
            "anomaly_score":    total_score,
            # Per-check details (flattened)
            **all_details,
        })

    anomalies.sort(key=lambda x: x["anomaly_score"], reverse=True)
    return anomalies
