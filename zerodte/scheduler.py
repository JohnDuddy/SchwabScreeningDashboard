"""
Mon/Wed/Fri auto-scheduler for the 0DTE anomaly scanner.

Runs a background daemon thread that wakes every 60 seconds and fires the
scan trigger when the current Central Time matches the configured schedule.
Uses only stdlib — no external scheduler library needed.
"""

import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Callable

from . import config

logger = logging.getLogger(__name__)

_scheduler_thread: threading.Thread | None = None


# ── Central Time helper (no pytz / tzdata required) ──────────────────────────

def _utc_to_ct(dt_utc: datetime) -> datetime:
    """
    Convert a UTC datetime to approximate Central Time.
    CDT (UTC-5): 2nd Sunday March at 02:00 UTC → 1st Sunday November at 02:00 UTC.
    CST (UTC-6): otherwise.
    """
    year = dt_utc.year

    # 2nd Sunday in March
    mar_1 = datetime(year, 3, 1, 2, 0, tzinfo=timezone.utc)
    first_sun_mar = mar_1 + timedelta(days=(6 - mar_1.weekday()) % 7)
    dst_start = first_sun_mar + timedelta(weeks=1)  # 2nd Sunday

    # 1st Sunday in November
    nov_1 = datetime(year, 11, 1, 2, 0, tzinfo=timezone.utc)
    dst_end = nov_1 + timedelta(days=(6 - nov_1.weekday()) % 7)

    offset = timedelta(hours=-5) if dst_start <= dt_utc < dst_end else timedelta(hours=-6)
    return dt_utc + offset


def _now_ct() -> datetime:
    return _utc_to_ct(datetime.now(timezone.utc))


# ── Scheduler logic ───────────────────────────────────────────────────────────

def _scheduler_loop(trigger_fn: Callable) -> None:
    """
    Polls every 60 seconds.  On Mon/Wed/Fri at the configured CT time (±window),
    calls trigger_fn() once per day.
    """
    last_fired_date = None

    while True:
        try:
            now_ct  = _now_ct()
            today   = now_ct.date()
            weekday = now_ct.weekday()   # 0=Mon … 6=Sun
            hour    = now_ct.hour
            minute  = now_ct.minute

            in_window = (
                weekday in config.SCAN_DAYS
                and abs(hour * 60 + minute - config.SCAN_HOUR_CT * 60 - config.SCAN_MINUTE_CT)
                <= config.SCAN_WINDOW_MINUTES
            )

            if in_window and last_fired_date != today:
                logger.info(
                    "0DTE scheduler: firing scan for %s (CT %02d:%02d)",
                    today, hour, minute,
                )
                last_fired_date = today
                try:
                    trigger_fn()
                except Exception as exc:
                    logger.error("0DTE scheduler trigger error: %s", exc)

        except Exception as exc:
            logger.warning("0DTE scheduler loop error: %s", exc)

        time.sleep(60)


def start_scheduler(trigger_fn: Callable) -> None:
    """
    Start the background scheduler thread (idempotent — safe to call multiple times).

    `trigger_fn` will be called on Monday, Wednesday, and Friday at the
    configured Central Time (see config.SCAN_HOUR_CT / SCAN_MINUTE_CT).
    """
    global _scheduler_thread

    if _scheduler_thread and _scheduler_thread.is_alive():
        logger.debug("0DTE scheduler already running — skipping start")
        return

    _scheduler_thread = threading.Thread(
        target=_scheduler_loop,
        args=(trigger_fn,),
        daemon=True,
        name="zerodte-scheduler",
    )
    _scheduler_thread.start()

    day_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
    days_str  = "/".join(day_names[d] for d in config.SCAN_DAYS)
    logger.info(
        "0DTE scheduler started — fires %s at %02d:%02d CT (±%d min)",
        days_str,
        config.SCAN_HOUR_CT,
        config.SCAN_MINUTE_CT,
        config.SCAN_WINDOW_MINUTES,
    )
