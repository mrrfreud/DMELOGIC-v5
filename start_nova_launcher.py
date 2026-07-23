from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
PACKAGE_DIR = SRC / "dmelogic"

API_PORT = 8400
UI_PORT = 8401


def _port_open(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.35)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _wait_for_port(port: int, timeout_seconds: float = 15.0) -> bool:
    deadline = time.time() + max(0.5, timeout_seconds)
    while time.time() < deadline:
        if _port_open(port):
            return True
        time.sleep(0.2)
    return _port_open(port)


def _load_local_env() -> None:
    try:
        if str(SRC) not in sys.path:
            sys.path.insert(0, str(SRC))
        from dmelogic.config import data_root
    except Exception:
        return

    env_path = data_root() / ".env"
    if not env_path.is_file():
        return

    try:
        for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key.lower().startswith("export "):
                key = key[len("export "):].strip()
            if not key or key in os.environ:
                continue
            os.environ[key] = value.strip().strip('"').strip("'")
    except Exception:
        pass


def _python_exe() -> Path:
    candidates = [
        ROOT / ".venv" / "Scripts" / "pythonw.exe",
        ROOT / ".venv" / "Scripts" / "python.exe",
        Path(sys.executable),
    ]
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except Exception:
            continue
    return Path(sys.executable)


def _spawn(script_name: str) -> None:
    script_path = PACKAGE_DIR / script_name
    if not script_path.exists():
        raise FileNotFoundError(script_path)
    subprocess.Popen(
        [str(_python_exe()), str(script_path)],
        cwd=str(PACKAGE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS) if os.name == "nt" else 0,
    )


def main() -> int:
    _load_local_env()

    if not _port_open(API_PORT):
        _spawn("dmelogic_api.py")
    if not _port_open(UI_PORT):
        _spawn("nova_ui_server.py")

    # Avoid opening a browser tab before Nova UI has started listening.
    _wait_for_port(API_PORT, timeout_seconds=12.0)
    _wait_for_port(UI_PORT, timeout_seconds=18.0)
    webbrowser.open("http://127.0.0.1:8401", new=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())