from __future__ import annotations

import sys
from types import SimpleNamespace


class FakeWidget:
    def __init__(self, name: str = "") -> None:
        self._name = name

    def objectName(self) -> str:
        return self._name

    def setObjectName(self, name: str) -> None:
        self._name = name


class FakeTabs:
    def __init__(self, items: list[tuple[str, FakeWidget]]) -> None:
        self._items = items
        self.visible: dict[int, bool] = {i: True for i in range(len(items))}

    def count(self) -> int:
        return len(self._items)

    def widget(self, i: int) -> FakeWidget:
        return self._items[i][1]

    def tabText(self, i: int) -> str:
        return self._items[i][0]

    def setTabVisible(self, i: int, visible: bool) -> None:
        self.visible[i] = visible


class FakeSignal:
    def connect(self, _callback) -> None:
        return


class FakeFrame:
    def __init__(self) -> None:
        self._visible = True

    def setObjectName(self, _name: str) -> None:
        return

    def setStyleSheet(self, _style: str) -> None:
        return

    def setVisible(self, visible: bool) -> None:
        self._visible = visible

    def hide(self) -> None:
        self._visible = False


class FakeLayout:
    def __init__(self, *_args, **_kwargs) -> None:
        return

    def setContentsMargins(self, *_args, **_kwargs) -> None:
        return

    def addWidget(self, *_args, **_kwargs) -> None:
        return

    def addStretch(self, *_args, **_kwargs) -> None:
        return


class FakeLabel:
    def __init__(self, _text: str = "") -> None:
        return

    def setStyleSheet(self, _style: str) -> None:
        return

    def setWordWrap(self, _flag: bool) -> None:
        return


class FakeToolButton:
    def __init__(self) -> None:
        self.clicked = FakeSignal()

    def setText(self, _text: str) -> None:
        return

    def setStyleSheet(self, _style: str) -> None:
        return


def test_set_tab_object_names_maps_known_tabs_and_preserves_existing_name() -> None:
    from dmelogic import app

    tabs = FakeTabs(
        [
            ("📄 Document Viewer", FakeWidget()),
            ("Orders", FakeWidget()),
            ("Billing", FakeWidget("already_named")),
        ]
    )
    win = SimpleNamespace(main_tabs=tabs)

    app._set_tab_object_names(win)

    assert tabs.widget(0).objectName() == "document_viewer"
    assert tabs.widget(1).objectName() == "orders"
    assert tabs.widget(2).objectName() == "already_named"


def test_apply_permission_ui_hides_inventory_and_reports_without_permissions(monkeypatch) -> None:
    from dmelogic import app

    tabs = FakeTabs(
        [
            ("Dashboard", FakeWidget("dashboard")),
            ("DME Inventory", FakeWidget("inventory")),
            ("Reports", FakeWidget("reports")),
        ]
    )
    win = SimpleNamespace(main_tabs=tabs)

    monkeypatch.setitem(
        sys.modules,
        "dmelogic.security.permissions",
        SimpleNamespace(has_permission=lambda _perm: False),
    )
    monkeypatch.setitem(
        sys.modules,
        "dmelogic.security.auth",
        SimpleNamespace(get_session=lambda: SimpleNamespace(username="tester", is_authenticated=True)),
    )

    app._apply_permission_ui(win)

    assert tabs.visible[0] is True
    assert tabs.visible[1] is False
    assert tabs.visible[2] is False


def test_apply_agent_mode_ui_hides_non_agent_tabs(monkeypatch) -> None:
    from dmelogic import app

    tabs = FakeTabs(
        [
            ("Dashboard", FakeWidget("dashboard")),
            ("Orders", FakeWidget("orders")),
            ("Billing", FakeWidget("billing")),
            ("Reports", FakeWidget("reports")),
        ]
    )
    win = SimpleNamespace(main_tabs=tabs)

    monkeypatch.setitem(
        sys.modules,
        "dmelogic.security.auth",
        SimpleNamespace(
            is_current_user_agent=lambda: True,
            get_session=lambda: SimpleNamespace(username="agent1"),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "dmelogic.ui.prefs",
        SimpleNamespace(
            is_banner_dismissed=lambda _key: False,
            dismiss_banner=lambda _key: None,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "PyQt6.QtWidgets",
        SimpleNamespace(
            QFrame=FakeFrame,
            QHBoxLayout=FakeLayout,
            QLabel=FakeLabel,
            QToolButton=FakeToolButton,
        ),
    )
    monkeypatch.setitem(sys.modules, "PyQt6.QtCore", SimpleNamespace(Qt=object()))

    app._apply_agent_mode_ui(win)

    assert tabs.visible[0] is True
    assert tabs.visible[1] is True
    assert tabs.visible[2] is False
    assert tabs.visible[3] is False
