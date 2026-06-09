"""
Logging configuration.

- File handler uses size-based rotation (10MB x 10 files).
- Optional JSON output for downstream tooling.
- Old daily log files (legacy format) are pruned after N days.
- `timed_step()` context manager logs duration of slow startup steps.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path


class JsonFormatter(logging.Formatter):
    """Minimal JSON-lines formatter — easy to grep, pipe, or ship."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(
    logs_dir: Path,
    *,
    json_logs: bool = False,
    retention_days: int = 30,
) -> None:
    """
    Configure root logging with rotation and optional JSON output.

    Also prunes legacy daily-named log files older than `retention_days`.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)

    log_file = logs_dir / "dmelogic.log"
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )

    if json_logs:
        formatter: logging.Formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
        )

    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Clear existing handlers so re-init in tests doesn't double up.
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    _prune_old_logs(logs_dir, retention_days)


def _prune_old_logs(logs_dir: Path, retention_days: int) -> None:
    """Delete legacy startup_YYYYMMDD.log files older than retention."""
    if retention_days <= 0:
        return
    cutoff = datetime.now() - timedelta(days=retention_days)
    for f in logs_dir.glob("startup_*.log"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime < cutoff:
                f.unlink()
        except OSError:
            pass  # don't let log cleanup ever crash startup


@contextmanager
def timed_step(name: str, logger: logging.Logger | None = None):
    """
    Context manager that logs how long a block took. Useful for finding
    slow startup steps.

        with timed_step("migrations"):
            run_all_migrations()
    """
    log = logger or logging.getLogger("perf")
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        log.info(f"[perf] {name}: {elapsed_ms:.0f}ms")
