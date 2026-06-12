"""
dmelogic.legacy — quarantined original "Fax Manager Pro" monolith.

`legacy_app.py` is the original single-file application (~41k lines). The
modern DMELogic shell still depends on a handful of classes that live here —
most notably ``PDFViewer``, a ~30k-line god-class that embeds the original
document-viewer/order-entry application wholesale.

This package exists to *fence off* that code, not to bless it. The #1 item on
the modernization roadmap (see docs/ARCHITECTURE.md) is decomposing
``PDFViewer`` into proper ``dmelogic.ui`` components and retiring this package.

Only the names in ``__all__`` are part of the supported surface; everything
else in ``legacy_app.py`` is dead and slated for removal.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "PDFViewer",
    "PrescriberDialog",
    "InventoryItemDialog",
    "ICD10SearchDialog",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        module = import_module("dmelogic.legacy.legacy_app")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
