"""
fax_contacts.py
===============
Shared vocabulary for the fax-contact directory.

A fax contact is anything we send a fax to:

* ``PRESCRIBER`` — a person (MD/NP/PA). Keeps NPI/DEA/license and may practice
  at several locations, each with its own facility name, phone and fax.
* ``DME``        — another DME supplier we refer a patient to when we can't
  service them ourselves.
* ``INS_MLTC``   — an insurance plan / MLTC we need to fax.
* ``OTHER``      — anything else worth keeping on file.

Contacts live in the ``prescribers`` table (kept under that name so the ~15
existing prescriber lookups keep working); their locations live in
``fax_contact_locations``. Organizations leave the person fields NULL.
"""

from __future__ import annotations


CATEGORY_PRESCRIBER = "PRESCRIBER"
CATEGORY_DME = "DME"
CATEGORY_INS_MLTC = "INS_MLTC"
CATEGORY_OTHER = "OTHER"

DEFAULT_CATEGORY = CATEGORY_PRESCRIBER

# Display label per category, in the order they should appear in pickers.
CATEGORY_OPTIONS: tuple[tuple[str, str], ...] = (
    (CATEGORY_PRESCRIBER, "Prescriber / MD Office"),
    (CATEGORY_DME, "DME Supplier"),
    (CATEGORY_INS_MLTC, "Insurance / MLTC"),
    (CATEGORY_OTHER, "Other"),
)

_CATEGORY_LABELS = dict(CATEGORY_OPTIONS)

# Categories that are organizations rather than people: no NPI/DEA/license.
ORGANIZATION_CATEGORIES = frozenset({CATEGORY_DME, CATEGORY_INS_MLTC, CATEGORY_OTHER})

# Referral message pre-filled on the fax cover sheet for organization contacts.
# Editable per send; this is only the starting text.
REFERRAL_COVER_MESSAGE = (
    "Please service member. Member has been given your contact information "
    "and asked to follow up."
)

DEFAULT_COVER_MESSAGES: dict[str, str] = {
    CATEGORY_DME: REFERRAL_COVER_MESSAGE,
    CATEGORY_INS_MLTC: REFERRAL_COVER_MESSAGE,
}


def normalize_category(value: str | None) -> str:
    """Return a known category code, defaulting to PRESCRIBER."""
    text = (value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if text in _CATEGORY_LABELS:
        return text
    if text in {"INS", "MLTC", "INSURANCE"}:
        return CATEGORY_INS_MLTC
    return DEFAULT_CATEGORY


def category_label(value: str | None) -> str:
    """Human-readable label such as 'Prescriber / MD Office'."""
    return _CATEGORY_LABELS.get(normalize_category(value), _CATEGORY_LABELS[DEFAULT_CATEGORY])


def category_labels() -> list[str]:
    """Combo-box labels in stable order."""
    return [label for _code, label in CATEGORY_OPTIONS]


def is_organization(value: str | None) -> bool:
    """True when the contact is an org (no prescriber person fields)."""
    return normalize_category(value) in ORGANIZATION_CATEGORIES


def default_cover_message(value: str | None) -> str:
    """Starting cover-sheet message for a category ('' for prescribers)."""
    return DEFAULT_COVER_MESSAGES.get(normalize_category(value), "")
