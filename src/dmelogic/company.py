"""
company.py — the company profile (single source of truth for branding).

All company contact details and the logo live here, stored as
``company.json`` in the data root. Every generated form, fax cover sheet, and
set of fax instructions reads from this profile, so a new business configures
its identity once (in onboarding or settings) and it flows everywhere.

Nothing here is hardcoded to a specific pharmacy — that's the point.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("company")

PROFILE_FILENAME = "company.json"
_LOGO_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif"}


@dataclass
class CompanyProfile:
    """A business's identity, used across every form and fax."""
    name: str = ""               # legal / business name
    subtitle: str = ""           # e.g. "Durable Medical Equipment Department"
    address_line1: str = ""
    address_line2: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    phone: str = ""
    fax: str = ""
    email: str = ""
    website: str = ""
    npi: str = ""
    tax_id: str = ""
    contact_name: str = ""       # person who signs faxes
    contact_title: str = ""
    logo_path: str = ""          # absolute path to the logo image, if uploaded

    # ── derived/formatted views used by forms & faxes ───────────────────
    def is_configured(self) -> bool:
        """True once the essentials are filled in (used to gate onboarding)."""
        return bool(self.name.strip())

    def city_state_zip(self) -> str:
        parts = [p for p in (self.city.strip(), self.state.strip()) if p]
        line = ", ".join(parts)
        if self.zip.strip():
            line = f"{line} {self.zip.strip()}".strip()
        return line

    def full_address(self, one_line: bool = True) -> str:
        lines = [self.address_line1, self.address_line2, self.city_state_zip()]
        lines = [l for l in (s.strip() for s in lines) if l]
        return ", ".join(lines) if one_line else "\n".join(lines)

    def contact_line(self) -> str:
        bits = []
        if self.phone.strip():
            bits.append(f"Tel. {self.phone.strip()}")
        if self.fax.strip():
            bits.append(f"Fax. {self.fax.strip()}")
        return " | ".join(bits)

    def signature_block(self) -> str:
        """Multi-line signature for fax cover sheets."""
        lines = []
        who = " | ".join([p for p in (self.contact_name.strip(),
                                      self.contact_title.strip()) if p])
        if who:
            lines.append(who)
        if self.full_address():
            lines.append(self.full_address(one_line=True))
        if self.contact_line():
            lines.append(self.contact_line())
        if self.email.strip():
            lines.append(self.email.strip())
        return "\n".join(lines)

    def has_logo(self) -> bool:
        return bool(self.logo_path) and Path(self.logo_path).exists()


# ── persistence ─────────────────────────────────────────────────────────
_CACHE: Optional[CompanyProfile] = None


def _profile_path() -> Path:
    from dmelogic.config import data_root
    return data_root() / PROFILE_FILENAME


def _branding_dir() -> Path:
    from dmelogic.config import data_subdir
    return data_subdir("Branding")


def load_company_profile(force: bool = False) -> CompanyProfile:
    """Load the company profile (cached)."""
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE
    path = _profile_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
            known = {f for f in CompanyProfile().__dict__}
            _CACHE = CompanyProfile(**{k: v for k, v in data.items() if k in known})
        except Exception as e:
            logger.warning("Could not read company profile: %s", e)
            _CACHE = CompanyProfile()
    else:
        _CACHE = CompanyProfile()
    return _CACHE


def save_company_profile(profile: CompanyProfile) -> None:
    """Persist the company profile and refresh the cache."""
    global _CACHE
    try:
        _profile_path().write_text(
            json.dumps(asdict(profile), indent=2), encoding="utf-8"
        )
        _CACHE = profile
    except Exception as e:
        logger.warning("Could not save company profile: %s", e)


def set_logo(source_image: str | Path) -> Optional[str]:
    """Copy an uploaded image into the data root's Branding folder.

    Returns the stored absolute path, or None on failure. Call
    save_company_profile afterwards with the returned path set on the profile.
    """
    src = Path(source_image)
    if not src.exists() or src.suffix.lower() not in _LOGO_SUFFIXES:
        return None
    dest = _branding_dir() / f"logo{src.suffix.lower()}"
    try:
        shutil.copy2(src, dest)
        return str(dest)
    except Exception as e:
        logger.warning("Could not store logo: %s", e)
        return None


def is_configured() -> bool:
    return load_company_profile().is_configured()
