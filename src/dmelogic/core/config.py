"""
Application configuration.

Loads from `config.toml` next to the app, falling back to baked-in
defaults. Per-pharmacy customization (logo path, theme colors, default
tabs, feature flags) goes here so it doesn't require code changes.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger("config")

# tomllib is stdlib on 3.11+. Fall back to the tomli backport on 3.10.
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib  # pip install tomli
    except ImportError:
        tomllib = None  # type: ignore[assignment]


@dataclass(frozen=True)
class ThemeConfig:
    default: str = "light"           # "light", "dark", or "system"
    accent_color: str = "#F97316"    # orange-500, used by agent banner
    follow_os_theme: bool = True     # if True, default is overridden by OS


@dataclass(frozen=True)
class SessionConfig:
    idle_timeout_minutes: int = 15
    failed_login_lockout_attempts: int = 5
    failed_login_lockout_minutes: int = 10
    remember_window_geometry: bool = True


@dataclass(frozen=True)
class ReminderConfig:
    show_unbilled_at_login: bool = True
    refresh_interval_minutes: int = 30    # 0 disables periodic refresh
    snooze_until_next_login: bool = True  # honor "don't show again today"


@dataclass(frozen=True)
class BackupConfig:
    backup_before_migrations: bool = True
    keep_last_n_backups: int = 7
    log_retention_days: int = 30


@dataclass(frozen=True)
class TelemetryConfig:
    enabled: bool = False          # OPT-IN. No PHI ever.
    endpoint: str = ""
    include_crash_reports: bool = False


@dataclass(frozen=True)
class NovaConfig:
    # Master switch for the entire Nova AI subsystem. Set False (or ship the
    # Nova-less edition) to disable the assistant, background host, wake
    # listener, RingCentral, and the agent-order intake in one place.
    enabled: bool = True
    ensure_background_services: bool = True
    wake_listener_enabled: bool = True


@dataclass(frozen=True)
class Config:
    pharmacy_name: str = "Central Pharmacy"
    pharmacy_logo_path: str = ""
    theme: ThemeConfig = field(default_factory=ThemeConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    reminders: ReminderConfig = field(default_factory=ReminderConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    nova: NovaConfig = field(default_factory=NovaConfig)

    @classmethod
    def load(cls, path: Path | None) -> "Config":
        """
        Load config from a TOML file. Missing file or any error → defaults.
        Never raises — config problems should never crash startup.
        """
        if path is None or not path.exists():
            logger.info("No config file found; using defaults.")
            return cls()

        if tomllib is None:
            logger.warning("tomllib/tomli not available; cannot parse config.toml. Using defaults.")
            return cls()

        try:
            with path.open("rb") as f:
                raw = tomllib.load(f)
        except Exception as e:
            logger.warning(f"Could not parse config {path}: {e}; using defaults.")
            return cls()

        try:
            return cls(
                pharmacy_name=raw.get("pharmacy_name", "Central Pharmacy"),
                pharmacy_logo_path=raw.get("pharmacy_logo_path", ""),
                theme=ThemeConfig(**raw.get("theme", {})),
                session=SessionConfig(**raw.get("session", {})),
                reminders=ReminderConfig(**raw.get("reminders", {})),
                backup=BackupConfig(**raw.get("backup", {})),
                telemetry=TelemetryConfig(**raw.get("telemetry", {})),
                nova=NovaConfig(**raw.get("nova", {})),
            )
        except TypeError as e:
            # Unknown key in TOML, etc.
            logger.warning(f"Config schema mismatch in {path}: {e}; using defaults.")
            return cls()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Module-level singleton populated by load_config() at startup.
_CONFIG: Config | None = None


def load_config(path: Path | None) -> Config:
    global _CONFIG
    _CONFIG = Config.load(path)
    return _CONFIG


def get_config() -> Config:
    """Returns the loaded config, or defaults if load_config() wasn't called."""
    return _CONFIG or Config()
