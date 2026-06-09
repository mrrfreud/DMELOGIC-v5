"""
services package — Business logic and service layer functions.

This package contains service-layer functions that coordinate multiple
repository operations, enforce business rules, and use the UnitOfWork
pattern for transactional consistency across multiple databases.

All service functions follow UoW pattern:
- Open UnitOfWork for multi-DB coordination
- Inject connections into repository functions
- Auto-commit on success, auto-rollback on exception
- Proper connection lifecycle management
"""

from __future__ import annotations

# Order service exports
from dmelogic.services.order_service import (
    create_order_with_enrichment,
    delete_order_with_audit,
)

# Refill service exports
from dmelogic.services.refill_service import (
    process_refills,
    process_refills_grouped,
)

# Agent order service exports
from dmelogic.services.agent_order_service import (
    create_order_as_agent,
)

__all__ = [
    # Order operations
    "create_order_with_enrichment",
    "delete_order_with_audit",
    # Refill operations
    "process_refills",
    "process_refills_grouped",
    # Agent operations
    "create_order_as_agent",
]
