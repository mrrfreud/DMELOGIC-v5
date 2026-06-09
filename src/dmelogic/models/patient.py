"""
Patient domain models.

Represents patients and their insurance information.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, List
from enum import Enum


class PatientStatus(Enum):
    """Patient status enumeration."""
    ACTIVE = "Active"
    INACTIVE = "Inactive"
    DECEASED = "Deceased"


@dataclass
class PatientInsurance:
    """
    Patient insurance information.
    
    Represents insurance coverage details for a patient.
    Can be primary or secondary insurance.
    """
    # Insurance provider
    insurance_name: str = ""
    insurance_id: str = ""  # Policy/member ID
    group_number: str = ""
    
    # Coverage details
    coverage_start: Optional[date] = None
    coverage_end: Optional[date] = None
    
    # Payer information
    payer_id: str = ""  # For electronic claims
    
    @property
    def is_active(self) -> bool:
        """Check if insurance coverage is currently active."""
        if self.coverage_end:
            return date.today() <= self.coverage_end
        return True  # No end date means active
    
    def validate(self) -> List[str]:
        """Validate insurance information."""
        errors = []
        
        if not self.insurance_name or not self.insurance_name.strip():
            errors.append("Insurance name is required")
        
        if not self.insurance_id or not self.insurance_id.strip():
            errors.append("Insurance ID is required")
        
        if self.coverage_start and self.coverage_end:
            if self.coverage_end < self.coverage_start:
                errors.append("Coverage end date must be after start date")
        
        return errors


@dataclass
class Patient:
    """
    Patient entity.
    
    Represents a DME patient with demographics, contact info,
    and insurance information.
    
    Business Rules:
    - Last name is required
    - DOB is required for Medicare/Medicaid
    - At least one contact method required
    """
    # Identity
    id: Optional[int] = None
    
    # Demographics
    first_name: str = ""
    last_name: str = ""
    middle_name: str = ""
    date_of_birth: Optional[date] = None
    sex: str = ""  # M, F, Other
    
    # Contact information
    phone: str = ""
    phone_2: str = ""
    email: str = ""
    
    # Address
    address: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    
    # Insurance (can have primary and secondary)
    primary_insurance: Optional[PatientInsurance] = None
    secondary_insurance: Optional[PatientInsurance] = None
    
    # Status
    status: PatientStatus = PatientStatus.ACTIVE
    
    # Notes
    notes: str = ""
    
    # Tracking
    created_date: Optional[date] = None
    last_updated: Optional[date] = None
    
    @property
    def full_name(self) -> str:
        """Get full name in 'LAST, FIRST MIDDLE' format."""
        parts = [self.last_name]
        if self.first_name:
            parts.append(self.first_name)
        if self.middle_name:
            parts.append(self.middle_name)
        
        if len(parts) == 1:
            return parts[0]
        return f"{parts[0]}, {' '.join(parts[1:])}"
    
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
    def age(self) -> Optional[int]:
        """Calculate patient age from DOB."""
        if not self.date_of_birth:
            return None
        
        today = date.today()
        age = today.year - self.date_of_birth.year
        
        # Adjust if birthday hasn't occurred this year
        if (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day):
            age -= 1
        
        return age
    
    @property
    def has_insurance(self) -> bool:
        """Check if patient has any insurance."""
        return self.primary_insurance is not None
    
    @property
    def is_active(self) -> bool:
        """Check if patient is active."""
        return self.status == PatientStatus.ACTIVE
    
    def validate(self) -> List[str]:
        """
        Validate business rules for patient.
        
        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []
        
        # Required fields
        if not self.last_name or not self.last_name.strip():
            errors.append("Last name is required")
        
        # DOB validation for Medicare/Medicaid
        if self.primary_insurance:
            insurance_name = self.primary_insurance.insurance_name.upper()
            if "MEDICARE" in insurance_name or "MEDICAID" in insurance_name:
                if not self.date_of_birth:
                    errors.append("Date of birth is required for Medicare/Medicaid")
        
        # Contact validation
        if not self.phone and not self.email:
            errors.append("At least one contact method (phone or email) is required")
        
        # Validate insurance if present
        if self.primary_insurance:
            insurance_errors = self.primary_insurance.validate()
            for error in insurance_errors:
                errors.append(f"Primary insurance: {error}")
        
        if self.secondary_insurance:
            insurance_errors = self.secondary_insurance.validate()
            for error in insurance_errors:
                errors.append(f"Secondary insurance: {error}")
        
        # Sex validation
        if self.sex and self.sex not in ("M", "F", "Other", ""):
            errors.append("Sex must be M, F, or Other")
        
        return errors
    
    def to_dict(self) -> dict:
        """Convert patient to dictionary."""
        return {
            "id": self.id,
            "full_name": self.full_name,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "date_of_birth": self.date_of_birth.isoformat() if self.date_of_birth else None,
            "age": self.age,
            "sex": self.sex,
            "phone": self.phone,
            "email": self.email,
            "address": self.full_address,
            "primary_insurance": self.primary_insurance.insurance_name if self.primary_insurance else None,
            "status": self.status.value,
        }
