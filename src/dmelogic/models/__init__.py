"""
Domain models package for DME Logic.

This package contains dataclass-based domain models that represent
the business entities in the application. These models:

1. Provide type safety and IDE autocomplete
2. Encapsulate business rules and validation
3. Decouple database representation from business logic
4. Enable rich domain behavior (methods, properties)
5. Serve as the contract between layers

Architecture:
- Repositories return domain models (not sqlite3.Row or dict)
- Services operate on domain models
- UI consumes domain models
- Database mapping happens only in repositories

Domain-Driven Design principles:
- Entities: Have identity (ID field)
- Value Objects: Immutable, no identity
- Aggregates: Clusters of entities with root
- Business Rules: Encoded in model methods
"""

from dmelogic.models.order import Order, OrderItem, OrderStatus
from dmelogic.models.patient import Patient, PatientInsurance
from dmelogic.models.prescriber import Prescriber, PrescriberStatus
from dmelogic.models.inventory import InventoryItem, InventoryStatus
from dmelogic.models.insurance import InsurancePolicy, InsuranceType

__all__ = [
    # Order domain
    "Order",
    "OrderItem",
    "OrderStatus",
    # Patient domain
    "Patient",
    "PatientInsurance",
    # Prescriber domain
    "Prescriber",
    "PrescriberStatus",
    # Inventory domain
    "InventoryItem",
    "InventoryStatus",
    # Insurance domain
    "InsurancePolicy",
    "InsuranceType",
]
