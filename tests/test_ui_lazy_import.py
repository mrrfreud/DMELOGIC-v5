from __future__ import annotations

import importlib
import sys


def test_dmelogic_ui_import_does_not_eager_import_main_window() -> None:
    sys.modules.pop("dmelogic.ui", None)
    sys.modules.pop("dmelogic.ui.main_window", None)

    import dmelogic.ui as ui

    assert "dmelogic.ui.main_window" not in sys.modules

    window_factory = ui.create_main_window
    assert callable(window_factory)

    # Accessing the function itself should still not import the heavy module.
    assert "dmelogic.ui.main_window" not in sys.modules


def test_create_main_window_triggers_lazy_import_only_when_called(monkeypatch) -> None:
    sys.modules.pop("dmelogic.ui", None)
    sys.modules.pop("dmelogic.ui.main_window", None)

    fake_module_name = "dmelogic.ui.main_window"
    fake_module = type(sys)(fake_module_name)

    def _fake_create_main_window():
        return "ok"

    fake_module.create_main_window = _fake_create_main_window
    sys.modules[fake_module_name] = fake_module

    ui = importlib.import_module("dmelogic.ui")
    assert ui.create_main_window() == "ok"
