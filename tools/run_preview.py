"""
run_preview.py — Launch the v5 app in isolation for a clean first-run demo.

* Forces v5/src to win over any editable install of the MAIN package.
* Points all runtime data at a dedicated preview folder so nothing the live
  Central Pharmacy build uses is touched.

Run with the project's Python:
    python tools\run_preview.py
"""
import os
import sys

# 1. Make v5/src authoritative (drop editable-install finders + MAIN root).
sys.meta_path = [
    f for f in sys.meta_path
    if "editable" not in type(f).__name__.lower()
    and "__editable" not in repr(f).lower()
]
sys.path = [p for p in sys.path if p.rstrip("\\").lower() != r"c:\dmelogic main"]
SRC = r"C:\DMELOGIC-v5\src"
sys.path.insert(0, SRC)

# 2. Isolated, disposable data root — separate from the live build.
os.environ.setdefault("DMELOGIC_DATA_DIR", r"C:\DMELogicV5_Preview")

# 3. Launch.
from dmelogic.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
