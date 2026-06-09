"""
Inventory domain models.

Represents DME inventory items and stock management.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional, List
from enum import Enum


class InventoryStatus(Enum):
    """Inventory item status."""
    ACTIVE = "Active"
    DISCONTINUED = "Discontinued"
    OUT_OF_STOCK = "Out of Stock"
    BACK_ORDERED = "Back Ordered"


@dataclass
class InventoryItem:
    """
    Inventory item entity.
    
    Represents a DME product in inventory with pricing,
    quantities, and supplier information.
    
    Business Rules:
    - HCPCS code is required
    - Retail price must be >= 0
    - Quantity on hand must be >= 0
    - Reorder point should be > 0 for active items
    """
    # Identity
    id: Optional[int] = None
    
    # Product identification
    item_number: str = ""  # Vendor/internal item number
    hcpcs_code: str = ""   # HCPCS billing code
    description: str = ""
    manufacturer: str = ""
    model_number: str = ""
    
    # Pricing
    cost: Optional[Decimal] = None          # Wholesale cost
    retail_price: Optional[Decimal] = None  # Retail/billing price
    
    # Inventory tracking
    quantity_on_hand: int = 0
    quantity_reserved: int = 0  # Reserved for orders
    reorder_point: int = 0      # When to reorder
    reorder_quantity: int = 0   # How many to order
    
    # Supplier information
    supplier_name: str = ""
    supplier_item_number: str = ""
    
    # Status
    status: InventoryStatus = InventoryStatus.ACTIVE
    
    # Product details
    unit_of_measure: str = "EA"  # EA, BX, CS, etc.
    package_size: int = 1
    
    # Notes
    notes: str = ""
    
    # Tracking
    date_added: Optional[date] = None
    last_updated: Optional[date] = None
    last_order_date: Optional[date] = None
    
    def __post_init__(self):
        """Validate and normalize data after initialization."""
        # Convert pricing to Decimal
        if self.cost is not None and not isinstance(self.cost, Decimal):
            self.cost = Decimal(str(self.cost))
        
        if self.retail_price is not None and not isinstance(self.retail_price, Decimal):
            self.retail_price = Decimal(str(self.retail_price))
        
        # Ensure quantities are non-negative
        if self.quantity_on_hand < 0:
            self.quantity_on_hand = 0
        
        if self.quantity_reserved < 0:
            self.quantity_reserved = 0
    
    @property
    def quantity_available(self) -> int:
        """Calculate available quantity (on hand - reserved)."""
        return max(0, self.quantity_on_hand - self.quantity_reserved)
    
    @property
    def is_in_stock(self) -> bool:
        """Check if item is in stock and available."""
        return self.quantity_available > 0 and self.status == InventoryStatus.ACTIVE
    
    @property
    def needs_reorder(self) -> bool:
        """Check if item quantity is at or below reorder point."""
        return self.quantity_available <= self.reorder_point
    
    @property
    def profit_margin(self) -> Optional[Decimal]:
        """Calculate profit margin percentage."""
        if self.cost and self.retail_price and self.cost > 0:
            margin = ((self.retail_price - self.cost) / self.cost) * Decimal("100")
            return margin.quantize(Decimal("0.01"))
        return None
    
    @property
    def is_active(self) -> bool:
        """Check if item is active."""
        return self.status == InventoryStatus.ACTIVE
    
    def reserve(self, quantity: int) -> None:
        """
        Reserve quantity for an order.
        
        Business Rule: Cannot reserve more than available.
        """
        if quantity <= 0:
            raise ValueError("Reserve quantity must be positive")
        
        if quantity > self.quantity_available:
            raise ValueError(
                f"Cannot reserve {quantity} units - only {self.quantity_available} available"
            )
        
        self.quantity_reserved += quantity
    
    def release(self, quantity: int) -> None:
        """
        Release reserved quantity (e.g., order cancelled).
        
        Business Rule: Cannot release more than reserved.
        """
        if quantity <= 0:
            raise ValueError("Release quantity must be positive")
        
        if quantity > self.quantity_reserved:
            raise ValueError(
                f"Cannot release {quantity} units - only {self.quantity_reserved} reserved"
            )
        
        self.quantity_reserved -= quantity
    
    def fulfill(self, quantity: int) -> None:
        """
        Fulfill an order by reducing on-hand and reserved quantities.
        
        Business Rule: Item must be reserved before fulfillment.
        """
        if quantity <= 0:
            raise ValueError("Fulfill quantity must be positive")
        
        if quantity > self.quantity_reserved:
            raise ValueError(
                f"Cannot fulfill {quantity} units - only {self.quantity_reserved} reserved"
            )
        
        if quantity > self.quantity_on_hand:
            raise ValueError(
                f"Cannot fulfill {quantity} units - only {self.quantity_on_hand} on hand"
            )
        
        self.quantity_on_hand -= quantity
        self.quantity_reserved -= quantity
    
    def receive_stock(self, quantity: int) -> None:
        """
        Receive new stock from supplier.
        
        Business Rule: Quantity must be positive.
        """
        if quantity <= 0:
            raise ValueError("Receive quantity must be positive")
        
        self.quantity_on_hand += quantity
        self.last_order_date = date.today()
    
    def validate(self) -> List[str]:
        """
        Validate business rules for inventory item.
        
        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []
        
        # Required fields
        if not self.hcpcs_code or not self.hcpcs_code.strip():
            errors.append("HCPCS code is required")
        
        if not self.description or not self.description.strip():
            errors.append("Description is required")
        
        # Pricing validation
        if self.cost is not None and self.cost < 0:
            errors.append("Cost cannot be negative")
        
        if self.retail_price is not None and self.retail_price < 0:
            errors.append("Retail price cannot be negative")
        
        if self.cost and self.retail_price and self.retail_price < self.cost:
            errors.append("Retail price should not be less than cost")
        
        # Quantity validation
        if self.quantity_on_hand < 0:
            errors.append("Quantity on hand cannot be negative")
        
        if self.quantity_reserved < 0:
            errors.append("Quantity reserved cannot be negative")
        
        if self.quantity_reserved > self.quantity_on_hand:
            errors.append("Quantity reserved cannot exceed quantity on hand")
        
        # Reorder validation for active items
        if self.status == InventoryStatus.ACTIVE:
            if self.reorder_point < 0:
                errors.append("Reorder point cannot be negative")
            
            if self.reorder_quantity < 0:
                errors.append("Reorder quantity cannot be negative")
        
        return errors
    
    def to_dict(self) -> dict:
        """Convert inventory item to dictionary."""
        return {
            "id": self.id,
            "item_number": self.item_number,
            "hcpcs_code": self.hcpcs_code,
            "description": self.description,
            "manufacturer": self.manufacturer,
            "cost": str(self.cost) if self.cost else None,
            "retail_price": str(self.retail_price) if self.retail_price else None,
            "quantity_on_hand": self.quantity_on_hand,
            "quantity_reserved": self.quantity_reserved,
            "quantity_available": self.quantity_available,
            "is_in_stock": self.is_in_stock,
            "needs_reorder": self.needs_reorder,
            "profit_margin": str(self.profit_margin) if self.profit_margin else None,
            "status": self.status.value,
            "supplier_name": self.supplier_name,
        }
