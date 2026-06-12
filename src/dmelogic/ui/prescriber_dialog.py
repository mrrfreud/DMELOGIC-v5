"""Prescriber dialog seam for incremental legacy extraction.

This UI-layer factory keeps call sites stable while the concrete dialog
still resides in the legacy monolith.
"""

from __future__ import annotations


def create_prescriber_dialog(parent=None, prescriber_data=None):
    """Create the Prescriber dialog via a lazy legacy import."""
    from dmelogic.legacy import PrescriberDialog

    return PrescriberDialog(parent, prescriber_data)
