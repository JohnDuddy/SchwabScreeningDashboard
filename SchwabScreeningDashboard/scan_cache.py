"""
Scan result persistence — save/load JSON cache files for all scan types.

Follows the same pattern as journal.py (JSON file persistence).
"""

import json
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

CACHE_FILES = {
    "csp":       ".cache_csp.json",
    "momentum":  ".cache_momentum.json",
    "dashboard": ".cache_dashboard.json",
    "options":   ".cache_options.json",
    "badass":    ".cache_badass.json",
}


def save_scan(scan_type: str, data: dict) -> None:
    """Write scan results to the appropriate cache file with a timestamp."""
    filename = CACHE_FILES.get(scan_type)
    if not filename:
        logger.warning("Unknown scan type for cache: %s", scan_type)
        return
    try:
        payload = {
            "timestamp": datetime.now().isoformat(),
            "data": data,
        }
        with open(filename, "w") as f:
            json.dump(payload, f, indent=2, default=str)
    except Exception as e:
        logger.warning("Failed to save %s cache: %s", scan_type, e)


def load_scan(scan_type: str) -> dict | None:
    """Return cached data or None if no cache exists."""
    filename = CACHE_FILES.get(scan_type)
    if not filename:
        return None
    if not os.path.exists(filename):
        return None
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("Failed to load %s cache: %s", scan_type, e)
        return None
