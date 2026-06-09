"""
Database Cache Layer
====================
Caching wrapper around common DB queries to reduce SQLite reads.
Uses TTL-based in-memory caching with manual invalidation.

Feature #6: Performance Optimizations

Integration:
    Replace direct DB calls with cached versions:
    
    from dmelogic.db.cache import db_cache
    
    # Instead of: rows = fetch_all_inventory()
    # Use:        rows = db_cache.get_inventory()
    
    # Invalidate after writes:
    db_cache.invalidate("inventory")
    
    # Or invalidate everything:
    db_cache.invalidate_all()
"""

from __future__ import annotations

import time
import sqlite3
from typing import Optional, List, Dict, Any, Callable
from threading import Lock

from dmelogic.db.base import get_connection


class TTLCache:
    """Simple thread-safe TTL cache."""

    def __init__(self, default_ttl: int = 30):
        self._store: Dict[str, Any] = {}
        self._timestamps: Dict[str, float] = {}
        self._ttl = default_ttl
        self._lock = Lock()

    def get(self, key: str) -> Any:
        with self._lock:
            if key in self._store:
                age = time.time() - self._timestamps.get(key, 0)
                if age < self._ttl:
                    return self._store[key]
                else:
                    del self._store[key]
                    del self._timestamps[key]
        return None

    def set(self, key: str, value: Any):
        with self._lock:
            self._store[key] = value
            self._timestamps[key] = time.time()

    def invalidate(self, key: str):
        with self._lock:
            self._store.pop(key, None)
            self._timestamps.pop(key, None)

    def invalidate_all(self):
        with self._lock:
            self._store.clear()
            self._timestamps.clear()

    def get_or_load(self, key: str, loader: Callable) -> Any:
        """Return cached value or call loader and cache the result."""
        value = self.get(key)
        if value is not None:
            return value
        value = loader()
        self.set(key, value)
        return value


class DatabaseCache:
    """
    Application-level cache for frequently queried data.
    
    Caches:
    - Full inventory list (30s TTL)
    - Inventory categories (120s TTL)
    - Order status counts (15s TTL)
    - Patient count (60s TTL)
    """

    def __init__(self, folder_path: Optional[str] = None):
        self.folder_path = folder_path
        self._cache = TTLCache(default_ttl=30)
        self._long_cache = TTLCache(default_ttl=120)

    # ------------------------------------------------------------------ Inventory

    def get_inventory(self) -> List[Dict[str, Any]]:
        """Cached version of fetch_all_inventory()."""
        return self._cache.get_or_load("inventory_all", self._load_inventory)

    def get_inventory_categories(self) -> List[str]:
        """Cached distinct category list."""
        return self._long_cache.get_or_load("inventory_categories", self._load_categories)

    def get_inventory_count(self) -> int:
        """Cached inventory item count."""
        return self._cache.get_or_load("inventory_count", self._load_inventory_count)

    def get_low_stock_items(self) -> List[Dict[str, Any]]:
        """Cached low-stock items."""
        return self._cache.get_or_load("low_stock", self._load_low_stock)

    # ------------------------------------------------------------------ Orders

    def get_order_status_counts(self) -> Dict[str, int]:
        """Cached order status breakdown."""
        return self._cache.get_or_load("order_status_counts", self._load_order_status_counts)

    def get_recent_orders(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Cached recent orders."""
        return self._cache.get_or_load(f"recent_orders_{limit}", lambda: self._load_recent_orders(limit))

    # ------------------------------------------------------------------ Invalidation

    def invalidate(self, domain: str):
        """
        Invalidate cache for a domain.
        
        Args:
            domain: "inventory", "orders", "patients", or "all"
        """
        if domain == "inventory":
            for key in ["inventory_all", "inventory_categories", "inventory_count", "low_stock"]:
                self._cache.invalidate(key)
                self._long_cache.invalidate(key)
        elif domain == "orders":
            for key in ["order_status_counts", "recent_orders_10", "recent_orders_20"]:
                self._cache.invalidate(key)
        elif domain == "all":
            self._cache.invalidate_all()
            self._long_cache.invalidate_all()

    def invalidate_all(self):
        self.invalidate("all")

    # ------------------------------------------------------------------ Loaders

    def _load_inventory(self) -> List[Dict[str, Any]]:
        try:
            conn = get_connection("inventory.db", folder_path=self.folder_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM inventory
                ORDER BY category COLLATE NOCASE, hcpcs_code COLLATE NOCASE
            """)
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return rows
        except Exception:
            return []

    def _load_categories(self) -> List[str]:
        try:
            conn = get_connection("inventory.db", folder_path=self.folder_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT category FROM inventory WHERE category IS NOT NULL ORDER BY category")
            cats = [row["category"] for row in cur.fetchall()]
            conn.close()
            return cats
        except Exception:
            return []

    def _load_inventory_count(self) -> int:
        try:
            conn = get_connection("inventory.db", folder_path=self.folder_path)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM inventory")
            count = cur.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

    def _load_low_stock(self) -> List[Dict[str, Any]]:
        try:
            conn = get_connection("inventory.db", folder_path=self.folder_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT hcpcs_code, description, stock_quantity, reorder_level
                FROM inventory
                WHERE (stock_quantity IS NOT NULL AND stock_quantity <= 0)
                   OR (reorder_level > 0 AND stock_quantity <= reorder_level)
                ORDER BY stock_quantity ASC
            """)
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return rows
        except Exception:
            return []

    def _load_order_status_counts(self) -> Dict[str, int]:
        try:
            conn = get_connection("orders.db", folder_path=self.folder_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT order_status, COUNT(*) as cnt FROM orders GROUP BY order_status")
            counts = {row["order_status"]: row["cnt"] for row in cur.fetchall()}
            conn.close()
            return counts
        except Exception:
            return {}

    def _load_recent_orders(self, limit: int) -> List[Dict[str, Any]]:
        try:
            conn = get_connection("orders.db", folder_path=self.folder_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,))
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return rows
        except Exception:
            return []


# ═══════════════════════════════════════════════════════════════════
# Singleton instance
# ═══════════════════════════════════════════════════════════════════

db_cache = DatabaseCache()
