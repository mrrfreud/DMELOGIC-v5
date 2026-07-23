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
_MODE_API_FLAG = "--run-dmelogic-api"
_MODE_UI_FLAG = "--run-nova-ui-server"


def is_dmelogic_api_running() -> bool:
    """Return True when the local DMELogic API host is reachable on port 8400."""
    return _port_open(API_PORT)


def is_nova_ui_running() -> bool:
    """Return True when the local Nova UI host is reachable on port 8401."""
    return _port_open(UI_PORT)


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
    candidates: list[Path] = []

    # Prefer the CURRENTLY running interpreter — it's guaranteed to be this
    # install's Python (never another build's venv). Use its windowless
    # pythonw.exe sibling first so Nova host processes don't pop a console.
    if "python" in Path(sys.executable).name.lower():
        exe = Path(sys.executable)
        pythonw = exe.with_name("pythonw.exe")
        if os.name == "nt" and pythonw.exists():
            candidates.append(pythonw)
        candidates.append(exe)

    candidates += [
        project_root / ".venv" / "Scripts" / "pythonw.exe",
        project_root / ".venv" / "Scripts" / "python.exe",
        project_root / "venv" / "Scripts" / "pythonw.exe",
        project_root / "venv" / "Scripts" / "python.exe",
        project_root / ".venv" / "bin" / "python",
        project_root / "venv" / "bin" / "python",
    ]

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
    roots: list[Path] = []

    # 1. The dmelogic package directory — in v5 the Nova host scripts
    #    (dmelogic_api.py, nova_ui_server.py) live alongside the package, e.g.
    #    src/dmelogic/. This is the authoritative location for THIS install and
    #    must win so we never accidentally run another build's copy.
    try:
        package_dir = Path(__file__).resolve().parents[1]  # …/dmelogic
        roots.append(package_dir)
    except Exception:
        pass

    if project_root not in roots:
        roots.append(project_root)

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


def _spawn_embedded_mode(mode_flag: str) -> bool:
    """Launch API/UI host mode via this same frozen executable."""
    if not getattr(sys, "frozen", False):
        return False

    exe = Path(sys.executable)
    try:
        if not exe.exists():
            return False
    except Exception:
        return False

    env = os.environ.copy()
    creationflags = 0
    startupinfo = None

    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

    try:
        subprocess.Popen(
            [str(exe), mode_flag],
            cwd=str(exe.parent),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
        return True
    except Exception as e:
        logger.warning("Failed to launch embedded Nova mode %s: %s", mode_flag, e)
        return False


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
        launched_embedded = False
        if getattr(sys, "frozen", False):
            if not api_up and _spawn_embedded_mode(_MODE_API_FLAG):
                logger.info("Started background DMELogic API host (embedded mode)")
                launched_embedded = True
            if not ui_up and _spawn_embedded_mode(_MODE_UI_FLAG):
                logger.info("Started background Nova UI host (embedded mode)")
                launched_embedded = True
            if launched_embedded:
                return

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


def ensure_dmelogic_api_service(project_root: Path, enabled: bool = True) -> None:
    """Ensure only the DMELogic API host process is running on this PC."""
    if not enabled:
        return

    if _port_open(API_PORT):
        return

    script_root = _resolve_script_root(project_root)
    python_exe = _resolve_python(script_root)

    if python_exe is None:
        if getattr(sys, "frozen", False):
            if _spawn_embedded_mode(_MODE_API_FLAG):
                logger.info("Started background DMELogic API host (embedded mode)")
                return

        # Fall back to the launcher script when a direct Python runtime cannot
        # be resolved from this install.
        if _launch_start_nova(script_root):
            return

        logger.warning(
            "DMELogic API launch skipped: no Python runtime found under %s",
            script_root,
        )
        return

    _spawn_script(python_exe, script_root / "dmelogic_api.py")
    logger.info("Started background DMELogic API host")
