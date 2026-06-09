"""
Order workflow service with transactional integrity.

This module provides high-level order operations that coordinate
multiple repositories within UnitOfWork transactions.

All functions handle:
- Validation
- Multi-step operations
- Audit logging
- Automatic rollback on failure
"""

from typing import List, Dict, Any, Optional
from datetime import date, datetime
from decimal import Decimal

from dmelogic.db.base import UnitOfWork, get_connection, row_to_dict
from dmelogic.db.repositories import (
    PatientRepository,
    PrescriberRepository,
    OrderRepository,
    InventoryRepository
)
from dmelogic.config import debug_log


class OrderValidationError(Exception):
    """Raised when order validation fails."""
    pass


class OrderWorkflowService:
    """
    Service for order-related workflows with transactional integrity.
    """
    
    def __init__(self, folder_path: Optional[str] = None):
        """
        Initialize order workflow service.
        
        Args:
            folder_path: Optional database folder path
        """
        self.folder_path = folder_path
    
    def create_order_with_items(
        self,
        patient_id: int,
        prescriber_id: int,
        items: List[Dict[str, Any]],
        order_date: Optional[str] = None,
        notes: Optional[str] = None,
        **additional_fields
    ) -> int:
        """
        Create order with items in a single transaction.
        
        This is the main entry point for order creation. It validates
        patient and prescriber, creates the order header, adds all items,
        and logs the action - all within a single transaction that will
        rollback if any step fails.
        
        Args:
            patient_id: Patient ID (must exist in patients.db)
            prescriber_id: Prescriber ID (must exist in prescribers.db)
            items: List of dicts with:
                - hcpcs_code: str (required)
                - quantity: int (required)
                - unit_price: float/Decimal (required)
                - description: str (optional)
            order_date: Order date (YYYY-MM-DD), defaults to today
            notes: Optional order notes
            **additional_fields: Additional order fields (patient_name, etc.)
        
        Returns:
            order_id: The created order ID
        
        Raises:
            OrderValidationError: If validation fails
            Exception: If database operation fails
        
        Example:
            service = OrderWorkflowService()
            order_id = service.create_order_with_items(
                patient_id=123,
                prescriber_id=456,
                items=[
                    {'hcpcs_code': 'E0601', 'quantity': 1, 'unit_price': 250.00},
                    {'hcpcs_code': 'A4604', 'quantity': 30, 'unit_price': 1.50}
                ],
                notes="Rush order"
            )
        """
        order_date = order_date or date.today().isoformat()
        
        try:
            with UnitOfWork(folder_path=self.folder_path) as uow:
                # Get connections for each database
                patient_conn = uow.connection("patients.db")
                prescriber_conn = uow.connection("prescribers.db")
                order_conn = uow.connection("orders.db")
                
                # Create repositories with shared connections
                patient_repo = PatientRepository(conn=patient_conn)
                prescriber_repo = PrescriberRepository(conn=prescriber_conn)
                
                # Validate patient exists
                patient = patient_repo.get_by_id(patient_id)
                if not patient:
                    raise OrderValidationError(f"Patient {patient_id} not found")
                
                # Validate prescriber exists
                prescriber = prescriber_repo.get_by_id(prescriber_id)
                if not prescriber:
                    raise OrderValidationError(f"Prescriber {prescriber_id} not found")
                
                # Validate items
                if not items:
                    raise OrderValidationError("Order must have at least one item")
                
                for idx, item in enumerate(items):
                    if 'hcpcs_code' not in item:
                        raise OrderValidationError(f"Item {idx} missing hcpcs_code")
                    if 'quantity' not in item or item['quantity'] <= 0:
                        raise OrderValidationError(f"Item {idx} invalid quantity")
                    if 'unit_price' not in item:
                        raise OrderValidationError(f"Item {idx} missing unit_price")
                
                # Create order header
                cursor = order_conn.cursor()
                
                # Build order fields
                order_fields = {
                    'order_date': order_date,
                    'patient_id': patient_id,
                    'prescriber_id': prescriber_id,
                    'patient_last_name': patient.get('last_name', ''),
                    'patient_first_name': patient.get('first_name', ''),
                    'patient_name': f"{patient.get('last_name', '')}, {patient.get('first_name', '')}",
                    'patient_dob': patient.get('dob', ''),
                    'patient_phone': patient.get('phone', ''),
                    'prescriber_name': f"{prescriber.get('last_name', '')} {prescriber.get('first_name', '')}",
                    'prescriber_npi': prescriber.get('npi', ''),
                    'prescriber_phone': prescriber.get('phone', ''),
                    'order_status': 'Pending',
                    'notes': notes or '',
                    **additional_fields  # Allow caller to override/add fields
                }
                
                # Insert order (note: production schema uses text fields, patient_id only)
                cursor.execute("""
                    INSERT INTO orders (
                        order_date, rx_date,
                        patient_last_name, patient_first_name, patient_name,
                        patient_dob, patient_phone, patient_id,
                        prescriber_name, prescriber_npi,
                        order_status, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    order_fields['order_date'],
                    order_fields['order_date'],  # rx_date defaults to order_date
                    order_fields['patient_last_name'],
                    order_fields['patient_first_name'],
                    order_fields['patient_name'],
                    order_fields['patient_dob'],
                    order_fields['patient_phone'],
                    order_fields['patient_id'],
                    order_fields['prescriber_name'],
                    order_fields['prescriber_npi'],
                    order_fields['order_status'],
                    order_fields['notes']
                ))
                
                order_id = cursor.lastrowid
                debug_log(f"Created order {order_id}")
                
                # Add order items (using actual schema column names)
                for item in items:
                    cursor.execute("""
                        INSERT INTO order_items (
                            order_id, hcpcs_code, description,
                            qty, cost_ea, total
                        ) VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        order_id,
                        item['hcpcs_code'],
                        item.get('description', ''),
                        str(item['quantity']),  # Schema uses TEXT for qty
                        str(item['unit_price']),  # Schema uses TEXT for cost_ea
                        str(Decimal(str(item['quantity'])) * Decimal(str(item['unit_price'])))
                    ))
                
                debug_log(f"Added {len(items)} items to order {order_id}")
                
                # Log audit entry
                self._log_audit(
                    order_conn,
                    action='order_created',
                    entity_type='order',
                    entity_id=order_id,
                    details=f"Order created with {len(items)} items"
                )
                
                # Commit transaction
                uow.commit()
                debug_log(f"Order {order_id} committed successfully")
                
                return order_id
                
        except OrderValidationError:
            # Rollback happens automatically
            raise
        except Exception as e:
            debug_log(f"Order creation failed: {e}")
            raise RuntimeError(f"Failed to create order: {e}") from e
    
    def soft_delete_order(
        self,
        order_id: int,
        deleted_by: str = "system",
        reason: Optional[str] = None
    ) -> bool:
        """
        Soft-delete order with audit trail.
        
        Marks order as deleted and creates audit log entry within
        a transaction. Can be restored later if needed.
        
        Args:
            order_id: Order ID to delete
            deleted_by: User identifier
            reason: Optional deletion reason
        
        Returns:
            True if deleted successfully
        
        Raises:
            OrderValidationError: If order not found
            Exception: If database operation fails
        """
        try:
            with UnitOfWork(folder_path=self.folder_path) as uow:
                order_conn = uow.connection("orders.db")
                order_repo = OrderRepository(conn=order_conn)
                
                # Validate order exists
                order = order_repo.get_by_id(order_id)
                if not order:
                    raise OrderValidationError(f"Order {order_id} not found")
                
                # Check if already deleted (via status)
                if order.get('order_status') == 'Deleted':
                    raise OrderValidationError(f"Order {order_id} already deleted")
                
                # Soft delete - mark status as Deleted and add note
                deleted_at = datetime.now().isoformat()
                delete_note = f"[DELETED {deleted_at} by {deleted_by}] {reason}"
                cursor = order_conn.cursor()
                cursor.execute("""
                    UPDATE orders
                    SET order_status = 'Deleted',
                        notes = COALESCE(notes || '\n', '') || ?,
                        updated_date = ?
                    WHERE id = ?
                """, (delete_note, deleted_at, order_id))
                
                # Log audit entry
                details = f"Deleted by {deleted_by}"
                if reason:
                    details += f": {reason}"
                
                self._log_audit(
                    order_conn,
                    action='order_deleted',
                    entity_type='order',
                    entity_id=order_id,
                    details=details
                )
                
                # Commit
                uow.commit()
                debug_log(f"Order {order_id} soft-deleted by {deleted_by}")
                
                return True
                
        except OrderValidationError:
            raise
        except Exception as e:
            debug_log(f"Order deletion failed: {e}")
            raise RuntimeError(f"Failed to delete order: {e}") from e
    
    def update_order_status(
        self,
        order_id: int,
        new_status: str,
        notes: Optional[str] = None
    ) -> bool:
        """
        Update order status with audit trail.
        
        Args:
            order_id: Order ID
            new_status: New status ('Pending', 'Shipped', 'Completed', etc.)
            notes: Optional notes about status change
        
        Returns:
            True if updated successfully
        
        Raises:
            OrderValidationError: If order not found
        """
        try:
            with UnitOfWork(folder_path=self.folder_path) as uow:
                order_conn = uow.connection("orders.db")
                order_repo = OrderRepository(conn=order_conn)
                
                # Validate order exists
                order = order_repo.get_by_id(order_id)
                if not order:
                    raise OrderValidationError(f"Order {order_id} not found")
                
                old_status = order.get('order_status', 'Unknown')
                
                # Update status
                cursor = order_conn.cursor()
                cursor.execute("""
                    UPDATE orders
                    SET order_status = ?, updated_date = ?
                    WHERE id = ?
                """, (new_status, datetime.now().isoformat(), order_id))
                
                # Log audit entry
                details = f"Status changed: {old_status} → {new_status}"
                if notes:
                    details += f" ({notes})"
                
                self._log_audit(
                    order_conn,
                    action='status_updated',
                    entity_type='order',
                    entity_id=order_id,
                    details=details
                )
                
                # Commit
                uow.commit()
                debug_log(f"Order {order_id} status updated to {new_status}")
                
                return True
                
        except OrderValidationError:
            raise
        except Exception as e:
            debug_log(f"Status update failed: {e}")
            raise RuntimeError(f"Failed to update order status: {e}") from e
    
    def _log_audit(
        self,
        conn,
        action: str,
        entity_type: str,
        entity_id: int,
        details: str
    ) -> None:
        """
        Log audit entry within a transaction.
        
        Args:
            conn: Database connection (part of UoW)
            action: Action type ('order_created', 'order_deleted', etc.)
            entity_type: Entity type ('order', 'patient', etc.)
            entity_id: Entity ID
            details: Action details
        """
        try:
            cursor = conn.cursor()
            
            # Check if audit_log table exists
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='audit_log'
            """)
            
            if cursor.fetchone():
                cursor.execute("""
                    INSERT INTO audit_log (
                        timestamp, action, entity_type, entity_id, details
                    ) VALUES (?, ?, ?, ?, ?)
                """, (
                    datetime.now().isoformat(),
                    action,
                    entity_type,
                    entity_id,
                    details
                ))
                debug_log(f"Audit logged: {action} on {entity_type} {entity_id}")
            else:
                debug_log("Audit table not found, skipping audit log")
                
        except Exception as e:
            debug_log(f"Audit logging failed (non-fatal): {e}")
            # Don't fail the transaction if audit log fails


# Convenience functions for backward compatibility
def create_order_with_items(
    patient_id: int,
    prescriber_id: int,
    items: List[Dict[str, Any]],
    order_date: Optional[str] = None,
    notes: Optional[str] = None,
    folder_path: Optional[str] = None,
    **additional_fields
) -> int:
    """
    Create order with items (convenience function).
    
    This is a convenience wrapper around OrderWorkflowService.create_order_with_items()
    for backward compatibility with existing code.
    
    Args:
        patient_id: Patient ID
        prescriber_id: Prescriber ID
        items: List of item dicts
        order_date: Order date (defaults to today)
        notes: Optional notes
        folder_path: Optional database folder
        **additional_fields: Additional order fields
    
    Returns:
        order_id: Created order ID
    """
    service = OrderWorkflowService(folder_path=folder_path)
    return service.create_order_with_items(
        patient_id=patient_id,
        prescriber_id=prescriber_id,
        items=items,
        order_date=order_date,
        notes=notes,
        **additional_fields
    )


def delete_order(
    order_id: int,
    deleted_by: str = "system",
    reason: Optional[str] = None,
    folder_path: Optional[str] = None
) -> bool:
    """
    Soft-delete order (convenience function).
    
    Args:
        order_id: Order ID to delete
        deleted_by: User identifier
        reason: Optional deletion reason
        folder_path: Optional database folder
    
    Returns:
        True if deleted successfully
    """
    service = OrderWorkflowService(folder_path=folder_path)
    return service.soft_delete_order(
        order_id=order_id,
        deleted_by=deleted_by,
        reason=reason
    )
