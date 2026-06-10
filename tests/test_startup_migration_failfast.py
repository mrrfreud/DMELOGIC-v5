from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from dmelogic.core.startup import Startup, StartupContext


def _install_db_init_stubs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, migration_results: dict[str, int]) -> None:
    monkeypatch.setitem(sys.modules, "dmelogic.paths", SimpleNamespace(db_dir=lambda: tmp_path))
    monkeypatch.setitem(sys.modules, "dmelogic.db.backup", SimpleNamespace(snapshot_databases=lambda *a, **k: None))
    monkeypatch.setitem(sys.modules, "dmelogic.db.audit", SimpleNamespace(init_audit_db=lambda *a, **k: None))
    monkeypatch.setitem(sys.modules, "dmelogic.security.lockout", SimpleNamespace(init_lockout_db=lambda *a, **k: None))
    monkeypatch.setitem(sys.modules, "dmelogic.db.users", SimpleNamespace(init_users_db=lambda *a, **k: None))
    monkeypatch.setitem(
        sys.modules,
        "dmelogic.db.migrations",
        SimpleNamespace(run_all_migrations=lambda: migration_results),
    )


def _make_startup(tmp_path: Path) -> tuple[Startup, StartupContext]:
    startup = Startup(project_root=tmp_path)
    config = SimpleNamespace(
        backup=SimpleNamespace(backup_before_migrations=False, keep_last_n_backups=3)
    )
    startup.config = config
    ctx = StartupContext(app=None, config=config, is_secondary=False, splash=None)
    return startup, ctx


def test_init_databases_raises_on_failed_migrations(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_db_init_stubs(monkeypatch, tmp_path, {"patients.db": 0, "orders.db": -1})
    startup, ctx = _make_startup(tmp_path)

    with pytest.raises(RuntimeError, match="Database migrations failed"):
        startup._init_databases(ctx)


def test_init_databases_accepts_successful_migrations(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_db_init_stubs(monkeypatch, tmp_path, {"patients.db": 0, "orders.db": 1})
    startup, ctx = _make_startup(tmp_path)

    startup._init_databases(ctx)
