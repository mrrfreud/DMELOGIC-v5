"""
Workflow services for DME Logic.

High-level business operations that coordinate multiple repositories
within UnitOfWork transactions.

Usage:
    from dmelogic.workflows import create_order_with_items, process_refill
    
    # Create order
    order_id = create_order_with_items(
        patient_id=123,
        prescriber_id=456,
        items=[{'hcpcs_code': 'E0601', 'quantity': 1, 'unit_price': 250.00}]
    )
    
    # Process refill
    refill_id = process_refill(order_item_id=789)
"""

from .order_workflow import (
    OrderWorkflowService,
    OrderValidationError,
    create_order_with_items,
    delete_order
)

from .refill_workflow_service import (
    RefillWorkflowService,
    RefillValidationError,
    process_refill
)

__all__ = [
    # Services
    'OrderWorkflowService',
    'RefillWorkflowService',
    
    # Exceptions
    'OrderValidationError',
    'RefillValidationError',
    
    # Convenience functions
    'create_order_with_items',
    'delete_order',
    'process_refill',
]
