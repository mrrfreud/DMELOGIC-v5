from __future__ import annotations

import importlib
import sys


def test_prescriber_dialog_factory_is_lazy(monkeypatch) -> None:
    sys.modules.pop("dmelogic.ui.prescriber_dialog", None)

    fake_legacy = type(sys)("dmelogic.legacy")

    class DummyDialog:
        def __init__(self, parent=None, prescriber_data=None) -> None:
            self.parent = parent
            self.prescriber_data = prescriber_data

    fake_legacy.PrescriberDialog = DummyDialog
    monkeypatch.setitem(sys.modules, "dmelogic.legacy", fake_legacy)

    module = importlib.import_module("dmelogic.ui.prescriber_dialog")
    dialog = module.create_prescriber_dialog(parent="p", prescriber_data={"id": 1})

    assert isinstance(dialog, DummyDialog)
    assert dialog.parent == "p"
    assert dialog.prescriber_data == {"id": 1}


def test_ui_package_exposes_prescriber_dialog_factory(monkeypatch) -> None:
    import dmelogic.ui as ui

    class DummyDialog:
        def __init__(self, parent=None, prescriber_data=None) -> None:
            self.parent = parent
            self.prescriber_data = prescriber_data

    monkeypatch.setattr(
        ui,
        "create_prescriber_dialog",
        lambda parent=None, prescriber_data=None: DummyDialog(parent, prescriber_data),
    )

    dialog = ui.create_prescriber_dialog(parent="x", prescriber_data={"npi": "123"})
    assert isinstance(dialog, DummyDialog)
    assert dialog.parent == "x"
    assert dialog.prescriber_data == {"npi": "123"}
