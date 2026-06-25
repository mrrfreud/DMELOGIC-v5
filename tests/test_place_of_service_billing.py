from __future__ import annotations

import sqlite3
from decimal import Decimal

from dmelogic.claims_1500 import hcfa1500_from_order
from dmelogic.db.converters import row_to_order
from dmelogic.db.models import BillingType, Order, OrderItem, OrderStatus
from dmelogic.db.state_portal_view import StatePortalOrderView
from dmelogic.place_of_service import place_of_service_code, place_of_service_label
from dmelogic.services.order_pricing import line_pricing_for_order_item


def _patch_inventory_db(monkeypatch, tmp_path) -> None:
    inventory_db = tmp_path / "inventory.db"
    conn = sqlite3.connect(inventory_db)
    conn.execute(
        """
        CREATE TABLE inventory (
            item_id INTEGER PRIMARY KEY,
            item_number TEXT,
            hcpcs_code TEXT,
            description TEXT,
            retail_price REAL,
            cost REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO inventory (item_id, item_number, hcpcs_code, description, retail_price, cost)
        VALUES (199, 'SUP3088BGE2XL', 'A4495-2-XL-CTBG44', 'Compression stockings', 15.12, 5.25)
        """
    )
    conn.commit()
    conn.close()

    def fake_get_connection(filename: str, folder_path=None):
        assert filename == "inventory.db"
        patched = sqlite3.connect(inventory_db)
        patched.row_factory = sqlite3.Row
        return patched

    monkeypatch.setattr("dmelogic.services.order_pricing.get_connection", fake_get_connection)


def test_place_of_service_helpers_default_and_format() -> None:
    assert place_of_service_code(None) == "12"
    assert place_of_service_code("31 - Skilled Nursing Facility") == "31"
    assert place_of_service_label("32") == "32 - Nursing Facility"


def test_row_to_order_reads_place_of_service() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            order_status TEXT,
            billing_selection TEXT,
            place_of_service TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO orders (id, order_status, billing_selection, place_of_service) VALUES (1, 'Unbilled', 'Insurance', '31')"
    )
    row = conn.execute("SELECT * FROM orders WHERE id = 1").fetchone()

    order = row_to_order(row)

    assert order.place_of_service == "31"


def test_billing_outputs_use_order_place_of_service() -> None:
    order = Order(
        id=7,
        order_status=OrderStatus.UNBILLED,
        billing_type=BillingType.INSURANCE,
        place_of_service="31",
        items=[
            OrderItem(
                id=1,
                order_id=7,
                hcpcs_code="E0143",
                description="Walker",
                quantity=1,
                total_cost=Decimal("50.00"),
            )
        ],
    )

    claim = hcfa1500_from_order(order)
    portal_view = StatePortalOrderView.from_order(order)

    assert claim.service_lines[0].place_of_service == "31"
    assert portal_view.line_items[0].place_of_service == "31"


def test_billing_amount_uses_inventory_retail_when_order_total_blank(monkeypatch, tmp_path) -> None:
    _patch_inventory_db(monkeypatch, tmp_path)
    item = OrderItem(
        id=2514,
        order_id=1047,
        hcpcs_code="A4495-2-XL-CTBG44",
        description="Compression stockings",
        quantity=4,
    )
    order = Order(
        id=1047,
        order_status=OrderStatus.UNBILLED,
        billing_type=BillingType.MEDICAID,
        items=[item],
    )

    pricing = line_pricing_for_order_item(item)
    claim = hcfa1500_from_order(order)
    portal_view = StatePortalOrderView.from_order(order)

    assert pricing.unit_price == Decimal("15.12")
    assert pricing.total == Decimal("60.48")
    assert pricing.source == "inventory_retail"
    assert claim.service_lines[0].charges == Decimal("60.48")
    assert portal_view.line_items[0].unit_price == Decimal("15.12")
    assert portal_view.line_items[0].line_total == Decimal("60.48")
    assert portal_view.total_billed_amount == Decimal("60.48")