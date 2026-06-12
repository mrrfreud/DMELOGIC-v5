"""
DMELogic entrypoint.

All real startup logic lives in `dmelogic.core.startup.Startup`. This
file subclasses it to wire in DMELogic-specific UI steps (permission
gating, agent banner, window title, calendar styling) that belong in the
application layer.

Long-term: this file should be the only thing PyInstaller's spec needs to
reference. The `_ensure_venv` dance below is a development-time
convenience that becomes irrelevant once the app is bundled.
"""

import os
import sys
from pathlib import Path

# Fix Unicode output on Windows consoles that default to cp1252
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _ensure_venv() -> None:
    """
    Development convenience: if running source-distributed Python without
    the project venv, re-exec under it. Becomes a no-op when bundled
    (PyInstaller sets sys.frozen).
    """
    try:
        if getattr(sys, "frozen", False):
            return

        module_dir = os.path.dirname(os.path.abspath(__file__))
        candidate_roots = [
            module_dir,
            os.path.dirname(module_dir),
            os.path.dirname(os.path.dirname(module_dir)),
            os.path.dirname(os.path.dirname(os.path.dirname(module_dir))),
        ]

        venv_python = None
        for root_dir in candidate_roots:
            for venv_name in (".venv", "venv"):
                win_path = os.path.join(root_dir, venv_name, "Scripts", "python.exe")
                posix_path = os.path.join(root_dir, venv_name, "bin", "python")
                if os.path.exists(win_path):
                    venv_python = win_path
                    break
                if os.path.exists(posix_path):
                    venv_python = posix_path
                    break
            if venv_python:
                break

        if not venv_python:
            return

        current = os.path.normpath(sys.executable)
        if os.path.normpath(venv_python) == current:
            return

        args = [venv_python, os.path.abspath(__file__)] + sys.argv[1:]
        os.execv(venv_python, args)
    except Exception as e:
        print(f"[_ensure_venv] Failed to re-exec under venv: {e}", file=sys.stderr)


# ── Helper UI functions ────────────────────────────────────────────────
# These have all-local imports so they're safe to define before _ensure_venv.

_TAB_TEXT_TO_OBJECT_NAME = {
    "Document Viewer": "document_viewer",
    "Patients": "patients",
    "Orders": "orders",
    "DME Inventory": "inventory",
    "Inventory": "inventory",
    "Billing": "billing",
    "Reports": "reports",
    "Dashboard": "dashboard",
}


def _set_tab_object_names(win) -> None:
    """
    Set stable objectName values on every main-tab widget so permission and
    agent-mode logic can match on them instead of fragile display text.
    """
    tabs = _get_main_tab_widget(win)
    if tabs is None:
        return
    for i in range(tabs.count()):
        widget = tabs.widget(i)
        if widget is None:
            continue
        if widget.objectName():
            continue  # already named — don't overwrite
        text = tabs.tabText(i).strip()
        # Strip leading emoji (any non-ASCII char up to a space)
        import re
        plain = re.sub(r"^[^\x00-\x7F\s]+\s*", "", text).strip()
        name = _TAB_TEXT_TO_OBJECT_NAME.get(text) or _TAB_TEXT_TO_OBJECT_NAME.get(plain)
        if name:
            widget.setObjectName(name)


def _get_main_tab_widget(win):
    """
    Resolve the main QTabWidget on the window.

    Handles the 'main_tabs' / 'tabs' naming drift that has existed across
    different parts of the codebase.
    """
    for attr in ("main_tabs", "tabs"):
        tabs = getattr(win, attr, None)
        if tabs is not None:
            return tabs
    return None


