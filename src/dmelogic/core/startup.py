"""
Startup orchestrator.

Replaces the procedural main() with a small class whose methods each do
one thing. Easier to test, easier to reorder, easier to skip specific
steps in dev (e.g. --skip-migrations).

app.py subclasses this to wire in DMELogic-specific UI steps that belong
in the application layer, not the generic orchestrator.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QMessageBox

from dmelogic.core.config import Config, load_config
from dmelogic.core.constants import FLAG_NO_SPLASH, FLAG_SECONDARY_WINDOW
from dmelogic.core.crash_reporter import install_crash_reporter
from dmelogic.core.logging_setup import setup_logging, timed_step
from dmelogic.core.single_instance import acquire_or_signal
from dmelogic.ui.splash import create_splash, update_splash
from dmelogic.ui.theme_detect import detect_os_theme
from dmelogic.ui.prefs import load_theme_override

logger = logging.getLogger("startup")


@dataclass
class StartupContext:
    """State shared across startup steps."""
    app: QApplication
    config: Config
    is_secondary: bool
    splash: Any | None = None
    ocr_status: Any | None = None
    session: Any | None = None


class Startup:
    """
    Runs the startup sequence. Caller does:

        s = Startup(project_root)
        s.run()  # blocks until app exits
    """

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.is_secondary = FLAG_SECONDARY_WINDOW in sys.argv

    # ── Steps (in order) ───────────────────────────────────────────────
    def run(self) -> int:
        self._setup_logging_and_config()
        self._install_crash_reporter()

        app = self._create_qapplication()

        # Allow multiple concurrent DMELogic windows/processes.
        # Keep --window-instance behavior unchanged for existing flows.
        if not self.is_secondary:
            logger.info("Multi-instance mode enabled; skipping single-instance enforcement.")

        ctx = StartupContext(
            app=app,
            config=self.config,
            is_secondary=self.is_secondary,
            splash=self._show_splash(app) if not self.is_secondary else None,
        )

        self._apply_theme(ctx)
        self._init_databases(ctx)
        self._init_services(ctx)
        self._configure_ocr(ctx)
        self._authenticate(ctx)
        win = self._build_window(ctx)
        self._post_window_setup(ctx, win)

        return app.exec()

    # ── Step implementations ───────────────────────────────────────────
    def _setup_logging_and_config(self) -> None:
        from dmelogic.paths import get_logs_dir
        logs_dir = get_logs_dir()
        # Use defaults for retention until config is loaded.
        setup_logging(logs_dir, json_logs=False, retention_days=30)

        config_path = self.project_root / "config.toml"
        self.config = load_config(config_path)
        logger.info(f"=== DMELogic startup ({'secondary' if self.is_secondary else 'primary'}) ===")
        logger.info(f"Pharmacy: {self.config.pharmacy_name}")

    def _install_crash_reporter(self) -> None:
        from dmelogic.paths import get_logs_dir
        crashes_dir = get_logs_dir() / "crashes"

        def on_crash(crash_path, tb):
            # Telemetry hook — only fires if telemetry+crashes both opted in.
            try:
                from dmelogic.services.telemetry import record_crash
                first_line = tb.strip().splitlines()[-1] if tb.strip() else ""
                exc_name = first_line.split(":")[0] if ":" in first_line else "Unknown"
                record_crash(exc_name, first_line)
            except Exception:
                pass

        install_crash_reporter(crashes_dir, show_dialog=True, on_crash=on_crash)

    def _create_qapplication(self) -> QApplication:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.Round
        )
        app = QApplication(sys.argv)
        font = app.font()
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        app.setFont(font)
        return app

    def _enforce_single_instance(self, app: QApplication):
        def raise_existing():
            # Bring the primary window to the foreground.
            for w in app.topLevelWidgets():
                if w.isWindow():
                    w.showNormal()
                    w.raise_()
                    w.activateWindow()
                    break
        return acquire_or_signal(raise_existing)

    def _show_splash(self, app: QApplication):
        if FLAG_NO_SPLASH in sys.argv:
            return None
        logo = Path(self.config.pharmacy_logo_path) if self.config.pharmacy_logo_path else None
        splash = create_splash(logo)
        update_splash(splash, "Starting…")
        return splash

    def _apply_theme(self, ctx: StartupContext) -> None:
        if ctx.splash:
            update_splash(ctx.splash, "Loading theme…")

        # Theme resolution: explicit user override → OS detection → config default
        theme = load_theme_override()
        if theme is None and ctx.config.theme.follow_os_theme:
            theme = detect_os_theme()
        if theme is None:
            theme = ctx.config.theme.default

        try:
            from dmelogic.dme_theme import apply_theme as apply_dme_theme
            apply_dme_theme(ctx.app, theme)
        except TypeError:
            # dme_theme.apply_theme may only accept one argument in older builds.
            try:
                from dmelogic.dme_theme import apply_theme as apply_dme_theme
                apply_dme_theme(ctx.app)
            except Exception as e:
                logger.warning(f"Could not apply DME theme: {e}")
        except Exception as e:
            logger.warning(f"Could not apply DME theme '{theme}': {e}")

    def _init_databases(self, ctx: StartupContext) -> None:
        if ctx.is_secondary:
            return
        if ctx.splash:
            update_splash(ctx.splash, "Backing up databases…")

        from dmelogic.paths import db_dir
        from dmelogic.db.backup import snapshot_databases
        from dmelogic.db.audit import init_audit_db
        from dmelogic.security.lockout import init_lockout_db

        try:
            if ctx.config.backup.backup_before_migrations:
                with timed_step("db_backup"):
                    snapshot_databases(
                        db_dir(),
                        db_dir() / "backups",
                        keep_last=ctx.config.backup.keep_last_n_backups,
                    )
        except Exception as e:
            logger.warning(f"DB backup failed (continuing): {e}")

        if ctx.splash:
            update_splash(ctx.splash, "Initializing authentication…")
        try:
            from dmelogic.db.users import init_users_db
            with timed_step("init_users_db"):
                init_users_db()
        except Exception as e:
            logger.exception(f"init_users_db failed: {e}")

        try:
            with timed_step("init_audit_db"):
                init_audit_db(db_dir() / "audit.db")
            with timed_step("init_lockout_db"):
                init_lockout_db(db_dir() / "auth.db")
        except Exception as e:
            logger.warning(f"audit/lockout init failed: {e}")

        if ctx.splash:
            update_splash(ctx.splash, "Running database migrations…")
        try:
            from dmelogic.db.migrations import run_all_migrations
            with timed_step("migrations"):
                run_all_migrations()
        except Exception as e:
            logger.exception(f"Migrations failed: {e}")

    def _init_services(self, ctx: StartupContext) -> None:
        if ctx.is_secondary:
            return
        if ctx.splash:
            update_splash(ctx.splash, "Checking services…")
        try:
            from dmelogic.paths import get_project_root
            from dmelogic.services.nova_background import ensure_nova_background_services
            from dmelogic.services.nova_wake_listener import ensure_nova_wake_listener

            with timed_step("nova_background_services"):
                ensure_nova_background_services(
                    get_project_root(),
                    enabled=ctx.config.nova.ensure_background_services,
                )
            with timed_step("nova_wake_listener"):
                ensure_nova_wake_listener(
                    enabled=ctx.config.nova.wake_listener_enabled,
                )
        except Exception as e:
            logger.warning(f"Nova background host check failed: {e}")

        try:
            from dmelogic.services.service_manager import (
                is_server_mode, ensure_service_running, get_service_status,
            )
            if is_server_mode():
                with timed_step("service_check"):
                    status = get_service_status()
                    if status != "RUNNING":
                        ensure_service_running()
        except Exception as e:
            logger.warning(f"Service check failed: {e}")

    def _configure_ocr(self, ctx: StartupContext) -> None:
        if ctx.splash:
            update_splash(ctx.splash, "Configuring OCR…")
        try:
            from dmelogic.ocr_status import ensure_ocr_configured
            with timed_step("ocr_config"):
                ctx.ocr_status = ensure_ocr_configured()
        except Exception as e:
            logger.warning(f"OCR config failed: {e}")

    def _authenticate(self, ctx: StartupContext) -> None:
        if ctx.splash:
            update_splash(ctx.splash, "Awaiting login…")
            ctx.splash.hide()  # don't fight with the login dialog for focus

        from dmelogic.ui.login_dialog import LoginDialog
        from dmelogic.security.auth import get_session

        dialog = LoginDialog()
        if dialog.exec() != LoginDialog.DialogCode.Accepted:
            logger.info("Login cancelled. Exiting.")
            sys.exit(0)

        session = get_session()
        if not session or not session.is_authenticated:
            QMessageBox.critical(None, "Authentication Error", "No valid session established.")
            sys.exit(1)

        ctx.session = session
        logger.info(f"User '{session.username}' logged in.")

    def _build_window(self, ctx: StartupContext):
        if ctx.splash:
            ctx.splash.show()
            update_splash(ctx.splash, "Loading main window…")

        from dmelogic.ui import create_main_window
        win = create_main_window()
        return win

    def _post_window_setup(self, ctx: StartupContext, win) -> None:
        from dmelogic.ui.prefs import restore_window_state
        from dmelogic.ui.shortcuts import install_tab_shortcuts
        from dmelogic.ui.reminders import schedule_unbilled_reminder
        from dmelogic.paths import db_dir

        # OCR limited-features warning (primary only)
        if not ctx.is_secondary and ctx.ocr_status is not None:
            if not getattr(ctx.ocr_status, "fully_operational", True) and \
               not getattr(ctx.ocr_status, "ocr_available", True):
                QMessageBox.warning(None, "OCR Features Limited", ctx.ocr_status.get_user_message())

        # Restore geometry / last tab
        if ctx.config.session.remember_window_geometry:
            restore_window_state(win)

        # Tab shortcuts (Ctrl+1..9)
        tabs = getattr(win, "main_tabs", None) or getattr(win, "tabs", None)
        if tabs is not None:
            install_tab_shortcuts(win, tabs)

        # Show window and dismiss splash
        win.show()
        if ctx.splash:
            ctx.splash.finish(win)

        # Schedule unbilled reminder (primary only)
        if not ctx.is_secondary:
            schedule_unbilled_reminder(
                win,
                db_dir() / "orders.db",
                refresh_interval_minutes=ctx.config.reminders.refresh_interval_minutes,
                honor_snooze=ctx.config.reminders.snooze_until_next_login,
            )

        # Idle lock (primary only)
        if not ctx.is_secondary and ctx.config.session.idle_timeout_minutes > 0:
            from dmelogic.security.idle_lock import IdleLockManager
            from dmelogic.ui.login_dialog import LoginDialog

            def on_idle():
                dialog = LoginDialog()
                dialog.setWindowTitle("Session Locked — Re-authenticate")
                if dialog.exec() == LoginDialog.DialogCode.Accepted:
                    win._idle_lock.notify_activity()
                else:
                    ctx.app.quit()

            win._idle_lock = IdleLockManager(
                ctx.app,
                ctx.config.session.idle_timeout_minutes,
                on_idle,
                parent=win,
            )
