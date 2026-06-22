from __future__ import annotations

from pathlib import Path

from dmelogic.db.users import (
    create_user,
    get_user_by_username,
    get_user_roles,
    init_users_db,
    reset_or_create_admin_user,
    seed_default_roles_and_permissions,
    set_user_password,
    verify_password,
)
from dmelogic.security.lockout import (
    check_lockout,
    clear_attempts,
    init_lockout_db,
    record_attempt,
)


def _users_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "db"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def test_reset_or_create_admin_user_creates_admin_when_missing(tmp_path: Path) -> None:
    folder = _users_folder(tmp_path)
    init_users_db(str(folder))
    seed_default_roles_and_permissions(str(folder))

    create_user(
        username="melvin",
        display_name="Melvin",
        password="mypass123",
        roles=["Clerk"],
        folder_path=str(folder),
    )

    created, user = reset_or_create_admin_user(str(folder))

    assert created is True
    assert user["username"].lower() == "admin"

    admin = get_user_by_username("admin", str(folder))
    assert admin is not None
    assert verify_password(admin["password_hash"], "admin123")
    assert bool(admin["force_password_change"]) is True

    roles = get_user_roles(admin["id"], str(folder))
    assert "Admin" in roles


def test_reset_or_create_admin_user_resets_existing_admin_password(tmp_path: Path) -> None:
    folder = _users_folder(tmp_path)
    init_users_db(str(folder))
    seed_default_roles_and_permissions(str(folder))

    _, user = reset_or_create_admin_user(
        str(folder),
        username="admin",
        display_name="Administrator",
        new_password="initialpw",
        force_password_change=False,
    )
    set_user_password(user["id"], "changedpw", str(folder))

    created, _ = reset_or_create_admin_user(
        str(folder),
        username="admin",
        display_name="Administrator",
        new_password="admin123",
        force_password_change=True,
    )

    assert created is False

    admin = get_user_by_username("ADMIN", str(folder))
    assert admin is not None
    assert verify_password(admin["password_hash"], "admin123")
    assert bool(admin["force_password_change"]) is True
    assert bool(admin["is_active"]) is True


def test_lockout_is_case_insensitive_and_clearable(tmp_path: Path) -> None:
    auth_db = tmp_path / "auth.db"
    init_lockout_db(auth_db)

    record_attempt(auth_db, "Melvin", success=False)
    record_attempt(auth_db, "melvin", success=False)

    status = check_lockout(
        auth_db,
        "MELVIN",
        max_attempts=2,
        window_minutes=10,
        lockout_minutes=10,
    )
    assert status.locked is True

    clear_attempts(auth_db, "mElViN")

    status_after_clear = check_lockout(
        auth_db,
        "melvin",
        max_attempts=2,
        window_minutes=10,
        lockout_minutes=10,
    )
    assert status_after_clear.locked is False
