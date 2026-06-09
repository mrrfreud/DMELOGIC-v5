"""
Opt-in anonymous telemetry.

Strict rules:
  - Disabled by default. Only enabled if Config.telemetry.enabled = True.
  - NEVER sends PHI, patient data, order contents, or user identifiers.
  - Only feature-usage counters and crash signatures (no full tracebacks).
  - Runs on a background thread; failures are silent and never block the UI.

If telemetry is disabled, every function here is a no-op.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import urllib.request
from collections import Counter

from dmelogic.core.config import get_config

logger = logging.getLogger("telemetry")

_event_counts: Counter[str] = Counter()
_lock = threading.Lock()


def record_event(event: str) -> None:
    """
    Increment a counter for `event`. Free-form string, but caller should
    pick from a small fixed vocabulary (e.g. "order.created", "tab.reports").
    """
    if not get_config().telemetry.enabled:
        return
    with _lock:
        _event_counts[event] += 1


def record_crash(exc_type_name: str, exc_msg_first_line: str) -> None:
    """
    Record that a crash happened — only the exception class name and the
    first line of the message. No traceback, no PHI.
    """
    cfg = get_config().telemetry
    if not (cfg.enabled and cfg.include_crash_reports):
        return
    # Hash the message so we don't accidentally exfiltrate PHI from it.
    msg_hash = hashlib.sha256(exc_msg_first_line.encode("utf-8")).hexdigest()[:16]
    record_event(f"crash.{exc_type_name}.{msg_hash}")


def flush() -> None:
    """Send pending counters in a background thread. Safe to call repeatedly."""
    cfg = get_config().telemetry
    if not cfg.enabled or not cfg.endpoint:
        return

    with _lock:
        if not _event_counts:
            return
        payload = dict(_event_counts)
        _event_counts.clear()

    def _send():
        try:
            data = json.dumps({"events": payload}).encode("utf-8")
            req = urllib.request.Request(
                cfg.endpoint,
                data=data,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5).close()
        except Exception as e:
            logger.debug(f"Telemetry flush failed: {e}")

    threading.Thread(target=_send, daemon=True).start()
