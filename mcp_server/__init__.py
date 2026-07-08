"""mcp_server — the FastMCP connector package for TransBench (BUILD_SPEC.md §7, KICKOFF.md Phase 6).

Deliberately NOT part of the installable ``transbench`` distribution
(``pyproject.toml``'s ``[tool.setuptools.packages.find]`` only looks under
``src/``) — this package lives at the repo root and is run in place via
``python -m mcp_server.server`` with the process ``cwd`` set to the repo root
(see ``run_stdio.sh`` / ``run_http.sh`` / ``README.md``'s ``mcpServers``
block), exactly mirroring ``CLAUDE_SCIENCE_SETUP.md``'s existing
registration example. An empty ``__init__.py`` (rather than relying on
Python's implicit namespace-package resolution) keeps that ``-m`` import
path unambiguous across Python/tooling versions.
"""
from __future__ import annotations
