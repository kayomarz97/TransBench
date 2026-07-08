"""config.py — env reads + constants (BUILD_SPEC.md §0.7/§0.8/§1, §9; KICKOFF Phase 1).

No secrets are hardcoded here — API keys are read from the process environment
only (populate a local, gitignored ``.env`` from ``.env.example``).
"""
from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Force deterministic LLM clients through the reused Iatronix ``create_llm()``
# factory. ``create_llm`` has no ``temperature`` kwarg of its own — it builds
# clients at ``settings.llm_temperature`` (Iatronix default 0.2), and that
# ``Settings`` object reads the env var ``LLM_TEMPERATURE`` (pydantic-settings,
# case-insensitive). ``setdefault`` never clobbers an operator's own explicit
# override. This is belt #1 of two (belt #2 is ``.bind(temperature=0)`` on every
# client at each agents.py call site, added in Phase 2 — BUILD_SPEC.md §0.7).
#
# This line MUST run before Iatronix's ``app.config.settings`` singleton is
# first constructed (i.e. before ``app.config`` is imported anywhere). It is
# placed here, and ``transbench/__init__.py`` imports this module first, which
# Python guarantees happens before any other ``transbench`` submodule (e.g.
# ``reuse.py``) runs — so this ordering is safe for every import path.
# ---------------------------------------------------------------------------
os.environ.setdefault("LLM_TEMPERATURE", "0")

# --- Model ids — real, registry-known Anthropic ids only (BUILD_SPEC.md §0.8) ---
# Reasoning agents (hypothesize / novelty / design): Sonnet.
MODEL_REASONING: str = "claude-sonnet-4-6"
# Mechanical agents (decompose / grade / entailment / assemble): Haiku.
MODEL_CHEAP: str = "claude-haiku-4-5-20251001"

# --- Caps (BUILD_SPEC.md §0.7, §3, §9) ---
MAX_HYPOTHESES: int = 3
ABSTRACT_CAP: int = 8
# Fan-out concurrency cap for per-hypothesis retrieval/grading/entailment
# (mirrors Iatronix's ``parallel_sections_max_concurrent`` — BUILD_SPEC.md §3).
CONCURRENCY: int = 3
TEMPERATURE: int = 0

# --- BYOK / runtime env (never hardcode secrets; BUILD_SPEC.md §0.4, .env.example) ---
# Feeds the *engine's* own Anthropic calls via ``create_llm(..., user_key=...)``
# (independent of Claude Science, which is only the MCP client). No fallback key.
ANTHROPIC_API_KEY: str | None = os.environ.get("ANTHROPIC_API_KEY")
# Optional — raises PubMed rate limits (maps to Iatronix ``settings.pubmed_api_key``).
PUBMED_API_KEY: str | None = os.environ.get("PUBMED_API_KEY")
