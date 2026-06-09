"""
Communications Log Database Repository

Stores all SMS, Fax, and Call communications for audit and history.
Links communications to patients and orders.

Table Schema:
    communications_log:
        id              INTEGER PRIMARY KEY
        patient_id      INTEGER     (FK to patients.id, nullable)
        order_id        TEXT        (FK to orders.order_id, nullable)
        direction       TEXT        ('outbound' or 'inbound')
        channel         TEXT        ('sms', 'fax', 'call')
        remote_id       TEXT        (RingCentral message/call ID)
        to_number       TEXT        (recipient phone/fax)
        from_number     TEXT        (sender phone/fax)
        status          TEXT        ('sent', 'delivered', 'failed', 'queued', etc.)
        subject         TEXT        (cover page text for fax, null for sms/call)
        body            TEXT        (message content for sms, null for fax/call)
        attachment_path TEXT        (path to fax document, if any)
        error_message   TEXT        (error details if failed)
        created_by      TEXT        (username who initiated)
        created_at      TEXT        (ISO timestamp)
        updated_at      TEXT        (ISO timestamp, for status updates)
        metadata        TEXT        (JSON for additional data)
"""

import sqlite3
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from dmelogic.db.base import get_connection, row_to_dict, rows_to_dicts
from dmelogic.config import debug_log


COMMUNICATIONS_DB = "communications.db"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create communications_log table if not exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS communications_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id      INTEGER,
            order_id        TEXT,
            direction       TEXT NOT NULL DEFAULT 'outbound',
            channel         TEXT NOT NULL,
            remote_id       TEXT,
            to_number       TEXT NOT NULL,
            from_number     TEXT,
            status          TEXT NOT NULL DEFAULT 'queued',
            subject         TEXT,
            body            TEXT,
            attachment_path TEXT,
            error_message   TEXT,
            created_by      TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT,
            metadata        TEXT
        )
    """)
    
    # Indexes for common queries
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_comm_patient_id 
        ON communications_log(patient_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_comm_order_id 
        ON communications_log(order_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_comm_channel 
        ON communications_log(channel)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_comm_created_at 
        ON communications_log(created_at)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_comm_remote_id 
        ON communications_log(remote_id)
    """)
    
    conn.commit()


