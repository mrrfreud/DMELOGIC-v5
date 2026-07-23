from __future__ import annotations

from pathlib import Path

import fitz
from PIL import Image

from dmelogic.triage.models import EventType
from dmelogic.triage.service import TriageService
from dmelogic.triage.store import TriageStore


def _make_service(tmp_path: Path) -> TriageService:
    return TriageService(store=TriageStore(db_path=tmp_path / "documents.db"))


def test_trim_document_crops_image_and_logs_event(tmp_path: Path):
    service = _make_service(tmp_path)
    image_path = tmp_path / "rx.png"
    Image.new("L", (120, 90), color=255).save(image_path)

    doc_id = service.store.add_document(image_path.name, str(image_path))
    doc = service.store.get_document(doc_id)
    assert doc is not None

    service.trim_document(doc, (10, 12, 70, 62))

    with Image.open(image_path) as trimmed:
        assert trimmed.size == (60, 50)

    events = service.store.list_events(doc.id)
    assert events
    assert events[-1].type == EventType.TRIMMED
    assert "60×50" in events[-1].detail


def test_undo_trim_restores_original_image(tmp_path: Path):
    service = _make_service(tmp_path)
    image_path = tmp_path / "rx.png"
    Image.new("L", (120, 90), color=255).save(image_path)

    doc_id = service.store.add_document(image_path.name, str(image_path))
    doc = service.store.get_document(doc_id)
    assert doc is not None

    service.trim_document(doc, (10, 12, 70, 62))
    assert doc.trim_backup_path
    assert Path(doc.trim_backup_path).exists()

    service.undo_trim(doc)

    with Image.open(image_path) as restored:
        assert restored.size == (120, 90)

    assert doc.trim_backup_path is None
    events = service.store.list_events(doc.id)
    assert events[0].type == EventType.UNTRIMMED


def test_trim_document_crops_pdf_page_and_logs_event(tmp_path: Path):
    service = _make_service(tmp_path)
    pdf_path = tmp_path / "rx.pdf"

    pdf = fitz.open()
    page = pdf.new_page(width=300, height=400)
    page.draw_rect(fitz.Rect(40, 60, 260, 340), color=(0, 0, 0), fill=(1, 1, 1))
    pdf.save(pdf_path)
    pdf.close()

    doc_id = service.store.add_document(pdf_path.name, str(pdf_path))
    doc = service.store.get_document(doc_id)
    assert doc is not None

    service.trim_document(doc, (50, 80, 240, 320), page_index=0)

    reopened = fitz.open(pdf_path)
    try:
        cropped = reopened[0].cropbox
        assert round(cropped.width) == 190
        assert round(cropped.height) == 240
    finally:
        reopened.close()

    events = service.store.list_events(doc.id)
    assert events
    assert events[-1].type == EventType.TRIMMED
    assert "page 1" in events[-1].detail


def test_undo_trim_restores_original_pdf_page(tmp_path: Path):
    service = _make_service(tmp_path)
    pdf_path = tmp_path / "rx.pdf"

    pdf = fitz.open()
    pdf.new_page(width=300, height=400)
    pdf.save(pdf_path)
    pdf.close()

    doc_id = service.store.add_document(pdf_path.name, str(pdf_path))
    doc = service.store.get_document(doc_id)
    assert doc is not None

    service.trim_document(doc, (50, 80, 240, 320), page_index=0)
    backup_path = Path(doc.trim_backup_path or "")
    assert backup_path.exists()

    service.undo_trim(doc)

    reopened = fitz.open(pdf_path)
    try:
        restored = reopened[0].cropbox
        assert round(restored.width) == 300
        assert round(restored.height) == 400
    finally:
        reopened.close()

    assert doc.trim_backup_path is None
    assert not backup_path.exists()
    events = service.store.list_events(doc.id)
    assert events[0].type == EventType.UNTRIMMED