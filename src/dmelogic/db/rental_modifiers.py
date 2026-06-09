"""
Rental equipment billing modifier logic.

Handles automatic assignment of K modifiers based on rental month
for equipment with RR (rental) modifier.

DME Rental Billing Rules:
- RR modifier indicates rental equipment
- K modifiers indicate rental month progression:
  * Month 1: KH (Initial claim, first month rental)
  * Months 2-3: KI (Second and third rental months)
  * Months 4-13: KJ (Fourth through thirteenth rental months)
  * Month 14+: Typically ownership transfers, no K modifier

Payment typically drops ~25% during months 4-13 (KJ period).
"""

from typing import Optional
from .models import OrderItem


def get_rental_k_modifier_for_month(rental_month: int) -> Optional[str]:
    """
    Get the appropriate K modifier for a given rental month.
    
    Args:
        rental_month: Rental month number (1-based)
    
    Returns:
        K modifier code (KH, KI, KJ) or None if not applicable
    
    Examples:
        >>> get_rental_k_modifier_for_month(1)
        'KH'
        >>> get_rental_k_modifier_for_month(2)
        'KI'
        >>> get_rental_k_modifier_for_month(5)
        'KJ'
        >>> get_rental_k_modifier_for_month(14)
        None
    """
    if rental_month <= 0:
        return None
    
    if rental_month == 1:
        return 'KH'  # Initial claim, first month rental
    elif 2 <= rental_month <= 3:
        return 'KI'  # Second and third rental months
    elif 4 <= rental_month <= 13:
        return 'KJ'  # Fourth through thirteenth rental months
    else:
        return None  # Ownership transfer, no K modifier


def auto_assign_rental_modifiers(item: OrderItem, refill_number: int = 0) -> None:
    """
    Automatically assign rental modifiers to an order item.
    
    For rental items (RR modifier):
    1. Sets RR as modifier1 if not already present
    2. Calculates rental_month based on refill_number
    3. Auto-assigns appropriate K modifier (KH, KI, KJ)
    
    Args:
        item: OrderItem to update
        refill_number: Current refill/rental number (0 = initial, 1 = first refill, etc.)
    
    Example:
        >>> item = OrderItem(...)
        >>> auto_assign_rental_modifiers(item, refill_number=0)
        >>> print(item.modifier1, item.modifier2)  # RR, KH
        
        >>> item = OrderItem(...)
        >>> auto_assign_rental_modifiers(item, refill_number=4)
        >>> print(item.modifier1, item.modifier2)  # RR, KJ
    """
    # Check if item is already marked as rental
    has_rr = 'RR' in [item.modifier1, item.modifier2, item.modifier3, item.modifier4]
    
    if not has_rr:
        # Not a rental item, do nothing
        return
    
    # Calculate rental month (refill 0 = month 1, refill 1 = month 2, etc.)
    rental_month = refill_number + 1
    item.rental_month = rental_month
    
    # Get appropriate K modifier
    k_modifier = get_rental_k_modifier_for_month(rental_month)
    
    if k_modifier:
        # Assign modifiers: RR as first, K modifier as second
        item.modifier1 = 'RR'
        item.modifier2 = k_modifier
        # modifier3 and modifier4 remain available for other modifiers


def update_rental_month_on_refill(item: OrderItem) -> None:
    """
    Update rental month and K modifier when processing a refill.
    
    Increments rental_month and updates the K modifier accordingly.
    Should be called when a refill is processed for a rental item.
    
    Args:
        item: OrderItem being refilled
    
    Example:
        >>> item = OrderItem(modifier1='RR', modifier2='KH', rental_month=1)
        >>> update_rental_month_on_refill(item)
        >>> print(item.rental_month, item.modifier2)  # 2, KI
    """
    if not item.is_rental:
        return
    
    # Increment rental month
    item.rental_month += 1
    
    # Update K modifier
    k_modifier = item.get_rental_k_modifier()
    
    if k_modifier:
        # Update modifier2 (assuming modifier1 is RR)
        item.modifier2 = k_modifier
    else:
        # Beyond 13 months, remove K modifier
        if item.modifier2 in ['KH', 'KI', 'KJ']:
            item.modifier2 = None


