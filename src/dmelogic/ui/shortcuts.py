"""
Application-wide keyboard shortcuts.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QShortcut, QKeySequence

logger = logging.getLogger("shortcuts")


def install_tab_shortcuts(window, tab_widget) -> None:
    """
    Bind Ctrl+1 through Ctrl+9 to switching the first nine *visible* tabs.
    Skips hidden tabs so agent-mode users get a sensible mapping.
    """
    for n in range(1, 10):
        shortcut = QShortcut(QKeySequence(f"Ctrl+{n}"), window)
        # Capture n via default arg — Python late binding would otherwise
        # make every shortcut switch to tab 9.
        shortcut.activated.connect(lambda i=n: _switch_to_visible_tab(tab_widget, i - 1))


def _switch_to_visible_tab(tab_widget, visible_index: int) -> None:
    """Switch to the Nth currently-visible tab (0-indexed)."""
    visible_count = 0
    for i in range(tab_widget.count()):
        if tab_widget.isTabVisible(i):
            if visible_count == visible_index:
                tab_widget.setCurrentIndex(i)
                return
            visible_count += 1
