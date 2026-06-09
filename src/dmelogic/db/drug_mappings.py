"""
Drug-to-HCPCS Learning Mappings
================================
Stores and learns associations between drug/product names from Rx PDFs
and HCPCS codes from your inventory. Gets smarter with every use.

Schema:
    drug_hcpcs_mappings:
        id, drug_name_normalized, hcpcs_code, description, confidence,
        use_count, last_used, created_at

    drug_name_aliases:
        id, alias_normalized, canonical_name, created_at

How it learns:
    1. When user confirms a drug→HCPCS mapping, we store it
    2. Next time the same drug name appears, we return it instantly
    3. Fuzzy matching finds similar drug names (Levenshtein)
    4. Confidence increases with use_count
    5. Aliases handle variations ("Depend Underwear Large" → "Depend Underwear")

Usage:
    from dmelogic.db.drug_mappings import DrugMapper

    mapper = DrugMapper(folder_path=...)
    
    # Look up a mapping
    result = mapper.find_hcpcs("DISPOSABLE UNDERPADS")
    # → {"hcpcs": "A4554", "description": "...", "confidence": 0.95, "use_count": 12}
    
    # Store a confirmed mapping (called when user confirms in wizard)
    mapper.learn("DISPOSABLE UNDERPADS", "A4554", "Disposable Underpads")
    
    # Find fuzzy matches
    candidates = mapper.suggest("Disposable Under")
    # → [{"drug_name": "DISPOSABLE UNDERPADS", "hcpcs": "A4554", ...}, ...]
"""

from __future__ import annotations

import re
import sqlite3
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from dmelogic.db.base import get_connection


@dataclass
class MappingResult:
    """Result from a drug→HCPCS lookup."""
    hcpcs: str
    description: str
    drug_name: str
    confidence: float          # 0.0 to 1.0
    use_count: int
    source: str = "learned"    # "learned", "fuzzy", "inventory"