def _apply_agent_mode_ui(win) -> None:
    """
    When the logged-in user is flagged as an agent, apply visual differences:
    1. Show an orange 'Agent Mode' banner at the top of the window.
    2. Hide tabs the agent doesn't need (Billing, Reports, Inventory, etc.).
    The agent still logs in normally — they just see a streamlined view.
    """
    import logging
    from dmelogic.security.auth import is_current_user_agent, get_session
    from dmelogic.ui.prefs import is_banner_dismissed, dismiss_banner
    from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton
    from PyQt6.QtCore import Qt

    logger = logging.getLogger("agent_mode")

    if not is_current_user_agent():
        return

    session = get_session()
    logger.info(f"Applying Agent Mode UI for user '{session.username}'")

    # ── 1. Orange banner ──────────────────────────────────────────
    banner = QFrame()
    banner.setObjectName("AgentModeBanner")
    banner.setStyleSheet("""
        QFrame#AgentModeBanner {
            background-color: #F97316;
            border-radius: 6px;
            padding: 8px 16px;
            margin: 4px 8px;
        }
    """)
    banner_layout = QHBoxLayout(banner)
    banner_layout.setContentsMargins(12, 6, 12, 6)

    icon_label = QLabel("\U0001f916")
    icon_label.setStyleSheet("font-size: 20px; background: transparent;")

    text_label = QLabel(
        f"AGENT MODE — You are signed in as <b>{session.username}</b>.  "
        "Orders you create will go to <b>Pending Approval</b> before entering the live list."
    )
    text_label.setStyleSheet("""
        color: #1a1a1a;
        font-size: 13px;
        font-weight: 600;
        background: transparent;
    """)
    text_label.setWordWrap(True)

    # Dismiss button — hides the banner and remembers the choice for this session.
    close_btn = QToolButton()
    close_btn.setText("✕")
    close_btn.setStyleSheet(
        "QToolButton { background: transparent; color: #1a1a1a; font-weight: bold;"
        " border: none; padding: 2px 6px; font-size: 14px; }"
        "QToolButton:hover { color: #000; }"
    )
    close_btn.clicked.connect(lambda: (banner.hide(), dismiss_banner("agent_mode")))

    banner_layout.addWidget(icon_label)
    banner_layout.addWidget(text_label, 1)
    banner_layout.addStretch()
    banner_layout.addWidget(close_btn)

    # Honor a previous dismissal from this session.
    if is_banner_dismissed("agent_mode"):
        banner.setVisible(False)

    if hasattr(win, "main_layout"):
        inserted = False
        backup_banner = getattr(win, "backup_banner", None)
        if backup_banner is not None:
            idx = win.main_layout.indexOf(backup_banner)
            if idx != -1:
                win.main_layout.insertWidget(idx + 1, banner)
                inserted = True
                logger.info(f"Agent Mode banner inserted after backup_banner (index {idx + 1})")

        if not inserted:
            win.main_layout.insertWidget(0, banner)
            logger.info("Agent Mode banner inserted at top (no backup_banner anchor found)")

        win._agent_mode_banner = banner

    # ── 2. Restrict tabs ──────────────────────────────────────────
    # Match on objectName (stable) first, fall back to display text.
    AGENT_ALLOWED_OBJ_NAMES = {"dashboard", "patients", "document_viewer", "orders"}
    AGENT_ALLOWED_TEXTS = {"Dashboard", "Patients", "Document Viewer", "Orders"}

    tabs = _get_main_tab_widget(win)
    if tabs is not None:
        for i in range(tabs.count()):
            w = tabs.widget(i)
            obj = w.objectName() if w else ""
            text = tabs.tabText(i)
            if obj:
                allowed = obj in AGENT_ALLOWED_OBJ_NAMES
            else:
                allowed = text in AGENT_ALLOWED_TEXTS
            if not allowed:
                tabs.setTabVisible(i, False)
                logger.info(f"Agent Mode — hidden tab: {text}")
    else:
        logger.warning("Agent Mode — no tab widget found on window; skipping tab restrictions")

    logger.info("Agent Mode UI applied")


