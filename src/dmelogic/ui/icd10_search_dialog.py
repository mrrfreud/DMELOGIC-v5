"""ICD-10 dialog seam for incremental legacy extraction.

This module provides a stable UI-layer entry point while the concrete dialog
implementation still lives in the legacy monolith.
"""

from __future__ import annotations


def create_icd10_search_dialog(parent=None):
    """Create the ICD-10 search dialog via a lazy legacy import."""
    from dmelogic.legacy import ICD10SearchDialog

    return ICD10SearchDialog(parent)
