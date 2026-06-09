from .base import (
    resolve_db_path,
    get_connection,
    UnitOfWork,
)

# Domain models
from .models import (
    Patient,
    PatientAddress,
    PatientInsurance,
    Prescriber,
    InventoryItem,
    Order,
    OrderItem,
    OrderInput,
    OrderItemInput,
    OrderStatus,
    BillingType,
    InventoryCategory,
)

# Conversion utilities
from .converters import (
    safe_int,
    safe_decimal,
    safe_date,
    safe_datetime,
    row_to_patient,
    row_to_prescriber,
    row_to_inventory_item,
    row_to_order,
    row_to_order_item,
)

# Order operations
from .orders import (
    fetch_order_with_items,
)

# Pending approval operations (agent-created orders)
from .pending_approvals import (
    fetch_pending_approval_orders,
    count_pending_approvals,
    approve_order,
    reject_order,
    mark_order_agent_created,
)

# Patient operations
from .patients import (
    fetch_all_patients,
    fetch_patient_by_id,
    find_patient_by_name_and_dob,
    fetch_patient_insurance,
    search_patients,
    get_patient,
    create_or_get_patient,
)

# Service layer for exports
from .order_workflow import (
    build_state_portal_json_for_order,
    build_state_portal_csv_row_for_order,
)

__all__ = [
    # Base
    "resolve_db_path",
    "get_connection",
    "UnitOfWork",
    # Models
    "Patient",
    "PatientAddress",
    "PatientInsurance",
    "Prescriber",
    "InventoryItem",
    "Order",
    "OrderItem",
    "OrderInput",
    "OrderItemInput",
    "OrderStatus",
    "BillingType",
    "InventoryCategory",
    # Converters
    "safe_int",
    "safe_decimal",
    "safe_date",
    "safe_datetime",
    "row_to_patient",
    "row_to_prescriber",
    "row_to_inventory_item",
    "row_to_order",
    "row_to_order_item",
    # Order operations
    "fetch_order_with_items",
    # Pending approvals (agent orders)
    "fetch_pending_approval_orders",
    "count_pending_approvals",
    "approve_order",
    "reject_order",
    "mark_order_agent_created",
    # Patient operations
    "fetch_all_patients",
    "fetch_patient_by_id",
    "find_patient_by_name_and_dob",
    "fetch_patient_insurance",
    "search_patients",
    "get_patient",
    "create_or_get_patient",
    # Service layer
    "build_state_portal_json_for_order",
    "build_state_portal_csv_row_for_order",
]

