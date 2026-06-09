"""Keep Nova host processes running in the background on this PC."""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("nova_background")

API_PORT = 8400
UI_PORT = 8401


def _port_open(port: int) -> bool:
    """Return True when something is actively listening on localhost:port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.35)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _candidate_python_paths(project_root: Path) -> list[Path]:
    candidates = [
        project_root / ".venv" / "Scripts" / "python.exe",
        project_root / "venv" / "Scripts" / "python.exe",
        project_root / ".venv" / "bin" / "python",
        project_root / "venv" / "bin" / "python",
    ]

    # In dev mode this is typically python.exe. In frozen mode this is DMELogic.exe
    # so we only use it if it looks like an actual Python executable.
    if "python" in Path(sys.executable).name.lower():
        candidates.append(Path(sys.executable))

    unique: list[Path] = []
    seen: set[str] = set()
    for p in candidates:
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return unique


def _resolve_python(project_root: Path) -> Path | None:
    for candidate in _candidate_python_paths(project_root):
        try:
            if candidate.exists():
                return candidate
        except Exception:
            continue
    return None


def _candidate_project_roots(project_root: Path) -> list[Path]:
    roots: list[Path] = [project_root]

    # Frozen mode often runs from install_root\_internal\dmelogic\..., while
    # helper scripts may live at install_root or a dev checkout.
    try:
        parent = project_root.parent
        if parent not in roots:
            roots.append(parent)
    except Exception:
        pass

    env_root = str(os.environ.get("DMELOGIC_PROJECT_ROOT", "") or "").strip()
    if env_root:
        p = Path(env_root)
        if p not in roots:
            roots.append(p)

    default_dev = Path("C:/DMELOGIC MAIN")
    if default_dev not in roots:
        roots.append(default_dev)

    return roots


def _resolve_script_root(project_root: Path) -> Path:
    for root in _candidate_project_roots(project_root):
        try:
            if (root / "dmelogic_api.py").exists() and (root / "nova_ui_server.py").exists():
                return root
        except Exception:
            continue
    return project_root


def _launch_start_nova(project_root: Path) -> bool:
    for root in _candidate_project_roots(project_root):
        start_bat = root / "start_nova.bat"
        try:
            if not start_bat.exists():
                continue
            subprocess.Popen(
                ["cmd.exe", "/c", str(start_bat)],
                cwd=str(root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS) if os.name == "nt" else 0,
            )
            logger.info("Launched Nova via start_nova.bat from %s", root)
            return True
        except Exception as e:
            logger.warning("Failed to launch start_nova.bat from %s: %s", root, e)
            continue
    return False


def _spawn_script(python_exe: Path, script_path: Path) -> None:
    """Launch a script detached with no console window."""
    if not script_path.exists():
        logger.warning("Nova background script missing: %s", script_path)
        return

    env = os.environ.copy()
    creationflags = 0
    startupinfo = None

    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

    subprocess.Popen(
        [str(python_exe), str(script_path)],
        cwd=str(script_path.parent),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        startupinfo=startupinfo,
    )


def ensure_nova_background_services(project_root: Path, enabled: bool = True) -> None:
    """Ensure Nova API/UI host processes are running on this PC."""
    if not enabled:
        return

    api_up = _port_open(API_PORT)
    ui_up = _port_open(UI_PORT)
    if api_up and ui_up:
        return

    script_root = _resolve_script_root(project_root)

    python_exe = _resolve_python(script_root)
    if python_exe is None:
        if _launch_start_nova(script_root):
            return
        logger.warning(
            "Nova background launch skipped: no Python runtime found under %s",
            script_root,
        )
        return

    if not api_up:
        _spawn_script(python_exe, script_root / "dmelogic_api.py")
        logger.info("Started background DMELogic API host")

    if not ui_up:
        _spawn_script(python_exe, script_root / "nova_ui_server.py")
        logger.info("Started background Nova UI host")
