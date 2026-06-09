"""
Prescriber domain models.

Represents healthcare providers who prescribe DME.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, List
from enum import Enum


class PrescriberStatus(Enum):
    """Prescriber status enumeration."""
    ACTIVE = "Active"
    INACTIVE = "Inactive"
    SUSPENDED = "Suspended"


@dataclass
class Prescriber:
    """
    Prescriber entity (healthcare provider).
    
    Represents a doctor, nurse practitioner, or other provider
    who can prescribe DME equipment.
    
    Business Rules:
    - NPI (National Provider Identifier) is required and must be 10 digits
    - At least one contact method required
    - Must have active status to prescribe
    """
    # Identity
    id: Optional[int] = None
    
    # Name
    first_name: str = ""
    last_name: str = ""
    credentials: str = ""  # MD, DO, NP, PA, etc.
    
    # Identifiers
    npi: str = ""  # National Provider Identifier (10 digits)
    dea: str = ""  # DEA number (for controlled substances)
    state_license: str = ""
    
    # Contact information
    phone: str = ""
    fax: str = ""
    email: str = ""
    
    # Practice information
    practice_name: str = ""
    specialty: str = ""
    
    # Address
    address: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    
    # Status
    status: PrescriberStatus = PrescriberStatus.ACTIVE
    
    # Notes
    notes: str = ""
    
    # Tracking
    created_date: Optional[date] = None
    last_updated: Optional[date] = None
    
    @property
    def full_name(self) -> str:
        """Get full name with credentials."""
        name = f"{self.last_name}, {self.first_name}" if self.first_name else self.last_name
        if self.credentials:
            return f"{name}, {self.credentials}"
        return name
    
    @property
    def display_name(self) -> str:
        """Get display name for UI (without credentials)."""
        if self.first_name:
            return f"{self.last_name}, {self.first_name}"
        return self.last_name
    
    @property
    def full_address(self) -> str:
        """Get full formatted address."""
        parts = []
        if self.address:
            parts.append(self.address)
        
        city_state_zip = []
        if self.city:
            city_state_zip.append(self.city)
        if self.state:
            city_state_zip.append(self.state)
        if self.zip_code:
            city_state_zip.append(self.zip_code)
        
        if city_state_zip:
            parts.append(", ".join(city_state_zip))
        
        return "\n".join(parts)
    
    @property
    def is_active(self) -> bool:
        """Check if prescriber is active."""
        return self.status == PrescriberStatus.ACTIVE
    
    @property
    def can_prescribe(self) -> bool:
        """Check if prescriber can write prescriptions."""
        # Must be active and have valid NPI
        return self.is_active and self.is_npi_valid
    
    @property
    def is_npi_valid(self) -> bool:
        """Check if NPI is properly formatted."""
        return bool(self.npi and len(self.npi.strip()) == 10 and self.npi.strip().isdigit())
    
    def validate(self) -> List[str]:
        """
        Validate business rules for prescriber.
        
        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []
        
        # Required fields
        if not self.last_name or not self.last_name.strip():
            errors.append("Last name is required")
        
        # NPI validation
        if not self.npi or not self.npi.strip():
            errors.append("NPI is required")
        elif not self.is_npi_valid:
            errors.append("NPI must be exactly 10 digits")
        
        # Contact validation
        if not self.phone and not self.fax and not self.email:
            errors.append("At least one contact method (phone, fax, or email) is required")
        
        # Specialty validation for certain credentials
        if self.credentials in ("MD", "DO") and not self.specialty:
            errors.append("Specialty is required for MD/DO credentials")
        
        return errors
    
    def to_dict(self) -> dict:
        """Convert prescriber to dictionary."""
        return {
            "id": self.id,
            "full_name": self.full_name,
            "display_name": self.display_name,
            "npi": self.npi,
            "phone": self.phone,
            "fax": self.fax,
            "email": self.email,
            "practice_name": self.practice_name,
            "specialty": self.specialty,
            "address": self.full_address,
            "status": self.status.value,
            "is_active": self.is_active,
            "can_prescribe": self.can_prescribe,
        }
