"""
run_triage_preview.py — open the New Rx Triage screen with sample data.

Isolated from any live build: uses its own data root and seeds a few sample
prescription PDFs into the New Rx folder so you can click through the flow
(view → rename → route to a bucket → notes → history → search).

    python tools\run_triage_preview.py
"""
import os
import sys

# Force v5/src to win over any editable install of the MAIN package.
sys.meta_path = [
    f for f in sys.meta_path
    if "editable" not in type(f).__name__.lower()
    and "__editable" not in repr(f).lower()
]
sys.path = [p for p in sys.path if p.rstrip("\\").lower() != r"c:\dmelogic main"]
sys.path.insert(0, r"C:\DMELOGIC-v5\src")

os.environ.setdefault("DMELOGIC_EDITION", "preview")
os.environ.setdefault("DMELOGIC_DATA_DIR", r"C:\DMELogicV5_Preview")


def _make_sample_pdf(path, lines):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 720
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, y, "PRESCRIPTION")
    c.setFont("Helvetica", 12)
    y -= 36
    for line in lines:
        c.drawString(72, y, line)
        y -= 22
    c.save()


def seed_samples():
    from dmelogic.triage.service import new_rx_folder
    folder = new_rx_folder()
    existing = [p for p in folder.iterdir() if p.is_file()] if folder.exists() else []
    if existing:
        return  # already seeded
    samples = {
        "incoming_fax_2026-06-09_0912.pdf": [
            "Patient: SMITH, JOHN", "DOB: 03/14/1958",
            "Rx: Oxygen Concentrator, 2 LPM", "Prescriber: Dr. A. Patel  NPI 1234567890",
        ],
        "scan_2026-06-09_1003.pdf": [
            "Patient: GARCIA, MARIA", "DOB: 11/02/1971",
            "Rx: CPAP + humidifier", "Insurance: (missing — verify)",
        ],
        "pasted_rx_unknown.pdf": [
            "Patient: (illegible)", "Rx: Wheelchair, standard",
            "Note: unable to reach patient by phone",
        ],
    }
    for name, lines in samples.items():
        _make_sample_pdf(folder / name, lines)


def main():
    from PyQt6.QtWidgets import QApplication
    from dmelogic.triage.ui.triage_widget import TriageWidget
    from dmelogic.triage.service import new_rx_folder

    seed_samples()
    app = QApplication(sys.argv)
    from dmelogic.ui.theme_modern import apply_modern_theme
    apply_modern_theme(app)
    w = TriageWidget()
    w.setWindowTitle("DMELogic — New Rx Triage (PREVIEW)")
    w.resize(1320, 820)
    w.show()
    print(f"New Rx folder: {new_rx_folder()}")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