class CommunicationsRepository:
    """
    Repository for communications log database access.
    
    Supports both standalone usage and UnitOfWork pattern.
    """
    
    def __init__(
        self,
        conn: Optional[sqlite3.Connection] = None,
        folder_path: Optional[str] = None
    ):
        """
        Initialize repository.
        
        Args:
            conn: Optional connection for UnitOfWork pattern
            folder_path: Database folder path (used when conn is None)
        """
        self._conn = conn
        self._folder_path = folder_path
        self._schema_ensured = False
    
    @contextmanager
    def _get_connection(self):
        """Get connection (provided or create new)."""
        if self._conn:
            if not self._schema_ensured:
                _ensure_schema(self._conn)
                self._schema_ensured = True
            yield self._conn
        else:
            conn = get_connection(COMMUNICATIONS_DB, folder_path=self._folder_path)
            try:
                if not self._schema_ensured:
                    _ensure_schema(conn)
                    self._schema_ensured = True
                yield conn
            finally:
                conn.close()
    
    def log_communication(
        self,
        channel: str,
        to_number: str,
        status: str,
        direction: str = "outbound",
        patient_id: Optional[int] = None,
        order_id: Optional[str] = None,
        remote_id: Optional[str] = None,
        from_number: Optional[str] = None,
        subject: Optional[str] = None,
        body: Optional[str] = None,
        attachment_path: Optional[str] = None,
        error_message: Optional[str] = None,
        created_by: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Log a communication event.
        
        Args:
            channel: 'sms', 'fax', or 'call'
            to_number: Recipient phone/fax number
            status: 'sent', 'delivered', 'failed', 'queued', etc.
            direction: 'outbound' or 'inbound'
            patient_id: Optional patient ID
            order_id: Optional order ID
            remote_id: RingCentral message/call ID
            from_number: Sender phone number
            subject: Cover text for fax
            body: Message content for SMS
            attachment_path: Path to fax document
            error_message: Error details if failed
            created_by: Username who initiated
            metadata: Additional JSON data
            
        Returns:
            The new communication log ID
        """
        now = datetime.now().isoformat()
        metadata_json = json.dumps(metadata) if metadata else None
        
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    INSERT INTO communications_log (
                        patient_id, order_id, direction, channel, remote_id,
                        to_number, from_number, status, subject, body,
                        attachment_path, error_message, created_by, created_at,
                        updated_at, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    patient_id, order_id, direction, channel, remote_id,
                    to_number, from_number, status, subject, body,
                    attachment_path, error_message, created_by, now,
                    now, metadata_json
                ))
                conn.commit()
                log_id = cursor.lastrowid
                debug_log(f"Communications: Logged {channel} to {to_number} (id={log_id})")
                return log_id
                
        except Exception as e:
            debug_log(f"Communications: Failed to log: {e}")
            raise
    
    def update_status(
        self,
        log_id: int,
        status: str,
        error_message: Optional[str] = None,
        remote_id: Optional[str] = None,
        metadata_update: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Update the status of a logged communication.
        
        Args:
            log_id: Communication log ID
            status: New status
            error_message: Error details if failed
            remote_id: RingCentral ID if now available
            metadata_update: Additional metadata to merge
            
        Returns:
            True if updated
        """
        now = datetime.now().isoformat()
        
        try:
            with self._get_connection() as conn:
                # Get existing record for metadata merge
                if metadata_update:
                    cursor = conn.execute(
                        "SELECT metadata FROM communications_log WHERE id = ?",
                        (log_id,)
                    )
                    row = cursor.fetchone()
                    existing_meta = json.loads(row[0]) if row and row[0] else {}
                    existing_meta.update(metadata_update)
                    metadata_json = json.dumps(existing_meta)
                else:
                    metadata_json = None
                
                # Build update query
                updates = ["status = ?", "updated_at = ?"]
                params = [status, now]
                
                if error_message is not None:
                    updates.append("error_message = ?")
                    params.append(error_message)
                
                if remote_id is not None:
                    updates.append("remote_id = ?")
                    params.append(remote_id)
                
                if metadata_json is not None:
                    updates.append("metadata = ?")
                    params.append(metadata_json)
                
                params.append(log_id)
                
                conn.execute(
                    f"UPDATE communications_log SET {', '.join(updates)} WHERE id = ?",
                    params
                )
                conn.commit()
                return True
                
        except Exception as e:
            debug_log(f"Communications: Failed to update status: {e}")
            return False
    
    def get_by_id(self, log_id: int) -> Optional[Dict[str, Any]]:
        """Get a single communication log entry by ID."""
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM communications_log WHERE id = ?",
                    (log_id,)
                )
                row = cursor.fetchone()
                return row_to_dict(row)
        except Exception as e:
            debug_log(f"Communications: get_by_id error: {e}")
            return None
    
    def get_by_remote_id(self, remote_id: str) -> Optional[Dict[str, Any]]:
        """Get a communication log entry by RingCentral message/call ID."""
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM communications_log WHERE remote_id = ?",
                    (remote_id,)
                )
                row = cursor.fetchone()
                return row_to_dict(row)
        except Exception as e:
            debug_log(f"Communications: get_by_remote_id error: {e}")
            return None
    
    def get_for_patient(
        self,
        patient_id: int,
        channel: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get communications for a patient.
        
        Args:
            patient_id: Patient ID
            channel: Optional filter by channel ('sms', 'fax', 'call')
            limit: Maximum results
            
        Returns:
            List of communication dicts, most recent first
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                
                if channel:
                    cursor = conn.execute("""
                        SELECT * FROM communications_log
                        WHERE patient_id = ? AND channel = ?
                        ORDER BY created_at DESC
                        LIMIT ?
                    """, (patient_id, channel, limit))
                else:
                    cursor = conn.execute("""
                        SELECT * FROM communications_log
                        WHERE patient_id = ?
                        ORDER BY created_at DESC
                        LIMIT ?
                    """, (patient_id, limit))
                
                return rows_to_dicts(cursor.fetchall())
                
        except Exception as e:
            debug_log(f"Communications: get_for_patient error: {e}")
            return []
    
    def get_for_order(
        self,
        order_id: str,
        channel: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get communications for an order.
        
        Args:
            order_id: Order ID
            channel: Optional filter by channel
            limit: Maximum results
            
        Returns:
            List of communication dicts, most recent first
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                
                if channel:
                    cursor = conn.execute("""
                        SELECT * FROM communications_log
                        WHERE order_id = ? AND channel = ?
                        ORDER BY created_at DESC
                        LIMIT ?
                    """, (order_id, channel, limit))
                else:
                    cursor = conn.execute("""
                        SELECT * FROM communications_log
                        WHERE order_id = ?
                        ORDER BY created_at DESC
                        LIMIT ?
                    """, (order_id, limit))
                
                return rows_to_dicts(cursor.fetchall())
                
        except Exception as e:
            debug_log(f"Communications: get_for_order error: {e}")
            return []
    
    def search(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        channel: Optional[str] = None,
        status: Optional[str] = None,
        to_number: Optional[str] = None,
        direction: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Search communications with filters.
        
        Args:
            start_date: ISO date string for start of range
            end_date: ISO date string for end of range
            channel: Filter by channel
            status: Filter by status
            to_number: Filter by recipient (partial match)
            direction: Filter by direction
            limit: Maximum results
            
        Returns:
            List of communication dicts
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                
                query = "SELECT * FROM communications_log WHERE 1=1"
                params = []
                
                if start_date:
                    query += " AND created_at >= ?"
                    params.append(start_date)
                
                if end_date:
                    query += " AND created_at <= ?"
                    params.append(end_date + "T23:59:59")
                
                if channel:
                    query += " AND channel = ?"
                    params.append(channel)
                
                if status:
                    query += " AND status = ?"
                    params.append(status)
                
                if to_number:
                    query += " AND to_number LIKE ?"
                    params.append(f"%{to_number}%")
                
                if direction:
                    query += " AND direction = ?"
                    params.append(direction)
                
                query += " ORDER BY created_at DESC LIMIT ?"
                params.append(limit)
                
                cursor = conn.execute(query, params)
                return rows_to_dicts(cursor.fetchall())
                
        except Exception as e:
            debug_log(f"Communications: search error: {e}")
            return []
    
    def get_stats(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get communication statistics.
        
        Returns:
            Dict with counts by channel and status
        """
        try:
            with self._get_connection() as conn:
                query_base = "SELECT channel, status, COUNT(*) as count FROM communications_log"
                params = []
                
                if start_date or end_date:
                    conditions = []
                    if start_date:
                        conditions.append("created_at >= ?")
                        params.append(start_date)
                    if end_date:
                        conditions.append("created_at <= ?")
                        params.append(end_date + "T23:59:59")
                    query_base += " WHERE " + " AND ".join(conditions)
                
                query_base += " GROUP BY channel, status"
                
                cursor = conn.execute(query_base, params)
                rows = cursor.fetchall()
                
                stats = {
                    'sms': {'sent': 0, 'delivered': 0, 'failed': 0, 'total': 0},
                    'fax': {'sent': 0, 'delivered': 0, 'failed': 0, 'queued': 0, 'total': 0},
                    'call': {'completed': 0, 'missed': 0, 'failed': 0, 'total': 0},
                    'total': 0
                }
                
                for channel, status, count in rows:
                    if channel in stats:
                        stats[channel][status] = stats[channel].get(status, 0) + count
                        stats[channel]['total'] += count
                        stats['total'] += count
                
                return stats
                
        except Exception as e:
            debug_log(f"Communications: get_stats error: {e}")
            return {'total': 0}
    
    def delete_old_logs(self, days: int = 365) -> int:
        """
        Delete communication logs older than specified days.
        
        Args:
            days: Delete logs older than this many days
            
        Returns:
            Number of deleted records
        """
        try:
            with self._get_connection() as conn:
                cutoff = datetime.now().isoformat()[:10]  # Date only
                # Calculate cutoff date
                from datetime import timedelta
                cutoff_dt = datetime.now() - timedelta(days=days)
                cutoff = cutoff_dt.isoformat()
                
                cursor = conn.execute(
                    "DELETE FROM communications_log WHERE created_at < ?",
                    (cutoff,)
                )
                conn.commit()
                deleted = cursor.rowcount
                debug_log(f"Communications: Deleted {deleted} old logs")
                return deleted
                
        except Exception as e:
            debug_log(f"Communications: delete_old_logs error: {e}")
            return 0


# Convenience function for logging from anywhere
def log_communication(**kwargs) -> int:
    """
    Convenience function to log a communication.
    
    See CommunicationsRepository.log_communication for args.
    """
    repo = CommunicationsRepository()
    return repo.log_communication(**kwargs)
