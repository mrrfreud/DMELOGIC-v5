"""
Insurance domain models.

Represents insurance policies and payer information.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, List
from enum import Enum


class InsuranceType(Enum):
    """Insurance type enumeration."""
    MEDICARE = "Medicare"
    MEDICAID = "Medicaid"
    COMMERCIAL = "Commercial"
    WORKERS_COMP = "Workers Compensation"
    AUTO_INSURANCE = "Auto Insurance"
    OTHER = "Other"
    
    @property
    def requires_authorization(self) -> bool:
        """Check if insurance type typically requires authorization."""
        return self in (
            InsuranceType.MEDICARE,
            InsuranceType.MEDICAID,
            InsuranceType.WORKERS_COMP,
        )
    
    @property
    def has_refill_limits(self) -> bool:
        """Check if insurance has strict refill timing rules."""
        return self in (
            InsuranceType.MEDICARE,
            InsuranceType.MEDICAID,
        )


@dataclass
class InsurancePolicy:
    """
    Insurance policy entity.
    
    Represents an insurance company/payer with policy details,
    billing requirements, and coverage rules.
    
    Business Rules:
    - Payer ID required for electronic billing
    - Medicare/Medicaid have specific refill and quantity rules
    - Authorization required for certain HCPCS codes
    """
    # Identity
    id: Optional[int] = None
    
    # Insurance company
    name: str = ""
    insurance_type: InsuranceType = InsuranceType.COMMERCIAL
    
    # Payer information (for electronic billing)
    payer_id: str = ""
    payer_name: str = ""
    
    # Contact information
    phone: str = ""
    fax: str = ""
    claims_address: str = ""
    
    # Policy details
    plan_name: str = ""
    group_number: str = ""
    
    # Coverage rules
    requires_prior_auth: bool = False
    auth_phone: str = ""
    
    # Billing details
    accepts_electronic: bool = True
    claim_submission_url: str = ""
    
    # Refill rules (for Medicare/Medicaid)
    refill_min_days_supply: int = 30  # Minimum days before refill allowed
    max_quantity_per_month: int = 0   # 0 = no limit
    
    # Notes
    notes: str = ""
    
    # Tracking
    date_added: Optional[date] = None
    last_updated: Optional[date] = None
    
    @property
    def display_name(self) -> str:
        """Get display name for UI."""
        if self.plan_name:
            return f"{self.name} - {self.plan_name}"
        return self.name
    
    @property
    def is_medicare(self) -> bool:
        """Check if this is a Medicare policy."""
        return self.insurance_type == InsuranceType.MEDICARE
    
    @property
    def is_medicaid(self) -> bool:
        """Check if this is a Medicaid policy."""
        return self.insurance_type == InsuranceType.MEDICAID
    
    @property
    def is_government(self) -> bool:
        """Check if this is a government insurance."""
        return self.insurance_type in (
            InsuranceType.MEDICARE,
            InsuranceType.MEDICAID,
        )
    
    @property
    def requires_auth_for_dme(self) -> bool:
        """Check if prior auth required for DME."""
        return self.requires_prior_auth or self.insurance_type.requires_authorization
    
    def get_refill_earliest_date(self, last_filled: date, day_supply: int) -> date:
        """
        Calculate earliest date a refill can be processed.
        
        Business Rules:
        - Medicare: 75% through supply (e.g., 23 days for 30-day supply)
        - Medicaid: 75% through supply
        - Commercial: Generally allows earlier refills
        
        Args:
            last_filled: Date of last fill
            day_supply: Days supply of the prescription
            
        Returns:
            Earliest date refill can be filled
        """
        from datetime import timedelta
        
        if self.is_government:
            # Medicare/Medicaid: 75% rule
            days_until_refill = int(day_supply * 0.75)
        else:
            # Commercial: More lenient (80% of supply)
            days_until_refill = int(day_supply * 0.80)
        
        return last_filled + timedelta(days=days_until_refill)
    
    def is_refill_allowed(
        self,
        last_filled: date,
        day_supply: int,
        quantity: int,
        today: Optional[date] = None,
    ) -> tuple[bool, str]:
        """
        Check if refill is allowed based on policy rules.
        
        Args:
            last_filled: Date of last fill
            day_supply: Days supply prescribed
            quantity: Quantity being requested
            today: Current date (defaults to today)
            
        Returns:
            Tuple of (is_allowed, reason_if_denied)
        """
        if today is None:
            today = date.today()
        
        # Check date eligibility
        earliest_date = self.get_refill_earliest_date(last_filled, day_supply)
        if today < earliest_date:
            days_remaining = (earliest_date - today).days
            return (
                False,
                f"Too soon to refill. Eligible on {earliest_date.isoformat()} ({days_remaining} days)",
            )
        
        # Check quantity limits (if applicable)
        if self.max_quantity_per_month > 0 and quantity > self.max_quantity_per_month:
            return (
                False,
                f"Quantity {quantity} exceeds monthly maximum of {self.max_quantity_per_month}",
            )
        
        return (True, "")
    
    def validate(self) -> List[str]:
        """
        Validate business rules for insurance policy.
        
        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []
        
        # Required fields
        if not self.name or not self.name.strip():
            errors.append("Insurance name is required")
        
        # Payer ID required for electronic billing
        if self.accepts_electronic and not self.payer_id:
            errors.append("Payer ID is required for electronic billing")
        
        # Contact validation
        if not self.phone and not self.fax:
            errors.append("At least one contact method (phone or fax) is required")
        
        # Auth phone required if auth is required
        if self.requires_prior_auth and not self.auth_phone:
            errors.append("Authorization phone number is required when prior auth is required")
        
        # Refill rules validation
        if self.refill_min_days_supply < 0:
            errors.append("Refill minimum days supply cannot be negative")
        
        if self.max_quantity_per_month < 0:
            errors.append("Maximum quantity per month cannot be negative")
        
        return errors
    
    def to_dict(self) -> dict:
        """Convert insurance policy to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "insurance_type": self.insurance_type.value,
            "payer_id": self.payer_id,
            "phone": self.phone,
            "fax": self.fax,
            "requires_prior_auth": self.requires_prior_auth,
            "accepts_electronic": self.accepts_electronic,
            "is_government": self.is_government,
            "refill_min_days_supply": self.refill_min_days_supply,
            "max_quantity_per_month": self.max_quantity_per_month,
        }
