# DMELogic with Nova

DME pharmacy order management — intake (fax / eRx / walk-in / phone), patient &
prescriber management, order workflow, refills, billing & fee schedules,
reporting, and the **Nova** AI assistant. Windows desktop app (PyQt6).

> **v5** is a reorganized, install-ready recreation of the original codebase,
> prepared for a development-team handoff. See
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the structure, the known
> tech-debt, and the modernization roadmap.

## Layout

```
DMELOGIC-v5/
├─ src/dmelogic/         The application package (single source of truth)
│  ├─ app.py             Entry point  →  dmelogic.app:main
│  ├─ core/              Startup orchestration, config, logging, crash reporting
│  ├─ db/                SQLite data access (patients, orders, refills, …)
│  ├─ models/            Domain models
│  ├─ services/          Order workflow, parsing, agent intake, background svcs
│  ├─ ui/                PyQt6 windows, dialogs, design system
│  ├─ reports/           Business/financial reporting + export
│  ├─ security/          Auth, permissions, idle-lock, lockout
│  ├─ ocr_tools.py       OCR + indexing
│  ├─ nova_*.py          Nova AI assistant (feature-flagged)
│  ├─ legacy/            Quarantined original monolith (see ARCHITECTURE.md)
│  ├─ config.py          Data-root resolution
│  ├─ paths.py           All runtime paths (data vs install separation)
│  └─ features.py        Edition / feature flags (Nova on/off)
├─ assets/               Icons, images, fee schedules (bundled, read-only)
├─ installer/            PyInstaller spec + Inno Setup script + build guide
├─ tools/               Maintenance scripts
├─ tests/
├─ docs/
└─ pyproject.toml
```

## Install vs. data

The install folder (`C:\Program Files\DMELogic`) is **read-only** at runtime.
Everything the app reads/writes lives under a single, configurable **data
root** — by default `C:\ProgramData\DMELogic`:

```
C:\ProgramData\DMELogic\
├─ Databases\   Backups\   Scans\   DeliveryTickets\
├─ FaxPackets\  PatientDocuments\   Tickets\   POD\   CMN\
├─ Exports\     Logs\      settings.json   config.toml
```

Override the root with the `DMELOGIC_DATA_DIR` env var or a `data_root` key in
`settings.json` (e.g. to point all workstations at a shared network folder).

## Develop

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e .[nova,dev]
python -m dmelogic.app          # or:  dmelogic
```

## Reliability Upgrade checks

Run these before releases (and in CI):

```powershell
python -m pytest -q
python tools/reliability_check.py
```

`tools/reliability_check.py` verifies critical hardening guarantees (startup
fail-fast hooks, lazy UI seams, and expected reliability regression tests).

## Editions

| Edition | Build |
|---|---|
| **DMELogic with Nova** (default) | `pip install -e .[nova]` |
| **DMELogic** (Nova-less) | omit the `nova` extra and/or set `[nova] enabled = false` / `DMELOGIC_NOVA=0` |

## Package

See [`installer/README.md`](installer/README.md) — `pyinstaller
installer\DMELogic.spec` then `iscc installer\DMELogic.iss`.
