# DMELogic v5 — Architecture & Modernization Roadmap

This document orients a new development team: how v5 is structured, what was
cleaned up from the original codebase, the known tech-debt, and a prioritized
plan to take the product to market.

---

## 1. How v5 came to be

v5 is a faithful recreation of the original application (internally
"DMELOGIC MAIN", which itself grew out of a single-file "Fax Manager Pro"
tool). The recreation:

- **Consolidated to one entry point** — `dmelogic.app:main`. The original had
  four parallel `app_*.py` variants; the duplicates and a 27k-line dead variant
  (`app_with_npi.py`) were removed.
- **Folded loose root modules into the `dmelogic` package** (OCR, Nova, theme,
  dialogs) and rewrote every import to a package-qualified form.
- **Removed ~67 one-off dev/debug/fix scripts**, a parallel `orderbase_share/`
  fork, and a duplicate `agent_order_service/` tree.
- **Separated data from install** (see §3).
- **Rebranded** to "DMELogic with Nova"; fax handling is now one feature.

Every step was validated by compiling the whole package and importing it
(including the GUI chain) against a real interpreter.

---

## 2. Module map

| Area | Package | Notes |
|---|---|---|
| Startup | `core/` | `Startup` orchestrator; `app.py` subclasses it for app-specific UI wiring. |
| Config / paths | `config.py`, `paths.py`, `core/config.py` | Two configs: top-level `config.py` = data-root/paths; `core/config.py` = typed `Config` dataclasses from `config.toml`. |
| Data access | `db/` | SQLite. WAL + busy-timeout applied for multi-user safety. |
| Domain | `models/`, `services/`, `workflows/` | Order workflow, RX parsing, duplicate detection, agent intake. |
| UI | `ui/` | PyQt6. Note the existing `ui/design_system.py` (`DS`) — the basis for §5. |
| Reporting | `reports/` | Business/financial reports + export manager. |
| Security | `security/` | Auth (argon2), permissions, idle-lock, lockout. |
| OCR | `ocr_tools.py`, `ocr_indexer.py` | Tesseract + watchdog indexing. |
| Nova (AI) | `nova_*.py`, `dmelogic_api.py`, `services/nova_*` | Feature-flagged (§4). |
| **Legacy** | `legacy/` | **The main tech-debt — see §6.** |

---

## 3. Install vs. data separation

`config.data_root()` resolves a single writable root (default
`C:\ProgramData\DMELogic`) in this order: `DMELOGIC_DATA_DIR` env →
`settings.json` `data_root` → platform default. `paths.py` derives every
runtime folder from it. The install tree under Program Files stays read-only;
the installer (`installer/DMELogic.iss`) provisions the data tree with
all-users write permission.

This replaced a scatter of hardcoded developer paths
(`C:\Users\pharmacy\Documents\FaxManagerData\…`, `…\DmeSolutionsV1\Data`).

---

## 4. Editions / feature flags

`features.nova_enabled(config)` gates the entire Nova subsystem
(`DMELOGIC_NOVA` env → `[nova] enabled` → whether `fastapi`/`uvicorn` are
installed). The startup path already guards the Nova background host and wake
listener with it.

**Follow-up to fully harden the Nova-less edition:** audit the `ui/` layer for
any always-on Nova entry points (assistant buttons/panels, the agent UI command
bridge in `app.py`) and gate them with the same flag so the AI-free build hides
them entirely.

---

## 5. UI modernization roadmap (commercial polish)

A `DesignSystem` (`ui/design_system.py`, exported as `DS`) already exists.
Recommended path to a cohesive, commercial-grade UI:

1. **Adopt the design system everywhere.** Many screens use ad-hoc inline
   stylesheets. Route colors/spacing/typography through `DS` tokens; add a
   single source of truth for light/dark QSS (consolidate `theme_assets/` +
   `dme_theme.py` + `theme_manager.py`, which currently overlap).
2. **Standardize components.** Promote the better widgets in `dme_widgets.py`
   (top bar, status badges, tables) into a documented component library and
   replace bespoke per-dialog styling.
3. **Polish the high-traffic screens first** — dashboard, orders list, order
   editor/wizard, document viewer — before the long tail of dialogs.
4. **First-run & onboarding.** The `first_run_wizard.py` is a good base; give
   it the branded splash/empty-states and a guided data-root/share setup.
5. **Licensing/activation** hooks for go-to-market (not present yet).

> Most legacy dialogs are rendered by the quarantined monolith (§6); meaningful
> UI modernization of those screens is blocked on decomposing it.

---

## 6. The #1 tech-debt: `legacy/legacy_app.py`

The original application is a single ~41k-line module. The modern shell still
depends on four classes from it, re-exported via `dmelogic/legacy/__init__.py`:
`PDFViewer`, `PrescriberDialog`, `InventoryItemDialog`, `ICD10SearchDialog`.

**`PDFViewer` alone is ~30k lines** — it is effectively the entire original
application (document viewer + much of order entry) embedded as one
`QMainWindow`. `ui/main_window.py` imports it at module load, so the live app
structurally depends on it.

The other ~17 classes in the file and its old procedural `main()` are dead.

**Recommended decomposition (incremental, test-guarded):**

1. Add characterization tests around current `PDFViewer` behavior.
2. Carve cohesive responsibilities out of `PDFViewer` into `ui/` components
   (document viewer, order panels, fax tooling), one seam at a time.
3. Extract the three remaining used dialogs into their own `ui/` modules.
4. Delete the ~17 dead classes and the dead `main()`.
5. Remove `legacy/` once `main_window.py` no longer imports from it.

This is the gating item for both UI modernization (§5) and long-term
maintainability.

---

## 7. Suggested next steps for the team

1. Stand up CI: lint, `py_compile`/import smoke, the test suite.
2. Fix the pre-existing `SyntaxWarning: invalid escape sequence` cases (raw
   strings) flagged during the v5 compile.
3. Begin the `PDFViewer` decomposition (§6).
4. Harden the Nova-less edition (§4) and adopt the design system (§5).
5. Add licensing/activation for go-to-market.
