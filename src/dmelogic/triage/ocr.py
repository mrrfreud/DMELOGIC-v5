"""
ocr.py — OCR for incoming triage documents.

Reuses the app's existing OCR engine (``ocr_tools.extract_text_from_pdf``) and
the structured Rx parser (``services.rx_parser.RxParser``) to, for each
incoming prescription:

  1. extract the text (so triage SEARCH can match content, not just filenames),
  2. auto-read the patient name + DOB (to pre-fill rename / patient link),
  3. score the read quality so low-confidence / unreadable scans are flagged
     ("strict" mode) instead of silently passing through.

OCR is slow, so it runs on a background worker (OcrWorker), never the UI thread.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

logger = logging.getLogger("triage.ocr")

# Quality grades (the "strict" signal).
GOOD = "good"      # text read AND a patient identity parsed → confident
FAIR = "fair"      # plenty of text but no clean patient parse
LOW = "low"        # sparse text → needs review
FAILED = "failed"  # essentially no text → unreadable


def ocr_document(path: str | Path) -> dict:
    """OCR one document → {text, name, dob, quality}. Never raises."""
    path = str(path)
    text = ""
    try:
        from dmelogic.ocr_tools import extract_text_from_pdf
        text = extract_text_from_pdf(path) or ""
    except Exception as e:
        logger.warning("OCR extract failed for %s: %s", path, e)

    name, dob = "", ""
    try:
        if text.strip():
            from dmelogic.services.rx_parser import RxParser
            parsed = RxParser().parse_text(text)
            if parsed:
                p = parsed[0].patient
                last, first = (p.last_name or "").strip(), (p.first_name or "").strip()
                if last or first:
                    name = f"{last}, {first}".strip(", ")
                elif (p.full_name or "").strip():
                    name = p.full_name.strip()
                dob = (p.dob or "").strip()
    except Exception as e:
        logger.warning("Rx parse failed for %s: %s", path, e)

    words = len(text.split())
    if words < 5:
        quality = FAILED
    elif name or dob:
        quality = GOOD
    elif words >= 40:
        quality = FAIR
    else:
        quality = LOW

    return {"text": text, "name": name, "dob": dob, "quality": quality}


def quality_badge(quality: str) -> str:
    """Short human label for a quality grade."""
    return {
        GOOD: "✓ Read",
        FAIR: "• Text only",
        LOW: "⚠ Low — review",
        FAILED: "⚠ Unreadable",
    }.get(quality, "")


class OcrWorker(QObject):
    """Background worker that OCRs a list of (doc_id, path) pairs.

    Emits ``one_done(doc_id, result_dict)`` per document and ``finished()`` at
    the end. Run it on a QThread (see TriageWidget).
    """

    one_done = pyqtSignal(int, dict)
    finished = pyqtSignal()

    def __init__(self, jobs: list[tuple[int, str]]):
        super().__init__()
        self._jobs = list(jobs)
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        for doc_id, path in self._jobs:
            if self._stop:
                break
            try:
                result = ocr_document(path)
            except Exception as e:  # pragma: no cover
                logger.warning("OCR job failed (%s): %s", path, e)
                result = {"text": "", "name": "", "dob": "", "quality": FAILED}
            self.one_done.emit(doc_id, result)
        self.finished.emit()
