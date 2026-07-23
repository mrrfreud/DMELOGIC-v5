"""
Example database migrations.

Shows how to use the migration system to evolve database schemas.

Usage:
    from dmelogic.db.migrations import PATIENT_MIGRATIONS, ORDER_MIGRATIONS
    from dmelogic.db.base import run_migrations
    
    # Run all pending migrations for patients
    run_migrations("patients.db", PATIENT_MIGRATIONS)
    
    # Run all pending migrations for orders
    run_migrations("orders.db", ORDER_MIGRATIONS)
"""

import sqlite3
from dmelogic.db.base import Migration


# ============================================================================
# Patient Database Migrations
# ============================================================================

class Migration001_AddPatientEmail(Migration):
    """Add email column to patients table."""
    version = 1
    description = "Add email column to patients table"
    
    def up(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ALTER TABLE patients ADD COLUMN email TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            # Column already exists
            pass


class Migration002_AddPatientPreferredContact(Migration):
    """Add preferred_contact_method column to patients table."""
    version = 2
    description = "Add preferred_contact_method column to patients table"
    
    def up(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ALTER TABLE patients ADD COLUMN preferred_contact_method TEXT DEFAULT 'phone'")
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration003_AddPatientEmergencyContact(Migration):
    """Add emergency contact fields to patients table."""
    version = 3
    description = "Add emergency contact fields to patients table"
    
    def up(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ALTER TABLE patients ADD COLUMN emergency_contact_name TEXT")
            conn.execute("ALTER TABLE patients ADD COLUMN emergency_contact_phone TEXT")
            conn.execute("ALTER TABLE patients ADD COLUMN emergency_contact_relationship TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration004_AddExtendedInsuranceFields(Migration):
    """Add detailed insurance tracking fields for primary and secondary payers."""
    version = 4
    description = "Add primary_payer, primary_billed_via, secondary_payer, secondary_billed_via, medicaid_policy_number"
    
    def up(self, conn: sqlite3.Connection) -> None:
        try:
            # Primary insurance payer name
            conn.execute("ALTER TABLE patients ADD COLUMN primary_payer TEXT")
            # Primary insurance billing via (e.g., electronic, paper)
            conn.execute("ALTER TABLE patients ADD COLUMN primary_billed_via TEXT")
            # Secondary insurance payer name
            conn.execute("ALTER TABLE patients ADD COLUMN secondary_payer TEXT")
            # Secondary insurance billing via
            conn.execute("ALTER TABLE patients ADD COLUMN secondary_billed_via TEXT")
            # Explicit Medicaid policy number field (separate from insurance_id)
            conn.execute("ALTER TABLE patients ADD COLUMN medicaid_policy_number TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration005_AddPrimaryPayerPolicyNumber(Migration):
    """Add primary_payer_policy_number to patients table."""
    version = 5
    description = "Add primary_payer_policy_number to patients table"

    def up(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ALTER TABLE patients ADD COLUMN primary_payer_policy_number TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass


# Patient migrations list (sorted by version)
PATIENT_MIGRATIONS = [
    Migration001_AddPatientEmail(),
    Migration002_AddPatientPreferredContact(),
    Migration003_AddPatientEmergencyContact(),
    Migration004_AddExtendedInsuranceFields(),
    Migration005_AddPrimaryPayerPolicyNumber(),
]


# ============================================================================
# Order Database Migrations
# ============================================================================

class Migration001_AddOrderPriority(Migration):
    """Add priority field to orders table."""
    version = 1
    description = "Add priority field to orders (Normal, Urgent, STAT)"
    
    def up(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ALTER TABLE orders ADD COLUMN priority TEXT DEFAULT 'Normal'")
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration002_AddOrderAssignedTo(Migration):
    """Add assigned_to field for workflow management."""
    version = 2
    description = "Add assigned_to field for order workflow"
    
    def up(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ALTER TABLE orders ADD COLUMN assigned_to TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration003_AddOrderAuditFields(Migration):
    """Add audit fields to track who modified orders."""
    version = 3
    description = "Add created_by and updated_by audit fields"
    
    def up(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ALTER TABLE orders ADD COLUMN created_by TEXT")
            conn.execute("ALTER TABLE orders ADD COLUMN updated_by TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration004_AddOrderItemInventoryFK(Migration):
    """Add inventory_item_id foreign key to order_items."""
    version = 4
    description = "Add inventory_item_id FK to link orders with inventory"
    
    def up(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ALTER TABLE order_items ADD COLUMN inventory_item_id INTEGER")
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration005_AddRefillTrackingIndexes(Migration):
    """Add indexes for refill tracking queries performance."""
    version = 5
    description = "Add indexes for refill tracking performance"
    
    def up(self, conn: sqlite3.Connection) -> None:
        try:
            # Index for refill due queries on order_items
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_order_items_refill_tracking
                ON order_items(last_filled_date, day_supply, refills)
                WHERE last_filled_date IS NOT NULL
                  AND last_filled_date != ''
                  AND CAST(refills AS INTEGER) > 0
            """)
            
            # Index for patient name sorting in orders
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_orders_patient_name
                ON orders(patient_last_name, patient_first_name)
            """)
            
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration006_AddBillingModifiers(Migration):
    """Add 4 billing modifier fields and rental month tracking to order_items."""
    version = 6
    description = "Add modifier1-4 and rental_month fields for HCFA-1500 and rental tracking"
    
    def up(self, conn: sqlite3.Connection) -> None:
        try:
            # Add modifier fields (up to 4 modifiers for HCFA-1500 form)
            conn.execute("ALTER TABLE order_items ADD COLUMN modifier1 TEXT")
            conn.execute("ALTER TABLE order_items ADD COLUMN modifier2 TEXT")
            conn.execute("ALTER TABLE order_items ADD COLUMN modifier3 TEXT")
            conn.execute("ALTER TABLE order_items ADD COLUMN modifier4 TEXT")
            
            # Add rental month tracking for automatic K modifier assignment
            conn.execute("ALTER TABLE order_items ADD COLUMN rental_month INTEGER DEFAULT 0")
            
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration007_AddIsRental(Migration):
    """Add is_rental flag to order_items."""
    version = 7
    description = "Add is_rental field to distinguish rental vs purchase items"
    
    def up(self, conn: sqlite3.Connection) -> None:
        try:
            # Add is_rental flag (INTEGER 0/1 for SQLite boolean)
            conn.execute("ALTER TABLE order_items ADD COLUMN is_rental INTEGER DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration008_AddRefillLocking(Migration):
    """Add refill locking and parent order tracking."""
    version = 8
    description = "Add parent_order_id and is_locked columns for refill processing"
    
    def up(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ALTER TABLE orders ADD COLUMN parent_order_id INTEGER")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        
        try:
            conn.execute("ALTER TABLE orders ADD COLUMN is_locked INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration009_AddRefillCompleted(Migration):
    """Add refill_completed flag to track which orders have been processed as refills."""
    version = 9
    description = "Add refill_completed and refill_completed_at columns for proper refill display"
    
    def up(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ALTER TABLE orders ADD COLUMN refill_completed INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        
        try:
            conn.execute("ALTER TABLE orders ADD COLUMN refill_completed_at TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        
        # Migrate existing is_locked=1 orders to refill_completed=1
        try:
            conn.execute("UPDATE orders SET refill_completed = 1 WHERE is_locked = 1 AND refill_completed = 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration010_EnsureRefillCompletedColumns(Migration):
    """Ensure refill_completed columns exist (heals DBs with inconsistent schema_version state)."""
    version = 10
    description = "Ensure refill_completed and refill_completed_at columns exist"

    def up(self, conn: sqlite3.Connection) -> None:
        # Add columns if missing
        try:
            conn.execute("ALTER TABLE orders ADD COLUMN refill_completed INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass

        try:
            conn.execute("ALTER TABLE orders ADD COLUMN refill_completed_at TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass

        # Backfill from legacy is_locked if present
        try:
            conn.execute("UPDATE orders SET refill_completed = 1 WHERE is_locked = 1 AND refill_completed = 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration011_AddSpecialInstructions(Migration):
    """Add special_instructions field for delivery notes."""
    version = 11
    description = "Add special_instructions field for delivery person notes"

    def up(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ALTER TABLE orders ADD COLUMN special_instructions TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration012_AddItemSortOrder(Migration):
    """Add sort_order column to order_items for user-defined item ordering."""
    version = 12
    description = "Add sort_order column to order_items for drag-and-drop reordering"

    def up(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ALTER TABLE order_items ADD COLUMN sort_order INTEGER DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        # Backfill: set sort_order = id for existing rows so original insertion order is preserved
        try:
            conn.execute("UPDATE order_items SET sort_order = id WHERE sort_order = 0 OR sort_order IS NULL")
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration013_AddItemPrescriber(Migration):
    """Add prescriber fields to order_items for multi-prescriber orders."""
    version = 13
    description = "Add prescriber_id, prescriber_name, prescriber_npi to order_items for orders with multiple prescribers"

    def up(self, conn: sqlite3.Connection) -> None:
        # Add prescriber_id FK
        try:
            conn.execute("ALTER TABLE order_items ADD COLUMN prescriber_id INTEGER")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        
        # Add prescriber_name snapshot
        try:
            conn.execute("ALTER TABLE order_items ADD COLUMN prescriber_name TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        
        # Add prescriber_npi snapshot
        try:
            conn.execute("ALTER TABLE order_items ADD COLUMN prescriber_npi TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        
        # Backfill existing items with order-level prescriber data
        try:
            conn.execute("""
                UPDATE order_items 
                SET prescriber_id = (SELECT prescriber_id FROM orders WHERE orders.id = order_items.order_id),
                    prescriber_name = (SELECT COALESCE(prescriber_name_at_order_time, prescriber_name) FROM orders WHERE orders.id = order_items.order_id),
                    prescriber_npi = (SELECT COALESCE(prescriber_npi_at_order_time, prescriber_npi) FROM orders WHERE orders.id = order_items.order_id)
                WHERE prescriber_id IS NULL
            """)
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration014_AddSoftDelete(Migration):
    """Add soft delete columns for safe order deletion."""
    version = 14
    description = "Add deleted_at and deleted_by columns for soft delete support"

    def up(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ALTER TABLE orders ADD COLUMN deleted_at TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration015_AddOrderPlaceOfService(Migration):
    """Add place_of_service for billing and claim submission."""
    version = 15
    description = "Add place_of_service to orders"

    def up(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ALTER TABLE orders ADD COLUMN place_of_service TEXT DEFAULT '12'")
            conn.commit()
        except sqlite3.OperationalError:
            pass

        try:
            conn.execute("UPDATE orders SET place_of_service = '12' WHERE place_of_service IS NULL OR TRIM(place_of_service) = ''")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        
        try:
            conn.execute("ALTER TABLE orders ADD COLUMN deleted_by TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass


# Order migrations list (sorted by version)
ORDER_MIGRATIONS = [
    Migration001_AddOrderPriority(),
    Migration002_AddOrderAssignedTo(),
    Migration003_AddOrderAuditFields(),
    Migration004_AddOrderItemInventoryFK(),
    Migration005_AddRefillTrackingIndexes(),
    Migration006_AddBillingModifiers(),
    Migration007_AddIsRental(),
    Migration008_AddRefillLocking(),
    Migration009_AddRefillCompleted(),
    Migration010_EnsureRefillCompletedColumns(),
    Migration011_AddSpecialInstructions(),
    Migration012_AddItemSortOrder(),
    Migration013_AddItemPrescriber(),
    Migration014_AddSoftDelete(),
    Migration015_AddOrderPlaceOfService(),
]


# ============================================================================
# Prescriber Database Migrations
# ============================================================================

class Migration001_AddPrescriberEPrescribe(Migration):
    """Add e-prescribe capability flag."""
    version = 1
    description = "Add e_prescribe_enabled flag to prescribers"
    
    def up(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ALTER TABLE prescribers ADD COLUMN e_prescribe_enabled INTEGER DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration002_AddPrescriberPortalAccess(Migration):
    """Add portal access credentials for prescribers."""
    version = 2
    description = "Add portal_username and portal_access_enabled fields"
    
    def up(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ALTER TABLE prescribers ADD COLUMN portal_username TEXT")
            conn.execute("ALTER TABLE prescribers ADD COLUMN portal_access_enabled INTEGER DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration003_AddFaxContactCategories(Migration):
    """
    Turn the prescribers table into the shared fax-contact directory.

    A contact is either a person (a PRESCRIBER, keeping npi/dea/license) or an
    organization we fax — another DME we refer a patient to, or an Ins/MLTC.
    Organizations leave the person fields NULL and are identified by
    display_name. `default_cover_message` seeds the fax cover sheet.
    """
    version = 3
    description = "Add contact category / display_name / default_cover_message to prescribers"

    def up(self, conn: sqlite3.Connection) -> None:
        for ddl in (
            "ALTER TABLE prescribers ADD COLUMN category TEXT DEFAULT 'PRESCRIBER'",
            "ALTER TABLE prescribers ADD COLUMN display_name TEXT",
            "ALTER TABLE prescribers ADD COLUMN default_cover_message TEXT",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # column already present

        # Existing rows are all prescribers.
        try:
            conn.execute(
                "UPDATE prescribers SET category = 'PRESCRIBER' "
                "WHERE category IS NULL OR TRIM(category) = ''"
            )
            # Give every contact a display name for the fax picker.
            conn.execute(
                """UPDATE prescribers
                      SET display_name = TRIM(
                          COALESCE(last_name, '') ||
                          CASE WHEN COALESCE(last_name,'') <> '' AND COALESCE(first_name,'') <> ''
                               THEN ', ' ELSE '' END ||
                          COALESCE(first_name, '')
                      )
                    WHERE display_name IS NULL OR TRIM(display_name) = ''"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prescribers_category ON prescribers(category)"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration004_AddFaxContactLocations(Migration):
    """
    Give every contact multiple locations (a prescriber may practice at 4+
    offices, each with its own facility name, phone and fax).

    Each existing prescriber is migrated to exactly one primary location built
    from its current flat address/phone/fax/practice_name columns. Those flat
    columns stay as a mirror of the primary location so existing lookups keep
    working unchanged.
    """
    version = 4
    description = "Add fax_contact_locations and migrate each prescriber to a primary location"

    def up(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fax_contact_locations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id      INTEGER NOT NULL,
                facility_name   TEXT,
                address_line1   TEXT,
                address_line2   TEXT,
                city            TEXT,
                state           TEXT,
                zip_code        TEXT,
                phone           TEXT,
                fax             TEXT,
                is_primary      INTEGER DEFAULT 0,
                status          TEXT DEFAULT 'Active',
                notes           TEXT,
                created_date    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_date    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (contact_id) REFERENCES prescribers(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fax_locations_contact ON fax_contact_locations(contact_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fax_locations_primary ON fax_contact_locations(contact_id, is_primary)"
        )

        # Backfill one primary location per contact that doesn't have one yet.
        # Idempotent: re-running never creates duplicates.
        conn.execute(
            """
            INSERT INTO fax_contact_locations (
                contact_id, facility_name, address_line1, address_line2,
                city, state, zip_code, phone, fax, is_primary, status
            )
            SELECT p.id, p.practice_name, p.address_line1, p.address_line2,
                   p.city, p.state, p.zip_code, p.phone, p.fax, 1,
                   COALESCE(NULLIF(TRIM(p.status), ''), 'Active')
              FROM prescribers p
             WHERE NOT EXISTS (
                   SELECT 1 FROM fax_contact_locations l WHERE l.contact_id = p.id
             )
            """
        )
        conn.commit()


# Prescriber / fax-contact migrations list
PRESCRIBER_MIGRATIONS = [
    Migration001_AddPrescriberEPrescribe(),
    Migration002_AddPrescriberPortalAccess(),
    Migration003_AddFaxContactCategories(),
    Migration004_AddFaxContactLocations(),
]


# ============================================================================
# Inventory Database Migrations
# ============================================================================

class Migration001_AddInventoryBarcode(Migration):
    """Add barcode/UPC field for inventory tracking."""
    version = 1
    description = "Add barcode field to inventory items"
    
    def up(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ALTER TABLE inventory ADD COLUMN barcode TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration002_AddInventoryLocation(Migration):
    """Add warehouse location field for inventory management."""
    version = 2
    description = "Add warehouse_location field to inventory"
    
    def up(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ALTER TABLE inventory ADD COLUMN warehouse_location TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass


class Migration003_AddInventoryExpirationTracking(Migration):
    """Add expiration date tracking for inventory."""
    version = 3
    description = "Add expiration_date and lot_number fields"
    
    def up(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ALTER TABLE inventory ADD COLUMN expiration_date TEXT")
            conn.execute("ALTER TABLE inventory ADD COLUMN lot_number TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass


# Inventory migrations list
INVENTORY_MIGRATIONS = [
    Migration001_AddInventoryBarcode(),
    Migration002_AddInventoryLocation(),
    Migration003_AddInventoryExpirationTracking(),
]


# ============================================================================
# Run All Migrations Helper
# ============================================================================

def run_all_migrations(folder_path: str = None) -> dict[str, int]:
    """
    Run all pending migrations for all databases.
    
    Args:
        folder_path: Optional database folder path
        
    Returns:
        Dict mapping database name to number of migrations applied
    """
    from dmelogic.db.base import run_migrations
    from dmelogic.config import debug_log
    
    results = {}
    
    # Run migrations for each database
    migrations_map = {
        "patients.db": PATIENT_MIGRATIONS,
        "orders.db": ORDER_MIGRATIONS,
        "prescribers.db": PRESCRIBER_MIGRATIONS,
        "inventory.db": INVENTORY_MIGRATIONS,
    }
    
    for db_name, migrations in migrations_map.items():
        try:
            count = run_migrations(db_name, migrations, folder_path=folder_path)
            results[db_name] = count
            debug_log(f"Migration: {db_name} - {count} migrations applied")
        except Exception as e:
            debug_log(f"Migration ERROR: {db_name} - {e}")
            results[db_name] = -1  # Indicate error
    
    return results
