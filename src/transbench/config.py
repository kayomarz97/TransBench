"""config.py — env reads + constants (BUILD_SPEC.md §0.7/§0.8/§1, §9; KICKOFF Phase 1-2).

No secrets are hardcoded here — API keys are read from the process environment
only (populate a local, gitignored ``.env`` from ``.env.example``).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load this repo's own gitignored .env — with override=True — BEFORE reading
# any key below. Phase 2 rationale: the *ambient process* environment can
# already hold a stale/invalid ANTHROPIC_API_KEY (e.g. exported once in a long
# -lived shell/subagent), and every subagent/test process inherits it. A plain
# ``load_dotenv()`` defaults to ``override=False``, so that stale value would
# silently win over the real key in .env and every live Anthropic call would
# keep failing. ``override=True`` makes .env authoritative instead — the
# correct BYOK behavior for this standalone repo (BUILD_SPEC.md §0.4: the key
# is "keyed by the MCP ANTHROPIC_API_KEY env"; .env is how that env is
# populated uniformly for the engine, tests, and the Phase 6 MCP server).
#
# repo_root: config.py lives at <repo>/src/transbench/config.py, so
# parents[0]=src/transbench, parents[1]=src, parents[2]=<repo root>.
# load_dotenv() on a missing path is a silent, safe no-op (returns False) —
# no existence check needed; this repo's own gitignored .env is optional at
# import time (mirrors PLAN.md's Phase 0 finding: no env var is *required* at
# import, defaults/absence are fine — this just makes a *present* .env win).
_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_REPO_ROOT / ".env", override=True)

# Public alias (post-release addition): other modules that need to resolve a
# repo-root-relative path (e.g. engine.py's TRANSBENCH_MODE=golden/snapshot
# bundled-file lookups, config.py's own `snapshots/` directory) can import
# this instead of duplicating the ``Path(__file__).resolve().parents[2]``
# computation above. Purely additive -- ``_REPO_ROOT`` itself is unchanged
# and still used as-is by the ``load_dotenv`` call above.
REPO_ROOT: Path = _REPO_ROOT

# ---------------------------------------------------------------------------
# Force deterministic LLM clients through the reused Iatronix ``create_llm()``
# factory. ``create_llm`` has no ``temperature`` kwarg of its own — it builds
# clients at ``settings.llm_temperature`` (Iatronix default 0.2), and that
# ``Settings`` object reads the env var ``LLM_TEMPERATURE`` (pydantic-settings,
# case-insensitive). Placed AFTER ``load_dotenv`` above: if .env sets
# LLM_TEMPERATURE, override=True already applied it; ``setdefault`` here is
# purely the belt-and-suspenders fallback for when .env doesn't set it at all
# (it never clobbers an explicit value either way). This is belt #1 of two
# (belt #2 is ``.bind(temperature=0)`` on every client at each agents.py call
# site, added in Phase 2 — BUILD_SPEC.md §0.7).
#
# This line MUST run before Iatronix's ``app.config.settings`` singleton is
# first constructed (i.e. before ``app.config`` is imported anywhere). It is
# placed here, and ``transbench/__init__.py`` imports this module first, which
# Python guarantees happens before any other ``transbench`` submodule (e.g.
# ``reuse.py``) runs — so this ordering is safe for every import path.
# ---------------------------------------------------------------------------
os.environ.setdefault("LLM_TEMPERATURE", "0")

# --- Model ids — real, registry-known Anthropic ids only (BUILD_SPEC.md §0.8) ---
# Reasoning tier (decompose / novelty): Sonnet. Decompose was upgraded from Haiku
# so it can reason about the observation's TYPE (drug-toxicity vs disease-response)
# when choosing the retrieval focus anchor.
MODEL_REASONING: str = "claude-sonnet-4-6"
# Deep-reasoning tier (hypothesize / experiment-design — the two biggest quality
# levers: the creative core + the runnable claude_science_prompt). Env-overridable
# via MODEL_DEEP; defaults to the reasoning tier so any entrypoint whose provider
# registry lacks Opus still works. The MCP server (run_http.sh) sets
# MODEL_DEEP=claude-opus-4-8 + PROVIDERS_CONFIG_PATH to a registry that has Opus.
MODEL_DEEP: str = os.environ.get("MODEL_DEEP") or MODEL_REASONING
# Mechanical tier (grade / entailment / assemble / query-neutralize): Haiku.
MODEL_CHEAP: str = "claude-haiku-4-5-20251001"

# --- Caps (BUILD_SPEC.md §0.7, §3, §9) ---
MAX_HYPOTHESES: int = 3
ABSTRACT_CAP: int = 8
# Fan-out concurrency cap for per-hypothesis retrieval/grading/entailment
# (mirrors Iatronix's ``parallel_sections_max_concurrent`` — BUILD_SPEC.md §3).
CONCURRENCY: int = 3
TEMPERATURE: int = 0

# --- Experiment-design fallback substrate (BUILD_SPEC.md §5/§8: "Pinned
# dataset (reproducibility)") -- a real, versioned, publicly downloadable
# human single-cell atlas with a T-cell compartment, guaranteed to resolve.
# `agents.run_design_experiment` (Phase 5) falls back to this whenever the
# model's own proposed dataset/dataset_pointer is empty or not a well-formed
# https URL to a recognized public dataset host ("never emit a fabricated/
# guessed accession"); `agents.run_assemble`'s "no eligible experiment"
# sentinel (when no hypothesis clears the novelty guard) also points here so
# even that placeholder names a real, resolvable substrate rather than
# nothing at all. Single source of truth -- both call sites import this,
# never a locally-duplicated literal.
DEFAULT_DATASET: str = "Tabula Sapiens (immune compartment)"
DEFAULT_DATASET_POINTER: str = "https://tabula-sapiens-portal.ds.czbiohub.org/"

# --- BYOK / runtime env (never hardcode secrets; BUILD_SPEC.md §0.4, .env.example) ---
# Feeds the *engine's* own Anthropic calls via ``create_llm(..., user_key=...)``
# (independent of Claude Science, which is only the MCP client). No fallback key.
ANTHROPIC_API_KEY: str | None = os.environ.get("ANTHROPIC_API_KEY")
# Optional — raises PubMed rate limits (maps to Iatronix ``settings.pubmed_api_key``).
PUBMED_API_KEY: str | None = os.environ.get("PUBMED_API_KEY")
