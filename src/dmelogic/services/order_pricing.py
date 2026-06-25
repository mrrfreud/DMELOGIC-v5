"""Shared pricing helpers for order line items."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Optional

from dmelogic.config import debug_log
from dmelogic.db.base import get_connection


_CENTS = Decimal("0.01")


@dataclass(frozen=True)
class OrderLinePricing:
    unit_price: Decimal
    total: Decimal
    source: str


def _decimal_or_none(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        cleaned = value.strip().replace("$", "").replace(",", "")
        if not cleaned:
            return None
        value = cleaned
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = _text(value)
        if not cleaned:
            continue
        key = cleaned.upper()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _quantity(item: Any) -> Decimal:
    qty = _decimal_or_none(getattr(item, "quantity", None))
    return qty if qty is not None else Decimal("0")


def _hcpcs_candidates(item: Any, hcpcs_candidates: Iterable[str] | None = None) -> list[str]:
    values: list[str] = []
    if hcpcs_candidates:
        values.extend(hcpcs_candidates)

    full_hcpcs = _text(getattr(item, "hcpcs_code", ""))
    if full_hcpcs:
        values.append(full_hcpcs)
        before_suffix = full_hcpcs.split("-", 1)[0]
        values.append(before_suffix)
        if "+" in before_suffix:
            values.extend(part.strip() for part in before_suffix.split("+"))
        if len(full_hcpcs) >= 5:
            values.append(full_hcpcs[:5])

    return _unique(values)


def _item_number_candidates(item: Any, item_number: str | None = None) -> list[str]:
    values = [item_number or "", _text(getattr(item, "item_number", ""))]
    full_hcpcs = _text(getattr(item, "hcpcs_code", ""))
    if "-" in full_hcpcs:
        values.append(full_hcpcs.split("-", 1)[1])
    return _unique(values)


def _inventory_item_id(item: Any) -> Optional[int]:
    raw_value = getattr(item, "inventory_item_id", None)
    if raw_value in (None, ""):
        return None
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def _retail_price_from_row(row: Any) -> Optional[Decimal]:
    if not row:
        return None
    try:
        return _decimal_or_none(row["retail_price"])
    except Exception:
        return None


def inventory_retail_price_for_order_item(
    item: Any,
    *,
    folder_path: Optional[str] = None,
    hcpcs_candidates: Iterable[str] | None = None,
    item_number: str | None = None,
) -> Optional[Decimal]:
    """Return the inventory retail price matching an order item, if available."""
    try:
        conn = get_connection("inventory.db", folder_path=folder_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()

            inventory_id = _inventory_item_id(item)
            if inventory_id is not None:
                cur.execute(
                    "SELECT retail_price FROM inventory WHERE item_id = ? LIMIT 1",
                    (inventory_id,),
                )
                price = _retail_price_from_row(cur.fetchone())
                if price is not None:
                    return price

            for candidate in _item_number_candidates(item, item_number):
                cur.execute(
                    """
                    SELECT retail_price
                    FROM inventory
                    WHERE UPPER(TRIM(item_number)) = UPPER(TRIM(?))
                    ORDER BY item_id DESC
                    LIMIT 1
                    """,
                    (candidate,),
                )
                price = _retail_price_from_row(cur.fetchone())
                if price is not None:
                    return price

            for candidate in _hcpcs_candidates(item, hcpcs_candidates):
                cur.execute(
                    """
                    SELECT retail_price
                    FROM inventory
                    WHERE UPPER(TRIM(hcpcs_code)) = UPPER(TRIM(?))
                    ORDER BY item_id DESC
                    LIMIT 1
                    """,
                    (candidate,),
                )
                price = _retail_price_from_row(cur.fetchone())
                if price is not None:
                    return price

                cur.execute(
                    """
                    SELECT retail_price
                    FROM inventory
                    WHERE UPPER(TRIM(hcpcs_code)) LIKE UPPER(TRIM(?)) || '%'
                    ORDER BY item_id DESC
                    LIMIT 1
                    """,
                    (candidate,),
                )
                price = _retail_price_from_row(cur.fetchone())
                if price is not None:
                    return price

            return None
        finally:
            conn.close()
    except Exception as exc:
        debug_log(f"Pricing: inventory retail lookup failed: {exc}")
        return None


def line_pricing_for_order_item(
    item: Any,
    *,
    folder_path: Optional[str] = None,
    hcpcs_candidates: Iterable[str] | None = None,
    item_number: str | None = None,
) -> OrderLinePricing:
    """Price a line item using inventory retail first, then stored order pricing."""
    qty = _quantity(item)
    retail_price = inventory_retail_price_for_order_item(
        item,
        folder_path=folder_path,
        hcpcs_candidates=hcpcs_candidates,
        item_number=item_number,
    )
    if retail_price is not None:
        unit_price = _money(retail_price)
        return OrderLinePricing(
            unit_price=unit_price,
            total=_money(unit_price * qty),
            source="inventory_retail",
        )

    stored_total = _decimal_or_none(getattr(item, "total_cost", None))
    if stored_total is not None:
        total = _money(stored_total)
        unit_price = _money(total / qty) if qty else Decimal("0.00")
        return OrderLinePricing(unit_price=unit_price, total=total, source="stored_total")

    stored_unit = _decimal_or_none(getattr(item, "cost_ea", None))
    if stored_unit is not None:
        unit_price = _money(stored_unit)
        return OrderLinePricing(
            unit_price=unit_price,
            total=_money(unit_price * qty),
            source="stored_unit_price",
        )

    return OrderLinePricing(
        unit_price=Decimal("0.00"),
        total=Decimal("0.00"),
        source="missing",
    )