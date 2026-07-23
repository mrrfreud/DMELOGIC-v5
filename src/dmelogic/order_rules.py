"""
order_rules.py
==============
Order-level business rules shared by New Order creation and Refill processing.

Two rule families are enforced here:

1. Quantity vs. fee-schedule Max Units
   Every line item's quantity is checked against the Medicaid Max Units value
   from the fee schedule (billing.db). Exceeding it is a soft block: the user
   is warned and must either correct the quantity or explicitly override when
   the higher quantity is intentional.

2. Incompatible HCPCS combinations
   Certain codes may not appear together in the same order / for the same
   patient. Currently: A4554 (disposable underpads) cannot be combined with
   T4537 or T4540 (reusable underpads). Flagged the same soft-block way.

The evaluation functions (`find_qty_violations`, `find_conflicts`,
`evaluate_items`) are pure logic and safe to import anywhere. The single UI
helper `confirm_item_rule_issues` imports PyQt6 lazily so this module stays
usable from non-GUI contexts (services, agents, tests).
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple, Optional, NamedTuple


# ─────────────────────────────────────────────────────────
#  RULE DATA
# ─────────────────────────────────────────────────────────

# Pairs of base HCPCS codes that may not appear together in one order.
# Extend this list to add new incompatibilities.
INCOMPATIBLE_PAIRS: List[frozenset] = [
    frozenset({"A4554", "T4537"}),
    frozenset({"A4554", "T4540"}),
]


def normalize_hcpcs(code: str) -> str:
    """Reduce an item code to its base 5-char HCPCS (strip suffix / spaces)."""
    if not code:
        return ""
    return code.strip().upper().split("-")[0][:5]


# ─────────────────────────────────────────────────────────
#  RESULT TYPES
# ─────────────────────────────────────────────────────────

class QtyViolation(NamedTuple):
    hcpcs: str          # base code, e.g. "A4554"
    label: str          # description or item label for display
    qty: int
    max_units: int


class Conflict(NamedTuple):
    code_a: str
    code_b: str
    label: str          # optional patient/context label ("" for single order)


class RuleReport(NamedTuple):
    qty_violations: List[QtyViolation]
    conflicts: List[Conflict]

    @property
    def has_issues(self) -> bool:
        return bool(self.qty_violations or self.conflicts)


# ─────────────────────────────────────────────────────────
#  EVALUATION (pure logic)
# ─────────────────────────────────────────────────────────

def _to_int(val) -> Optional[int]:
    if val is None or val == "":
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def find_qty_violations(
    items: Iterable[Tuple[str, object, str]],
    fee_reader,
) -> List[QtyViolation]:
    """
    items: iterable of (hcpcs_code, quantity, label).
    Returns a QtyViolation for every line whose qty exceeds the fee-schedule max.
    """
    out: List[QtyViolation] = []
    if not fee_reader:
        return out
    for raw_code, raw_qty, label in items:
        base = normalize_hcpcs(raw_code)
        if not base:
            continue
        qty = _to_int(raw_qty)
        if qty is None or qty <= 0:
            continue
        max_u = _to_int(fee_reader.get_max_units(base))
        if max_u is not None and max_u > 0 and qty > max_u:
            out.append(QtyViolation(hcpcs=base, label=label or base, qty=qty, max_units=max_u))
    return out


def find_conflicts(
    codes: Sequence[str],
    label: str = "",
) -> List[Conflict]:
    """
    codes: sequence of HCPCS codes present in one order (or for one patient).
    Returns a Conflict for each incompatible pair fully present in `codes`.
    """
    present = {normalize_hcpcs(c) for c in codes if c}
    present.discard("")
    out: List[Conflict] = []
    for pair in INCOMPATIBLE_PAIRS:
        if pair <= present:
            a, b = sorted(pair)
            out.append(Conflict(code_a=a, code_b=b, label=label))
    return out


def evaluate_items(
    items: Sequence[Tuple[str, object, str]],
    fee_reader,
    conflict_label: str = "",
) -> RuleReport:
    """
    Evaluate a single order's items (list of (hcpcs, qty, label)) for both
    quantity violations and incompatible combinations.
    """
    qty_violations = find_qty_violations(items, fee_reader)
    conflicts = find_conflicts([c for c, _q, _l in items], label=conflict_label)
    return RuleReport(qty_violations=qty_violations, conflicts=conflicts)


# ─────────────────────────────────────────────────────────
#  UI PROMPT (PyQt6 imported lazily)
# ─────────────────────────────────────────────────────────

def build_issue_html(report: RuleReport, *, context: str = "order",
                     max_items: int = 12) -> str:
    """Human-readable HTML summary of the rule issues (used by the dialog).

    Long lists (e.g. bulk "Process All Due") are capped at `max_items` per
    section with a "…and N more" line so the dialog stays usable.
    """
    parts: List[str] = []

    if report.qty_violations:
        parts.append("<b>Quantity over Medicaid limit:</b><ul>")
        for v in report.qty_violations[:max_items]:
            parts.append(
                f"<li><b>{v.hcpcs}</b> — {v.label}<br>"
                f"Quantity <b>{v.qty}</b> exceeds the Max Units limit of "
                f"<b>{v.max_units}</b>.</li>"
            )
        extra = len(report.qty_violations) - max_items
        if extra > 0:
            parts.append(f"<li>…and <b>{extra}</b> more over-limit item(s).</li>")
        parts.append("</ul>")

    if report.conflicts:
        parts.append("<b>Incompatible items in the same order:</b><ul>")
        for c in report.conflicts[:max_items]:
            who = f" for {c.label}" if c.label else ""
            parts.append(
                f"<li><b>{c.code_a}</b> cannot be billed together with "
                f"<b>{c.code_b}</b>{who}.</li>"
            )
        extra = len(report.conflicts) - max_items
        if extra > 0:
            parts.append(f"<li>…and <b>{extra}</b> more conflicting order(s).</li>")
        parts.append("</ul>")

    return "".join(parts)


def confirm_item_rule_issues(
    parent,
    report: RuleReport,
    *,
    context: str = "order",
) -> bool:
    """
    Show a blocking warning for any rule issues and let the user decide.

    Returns:
        True  -> proceed (no issues, OR the user explicitly overrode)
        False -> block (the user chose to go back and edit)

    The override path is only offered when there are issues, and the default
    button is always "Go Back & Edit" so proceeding is a deliberate choice.
    """
    if not report.has_issues:
        return True

    # Lazy import so the pure-logic side of this module has no Qt dependency.
    from PyQt6.QtWidgets import QMessageBox
    from PyQt6.QtCore import Qt

    intro = (
        "This refill carries values that need attention before it can be finished:"
        if context == "refill"
        else "This order has values that must be corrected before it can be created:"
    )
    body = (
        f"<p>{intro}</p>"
        + build_issue_html(report, context=context)
        + "<p>Correct the item(s) above, or override only if you are sure the "
        "order is intentionally this way.</p>"
    )

    box = QMessageBox(parent)
    box.setWindowTitle("Order Limit / Combination Warning")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setText(body)

    edit_btn = box.addButton("Go Back && Edit", QMessageBox.ButtonRole.RejectRole)
    override_btn = box.addButton("Override && Continue", QMessageBox.ButtonRole.AcceptRole)
    box.setDefaultButton(edit_btn)
    box.exec()

    if box.clickedButton() is override_btn:
        try:
            from dmelogic.config import debug_log
            summary = "; ".join(
                [f"qty {v.hcpcs}={v.qty}>{v.max_units}" for v in report.qty_violations]
                + [f"conflict {c.code_a}+{c.code_b}" for c in report.conflicts]
            )
            debug_log(f"[order_rules] OVERRIDE ({context}): {summary}")
        except Exception:
            pass
        return True

    return False


def order_display_label(order) -> str:
    """Human label for an order object: ORD-<parent>-R<n> for refills, else ORD-<id>."""
    try:
        pid = getattr(order, "parent_order_id", None)
        rn = int(getattr(order, "refill_number", 0) or 0)
        oid = int(getattr(order, "id", 0) or 0)
        if pid and rn:
            return f"ORD-{int(pid):03d}-R{rn}"
        return f"ORD-{oid:03d}"
    except Exception:
        return "the new order"


def evaluate_order_object(order, folder_path=None) -> RuleReport:
    """Evaluate a concrete Order object's items for qty limits + conflicts."""
    from dmelogic.fee_schedule_enhancements import DbFeeScheduleReader
    reader = DbFeeScheduleReader(folder_path=folder_path)
    items = [
        (
            getattr(it, "hcpcs_code", "") or "",
            getattr(it, "quantity", 0),
            getattr(it, "description", "") or getattr(it, "hcpcs_code", "") or "",
        )
        for it in (getattr(order, "items", None) or [])
    ]
    return evaluate_items(items, reader, conflict_label=order_display_label(order))


