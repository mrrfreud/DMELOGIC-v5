"""Inventory item dialog seam for incremental legacy extraction.

This UI-layer factory keeps modern call sites stable while the concrete
implementation remains in the legacy module.
"""

from __future__ import annotations


def create_inventory_item_dialog(parent=None, item_data=None):
    """Create the Inventory Item dialog via a lazy legacy import."""
    from dmelogic.legacy import InventoryItemDialog

    return InventoryItemDialog(parent, item_data)
