"""
Startup splash screen.

Shows the user that the app is loading, with rolling status messages
so they know it's not hung. Auto-dismisses when the main window shows.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont
from PyQt6.QtWidgets import QSplashScreen, QApplication


def create_splash(logo_path: Path | str | None = None, width: int = 480, height: int = 280) -> QSplashScreen:
    """
    Build a QSplashScreen. If logo_path doesn't exist, paint a plain
    branded panel so startup never fails because of a missing asset.
    """
    pix = None
    if logo_path:
        p = Path(logo_path)
        if p.exists():
            pix = QPixmap(str(p))

    if pix is None or pix.isNull():
        pix = QPixmap(width, height)
        pix.fill(QColor("#1f2937"))  # slate-800
        painter = QPainter(pix)
        painter.setPen(QColor("#f9fafb"))
        font = QFont()
        font.setPointSize(28)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "DMELogic")
        painter.end()

    splash = QSplashScreen(pix, Qt.WindowType.WindowStaysOnTopHint)
    splash.show()
    QApplication.processEvents()
    return splash


def update_splash(splash: QSplashScreen, message: str) -> None:
    """Update the splash message and let Qt actually paint it."""
    splash.showMessage(
        message,
        Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
        QColor("#f9fafb"),
    )
    QApplication.processEvents()
