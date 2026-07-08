"""TransBench — translational clinician<->bench agent, shipped as an MCP connector.

Standalone package. Reuses the Iatronix backend read-only via the single seam in
``reuse.py`` (BUILD_SPEC.md §2) — never edits it.

Import order matters here: this file imports ``config`` first (before any other
submodule can run) so that ``LLM_TEMPERATURE=0`` (BUILD_SPEC.md §0.7) is set in
the process environment *before* anything imports Iatronix's
``app.config.settings`` singleton. Python guarantees ``transbench/__init__.py``
runs to completion before any ``transbench.<submodule>`` body executes — e.g.
``from transbench.reuse import ...`` or ``from transbench.engine import ...``
both trigger this file first — so this ordering is safe regardless of which
submodule a caller imports first.
"""
from __future__ import annotations

from . import config  # noqa: F401  (side effect: os.environ.setdefault("LLM_TEMPERATURE", "0"))

__all__ = ["config"]
__version__ = "0.1.0"
