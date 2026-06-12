from __future__ import annotations

from pathlib import Path


def test_ui_modules_use_seams_for_legacy_dialogs() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    ui_dir = repo_root / "src" / "dmelogic" / "ui"

    allowed_seam_files = {
        "icd10_search_dialog.py",
        "prescriber_dialog.py",
        "inventory_item_dialog.py",
    }
    forbidden_imports = {
        "from dmelogic.legacy import ICD10SearchDialog",
        "from dmelogic.legacy import PrescriberDialog",
        "from dmelogic.legacy import InventoryItemDialog",
    }

    violations: list[str] = []
    for path in sorted(ui_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.name in allowed_seam_files:
            continue
        for needle in forbidden_imports:
            if needle in text:
                violations.append(f"{path.name}: contains '{needle}'")

    assert not violations, "\n".join(violations)