class DrugMapper:
    """
    Learning mapper from drug/product names to HCPCS codes.
    
    Stores mappings in orders.db alongside order data.
    Uses normalized names for consistent matching.
    """

    def __init__(self, folder_path: Optional[str] = None):
        self.folder_path = folder_path
        self._ensure_tables()

    # ------------------------------------------------------------------ Schema

    def _ensure_tables(self):
        """Create mapping tables if they don't exist."""
        conn = get_connection("orders.db", folder_path=self.folder_path)
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS drug_hcpcs_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                drug_name_normalized TEXT NOT NULL,
                hcpcs_code TEXT NOT NULL,
                description TEXT DEFAULT '',
                confidence REAL DEFAULT 0.5,
                use_count INTEGER DEFAULT 1,
                last_used TEXT DEFAULT (datetime('now')),
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(drug_name_normalized, hcpcs_code)
            )
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS drug_name_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alias_normalized TEXT NOT NULL UNIQUE,
                canonical_name TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        
        # Index for fast lookups
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_drug_mappings_name
            ON drug_hcpcs_mappings(drug_name_normalized)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_drug_aliases
            ON drug_name_aliases(alias_normalized)
        """)
        
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------ Normalize

    @staticmethod
    def normalize(name: str) -> str:
        """
        Normalize a drug/product name for consistent matching.
        
        - Uppercase
        - Remove "Unspecified" suffix (common in e-Rx)
        - Strip extra whitespace
        - Remove common noise words
        """
        if not name:
            return ""
        n = name.upper().strip()
        # Remove common suffixes that don't help matching
        for noise in ["UNSPECIFIED", "UNSPEC", "NOS", "N.O.S."]:
            n = n.replace(noise, "")
        # Remove extra whitespace
        n = re.sub(r"\s+", " ", n).strip()
        return n

    # ------------------------------------------------------------------ Lookup

    def find_hcpcs(self, drug_name: str) -> Optional[MappingResult]:
        """
        Look up a drug name and return the best HCPCS mapping.
        
        Search order:
        1. Exact normalized match in mappings table
        2. Alias table → canonical name → mappings
        3. Fuzzy match against mappings table
        4. Fuzzy match against inventory descriptions
        
        Returns the best match or None.
        """
        norm = self.normalize(drug_name)
        if not norm:
            return None

        # 1) Exact match in mappings
        result = self._exact_match(norm)
        if result:
            return result

        # 2) Alias lookup
        canonical = self._resolve_alias(norm)
        if canonical and canonical != norm:
            result = self._exact_match(canonical)
            if result:
                result.source = "learned"
                return result

        # 3) Fuzzy match against mappings
        result = self._fuzzy_match_mappings(norm)
        if result and result.confidence >= 0.6:
            return result

        # 4) Fuzzy match against inventory
        result = self._fuzzy_match_inventory(norm)
        if result:
            return result

        return None

    def suggest(self, drug_name: str, limit: int = 5) -> List[MappingResult]:
        """
        Return a ranked list of HCPCS suggestions for a drug name.
        Includes both learned mappings and inventory fuzzy matches.
        """
        norm = self.normalize(drug_name)
        if not norm:
            return []

        results: List[MappingResult] = []

        # Get all learned mappings that are close
        conn = get_connection("orders.db", folder_path=self.folder_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # LIKE-based fuzzy
        words = norm.split()
        if words:
            like_clauses = " AND ".join(
                f"drug_name_normalized LIKE ?"
                for _ in words
            )
            params = [f"%{w}%" for w in words]
            cur.execute(f"""
                SELECT drug_name_normalized, hcpcs_code, description, confidence, use_count
                FROM drug_hcpcs_mappings
                WHERE {like_clauses}
                ORDER BY use_count DESC, confidence DESC
                LIMIT ?
            """, params + [limit])

            for row in cur.fetchall():
                results.append(MappingResult(
                    hcpcs=row["hcpcs_code"],
                    description=row["description"],
                    drug_name=row["drug_name_normalized"],
                    confidence=row["confidence"],
                    use_count=row["use_count"],
                    source="learned",
                ))
        conn.close()

        # Also search inventory
        inv_results = self._search_inventory_fuzzy(norm, limit=limit)
        results.extend(inv_results)

        # De-dup by HCPCS
        seen = set()
        unique = []
        for r in results:
            if r.hcpcs not in seen:
                seen.add(r.hcpcs)
                unique.append(r)
        
        return unique[:limit]

    # ------------------------------------------------------------------ Learn

    def learn(self, drug_name: str, hcpcs_code: str, description: str = "") -> None:
        """
        Store or reinforce a drug→HCPCS mapping.
        Called when user confirms a mapping in the import wizard.
        
        If the mapping exists, increments use_count and confidence.
        If new, creates it with initial confidence.
        """
        norm = self.normalize(drug_name)
        hcpcs = hcpcs_code.strip().upper()
        if not norm or not hcpcs:
            return

        conn = get_connection("orders.db", folder_path=self.folder_path)
        cur = conn.cursor()

        # Check if mapping exists
        cur.execute("""
            SELECT id, use_count, confidence FROM drug_hcpcs_mappings
            WHERE drug_name_normalized = ? AND hcpcs_code = ?
        """, (norm, hcpcs))
        row = cur.fetchone()

        if row:
            # Reinforce: increment count and boost confidence
            new_count = row[1] + 1
            # Confidence grows with use: starts at 0.5, approaches 1.0
            new_conf = min(0.99, 0.5 + (new_count / (new_count + 3)) * 0.5)
            cur.execute("""
                UPDATE drug_hcpcs_mappings
                SET use_count = ?, confidence = ?, last_used = datetime('now'),
                    description = COALESCE(NULLIF(?, ''), description)
                WHERE id = ?
            """, (new_count, new_conf, description, row[0]))
        else:
            # New mapping
            cur.execute("""
                INSERT INTO drug_hcpcs_mappings (drug_name_normalized, hcpcs_code, description, confidence, use_count)
                VALUES (?, ?, ?, 0.5, 1)
            """, (norm, hcpcs, description))

        conn.commit()
        conn.close()

    def learn_alias(self, alias: str, canonical: str) -> None:
        """Store an alias mapping (e.g., 'DEPEND UNDERWEAR LARGE' → 'DEPEND UNDERWEAR')."""
        alias_n = self.normalize(alias)
        canon_n = self.normalize(canonical)
        if not alias_n or not canon_n or alias_n == canon_n:
            return

        conn = get_connection("orders.db", folder_path=self.folder_path)
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT OR REPLACE INTO drug_name_aliases (alias_normalized, canonical_name)
                VALUES (?, ?)
            """, (alias_n, canon_n))
            conn.commit()
        except Exception:
            pass
        conn.close()

    def get_all_mappings(self) -> List[Dict[str, Any]]:
        """Return all stored mappings for admin/debug viewing."""
        conn = get_connection("orders.db", folder_path=self.folder_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM drug_hcpcs_mappings
            ORDER BY use_count DESC, drug_name_normalized
        """)
        results = [dict(r) for r in cur.fetchall()]
        conn.close()
        return results

    # ------------------------------------------------------------------ Internal matchers

    def _exact_match(self, norm: str) -> Optional[MappingResult]:
        conn = get_connection("orders.db", folder_path=self.folder_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT drug_name_normalized, hcpcs_code, description, confidence, use_count
            FROM drug_hcpcs_mappings
            WHERE drug_name_normalized = ?
            ORDER BY use_count DESC
            LIMIT 1
        """, (norm,))
        row = cur.fetchone()
        conn.close()

        if not row:
            return None
        return MappingResult(
            hcpcs=row["hcpcs_code"],
            description=row["description"],
            drug_name=row["drug_name_normalized"],
            confidence=row["confidence"],
            use_count=row["use_count"],
            source="learned",
        )

    def _resolve_alias(self, norm: str) -> Optional[str]:
        conn = get_connection("orders.db", folder_path=self.folder_path)
        cur = conn.cursor()
        cur.execute("SELECT canonical_name FROM drug_name_aliases WHERE alias_normalized = ?", (norm,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None

    def _fuzzy_match_mappings(self, norm: str) -> Optional[MappingResult]:
        """LIKE-based fuzzy match against stored mappings."""
        conn = get_connection("orders.db", folder_path=self.folder_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Try matching on first significant word
        words = norm.split()
        if not words:
            conn.close()
            return None

        # Use longest word as anchor
        anchor = max(words, key=len)
        if len(anchor) < 3:
            conn.close()
            return None

        cur.execute("""
            SELECT drug_name_normalized, hcpcs_code, description, confidence, use_count
            FROM drug_hcpcs_mappings
            WHERE drug_name_normalized LIKE ?
            ORDER BY use_count DESC
            LIMIT 5
        """, (f"%{anchor}%",))

        best = None
        best_score = 0
        for row in cur.fetchall():
            stored_name = row["drug_name_normalized"]
            score = self._similarity(norm, stored_name)
            if score > best_score:
                best_score = score
                best = MappingResult(
                    hcpcs=row["hcpcs_code"],
                    description=row["description"],
                    drug_name=stored_name,
                    confidence=min(row["confidence"], score),
                    use_count=row["use_count"],
                    source="fuzzy",
                )
        conn.close()
        return best

    def _fuzzy_match_inventory(self, norm: str) -> Optional[MappingResult]:
        """Search inventory descriptions for a match."""
        results = self._search_inventory_fuzzy(norm, limit=1)
        return results[0] if results else None

    def _search_inventory_fuzzy(self, norm: str, limit: int = 5) -> List[MappingResult]:
        """Search inventory table for items matching the drug name."""
        results = []
        try:
            conn = get_connection("inventory.db", folder_path=self.folder_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            words = norm.split()
            if not words:
                conn.close()
                return []

            # Build WHERE clause: all words must match in EITHER description OR hcpcs
            clauses = []
            params = []
            for w in words:
                if len(w) < 2:
                    continue
                clauses.append("(UPPER(description) LIKE ? OR UPPER(hcpcs_code) LIKE ? OR UPPER(category) LIKE ?)")
                params.extend([f"%{w}%", f"%{w}%", f"%{w}%"])

            if not clauses:
                conn.close()
                return []

            where = " AND ".join(clauses)
            cur.execute(f"""
                SELECT hcpcs_code, description, category
                FROM inventory
                WHERE {where}
                ORDER BY hcpcs_code
                LIMIT ?
            """, params + [limit])

            for row in cur.fetchall():
                desc_upper = (row["description"] or "").upper()
                score = self._similarity(norm, desc_upper)
                results.append(MappingResult(
                    hcpcs=row["hcpcs_code"],
                    description=row["description"],
                    drug_name=norm,
                    confidence=score * 0.7,  # Lower confidence for inventory-only matches
                    use_count=0,
                    source="inventory",
                ))
            conn.close()
        except Exception as e:
            print(f"DrugMapper inventory search error: {e}")
        return results

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        """
        Simple word-overlap similarity score (0.0 to 1.0).
        Fast alternative to full Levenshtein for our use case.
        """
        if not a or not b:
            return 0.0
        words_a = set(a.upper().split())
        words_b = set(b.upper().split())
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        union = words_a | words_b
        return len(intersection) / len(union)
