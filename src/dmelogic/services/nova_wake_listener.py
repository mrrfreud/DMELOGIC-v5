"""Launch and supervise the hidden Windows wake listener process for Nova."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger("nova_wake_listener")

# Identity-scoped so a parallel edition's Startup entry never overwrites another
# installed build's (e.g. "DMELogic5_NovaWakeListener.cmd" vs the release name).
try:
    from dmelogic.identity import APP_ID as _APP_ID
except Exception:
    _APP_ID = "DMELogic"
STARTUP_LAUNCHER_NAME = f"{_APP_ID}_NovaWakeListener.cmd"


def _wake_script_path() -> Path:
    return Path(__file__).with_name("nova_wake_listener.ps1")


def _ensure_startup_launcher(script_path: Path) -> None:
    """Ensure wake listener starts automatically from the user's Startup folder."""
    try:
        appdata = os.environ.get("APPDATA", "").strip()
        if not appdata:
            logger.warning("APPDATA not available; cannot install Startup launcher")
            return

        startup_dir = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        launcher_path = startup_dir / STARTUP_LAUNCHER_NAME
        launcher_content = (
            "@echo off\r\n"
            f"start \"\" /min powershell.exe -NoProfile -WindowStyle Hidden "
            f"-ExecutionPolicy Bypass -File \"{script_path}\"\r\n"
        )

        startup_dir.mkdir(parents=True, exist_ok=True)
        current = ""
        if launcher_path.exists():
            current = launcher_path.read_text(encoding="ascii", errors="ignore")
        if current != launcher_content:
            launcher_path.write_text(launcher_content, encoding="ascii")
            logger.info("Installed Startup launcher: %s", launcher_path)
    except Exception as e:
        logger.warning("Could not ensure Nova wake listener Startup launcher: %s", e)


def _is_wake_listener_running() -> bool:
    """Detect running listener by process command line text."""
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_Process | "
                "Where-Object { $_.Name -match 'powershell' -and $_.CommandLine -like '*nova_wake_listener.ps1*' } | "
                "Measure-Object).Count",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        count = int((result.stdout or "0").strip() or "0")
        return result.returncode == 0 and count > 0
    except Exception:
        return False


def _stop_existing_wake_listeners() -> None:
    """Stop any running listener instances so updated script changes take effect."""
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.Name -match 'powershell' -and $_.CommandLine -like '*nova_wake_listener.ps1*' } | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
    except Exception:
        # Best-effort cleanup only.
        pass


def ensure_nova_wake_listener(enabled: bool = True) -> None:
    """Start the hidden wake listener if it is not already running."""
    if not enabled:
        return

    if os.name != "nt":
        return

    script_path = _wake_script_path()
    if not script_path.exists():
        logger.warning("Wake listener script missing: %s", script_path)
        return

    _ensure_startup_launcher(script_path)

    if _is_wake_listener_running():
        _stop_existing_wake_listeners()

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE

    # `start` has proven more reliable for detached hidden PowerShell tasks
    # than direct CREATE_NO_WINDOW+DETACHED_PROCESS on some Windows builds.
    subprocess.run(
        [
            "cmd.exe",
            "/c",
            "start",
            "",
            "/min",
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ],
        cwd=str(script_path.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        startupinfo=startupinfo,
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=10,
        check=False,
    )
    logger.info("Started Nova wake listener process")
