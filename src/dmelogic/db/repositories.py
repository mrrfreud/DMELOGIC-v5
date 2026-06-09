"""
Repository classes for database access with UnitOfWork support.

These repositories follow the Repository pattern and support both
standalone usage and participation in UnitOfWork transactions.

Pattern:
    # Standalone (auto-commit)
    repo = PatientRepository()
    patient = repo.get_by_id(123)
    
    # Within UnitOfWork (transactional)
    with UnitOfWork() as uow:
        conn = uow.connection("patients.db")
        repo = PatientRepository(conn=conn)
        patient = repo.get_by_id(123)
        uow.commit()
"""

from __future__ import annotations
import sqlite3
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from .base import get_connection, row_to_dict, rows_to_dicts
from dmelogic.config import debug_log


class PatientRepository:
    """
    Patient data access with optional connection injection for UnitOfWork.
    """
    
    def __init__(self, conn: Optional[sqlite3.Connection] = None, folder_path: Optional[str] = None):
        """
        Initialize repository.
        
        Args:
            conn: Optional connection for UnitOfWork pattern.
                  If None, will create own connections per operation.
            folder_path: Database folder path (used when conn is None)
        """
        self._conn = conn
        self._folder_path = folder_path
    
    @contextmanager
    def _get_connection(self):
        """Get connection (provided or create new)."""
        if self._conn:
            # Part of UoW - use provided connection, don't close it
            yield self._conn
        else:
            # Standalone - create and manage connection
            conn = get_connection('patients.db', folder_path=self._folder_path)
            try:
                yield conn
            finally:
                conn.close()
    
    def get_all(self) -> List[Dict[str, Any]]:
        """
        Fetch all patients ordered by last_name, first_name.
        
        Returns:
            List of patient dicts
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT *
                    FROM patients
                    ORDER BY last_name COLLATE NOCASE ASC,
                             first_name COLLATE NOCASE ASC,
                             dob ASC
                """)
                rows = cursor.fetchall()
                return rows_to_dicts(rows)
        except Exception as e:
            debug_log(f"PatientRepository.get_all error: {e}")
            return []
    
    def get_by_id(self, patient_id: int) -> Optional[Dict[str, Any]]:
        """
        Fetch patient by ID.
        
        Args:
            patient_id: Patient primary key
        
        Returns:
            Patient dict or None if not found
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM patients WHERE id = ?",
                    (patient_id,)
                )
                row = cursor.fetchone()
                return row_to_dict(row)
        except Exception as e:
            debug_log(f"PatientRepository.get_by_id({patient_id}) error: {e}")
            return None
    
    def search_by_name(self, last_name: str, first_name: str, 
                       dob: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Search patients by name and optional DOB.
        
        Args:
            last_name: Patient last name
            first_name: Patient first name
            dob: Optional date of birth (YYYY-MM-DD)
        
        Returns:
            List of matching patient dicts
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                if dob:
                    cursor = conn.execute("""
                        SELECT *
                        FROM patients
                        WHERE UPPER(last_name) = UPPER(?)
                          AND UPPER(first_name) = UPPER(?)
                          AND dob = ?
                        ORDER BY last_name, first_name
                    """, (last_name, first_name, dob))
                else:
                    cursor = conn.execute("""
                        SELECT *
                        FROM patients
                        WHERE UPPER(last_name) = UPPER(?)
                          AND UPPER(first_name) = UPPER(?)
                        ORDER BY last_name, first_name
                    """, (last_name, first_name))
                
                rows = cursor.fetchall()
                return rows_to_dicts(rows)
        except Exception as e:
            debug_log(f"PatientRepository.search_by_name error: {e}")
            return []

    def search(self, query: Optional[str], limit: int = 50) -> List[Dict[str, Any]]:
        """Search patients by phone number digits.

        This helper is primarily used by the communications inbox to match
        incoming numbers against patient phone fields. The search normalizes
        both the incoming query and database values to digits-only strings so
        formatting differences do not prevent matches.

        Args:
            query: Phone number string to match. Characters other than digits
                   are ignored.
            limit: Maximum number of rows to return.

        Returns:
            List of patient dicts whose primary or secondary phone matches.
        """
        if not query:
            return []

        digits_only = ''.join(ch for ch in query if ch.isdigit())
        if not digits_only:
            return []

        def digits_expr(field: str) -> str:
            """Return SQL expression that strips formatting from a phone field."""
            return (
                "REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE("
                f"COALESCE({field}, ''), '-', ''), '(', ''), ')', ''), ' ', ''), '.', ''), '+', '')"
            )

        phone_expr = digits_expr('phone')
        secondary_expr = digits_expr('secondary_contact')

        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                try:
                    cursor = conn.execute(
                        f"""
                            SELECT *
                            FROM patients
                            WHERE {phone_expr} = ?
                               OR {secondary_expr} = ?
                            ORDER BY last_name COLLATE NOCASE ASC,
                                     first_name COLLATE NOCASE ASC,
                                     dob ASC
                            LIMIT ?
                        """,
                        (digits_only, digits_only, limit)
                    )
                except sqlite3.OperationalError as exc:
                    # Older databases may not have the secondary_contact column yet.
                    if 'secondary_contact' not in str(exc).lower():
                        raise
                    cursor = conn.execute(
                        f"""
                            SELECT *
                            FROM patients
                            WHERE {phone_expr} = ?
                            ORDER BY last_name COLLATE NOCASE ASC,
                                     first_name COLLATE NOCASE ASC,
                                     dob ASC
                            LIMIT ?
                        """,
                        (digits_only, limit)
                    )
                rows = cursor.fetchall()
                return rows_to_dicts(rows)
        except Exception as e:
            debug_log(f"PatientRepository.search error: {e}")
            return []


class PrescriberRepository:
    """
    Prescriber data access with optional connection injection for UnitOfWork.
    """
    
    def __init__(self, conn: Optional[sqlite3.Connection] = None, folder_path: Optional[str] = None):
        """
        Initialize repository.
        
        Args:
            conn: Optional connection for UnitOfWork pattern
            folder_path: Database folder path (used when conn is None)
        """
        self._conn = conn
        self._folder_path = folder_path
    
    @contextmanager
    def _get_connection(self):
        """Get connection (provided or create new)."""
        if self._conn:
            yield self._conn
        else:
            conn = get_connection('prescribers.db', folder_path=self._folder_path)
            try:
                yield conn
            finally:
                conn.close()
    
    def get_all(self) -> List[Dict[str, Any]]:
        """
        Fetch all prescribers ordered by last_name, first_name.
        
        Returns:
            List of prescriber dicts
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT *
                    FROM prescribers
                    ORDER BY last_name COLLATE NOCASE ASC,
                             first_name COLLATE NOCASE ASC
                """)
                rows = cursor.fetchall()
                return rows_to_dicts(rows)
        except Exception as e:
            debug_log(f"PrescriberRepository.get_all error: {e}")
            return []
    
    def get_by_id(self, prescriber_id: int) -> Optional[Dict[str, Any]]:
        """
        Fetch prescriber by ID.
        
        Args:
            prescriber_id: Prescriber primary key
        
        Returns:
            Prescriber dict or None if not found
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM prescribers WHERE id = ?",
                    (prescriber_id,)
                )
                row = cursor.fetchone()
                return row_to_dict(row)
        except Exception as e:
            debug_log(f"PrescriberRepository.get_by_id({prescriber_id}) error: {e}")
            return None
    
    def get_by_npi(self, npi: str) -> Optional[Dict[str, Any]]:
        """
        Fetch prescriber by NPI number.
        
        Args:
            npi: NPI number (10 digits)
        
        Returns:
            Prescriber dict or None if not found
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM prescribers WHERE npi = ?",
                    (npi,)
                )
                row = cursor.fetchone()
                return row_to_dict(row)
        except Exception as e:
            debug_log(f"PrescriberRepository.get_by_npi({npi}) error: {e}")
            return None


class OrderRepository:
    """
    Order data access with optional connection injection for UnitOfWork.
    """
    
    def __init__(self, conn: Optional[sqlite3.Connection] = None, folder_path: Optional[str] = None):
        """
        Initialize repository.
        
        Args:
            conn: Optional connection for UnitOfWork pattern
            folder_path: Database folder path (used when conn is None)
        """
        self._conn = conn
        self._folder_path = folder_path
    
    @contextmanager
    def _get_connection(self):
        """Get connection (provided or create new)."""
        if self._conn:
            yield self._conn
        else:
            conn = get_connection('orders.db', folder_path=self._folder_path)
            try:
                yield conn
            finally:
                conn.close()
    
    def get_by_id(self, order_id: int) -> Optional[Dict[str, Any]]:
        """
        Fetch order by ID.
        
        Args:
            order_id: Order primary key
        
        Returns:
            Order dict or None if not found
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM orders WHERE id = ?",
                    (order_id,)
                )
                row = cursor.fetchone()
                return row_to_dict(row)
        except Exception as e:
            debug_log(f"OrderRepository.get_by_id({order_id}) error: {e}")
            return None
    
    def get_deleted_orders(self) -> List[Dict[str, Any]]:
        """
        Fetch all soft-deleted orders.
        
        Returns:
            List of deleted order dicts
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT id, original_order_id, rx_date, order_date, 
                           patient_last_name, patient_first_name, patient_dob,
                           prescriber_name, primary_insurance, order_status,
                           deleted_date, deleted_by
                    FROM deleted_orders
                    ORDER BY deleted_date DESC
                """)
                rows = cursor.fetchall()
                return rows_to_dicts(rows)
        except Exception as e:
            debug_log(f"OrderRepository.get_deleted_orders error: {e}")
            return []
    
    def soft_delete(self, order_id: int, deleted_by: str = "system") -> bool:
        """
        Soft delete an order by marking deleted_at timestamp.
        
        Args:
            order_id: Order ID to delete
            deleted_by: User or system identifier
        
        Returns:
            True if successful, False otherwise
        """
        from datetime import datetime
        
        try:
            with self._get_connection() as conn:
                deleted_at = datetime.now().isoformat()
                conn.execute("""
                    UPDATE orders 
                    SET deleted_at = ?, deleted_by = ?
                    WHERE order_id = ?
                """, (deleted_at, deleted_by, order_id))
                
                # If not part of UoW, commit now
                if not self._conn:
                    conn.commit()
                
                return True
        except Exception as e:
            debug_log(f"OrderRepository.soft_delete({order_id}) error: {e}")
            return False


