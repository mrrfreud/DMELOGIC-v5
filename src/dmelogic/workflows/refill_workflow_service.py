"""
Refill workflow service with transactional integrity.

Handles refill processing with validation, order creation,
and audit logging within UnitOfWork transactions.
"""

from typing import Optional, Dict, Any
from datetime import date, datetime, timedelta
from decimal import Decimal

from dmelogic.db.base import UnitOfWork, get_connection, row_to_dict
from dmelogic.db.repositories import OrderRepository
from dmelogic.db.orders import fetch_order_item_with_header
from dmelogic.config import debug_log


class RefillValidationError(Exception):
    """Raised when refill validation fails."""
    pass


class RefillWorkflowService:
    """
    Service for refill-related workflows with transactional integrity.
    """
    
    def __init__(self, folder_path: Optional[str] = None):
        """
        Initialize refill workflow service.
        
        Args:
            folder_path: Optional database folder path
        """
        self.folder_path = folder_path
    
    def process_refill(
        self,
        order_item_id: int,
        fill_date: Optional[str] = None,
        force: bool = False
    ) -> int:
        """
        Process refill for an order item.
        
        Creates a new order for the refill, decrements refill count,
        and logs the action - all within a single transaction.
        
        Args:
            order_item_id: Source order_item ID to refill
            fill_date: Refill date (YYYY-MM-DD), defaults to today
            force: Skip refill eligibility check
        
        Returns:
            new_order_id: The created refill order ID
        
        Raises:
            RefillValidationError: If validation fails
            Exception: If database operation fails
        
        Example:
            service = RefillWorkflowService()
            refill_order_id = service.process_refill(
                order_item_id=789,
                fill_date="2025-12-05"
            )
        """
        fill_date = fill_date or date.today().isoformat()
        
        try:
            with UnitOfWork(folder_path=self.folder_path) as uow:
                order_conn = uow.connection("orders.db")
                
                # Fetch source order item with header data
                source_data = self._get_source_order_item(order_conn, order_item_id)
                if not source_data:
                    raise RefillValidationError(
                        f"Order item {order_item_id} not found"
                    )
                
                # Validate refill eligibility
                if not force:
                    self._validate_refill_eligibility(source_data, fill_date)
                
                # Create refill order
                new_order_id = self._create_refill_order(
                    order_conn,
                    source_data,
                    fill_date
                )
                
                # Update source item refill tracking
                self._update_refill_tracking(
                    order_conn,
                    order_item_id,
                    fill_date
                )
                
                # Log audit entry
                self._log_audit(
                    order_conn,
                    action='refill_processed',
                    entity_type='order',
                    entity_id=new_order_id,
                    details=f"Refill from order_item {order_item_id}"
                )
                
                # Commit transaction
                uow.commit()
                debug_log(f"Refill processed: new order {new_order_id}")
                
                return new_order_id
                
        except RefillValidationError:
            raise
        except Exception as e:
            debug_log(f"Refill processing failed: {e}")
            raise RuntimeError(f"Failed to process refill: {e}") from e
    
    def _get_source_order_item(
        self,
        conn,
        order_item_id: int
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch source order item with header data.
        
        Args:
            conn: Database connection
            order_item_id: Order item ID
        
        Returns:
            Dict with order + order_item fields, or None
        """
        # Use existing function from dmelogic.db.orders
        result = fetch_order_item_with_header(
            order_item_id,
            conn=conn,
            folder_path=self.folder_path
        )
        
        return result
    
    def _validate_refill_eligibility(
        self,
        source: Dict[str, Any],
        fill_date: str
    ) -> None:
        """
        Validate that refill is allowed.
        
        Checks:
        - Refills remaining > 0
        - Sufficient time since last fill (if applicable)
        - Item is eligible for refills
        
        Args:
            source: Source order item data
            fill_date: Requested fill date
        
        Raises:
            RefillValidationError: If refill not allowed
        """
        # Check refills remaining
        refills_remaining = source.get('refills', 0)
        if isinstance(refills_remaining, str):
            try:
                refills_remaining = int(refills_remaining)
            except (ValueError, TypeError):
                refills_remaining = 0
        
        if refills_remaining <= 0:
            raise RefillValidationError(
                "No refills remaining for this item"
            )
        
        # Check last fill date (90-day rule for DME)
        last_filled = source.get('last_filled_date')
        if last_filled:
            try:
                last_date = datetime.fromisoformat(last_filled).date()
                fill_date_obj = datetime.fromisoformat(fill_date).date()
                days_since = (fill_date_obj - last_date).days
                
                # DME typically requires 90 days between refills
                if days_since < 90:
                    raise RefillValidationError(
                        f"Refill not due yet. Only {days_since} days since last fill "
                        f"(minimum 90 days required)"
                    )
            except (ValueError, TypeError):
                # If date parsing fails, allow refill
                pass
        
        debug_log(f"Refill validation passed for item {source.get('order_item_id')}")
    
    def _create_refill_order(
        self,
        conn,
        source: Dict[str, Any],
        fill_date: str
    ) -> int:
        """
        Create new order for refill.
        
        Args:
            conn: Database connection
            source: Source order item data
            fill_date: Fill date
        
        Returns:
            new_order_id: Created order ID
        """
        cursor = conn.cursor()
        
        # Calculate refills remaining
        current_refills = source.get('refills', 0)
        if isinstance(current_refills, str):
            try:
                current_refills = int(current_refills)
            except (ValueError, TypeError):
                current_refills = 0
        
        remaining_refills = max(0, current_refills - 1)
        
        # Create order header
        cursor.execute("""
            INSERT INTO orders (
                order_date,
                patient_last_name,
                patient_first_name,
                patient_name,
                patient_dob,
                patient_phone,
                patient_address,
                patient_city,
                patient_state,
                patient_zip,
                prescriber_name,
                prescriber_npi,
                prescriber_phone,
                diagnosis_code,
                primary_insurance,
                secondary_insurance,
                order_status,
                original_order_id,
                notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fill_date,
            source.get('patient_last_name', ''),
            source.get('patient_first_name', ''),
            source.get('patient_name', ''),
            source.get('patient_dob', ''),
            source.get('patient_phone', ''),
            source.get('patient_address', ''),
            source.get('patient_city', ''),
            source.get('patient_state', ''),
            source.get('patient_zip', ''),
            source.get('prescriber_name', ''),
            source.get('prescriber_npi', ''),
            source.get('prescriber_phone', ''),
            source.get('diagnosis_code', ''),
            source.get('primary_insurance', ''),
            source.get('secondary_insurance', ''),
            'Unbilled',  # Always start refills in the Unbilled workflow state
            source.get('order_id'),  # Link to original order
            f"Refill - {remaining_refills} refills remaining"
        ))
        
        new_order_id = cursor.lastrowid
        
        # Create order item with decremented refills
        cursor.execute("""
            INSERT INTO order_items (
                order_id,
                rx_no,
                hcpcs_code,
                description,
                item_number,
                quantity,
                day_supply,
                refills,
                last_filled_date,
                unit_price,
                line_total
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            new_order_id,
            source.get('rx_no', ''),
            source.get('hcpcs_code', ''),
            source.get('description', ''),
            source.get('item_number', ''),
            source.get('quantity', 1),
            source.get('day_supply', ''),
            remaining_refills,
            fill_date,  # Update last_filled_date
            source.get('unit_price', 0),
            Decimal(str(source.get('quantity', 1))) * Decimal(str(source.get('unit_price', 0)))
        ))
        
        debug_log(f"Created refill order {new_order_id} from source item {source.get('order_item_id')}")
        
        return new_order_id
    
    def _update_refill_tracking(
        self,
        conn,
        order_item_id: int,
        fill_date: str
    ) -> None:
        """
        Update source order item refill tracking.
        
        Args:
            conn: Database connection
            order_item_id: Source order item ID
            fill_date: Fill date
        """
        cursor = conn.cursor()
        
        # Decrement refills and update last_filled_date
        cursor.execute("""
            UPDATE order_items
            SET refills = MAX(0, refills - 1),
                last_filled_date = ?,
                refills_processed = COALESCE(refills_processed, 0) + 1
            WHERE order_item_id = ?
        """, (fill_date, order_item_id))
        
        debug_log(f"Updated refill tracking for item {order_item_id}")
    
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
            conn: Database connection
            action: Action type
            entity_type: Entity type
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


# Convenience function for backward compatibility
def process_refill(
    order_item_id: int,
    fill_date: Optional[str] = None,
    force: bool = False,
    folder_path: Optional[str] = None
) -> int:
    """
    Process refill (convenience function).
    
    Args:
        order_item_id: Source order item ID
        fill_date: Fill date (defaults to today)
        force: Skip eligibility check
        folder_path: Optional database folder
    
    Returns:
        new_order_id: Created refill order ID
    """
    service = RefillWorkflowService(folder_path=folder_path)
    return service.process_refill(
        order_item_id=order_item_id,
        fill_date=fill_date,
        force=force
    )
