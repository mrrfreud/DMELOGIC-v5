from __future__ import annotations

import sys
from pathlib import Path


def _fail(msg: str) -> int:
    print(f"[FAIL] {msg}")
    return 1


def _ok(msg: str) -> None:
    print(f"[OK] {msg}")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]

    startup_path = repo_root / "src" / "dmelogic" / "core" / "startup.py"
    app_path = repo_root / "src" / "dmelogic" / "app.py"
    ui_init_path = repo_root / "src" / "dmelogic" / "ui" / "__init__.py"

    for path in (startup_path, app_path, ui_init_path):
                if not path.exists():
                        return _fail(f"Missing expected file: {path}")

    startup_text = startup_path.read_text(encoding="utf-8", errors="replace")
    app_text = app_path.read_text(encoding="utf-8", errors="replace")
    ui_init_text = ui_init_path.read_text(encoding="utf-8", errors="replace")

    checks: list[tuple[bool, str]] = [
        (
            "Database Initialization Error" in startup_text,
            "startup shows explicit DB init fatal dialog",
        ),
        (
            "Database migrations failed for:" in startup_text,
            "startup enforces migration fail-fast",
        ),
        (
            "candidate_roots" in app_text and '".venv"' in app_text,
            "app venv bootstrap searches project-level .venv",
        ),
        (
            "def create_main_window()" in ui_init_text,
            "ui package keeps lazy main window factory",
        ),
        (
            "def create_icd10_search_dialog" in ui_init_text,
            "ui package exposes ICD-10 seam",
        ),
        (
            "def create_prescriber_dialog" in ui_init_text,
            "ui package exposes Prescriber seam",
        ),
        (
            "def create_inventory_item_dialog" in ui_init_text,
            "ui package exposes Inventory seam",
        ),
    ]

    failures = 0
    for passed, desc in checks:
        if passed:
            _ok(desc)
        else:
            print(f"[FAIL] {desc}")
            failures += 1

    tests_dir = repo_root / "tests"
    if not tests_dir.exists():
        print("[FAIL] tests directory missing")
        failures += 1
    else:
        test_files = sorted(p.name for p in tests_dir.glob("test_*.py"))
        expected = {
            "test_startup_migration_failfast.py",
            "test_ui_lazy_import.py",
            "test_app_ui_gates.py",
            "test_app_window_gate_integration.py",
            "test_app_ensure_venv.py",
            "test_prescriber_dialog_seam.py",
            "test_inventory_item_dialog_seam.py",
            "test_ui_no_direct_legacy_dialog_imports.py",
        }
        missing = sorted(expected - set(test_files))
        if missing:
            print(f"[FAIL] missing reliability tests: {', '.join(missing)}")
            failures += 1
        else:
            _ok("reliability regression tests are present")

    if failures:
        print(f"\nReliability check failed with {failures} issue(s).")
        return 1

    print("\nReliability check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