def _apply_permission_ui(win) -> None:
    """
    Apply permission-based visibility/enabled state to UI elements.
    Called after login to hide/disable features the user cannot access.
    """
    import logging
    from dmelogic.security.permissions import has_permission
    from dmelogic.security.auth import get_session

    logger = logging.getLogger("permissions")
    session = get_session()

    if not session or not session.is_authenticated:
        logger.warning("No session when applying UI permissions")
        return

    logger.info(f"Applying UI permissions for user '{session.username}'")

    tabs = _get_main_tab_widget(win)
    if tabs is not None:
        if not has_permission("inventory.view"):
            for i in range(tabs.count()):
                w = tabs.widget(i)
                obj = w.objectName() if w else ""
                text = tabs.tabText(i)
                if obj == "inventory" or "Inventory" in text:
                    tabs.setTabVisible(i, False)
                    logger.info(f"Hidden: {text} tab (inventory)")

        if not has_permission("reports.view"):
            for i in range(tabs.count()):
                w = tabs.widget(i)
                obj = w.objectName() if w else ""
                text = tabs.tabText(i)
                if obj == "reports" or "Report" in text:
                    tabs.setTabVisible(i, False)
                    logger.info(f"Hidden: {text} tab (reports)")
    else:
        logger.warning("No tab widget found on window; skipping tab-based permissions")

    logger.info("UI permissions applied")


def _install_agent_ui_command_bridge(win) -> None:
    """Poll command queue written by API/Nova and execute supported UI actions."""
    import json
    import logging
    from dmelogic.paths import db_dir
    from PyQt6.QtCore import QTimer

    logger = logging.getLogger("agent_ui_bridge")
    commands_path = db_dir() / "agent_ui_commands.jsonl"

    try:
        initial_offset = commands_path.stat().st_size if commands_path.exists() else 0
    except Exception:
        initial_offset = 0

    state = {"offset": initial_offset}

    def _process_commands() -> None:
        try:
            if not commands_path.exists():
                return

            size = commands_path.stat().st_size
            if size < state["offset"]:
                state["offset"] = 0

            with commands_path.open("r", encoding="utf-8") as fh:
                fh.seek(state["offset"])
                chunk = fh.read()
                state["offset"] = fh.tell()

            if not chunk.strip():
                return

            for line in chunk.splitlines():
                raw = line.strip()
                if not raw:
                    continue

                try:
                    cmd = json.loads(raw)
                except Exception:
                    logger.warning("Skipping malformed UI command line")
                    continue

                action = str(cmd.get("action") or "").strip().lower()
                command_id = str(cmd.get("command_id") or "")
                params = cmd.get("parameters") if isinstance(cmd.get("parameters"), dict) else {}

                if action == "open_reconciliation_report":
                    handler = getattr(win, "open_foundation_reconciliation_report", None)
                    if not callable(handler):
                        logger.warning("UI command ignored (%s): reconciliation handler missing", command_id)
                        continue
                    try:
                        win.showNormal()
                        win.raise_()
                        win.activateWindow()
                    except Exception:
                        pass
                    try:
                        try:
                            handler(
                                start_date=params.get("start_date"),
                                end_date=params.get("end_date"),
                                insurance=params.get("insurance"),
                            )
                        except TypeError:
                            # Backward compatibility with older signatures.
                            handler()
                        logger.info("Executed UI command %s: open_reconciliation_report", command_id)
                    except Exception as e:
                        logger.warning("UI command failed %s: %s", command_id, e)
                else:
                    logger.info("Ignoring unsupported UI command '%s' (%s)", action, command_id)
        except Exception as e:
            logger.warning("Agent UI bridge poll error: %s", e)

    timer = QTimer(win)
    timer.setInterval(1200)
    timer.timeout.connect(_process_commands)
    timer.start()

    # Keep timer alive on the window object.
    win._agent_ui_bridge_timer = timer


# ── Application entry point ────────────────────────────────────────────

