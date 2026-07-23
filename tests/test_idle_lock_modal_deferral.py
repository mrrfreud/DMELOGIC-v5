from __future__ import annotations

from datetime import datetime, timedelta

from dmelogic.security import idle_lock


class _FakeSignal:
    def __init__(self) -> None:
        self._callbacks = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)


class _FakeTimer:
    def __init__(self, parent=None) -> None:
        self.parent = parent
        self.interval_ms = 0
        self.timeout = _FakeSignal()
        self.started = False

    def setInterval(self, interval_ms: int) -> None:
        self.interval_ms = interval_ms

    def start(self) -> None:
        self.started = True


class _FakeModal:
    def __init__(self, title: str = "Add New Patient", visible: bool = True) -> None:
        self._title = title
        self._visible = visible

    def isVisible(self) -> bool:
        return self._visible

    def windowTitle(self) -> str:
        return self._title


class _FakeApp:
    def __init__(self, modal=None) -> None:
        self._modal = modal
        self.filters = []

    def installEventFilter(self, event_filter) -> None:
        self.filters.append(event_filter)

    def activeModalWidget(self):
        return self._modal


def _make_manager(monkeypatch, app, lock_callback):
    monkeypatch.setattr(idle_lock, "QTimer", _FakeTimer)
    return idle_lock.IdleLockManager(app, timeout_minutes=1, lock_callback=lock_callback)


def test_idle_lock_defers_when_modal_dialog_is_visible(monkeypatch):
    calls: list[str] = []
    app = _FakeApp(modal=_FakeModal("Add New Patient", visible=True))
    manager = _make_manager(monkeypatch, app, lambda: calls.append("lock"))

    manager._last_activity = datetime.utcnow() - timedelta(minutes=2)
    manager._check()

    assert calls == []
    assert manager._locked is False
    assert manager._deferred_due_to_modal is True


def test_idle_lock_triggers_after_modal_dialog_closes(monkeypatch):
    calls: list[str] = []
    modal = _FakeModal("Add New Patient", visible=True)
    app = _FakeApp(modal=modal)
    manager = _make_manager(monkeypatch, app, lambda: calls.append("lock"))

    manager._last_activity = datetime.utcnow() - timedelta(minutes=2)
    manager._check()

    assert calls == []
    assert manager._locked is False

    app._modal = None
    manager._check()

    assert calls == ["lock"]
    assert manager._locked is True
    assert manager._deferred_due_to_modal is False