BILLED_STATUSES = {"BILLED", "PAID", "COMPLETE", "COMPLETED", "CANCELLED", "VOID", "DELETED"}


def order_is_editable_prebilling(order) -> bool:
    """True if the order has not been billed/finalised yet (safe to edit qty)."""
    status = getattr(order, "order_status", "") or ""
    status = getattr(status, "value", status)
    return str(status).strip().upper() not in BILLED_STATUSES


def warn_order_needs_edit(parent, order, report: RuleReport) -> None:
    """
    Non-blocking notice telling the user to correct THIS order (reduce quantity
    or remove an incompatible item) before billing. Used both right after a
    refill is created and whenever an unbilled over-limit order is opened.
    """
    if not report.has_issues:
        return
    from PyQt6.QtWidgets import QMessageBox
    from PyQt6.QtCore import Qt

    label = order_display_label(order)
    body = (
        f"<p><b>{label}</b> needs a correction before it can be billed:</p>"
        + build_issue_html(report, context="refill")
        + "<p>Reduce the quantity (or remove the conflicting item) on "
        f"<b>{label}</b> so what is saved matches what will be billed. "
        "Any previous/source order is left unchanged.</p>"
    )
    box = QMessageBox(parent)
    box.setWindowTitle("Order Needs a Quantity / Item Edit")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setText(body)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()


