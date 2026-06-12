from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace


class FakeWidget:
    def __init__(self, name: str = "") -> None:
        self._name = name

    def objectName(self) -> str:
        return self._name

    def setObjectName(self, value: str) -> None:
        self._name = value


class FakeTabs:
    def __init__(self, items: list[tuple[str, FakeWidget]]) -> None:
        self._items = items
        self.visible = {idx: True for idx in range(len(items))}

    def count(self) -> int:
        return len(self._items)

    def widget(self, idx: int) -> FakeWidget:
        return self._items[idx][1]

    def tabText(self, idx: int) -> str:
        return self._items[idx][0]

    def setTabVisible(self, idx: int, visible: bool) -> None:
        self.visible[idx] = visible


def test_tab_name_assignment_then_permission_gate_hides_expected_tabs(monkeypatch) -> None:
    from dmelogic import app

    tabs = FakeTabs(
        [
            ("Dashboard", FakeWidget()),
            ("DME Inventory", FakeWidget()),
            ("Reports", FakeWidget()),
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
        SimpleNamespace(get_session=lambda: SimpleNamespace(username="u", is_authenticated=True)),
    )

    app._set_tab_object_names(win)
    app._apply_permission_ui(win)

    assert tabs.widget(1).objectName() == "inventory"
    assert tabs.widget(2).objectName() == "reports"
    assert tabs.visible[0] is True
    assert tabs.visible[1] is False
    assert tabs.visible[2] is False


def test_get_main_tab_widget_supports_tabs_fallback() -> None:
    from dmelogic import app

    tabs = object()
    win = SimpleNamespace(tabs=tabs)

    assert app._get_main_tab_widget(win) is tabs


def test_icd10_dialog_factory_is_lazy(monkeypatch) -> None:
    sys.modules.pop("dmelogic.ui.icd10_search_dialog", None)

    fake_legacy = type(sys)("dmelogic.legacy")

    class DummyDialog:
        def __init__(self, parent=None) -> None:
            self.parent = parent

    fake_legacy.ICD10SearchDialog = DummyDialog
    monkeypatch.setitem(sys.modules, "dmelogic.legacy", fake_legacy)

    module = importlib.import_module("dmelogic.ui.icd10_search_dialog")
    dialog = module.create_icd10_search_dialog(parent="p")

    assert isinstance(dialog, DummyDialog)
    assert dialog.parent == "p"
