"""
User preferences backed by QSettings.

Per-user, per-machine persistence for:
  - Main window geometry & last-active tab
  - Theme override (user's explicit choice beats OS detection)
  - Dismissed banner IDs (so X-ing the agent banner sticks for the session)
  - "Don't show today" snooze for the unbilled reminder
"""

from __future__ import annotations

from datetime import date

from PyQt6.QtCore import QSettings, QByteArray

_ORG = "CentralPharmacy"
_APP = "DMELogic"


def _s() -> QSettings:
    return QSettings(_ORG, _APP)


# ── Window geometry ───────────────────────────────────────────────────
def save_window_state(window) -> None:
    s = _s()
    s.setValue("window/geometry", window.saveGeometry())
    if hasattr(window, "saveState"):
        s.setValue("window/state", window.saveState())


def restore_window_state(window) -> bool:
    s = _s()
    geom = s.value("window/geometry", type=QByteArray)
    state = s.value("window/state", type=QByteArray)
    restored = False
    if geom and not geom.isEmpty():
        window.restoreGeometry(geom)
        restored = True
    if state and not state.isEmpty() and hasattr(window, "restoreState"):
        window.restoreState(state)
    return restored


# ── Last active tab ───────────────────────────────────────────────────
def save_active_tab(tab_index: int) -> None:
    _s().setValue("ui/last_tab_index", tab_index)


def load_active_tab() -> int:
    return int(_s().value("ui/last_tab_index", 0))


# ── Theme override ────────────────────────────────────────────────────
def save_theme_override(theme: str | None) -> None:
    """`theme` is 'light', 'dark', or None to clear override (follow OS)."""
    s = _s()
    if theme is None:
        s.remove("ui/theme_override")
    else:
        s.setValue("ui/theme_override", theme)


def load_theme_override() -> str | None:
    return _s().value("ui/theme_override", None, type=str) or None


# ── Dismissed banners ─────────────────────────────────────────────────
def dismiss_banner(banner_id: str) -> None:
    s = _s()
    dismissed = set(s.value("ui/dismissed_banners", [], type=list))
    dismissed.add(banner_id)
    s.setValue("ui/dismissed_banners", list(dismissed))


def is_banner_dismissed(banner_id: str) -> bool:
    dismissed = _s().value("ui/dismissed_banners", [], type=list)
    return banner_id in dismissed


def clear_dismissed_banners() -> None:
    """Call on logout/login to start fresh."""
    _s().remove("ui/dismissed_banners")


# ── Unbilled reminder snooze ──────────────────────────────────────────
def snooze_unbilled_reminder_today() -> None:
    _s().setValue("reminders/unbilled_snoozed_date", date.today().isoformat())


def is_unbilled_reminder_snoozed() -> bool:
    snoozed = _s().value("reminders/unbilled_snoozed_date", "", type=str)
    return snoozed == date.today().isoformat()
