from __future__ import annotations

from pathlib import Path

from dmelogic.services import nova_background


def test_frozen_without_python_uses_embedded_modes(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(nova_background, "_port_open", lambda _port: False)
    monkeypatch.setattr(nova_background, "_resolve_script_root", lambda root: root)
    monkeypatch.setattr(nova_background, "_resolve_python", lambda _root: None)
    monkeypatch.setattr(nova_background, "_launch_start_nova", lambda _root: False)
    monkeypatch.setattr(
        nova_background,
        "_spawn_embedded_mode",
        lambda flag: calls.append(flag) or True,
    )
    monkeypatch.setattr(nova_background.sys, "frozen", True, raising=False)

    nova_background.ensure_nova_background_services(Path("C:/Program Files/DMELogic 5"), enabled=True)

    assert nova_background._MODE_API_FLAG in calls
    assert nova_background._MODE_UI_FLAG in calls


def test_non_frozen_without_python_keeps_start_bat_fallback(monkeypatch):
    calls = {"embedded": 0, "start_bat": 0}

    monkeypatch.setattr(nova_background, "_port_open", lambda _port: False)
    monkeypatch.setattr(nova_background, "_resolve_script_root", lambda root: root)
    monkeypatch.setattr(nova_background, "_resolve_python", lambda _root: None)
    monkeypatch.setattr(
        nova_background,
        "_spawn_embedded_mode",
        lambda _flag: calls.__setitem__("embedded", calls["embedded"] + 1) or True,
    )
    monkeypatch.setattr(
        nova_background,
        "_launch_start_nova",
        lambda _root: calls.__setitem__("start_bat", calls["start_bat"] + 1) or False,
    )
    monkeypatch.setattr(nova_background.sys, "frozen", False, raising=False)

    nova_background.ensure_nova_background_services(Path("C:/DMELOGIC-v5"), enabled=True)

    assert calls["embedded"] == 0
    assert calls["start_bat"] == 1
