"""
HCPCS Description Mapper

Maps HCPCS codes to simplified descriptions for use in fax forms and documents.
"""

import json
import os
from pathlib import Path
from typing import List, Dict, Optional


class HCPCSMapper:
    """Manages HCPCS code to description mappings"""
    
    def __init__(self):
        self.mappings: Dict[str, List[str]] = {}
        self.mapping_file = self._get_mapping_file_path()
        self.load_mappings()
    
    def _get_mapping_file_path(self) -> Path:
        """Get the path to the HCPCS mappings JSON file"""
        # Try assets folder first
        if hasattr(sys, '_MEIPASS'):
            # Running as PyInstaller bundle
            base_path = Path(sys._MEIPASS) / 'assets'
        else:
            # Running as script
            base_path = Path(__file__).parent.parent.parent / 'assets'
        
        mapping_file = base_path / 'hcpcs_descriptions.json'
        
        # If file doesn't exist in assets, create it
        if not mapping_file.exists():
            mapping_file.parent.mkdir(parents=True, exist_ok=True)
            self._create_default_mappings(mapping_file)
        
        return mapping_file
    
    def _create_default_mappings(self, file_path: Path):
        """Create default HCPCS mappings file"""
        default_mappings = {
            "A4554": ["DISPOSABLE UNDERPADS"],
            "T4521": ["ADULT BRIEFS/ PULL-UPS - SMALL"],
            "T4522": ["ADULT BRIEFS/ PULL-UPS - MEDIUM"],
            "T4523": ["ADULT BRIEFS/ PULL-UPS - LARGE"],
            "T4524": ["ADULT BRIEFS/ PULL-UPS - EXTRA-LARGE"],
            "T4543": ["ADULT BRIEFS/ PULL-UPS - 2X LARGE"],
            "T4530": ["CHILDREN'S DIAPERS"],
            "T4533": ["JUNIOR DIAPERS"],
            "A4927": ["DISPOSABLE GLOVES"],
            "T4537": ["REUSABLE UNDERPADS (BED SIZE)"],
            "T4540": ["REUSABLE UNDERPADS (CHAIR SIZE)"],
            "A4402": ["A&D OINTMENT"]
        }
        
        with open(file_path, 'w') as f:
            json.dump(default_mappings, f, indent=2)
    
    def load_mappings(self):
        """Load HCPCS mappings from JSON file"""
        try:
            with open(self.mapping_file, 'r') as f:
                self.mappings = json.load(f)
        except Exception as e:
            print(f"Error loading HCPCS mappings: {e}")
            self.mappings = {}
    
    def save_mappings(self):
        """Save HCPCS mappings to JSON file"""
        try:
            with open(self.mapping_file, 'w') as f:
                json.dump(self.mappings, f, indent=2)
            return True
        except Exception as e:
            print(f"Error saving HCPCS mappings: {e}")
            return False
    
    def get_description(self, hcpcs_code: str, original_description: str, allow_selection: bool = False) -> str:
        """
        Get the simplified description for a HCPCS code.
        
        Args:
            hcpcs_code: The HCPCS code to look up
            original_description: The original item description (fallback)
            allow_selection: If True and multiple descriptions exist, prompt user to choose
        
        Returns:
            Simplified description if found, otherwise original description
        """
        if not hcpcs_code:
            return original_description
        
        # Clean up HCPCS code (remove whitespace, convert to uppercase)
        hcpcs_code = hcpcs_code.strip().upper()
        
        # Look up in mappings
        descriptions = self.mappings.get(hcpcs_code, [])
        
        if not descriptions:
            return original_description
        
        if len(descriptions) == 1:
            return descriptions[0]
        
        if allow_selection and len(descriptions) > 1:
            # Multiple descriptions - will need to prompt user
            # This will be handled by the calling code
            return descriptions
        
        # Default to first description if multiple and not allowing selection
        return descriptions[0]
    
    def get_all_descriptions(self, hcpcs_code: str) -> List[str]:
        """Get all descriptions for a HCPCS code"""
        if not hcpcs_code:
            return []
        
        hcpcs_code = hcpcs_code.strip().upper()
        return self.mappings.get(hcpcs_code, [])
    
    def add_mapping(self, hcpcs_code: str, description: str) -> bool:
        """Add a new HCPCS to description mapping"""
        if not hcpcs_code or not description:
            return False
        
        hcpcs_code = hcpcs_code.strip().upper()
        
        if hcpcs_code not in self.mappings:
            self.mappings[hcpcs_code] = []
        
        if description not in self.mappings[hcpcs_code]:
            self.mappings[hcpcs_code].append(description)
            return self.save_mappings()
        
        return True
    
    def remove_mapping(self, hcpcs_code: str, description: Optional[str] = None) -> bool:
        """
        Remove a HCPCS mapping.
        
        Args:
            hcpcs_code: The HCPCS code
            description: Specific description to remove, or None to remove all
        """
        hcpcs_code = hcpcs_code.strip().upper()
        
        if hcpcs_code not in self.mappings:
            return False
        
        if description is None:
            # Remove entire HCPCS entry
            del self.mappings[hcpcs_code]
        else:
            # Remove specific description
            if description in self.mappings[hcpcs_code]:
                self.mappings[hcpcs_code].remove(description)
                # If no descriptions left, remove the HCPCS entry
                if not self.mappings[hcpcs_code]:
                    del self.mappings[hcpcs_code]
        
        return self.save_mappings()
    
    def get_all_mappings(self) -> Dict[str, List[str]]:
        """Get all HCPCS mappings"""
        return self.mappings.copy()


# Global instance
_mapper_instance = None


def get_hcpcs_mapper() -> HCPCSMapper:
    """Get the global HCPCS mapper instance"""
    global _mapper_instance
    if _mapper_instance is None:
        _mapper_instance = HCPCSMapper()
    return _mapper_instance


import sys
