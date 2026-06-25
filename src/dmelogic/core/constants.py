"""
Central constants for the application.

All magic strings that appear in more than one place live here so that
typos become import errors instead of silent bugs.
"""

from enum import Enum


class OrderStatus(str, Enum):
    """
    Canonical order statuses. Subclasses `str` so SQL queries can use
    `OrderStatus.UNBILLED` directly and it serializes to "Unbilled".
    """
    INCOMPLETE = "Incomplete"
    UNBILLED = "Unbilled"
    ON_HOLD = "On Hold"
    PENDING_APPROVAL = "Pending Approval"
    APPROVED = "Approved"
    BILLED = "Billed"
    DELIVERED = "Delivered"
    CANCELLED = "Cancelled"

    def __str__(self) -> str:  # so f"{status}" prints "Unbilled" not "OrderStatus.UNBILLED"
        return self.value


class Permission(str, Enum):
    """Permission keys used with has_permission()."""
    INVENTORY_VIEW = "inventory.view"
    INVENTORY_EDIT = "inventory.edit"
    REPORTS_VIEW = "reports.view"
    REPORTS_EXPORT = "reports.export"
    FINANCIAL_VIEW = "financial.view"
    FINANCIAL_EDIT = "financial.edit"
    USERS_MANAGE = "users.manage"
    ORDERS_VOID = "orders.void"
    ORDERS_APPROVE = "orders.approve"

    def __str__(self) -> str:
        return self.value


class TabName(str, Enum):
    """
    Stable object names for main tabs.

    Use these with `tab.setObjectName(TabName.DASHBOARD)` when building the
    UI, and match against them in permission logic — never against the
    display text, which can be localized or renamed by designers.
    """
    DASHBOARD = "dashboard"
    PATIENTS = "patients"
    DOCUMENT_VIEWER = "document_viewer"
    ORDERS = "orders"
    BILLING = "billing"
    REPORTS = "reports"
    INVENTORY = "inventory"
    SETTINGS = "settings"

    def __str__(self) -> str:
        return self.value


# Tabs visible to users flagged as "agent". See Config for runtime override.
DEFAULT_AGENT_ALLOWED_TABS = frozenset({
    TabName.DASHBOARD,
    TabName.PATIENTS,
    TabName.DOCUMENT_VIEWER,
    TabName.ORDERS,
})


# CLI flags
FLAG_SECONDARY_WINDOW = "--window-instance"
FLAG_NO_SPLASH = "--no-splash"
FLAG_RESET_PREFS = "--reset-prefs"
