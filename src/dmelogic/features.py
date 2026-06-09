"""
features.py — Edition / feature-flag resolution.

DMELogic ships in two editions from the same codebase:

* **DMELogic with Nova** (default) — includes the Nova AI assistant, its
  background host + wake listener, RingCentral integration, AI remittance
  parsing, and automated agent-order intake.
* **DMELogic** (Nova-less) — the same order/billing/fax product with the AI
  subsystem disabled.

Switching editions is a single flag, resolved here (no code forks):

    1. ``DMELOGIC_NOVA`` environment variable ("0"/"false"/"off" disables).
    2. The ``[nova] enabled`` config value (``config.toml``).
    3. Whether Nova's optional dependencies are importable at all.

Guard every Nova entry point with :func:`nova_enabled` so the Nova-less build
simply skips that code rather than failing.
"""

from __future__ import annotations

import importlib.util
import os
from typing import Any

# True only if Nova's optional runtime deps are installed (see the "nova"
# extra in pyproject.toml). The Nova-less installer omits these.
NOVA_DEPENDENCIES_AVAILABLE = all(
    importlib.util.find_spec(mod) is not None for mod in ("fastapi", "uvicorn")
)


def _env_override() -> bool | None:
    """Return an explicit on/off from the environment, or None if unset."""
    raw = os.environ.get("DMELOGIC_NOVA")
    if raw is None:
        return None
    return raw.strip().lower() not in ("0", "false", "off", "no")


def nova_enabled(config: Any | None = None) -> bool:
    """Resolve whether the Nova subsystem should run.

    Pass the loaded ``Config`` to honor its ``[nova] enabled`` flag; when no
    config is provided, fall back to the environment and dependency check.
    """
    override = _env_override()
    if override is not None:
        return override and NOVA_DEPENDENCIES_AVAILABLE

    config_enabled = True
    if config is not None:
        try:
            config_enabled = bool(config.nova.enabled)
        except Exception:
            config_enabled = True

    return config_enabled and NOVA_DEPENDENCIES_AVAILABLE
