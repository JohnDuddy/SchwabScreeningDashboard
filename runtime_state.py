"""Small helpers for scan state dictionaries used by the Flask app."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def make_scan_state(
    *,
    include_progress: bool = True,
    include_results: bool = True,
    include_summary: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a fresh background-scan state dictionary.

    The app has several independent background jobs with the same lifecycle:
    idle, running, completed, progress/current ticker, result rows, and error.
    Keeping that shape in one place makes new scan pages less copy-paste prone.
    """
    state: dict[str, Any] = {
        "running": False,
        "started": None,
        "completed": None,
        "error": None,
    }
    if include_progress:
        state.update({"progress": 0, "total": 0, "current": ""})
    if include_results:
        state["results"] = None
    if include_summary:
        state["summary"] = None
    if extra:
        state.update(extra)
    return state


def reset_scan_state(state: dict[str, Any], **overrides: Any) -> None:
    """Mark a scan state as running and clear common transient fields."""
    updates = {
        "running": True,
        "started": datetime.now(),
        "completed": None,
        "error": None,
    }
    if "progress" in state:
        updates.update({"progress": 0, "total": 0, "current": ""})
    if "results" in state:
        updates["results"] = None
    updates.update(overrides)
    state.update(updates)


def completed_iso(state: dict[str, Any]) -> str | None:
    completed = state.get("completed")
    return completed.isoformat() if completed else None