class InventoryRepository:
    """
    Inventory data access with optional connection injection for UnitOfWork.
    """
    
    def __init__(self, conn: Optional[sqlite3.Connection] = None, folder_path: Optional[str] = None):
        """
        Initialize repository.
        
        Args:
            conn: Optional connection for UnitOfWork pattern
            folder_path: Database folder path (used when conn is None)
        """
        self._conn = conn
        self._folder_path = folder_path
    
    @contextmanager
    def _get_connection(self):
        """Get connection (provided or create new)."""
        if self._conn:
            yield self._conn
        else:
            conn = get_connection('inventory.db', folder_path=self._folder_path)
            try:
                yield conn
            finally:
                conn.close()
    
    def get_all(self) -> List[Dict[str, Any]]:
        """
        Fetch all inventory items.
        
        Returns:
            List of inventory item dicts
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT *
                    FROM inventory
                    ORDER BY hcpcs_code
                """)
                rows = cursor.fetchall()
                return rows_to_dicts(rows)
        except Exception as e:
            debug_log(f"InventoryRepository.get_all error: {e}")
            return []
    
    def get_by_hcpcs(self, hcpcs_code: str) -> Optional[Dict[str, Any]]:
        """
        Fetch inventory item by HCPCS code.
        
        Args:
            hcpcs_code: HCPCS code
        
        Returns:
            Inventory item dict or None if not found
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM inventory WHERE hcpcs_code = ?",
                    (hcpcs_code,)
                )
                row = cursor.fetchone()
                return row_to_dict(row)
        except Exception as e:
            debug_log(f"InventoryRepository.get_by_hcpcs({hcpcs_code}) error: {e}")
            return None
