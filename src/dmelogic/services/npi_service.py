"""
NPI Registry Service with Caching

Provides prescriber lookup from CMS NPI Registry with local caching to:
1. Reduce API calls
2. Improve response time
3. Work offline for previously looked-up prescribers
4. Handle API errors gracefully

Database schema:
    npi_cache table stores recent lookups with full prescriber data
"""
import sqlite3
import json
import time
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from pathlib import Path

import requests


class NPILookupService:
    """
    NPI Registry lookup service with local caching.
    
    Features:
    - Caches successful lookups for 30 days
    - Handles API timeouts gracefully
    - Provides detailed error messages
    - Supports both NPI number and name search
    """
    
    # CMS NPI Registry API endpoint
    NPI_API_URL = "https://npiregistry.cms.hhs.gov/api/"
    
    # Timeout for API requests (seconds)
    API_TIMEOUT = 10
    
    # Cache duration (days)
    CACHE_DAYS = 30
    
    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize NPI lookup service with caching.
        
        Args:
            db_path: Path to SQLite database for caching (defaults to npi_cache.db)
        """
        if db_path is None:
            db_path = "npi_cache.db"
        
        self.db_path = Path(db_path)
        self._ensure_cache_db()
    
    def _ensure_cache_db(self):
        """Create cache database and table if not present."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS npi_cache (
                    npi TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    cached_at REAL NOT NULL,
                    last_accessed REAL NOT NULL
                )
            """)
            
            # Index for cleanup queries
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cached_at 
                ON npi_cache(cached_at)
            """)
            
            conn.commit()
    
    def _get_from_cache(self, npi: str) -> Optional[Dict[str, Any]]:
        """
        Get prescriber data from cache if available and not expired.
        
        Args:
            npi: NPI number
        
        Returns:
            Cached prescriber data or None if not in cache/expired
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Calculate expiration timestamp
            expire_time = time.time() - (self.CACHE_DAYS * 24 * 60 * 60)
            
            result = cursor.execute("""
                SELECT data, cached_at
                FROM npi_cache
                WHERE npi = ? AND cached_at > ?
            """, (npi, expire_time)).fetchone()
            
            if result:
                # Update last accessed time
                conn.execute("""
                    UPDATE npi_cache
                    SET last_accessed = ?
                    WHERE npi = ?
                """, (time.time(), npi))
                conn.commit()
                
                return json.loads(result['data'])
            
            return None
    
    def _save_to_cache(self, npi: str, data: Dict[str, Any]):
        """
        Save prescriber data to cache.
        
        Args:
            npi: NPI number
            data: Prescriber data dictionary
        """
        with sqlite3.connect(self.db_path) as conn:
            now = time.time()
            conn.execute("""
                INSERT OR REPLACE INTO npi_cache (npi, data, cached_at, last_accessed)
                VALUES (?, ?, ?, ?)
            """, (npi, json.dumps(data), now, now))
            conn.commit()
    
    def cleanup_old_cache(self, days: int = 90):
        """
        Remove cache entries older than specified days.
        
        Args:
            days: Remove entries cached more than this many days ago
        """
        expire_time = time.time() - (days * 24 * 60 * 60)
        
        with sqlite3.connect(self.db_path) as conn:
            result = conn.execute("""
                DELETE FROM npi_cache
                WHERE cached_at < ?
            """, (expire_time,))
            
            deleted = result.rowcount
            conn.commit()
            
            return deleted
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache statistics
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            total = cursor.execute("SELECT COUNT(*) FROM npi_cache").fetchone()[0]
            
            expire_time = time.time() - (self.CACHE_DAYS * 24 * 60 * 60)
            valid = cursor.execute(
                "SELECT COUNT(*) FROM npi_cache WHERE cached_at > ?",
                (expire_time,)
            ).fetchone()[0]
            
            if total > 0:
                oldest = cursor.execute(
                    "SELECT MIN(cached_at) FROM npi_cache"
                ).fetchone()[0]
                newest = cursor.execute(
                    "SELECT MAX(cached_at) FROM npi_cache"
                ).fetchone()[0]
                
                oldest_date = datetime.fromtimestamp(oldest).strftime("%Y-%m-%d %H:%M")
                newest_date = datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M")
            else:
                oldest_date = newest_date = "N/A"
            
            return {
                "total_entries": total,
                "valid_entries": valid,
                "expired_entries": total - valid,
                "oldest_entry": oldest_date,
                "newest_entry": newest_date,
                "cache_days": self.CACHE_DAYS,
                "db_path": str(self.db_path)
            }
    
    def lookup_by_npi(
        self, 
        npi: str, 
        use_cache: bool = True
    ) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Look up prescriber by NPI number.
        
        Args:
            npi: NPI number (10 digits)
            use_cache: Whether to use cached data if available
        
        Returns:
            tuple: (prescriber_data, error_message)
                   prescriber_data is None if lookup failed
                   error_message is None if successful
        """
        # Validate NPI format
        if not npi.isdigit() or len(npi) != 10:
            return None, "NPI must be exactly 10 digits"
        
        # Check cache first
        if use_cache:
            cached_data = self._get_from_cache(npi)
            if cached_data:
                print(f"[CACHE HIT] NPI {npi}")
                return cached_data, None
        
        # Query NPI Registry API
        try:
            params = {
                "version": "2.1",
                "number": npi
            }
            
            print(f"[API CALL] Looking up NPI {npi}...")
            response = requests.get(
                self.NPI_API_URL,
                params=params,
                timeout=self.API_TIMEOUT
            )
            
            response.raise_for_status()
            data = response.json()
            
            if data.get("result_count", 0) == 0:
                return None, f"No prescriber found with NPI {npi}"
            
            # Extract first result
            result = data["results"][0]
            prescriber_data = self._extract_prescriber_data(result)
            
            # Cache successful lookup
            self._save_to_cache(npi, prescriber_data)
            
            return prescriber_data, None
        
        except requests.Timeout:
            return None, (
                "NPI Registry request timed out.\n"
                "Please check your internet connection and try again."
            )
        
        except requests.ConnectionError as e:
            return None, (
                f"Failed to connect to NPI Registry.\n"
                f"Please check your internet connection.\n\n"
                f"Error: {e}"
            )
        
        except requests.HTTPError as e:
            return None, (
                f"NPI Registry returned an error.\n"
                f"Status: {e.response.status_code}\n\n"
                f"Error: {e}"
            )
        
        except Exception as e:
            return None, f"Unexpected error during NPI lookup:\n{e}"
    
    def lookup_by_name(
        self,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        state: Optional[str] = None,
        limit: int = 10
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """
        Look up prescribers by name.
        
        Args:
            first_name: First name to search
            last_name: Last name to search
            state: State abbreviation (e.g., "CA")
            limit: Maximum number of results (1-200)
        
        Returns:
            tuple: (results_list, error_message)
                   results_list is empty if lookup failed
                   error_message is None if successful
        """
        if not first_name and not last_name:
            return [], "Please provide at least a first or last name"
        
        # Build query parameters
        params = {
            "version": "2.1",
            "limit": min(limit, 200)
        }
        
        if first_name:
            params["first_name"] = first_name.strip()
        if last_name:
            params["last_name"] = last_name.strip()
        if state:
            params["state"] = state.strip().upper()
        
        # Query NPI Registry API
        try:
            print(f"[API CALL] Name search: {params}")
            response = requests.get(
                self.NPI_API_URL,
                params=params,
                timeout=self.API_TIMEOUT
            )
            
            response.raise_for_status()
            data = response.json()
            
            if data.get("result_count", 0) == 0:
                return [], "No prescribers found matching search criteria"
            
            # Extract and cache all results
            results = []
            for result in data["results"]:
                prescriber_data = self._extract_prescriber_data(result)
                results.append(prescriber_data)
                
                # Cache each result
                if prescriber_data.get("npi"):
                    self._save_to_cache(prescriber_data["npi"], prescriber_data)
            
            return results, None
        
        except requests.Timeout:
            return [], (
                "NPI Registry request timed out.\n"
                "Please check your internet connection and try again."
            )
        
        except requests.ConnectionError as e:
            return [], (
                f"Failed to connect to NPI Registry.\n"
                f"Please check your internet connection.\n\n"
                f"Error: {e}"
            )
        
        except requests.HTTPError as e:
            return [], (
                f"NPI Registry returned an error.\n"
                f"Status: {e.response.status_code}\n\n"
                f"Error: {e}"
            )
        
        except Exception as e:
            return [], f"Unexpected error during NPI lookup:\n{e}"
    
    def _extract_prescriber_data(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract prescriber data from NPI Registry result.
        
        Args:
            result: Raw result from NPI Registry API
        
        Returns:
            Standardized prescriber data dictionary
        """
        basic = result.get("basic", {})
        
        # Get name (handle both individual and organization)
        first_name = basic.get("first_name", "")
        last_name = basic.get("last_name", "")
        
        if not last_name:
            # Organization
            last_name = basic.get("organization_name", "")
        
        # Get primary practice address
        addresses = result.get("addresses", [])
        address_data = {}
        
        for addr in addresses:
            if addr.get("address_purpose") == "LOCATION":
                address_data = addr
                break
        
        if not address_data and addresses:
            address_data = addresses[0]
        
        # Get primary taxonomy (specialty)
        taxonomies = result.get("taxonomies", [])
        primary_taxonomy = {}
        
        for tax in taxonomies:
            if tax.get("primary"):
                primary_taxonomy = tax
                break
        
        if not primary_taxonomy and taxonomies:
            primary_taxonomy = taxonomies[0]
        
        specialty = primary_taxonomy.get("desc", "")
        
        # Build address string
        addr_line1 = address_data.get("address_1", "")
        addr_line2 = address_data.get("address_2", "")
        city = address_data.get("city", "")
        state = address_data.get("state", "")
        zip_code = address_data.get("postal_code", "")
        
        full_address = addr_line1
        if addr_line2:
            full_address += f", {addr_line2}"
        if city:
            full_address += f", {city}"
        if state:
            full_address += f", {state}"
        if zip_code:
            full_address += f" {zip_code}"
        
        return {
            "npi": result.get("number", ""),
            "first_name": first_name,
            "last_name": last_name,
            "full_name": f"{first_name} {last_name}".strip(),
            "specialty": specialty,
            "phone": address_data.get("telephone_number", ""),
            "fax": address_data.get("fax_number", ""),
            "address": full_address,
            "city": city,
            "state": state,
            "zip": zip_code,
            "enumeration_date": basic.get("enumeration_date", ""),
            "last_updated": basic.get("last_updated", ""),
            "status": basic.get("status", ""),
            "credential": basic.get("credential", ""),
            "gender": basic.get("gender", ""),
            "dea": "",  # Not provided by NPI Registry
            "cached": False  # Indicate this is fresh from API
        }


# Module-level instance for easy use
_npi_service = None

def get_npi_service(db_path: Optional[str] = None) -> NPILookupService:
    """
    Get singleton NPI lookup service instance.
    
    Args:
        db_path: Path to cache database (only used on first call)
    
    Returns:
        NPILookupService instance
    """
    global _npi_service
    
    if _npi_service is None:
        _npi_service = NPILookupService(db_path)
    
    return _npi_service
