"""
merge_contacts_dialog.py
========================
Fold duplicate contacts into one.

Before locations existed, a prescriber working at several offices was cloned
once per office, so the same doctor appears several times under one NPI. This
reviews those groups and merges each into a single contact holding all its
offices.

Nothing is merged automatically: an NPI belongs to one provider, so a group
whose surnames disagree is a data-entry error rather than a duplicate, and is
flagged for a human to look at.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTreeWidget,
    QTreeWidgetItem, QMessageBox, QAbstractItemView,
)

from dmelogic.db import fax_contact_locations as repo


class MergeContactsDialog(QDialog):
    """Review duplicate-NPI groups and merge them."""

    def __init__(self, parent=None, folder_path: Optional[str] = None):
        super().__init__(parent)
        self.folder_path = folder_path
        self.setWindowTitle("Merge Duplicate Contacts")
        self.resize(940, 560)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<b>Contacts sharing an NPI</b><br>"
            "<span style='color:#64748b'>These are usually one prescriber that was "
            "cloned once per office. Merging keeps one contact and moves the others' "
            "offices onto it — order history is unaffected.<br>"
            "Pick the record to keep (it starts on the oldest), then Merge. Groups "
            "marked <b style='color:#b91c1c'>name mismatch</b> have different surnames "
            "under one NPI, which is a data error — check those before merging.</span>"
        ))

        self.tree = QTreeWidget()
        self.tree.setColumnCount(7)
        self.tree.setHeaderLabels(
            ["NPI / Contact", "Name", "Practice", "City", "Fax", "Offices", "Keep"]
        )
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setAlternatingRowColors(True)
        layout.addWidget(self.tree)

        row = QHBoxLayout()
        self.btn_keep = QPushButton("★ Keep selected record")
        self.btn_keep.setToolTip("Make the highlighted record the one the group merges into")
        self.btn_merge = QPushButton("Merge this group")
        self.btn_merge_safe = QPushButton("Merge all matching-name groups")
        self.btn_merge_safe.setToolTip(
            "Merge every group whose surnames agree, leaving mismatches alone"
        )
        self.btn_close = QPushButton("Close")
        self.btn_keep.clicked.connect(self._set_keeper)
        self.btn_merge.clicked.connect(self._merge_selected_group)
        self.btn_merge_safe.clicked.connect(self._merge_all_safe)
        self.btn_close.clicked.connect(self.accept)
        for b in (self.btn_keep, self.btn_merge, self.btn_merge_safe):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(self.btn_close)
        layout.addLayout(row)

        self.status = QLabel("")
        self.status.setStyleSheet("color:#64748b;font-size:12px;")
        layout.addWidget(self.status)

        self._reload()

    # ---------------- internals ----------------

    def _reload(self) -> None:
        self.tree.clear()
        groups = repo.find_duplicate_contacts(folder_path=self.folder_path)
        self._groups = groups
        total_extra = 0
        for g in groups:
            members = g["members"]
            total_extra += len(members) - 1
            top = QTreeWidgetItem(self.tree)
            label = f"NPI {g['npi']}  ({len(members)} records)"
            if g["name_mismatch"]:
                label += "   ⚠ name mismatch"
                top.setForeground(0, QBrush(QColor("#b91c1c")))
            top.setText(0, label)
            top.setData(0, Qt.ItemDataRole.UserRole, {"group": g, "keeper": members[0]["id"]})
            f = top.font(0); f.setBold(True); top.setFont(0, f)

            for i, m in enumerate(members):
                child = QTreeWidgetItem(top)
                child.setText(0, f"id {m['id']}")
                child.setText(1, " ".join(x for x in ((m["last_name"] or ""), (m["first_name"] or "")) if x))
                child.setText(2, m["practice_name"] or "")
                child.setText(3, m["city"] or "")
                child.setText(4, m["fax"] or "")
                child.setText(5, str(repo.count_locations(m["id"], folder_path=self.folder_path)))
                child.setText(6, "★" if i == 0 else "")
                child.setData(0, Qt.ItemDataRole.UserRole, {"member_id": m["id"]})
            top.setExpanded(True)

        for c in range(self.tree.columnCount()):
            self.tree.resizeColumnToContents(c)
        n_safe = sum(1 for g in groups if not g["name_mismatch"])
        self.status.setText(
            f"{len(groups)} duplicated NPI(s), {total_extra} redundant record(s) · "
            f"{n_safe} safe to merge, {len(groups) - n_safe} need review"
        )
        self.btn_merge_safe.setEnabled(n_safe > 0)

    def _selected_group_item(self):
        item = self.tree.currentItem()
        if item is None:
            return None
        return item if item.parent() is None else item.parent()

    def _set_keeper(self) -> None:
        item = self.tree.currentItem()
        if item is None or item.parent() is None:
            QMessageBox.information(self, "Merge", "Select one of the records under a group.")
            return
        parent = item.parent()
        data = parent.data(0, Qt.ItemDataRole.UserRole)
        member = item.data(0, Qt.ItemDataRole.UserRole)
        data["keeper"] = member["member_id"]
        parent.setData(0, Qt.ItemDataRole.UserRole, data)
        for i in range(parent.childCount()):
            child = parent.child(i)
            child.setText(6, "★" if child is item else "")

    def _merge_group(self, group_item) -> tuple[bool, str]:
        data = group_item.data(0, Qt.ItemDataRole.UserRole)
        g, keeper = data["group"], data["keeper"]
        dup_ids = [m["id"] for m in g["members"] if m["id"] != keeper]
        return repo.merge_contacts(keeper, dup_ids, folder_path=self.folder_path)

    def _merge_selected_group(self) -> None:
        item = self._selected_group_item()
        if item is None:
            QMessageBox.information(self, "Merge", "Select a group first.")
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        warn = ""
        if data["group"]["name_mismatch"]:
            warn = ("\n\n⚠ The surnames in this group differ. An NPI belongs to one "
                    "provider, so this is probably a typo rather than a duplicate — "
                    "merging would combine two different people.")
        if QMessageBox.question(
            self, "Merge Group",
            f"Merge NPI {data['group']['npi']} into contact id {data['keeper']}?"
            f"{warn}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        ok, msg = self._merge_group(item)
        self._reload()
        self.status.setText(msg if ok else f"Merge failed: {msg}")

    def _merge_all_safe(self) -> None:
        safe = [i for i in range(self.tree.topLevelItemCount())
                if not self.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)["group"]["name_mismatch"]]
        if not safe:
            return
        if QMessageBox.question(
            self, "Merge All",
            f"Merge {len(safe)} group(s) whose names agree?\n\n"
            "Each keeps its oldest record (or whichever you marked ★) and gains the "
            "others' offices. Groups with a name mismatch are left alone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        done = failed = 0
        for i in safe:
            ok, _ = self._merge_group(self.tree.topLevelItem(i))
            done += 1 if ok else 0
            failed += 0 if ok else 1
        self._reload()
        self.status.setText(
            f"Merged {done} group(s)" + (f", {failed} failed" if failed else "")
        )
