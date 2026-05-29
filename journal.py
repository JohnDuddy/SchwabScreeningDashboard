"""
Trade journal with JSON-based persistence.

Stores CSP trades with open/close tracking, P&L calculation, and stats.
"""

import json
import os
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional

JOURNAL_FILE = "csp_journal.json"


def _load() -> List[Dict[str, Any]]:
    if os.path.exists(JOURNAL_FILE):
        try:
            with open(JOURNAL_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def _save(trades: List[Dict[str, Any]]) -> None:
    with open(JOURNAL_FILE, "w") as f:
        json.dump(trades, f, indent=2, default=str)


def add_trade(trade_dict: Dict[str, Any]) -> str:
    """
    Open a new trade. Returns the trade ID.

    Expected fields: ticker, strike, expiration, premium, contracts, notes
    """
    trades = _load()
    trade_id = str(uuid.uuid4())[:8]
    trade = {
        "id": trade_id,
        "ticker": trade_dict.get("ticker", "").upper().strip(),
        "strike": float(trade_dict.get("strike", 0)),
        "expiration": trade_dict.get("expiration", ""),
        "premium": float(trade_dict.get("premium", 0)),
        "contracts": int(trade_dict.get("contracts", 1)),
        "notes": trade_dict.get("notes", ""),
        "opened": datetime.now().isoformat(),
        "status": "open",
        "closed": None,
        "close_premium": None,
        "close_reason": None,
        "pnl": None,
    }
    # Calculate total premium received
    trade["total_premium_received"] = trade["premium"] * trade["contracts"] * 100
    trade["cash_required"] = trade["strike"] * trade["contracts"] * 100
    trades.append(trade)
    _save(trades)
    return trade_id


def close_trade(trade_id: str, close_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Close a trade. Returns updated trade dict or None if not found.

    close_data: close_premium (per share), close_reason (expired/bought_back/assigned)
    """
    trades = _load()
    for trade in trades:
        if trade["id"] == trade_id and trade["status"] == "open":
            close_premium = float(close_data.get("close_premium", 0))
            close_reason = close_data.get("close_reason", "expired")

            trade["status"] = "closed"
            trade["closed"] = datetime.now().isoformat()
            trade["close_premium"] = close_premium
            trade["close_reason"] = close_reason

            # P&L = premium received - premium paid to close (both per contract * 100 * contracts)
            premium_received = trade["premium"] * trade["contracts"] * 100
            premium_paid = close_premium * trade["contracts"] * 100
            trade["pnl"] = round(premium_received - premium_paid, 2)

            _save(trades)
            return trade
    return None


def get_all_trades() -> List[Dict[str, Any]]:
    """Return all trades, newest first."""
    trades = _load()
    trades.sort(key=lambda t: t.get("opened", ""), reverse=True)
    return trades


def get_stats() -> Dict[str, Any]:
    """Compute journal statistics."""
    trades = _load()
    total = len(trades)
    open_trades = [t for t in trades if t["status"] == "open"]
    closed_trades = [t for t in trades if t["status"] == "closed"]

    wins = losses = assigned = expired = bought_back = 0
    total_pnl = return_sum = 0.0
    return_count = 0

    for t in closed_trades:
        pnl = t.get("pnl") or 0
        total_pnl += pnl
        if pnl > 0:
            wins += 1
        else:
            losses += 1
        reason = t.get("close_reason", "")
        if reason == "assigned":
            assigned += 1
        elif reason == "expired":
            expired += 1
        elif reason == "bought_back":
            bought_back += 1
        cash = t.get("cash_required") or 0
        if cash > 0:
            return_sum += pnl / cash * 100
            return_count += 1

    avg_return = return_sum / return_count if return_count else 0.0

    return {
        "total": total,
        "open": len(open_trades),
        "closed": len(closed_trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(closed_trades) * 100, 1) if closed_trades else 0.0,
        "total_pnl": round(total_pnl, 2),
        "avg_return_pct": round(avg_return, 2),
        "assigned": assigned,
        "expired": expired,
        "bought_back": bought_back,
    }
