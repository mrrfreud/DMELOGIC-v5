from __future__ import annotations


DEFAULT_PLACE_OF_SERVICE = "12"

PLACE_OF_SERVICE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("12", "Home"),
    ("11", "Office"),
    ("13", "Assisted Living Facility"),
    ("14", "Group Home"),
    ("31", "Skilled Nursing Facility"),
    ("32", "Nursing Facility"),
    ("33", "Custodial Care Facility"),
    ("04", "Homeless Shelter"),
    ("15", "Mobile Unit"),
    ("16", "Temporary Lodging"),
    ("49", "Independent Clinic"),
    ("99", "Other Place of Service"),
)

_PLACE_OF_SERVICE_DESCRIPTIONS = dict(PLACE_OF_SERVICE_OPTIONS)


def place_of_service_code(value: str | None) -> str:
    """Return the 2-digit POS code, defaulting to Home (12)."""
    text = (value or "").strip()
    if not text:
        return DEFAULT_PLACE_OF_SERVICE

    for code, _description in PLACE_OF_SERVICE_OPTIONS:
        if text == code or text.startswith(f"{code} ") or text.startswith(f"{code}-") or text.startswith(f"{code}:"):
            return code

    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 2:
        return digits[:2]

    return DEFAULT_PLACE_OF_SERVICE


def place_of_service_label(value: str | None) -> str:
    """Return a human-readable POS label such as '12 - Home'."""
    code = place_of_service_code(value)
    description = _PLACE_OF_SERVICE_DESCRIPTIONS.get(code)
    return f"{code} - {description}" if description else code


def place_of_service_labels() -> list[str]:
    """Return combo-box display labels in stable order."""
    return [f"{code} - {description}" for code, description in PLACE_OF_SERVICE_OPTIONS]