def format_modifiers_for_display(item: OrderItem) -> str:
    """
    Format all modifiers as a comma-separated string for display.
    
    Args:
        item: OrderItem with modifiers
    
    Returns:
        Formatted modifier string (e.g., "RR, KH" or "RR, KJ, NU")
    
    Example:
        >>> item = OrderItem(modifier1='RR', modifier2='KH', modifier3='NU')
        >>> format_modifiers_for_display(item)
        'RR, KH, NU'
    """
    modifiers = item.all_modifiers
    return ', '.join(modifiers) if modifiers else 'None'


def validate_rental_modifiers(item: OrderItem) -> list[str]:
    """
    Validate rental modifier combinations for billing compliance.
    
    Business rules:
    - RR must be present for rental items
    - Only one K modifier (KH, KI, or KJ) per claim
    - K modifier must match rental month
    
    Args:
        item: OrderItem to validate
    
    Returns:
        List of validation error messages (empty if valid)
    
    Example:
        >>> item = OrderItem(modifier1='RR', modifier2='KH', rental_month=5)
        >>> errors = validate_rental_modifiers(item)
        >>> print(errors)
        ['Rental month 5 should use KJ modifier, not KH']
    """
    errors = []
    
    # Check for RR modifier
    if item.is_rental:
        # Validate K modifier matches rental month
        if item.rental_month > 0:
            expected_k = get_rental_k_modifier_for_month(item.rental_month)
            actual_k = None
            
            for mod in [item.modifier1, item.modifier2, item.modifier3, item.modifier4]:
                if mod in ['KH', 'KI', 'KJ']:
                    if actual_k:
                        errors.append(f"Multiple K modifiers found: {actual_k} and {mod}")
                    actual_k = mod
            
            if expected_k and actual_k != expected_k:
                errors.append(
                    f"Rental month {item.rental_month} should use {expected_k} modifier, "
                    f"not {actual_k or 'None'}"
                )
    
    return errors


# Preset modifier combinations for common scenarios
COMMON_MODIFIER_PRESETS = {
    "rental_month_1": ("RR", "KH", None, None),
    "rental_month_2": ("RR", "KI", None, None),
    "rental_month_3": ("RR", "KI", None, None),
    "rental_month_4": ("RR", "KJ", None, None),
    "rental_month_5": ("RR", "KJ", None, None),
    "rental_new": ("RR", "NU", None, None),  # New rental equipment
    "rental_used": ("RR", "UE", None, None),  # Used rental equipment
    "purchase_new": ("NU", None, None, None),  # New purchase
    "purchase_used": ("UE", None, None, None),  # Used purchase
}


def apply_modifier_preset(item: OrderItem, preset_name: str) -> bool:
    """
    Apply a preset modifier combination to an item.
    
    Args:
        item: OrderItem to update
        preset_name: Name of preset from COMMON_MODIFIER_PRESETS
    
    Returns:
        True if preset was applied, False if preset not found
    
    Example:
        >>> item = OrderItem(...)
        >>> apply_modifier_preset(item, "rental_month_1")
        >>> print(item.modifier1, item.modifier2)  # RR, KH
    """
    if preset_name not in COMMON_MODIFIER_PRESETS:
        return False
    
    mod1, mod2, mod3, mod4 = COMMON_MODIFIER_PRESETS[preset_name]
    item.modifier1 = mod1
    item.modifier2 = mod2
    item.modifier3 = mod3
    item.modifier4 = mod4
    
    # Set rental month if preset is a rental
    if preset_name.startswith("rental_month_"):
        try:
            month = int(preset_name.split("_")[-1])
            item.rental_month = month
        except ValueError:
            pass
    
    return True