def main() -> int:
    _ensure_venv()

    project_root = Path(__file__).resolve().parent

    from dmelogic.core.startup import Startup, StartupContext

    class DMELogicStartup(Startup):
        """Extends the generic orchestrator with app-specific UI wiring."""

        def _apply_theme(self, ctx: StartupContext) -> None:
            from dmelogic.ui.splash import update_splash
            if ctx.splash:
                update_splash(ctx.splash, "Loading theme…")
            # Modern "Calm Clinical" theme, applied app-wide. This is the
            # primary look; it supersedes the legacy dme_theme stylesheet.
            try:
                from dmelogic.ui.theme_modern import apply_modern_theme
                dark = False
                try:
                    mode = (getattr(ctx.config.theme, "default", "light") or "light").lower()
                    if mode == "dark":
                        dark = True
                    elif mode == "system":
                        from dmelogic.ui.theme_detect import detect_os_theme
                        dark = detect_os_theme() == "dark"
                except Exception:
                    pass
                apply_modern_theme(ctx.app, dark=dark)
            except Exception as e:
                import logging
                logging.getLogger("theme").warning(f"Could not apply modern theme: {e}")

        def _build_window(self, ctx: StartupContext):
            import logging
            from dmelogic.ui.splash import update_splash
            if ctx.splash:
                ctx.splash.show()
                update_splash(ctx.splash, "Loading main window…")

            from dmelogic.ui import create_main_window
            win = create_main_window()

            # Assign stable object names to the main tabs so permission/agent
            # logic can match on them rather than fragile display text.
            _set_tab_object_names(win)

            # Permission and agent-mode UI gates.
            _apply_permission_ui(win)
            _apply_agent_mode_ui(win)

            # Update window title to show the logged-in user and role.
            if ctx.session:
                from dmelogic.db.users import get_user_roles
                base_title = win.windowTitle()
                roles = get_user_roles(ctx.session.user_id, ctx.session._folder_path)
                role_str = ", ".join(roles) if roles else "User"
                window_label = " (2)" if ctx.is_secondary else ""
                agent_label = "  \U0001f916 AGENT MODE" if ctx.session.is_agent else ""
                win.setWindowTitle(
                    f"{base_title}  |  \U0001f464 {ctx.session.username} ({role_str}){window_label}{agent_label}"
                )

            return win

        def _post_window_setup(self, ctx: StartupContext, win) -> None:
            super()._post_window_setup(ctx, win)

            # Re-apply the modern theme on a timer AFTER the window's own init
            # finishes (UI-scale/theme-manager calls during window build can
            # otherwise re-stamp the app stylesheet). Fires via the event loop,
            # so it isn't blocked by the onboarding modal below.
            def _final_theme():
                try:
                    from dmelogic.ui.theme_modern import apply_modern_theme
                    apply_modern_theme(ctx.app)
                except Exception as _e:
                    import logging
                    logging.getLogger("theme").warning(f"Final theme re-apply failed: {_e}")
            try:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(600, _final_theme)
            except Exception:
                pass

            # First-run onboarding: if no company profile is configured yet,
            # collect the business details (skippable) so forms/faxes are
            # branded. Editable later via Settings → Company Profile.
            try:
                from dmelogic.company import is_configured
                if not is_configured():
                    from dmelogic.ui.company_profile_dialog import CompanyProfileDialog
                    CompanyProfileDialog(win, onboarding=True).exec()
            except Exception as e:
                import logging
                logging.getLogger("onboarding").warning(
                    f"Company onboarding skipped: {e}")

            # Allow Nova/API to request opening selected UI screens in the live app.
            _install_agent_ui_command_bridge(win)

            # Style calendar popups now that the full UI is built.
            try:
                from dmelogic.dme_theme import style_all_calendars
                style_all_calendars(win)
            except Exception as e:
                import logging
                logging.getLogger("theme").warning(f"Could not style calendars: {e}")

            # Diagnostic DB path logging (dev-mode sanity check).
            try:
                from dmelogic.diagnostics import log_db_diagnostics
                log_db_diagnostics()
            except Exception:
                pass

            # Force the modern theme as the FINAL stylesheet, after the window
            # and all legacy widgets are built. This guarantees the modern look
            # wins over the legacy dme_theme stylesheet regardless of which
            # _apply_theme ran earlier in the sequence.
            try:
                import logging
                from dmelogic.ui.theme_modern import apply_modern_theme
                apply_modern_theme(ctx.app)
                logging.getLogger("theme").info(
                    "Modern theme applied post-window (final stylesheet, %d chars)",
                    len(ctx.app.styleSheet()),
                )
            except Exception as e:
                import logging
                logging.getLogger("theme").warning(f"Modern theme post-apply failed: {e}")

    return DMELogicStartup(project_root).run()


if __name__ == "__main__":
    sys.exit(main())