# Backwards-compatible alias.
warn_new_order_needs_edit = warn_order_needs_edit


def evaluate_orders_from_db(order_ids, folder_path=None, orders_db_file=None):
    """
    Load the given orders' items from orders.db and evaluate each for rule
    issues. Returns a list of (label, RuleReport) for orders WITH issues only.
    Used to summarise freshly created batch refills.
    """
    ids = [int(o) for o in order_ids if isinstance(o, int) or (str(o).strip().isdigit())]
    if not ids:
        return []
    import sqlite3
    try:
        if orders_db_file:
            conn = sqlite3.connect(orders_db_file)
        else:
            from dmelogic.db.base import get_connection
            conn = get_connection("orders.db", folder_path=folder_path)
        conn.row_factory = sqlite3.Row
        qmarks = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"""SELECT oi.order_id, oi.hcpcs_code, oi.qty, oi.description,
                       o.parent_order_id, o.refill_number
                FROM order_items oi JOIN orders o ON o.id = oi.order_id
                WHERE oi.order_id IN ({qmarks})""",
            ids,
        ).fetchall()
        conn.close()
    except Exception:
        return []

    from dmelogic.fee_schedule_enhancements import DbFeeScheduleReader
    reader = DbFeeScheduleReader(folder_path=folder_path)
    groups: dict = {}
    meta: dict = {}
    for r in rows:
        oid = r["order_id"]
        groups.setdefault(oid, []).append(
            (r["hcpcs_code"] or "", r["qty"], r["description"] or "")
        )
        meta[oid] = (r["parent_order_id"], r["refill_number"])
    out = []
    for oid, items in groups.items():
        pid, rn = meta.get(oid, (None, 0))
        label = f"ORD-{int(pid):03d}-R{int(rn)}" if pid and rn else f"ORD-{int(oid):03d}"
        rep = evaluate_items(items, reader, conflict_label=label)
        if rep.has_issues:
            out.append((label, rep))
    return out


def run_refill_with_override(parent, order_id, folder_path=None):
    """
    Process a refill for one source order and, if the resulting NEW order
    exceeds Max-Units limits or contains an incompatible combination, warn the
    user to edit that NEW order before billing.

    The source order is never modified. Returns the new order object. Non-rule
    errors (e.g. the 365-day rule) propagate so the caller's existing error
    handling still applies.
    """
    from dmelogic.refill_service import process_refill

    new_order = process_refill(int(order_id), folder_path=folder_path or "")
    try:
        report = evaluate_order_object(new_order, folder_path)
        if report.has_issues:
            warn_new_order_needs_edit(parent, new_order, report)
    except Exception as e:
        try:
            from dmelogic.config import debug_log
            debug_log(f"[order_rules] new-order flag skipped: {e}")
        except Exception:
            pass
    return new_order
