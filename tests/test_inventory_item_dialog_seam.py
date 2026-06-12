from __future__ import annotations

import importlib
import sys


def test_inventory_item_dialog_factory_is_lazy(monkeypatch) -> None:
    sys.modules.pop("dmelogic.ui.inventory_item_dialog", None)

    fake_legacy = type(sys)("dmelogic.legacy")

    class DummyDialog:
        def __init__(self, parent=None, item_data=None) -> None:
            self.parent = parent
            self.item_data = item_data

    fake_legacy.InventoryItemDialog = DummyDialog
    monkeypatch.setitem(sys.modules, "dmelogic.legacy", fake_legacy)

    module = importlib.import_module("dmelogic.ui.inventory_item_dialog")
    dialog = module.create_inventory_item_dialog(parent="p", item_data={"id": 9})

    assert isinstance(dialog, DummyDialog)
    assert dialog.parent == "p"
    assert dialog.item_data == {"id": 9}


def test_ui_package_exposes_inventory_item_dialog_factory(monkeypatch) -> None:
    import dmelogic.ui as ui

    class DummyDialog:
        def __init__(self, parent=None, item_data=None) -> None:
            self.parent = parent
            self.item_data = item_data

    monkeypatch.setattr(
        ui,
        "create_inventory_item_dialog",
        lambda parent=None, item_data=None: DummyDialog(parent, item_data),
    )

    dialog = ui.create_inventory_item_dialog(parent="x", item_data={"sku": "ABC"})
    assert isinstance(dialog, DummyDialog)
    assert dialog.parent == "x"
    assert dialog.item_data == {"sku": "ABC"}
