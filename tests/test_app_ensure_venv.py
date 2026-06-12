from __future__ import annotations

import os
import sys


def test_ensure_venv_uses_project_dot_venv(monkeypatch) -> None:
    from dmelogic import app

    fake_file = os.path.join("C:\\DMELOGIC-v5", "src", "dmelogic", "app.py")
    expected = os.path.normpath(os.path.join("C:\\DMELOGIC-v5", ".venv", "Scripts", "python.exe"))

    monkeypatch.setattr(app.sys, "frozen", False, raising=False)
    monkeypatch.setattr(app, "__file__", fake_file)

    def _fake_exists(path: str) -> bool:
        return os.path.normpath(path) == expected

    monkeypatch.setattr(app.os.path, "exists", _fake_exists)
    monkeypatch.setattr(app.sys, "executable", os.path.join("C:\\Python313", "python.exe"))
    monkeypatch.setattr(app.sys, "argv", [fake_file, "--flag"])

    called = {}

    def _fake_execv(exe, args):
        called["exe"] = exe
        called["args"] = args
        raise RuntimeError("stop")

    monkeypatch.setattr(app.os, "execv", _fake_execv)

    app._ensure_venv()

    assert os.path.normpath(called["exe"]) == expected
    assert os.path.normpath(called["args"][0]) == expected
    assert called["args"][1].endswith(os.path.join("src", "dmelogic", "app.py"))
    assert called["args"][2:] == ["--flag"]


def test_ensure_venv_noop_when_already_in_target(monkeypatch) -> None:
    from dmelogic import app

    fake_file = os.path.join("C:\\DMELOGIC-v5", "src", "dmelogic", "app.py")
    expected = os.path.normpath(os.path.join("C:\\DMELOGIC-v5", ".venv", "Scripts", "python.exe"))

    monkeypatch.setattr(app.sys, "frozen", False, raising=False)
    monkeypatch.setattr(app, "__file__", fake_file)
    monkeypatch.setattr(app.os.path, "exists", lambda p: os.path.normpath(p) == expected)
    monkeypatch.setattr(app.sys, "executable", expected)

    called = {"execv": False}
    monkeypatch.setattr(app.os, "execv", lambda *_args, **_kwargs: called.__setitem__("execv", True))

    app._ensure_venv()

    assert called["execv"] is False
