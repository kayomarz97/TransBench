"""engine.py — thin public entrypoint for the TransBench engine (BUILD_SPEC.md §1).

``run_transbench`` is the single async entrypoint every caller (tests, the
MCP server in Phase 6, Phase 2-5 internals) should import. ``run_transbench_sync``
wraps it with ``asyncio.run`` for non-async callers. Parameter names mirror
``schemas.TransRequest`` 1:1 (minus ``user_key``'s required-ness — see note
below) so a caller can do ``run_transbench(**request.model_dump(exclude={"user_key"}),
user_key=request.user_key)`` without any renaming.

Note on ``user_key`` (BYOK, BUILD_SPEC.md §0.4): ``TransRequest.user_key`` is a
*required* field there because a real request always needs BYOK credentials.
This entrypoint's own signature instead defaults ``user_key=None`` per
KICKOFF.md ("``run_transbench(observation, focus_drug=None, user_key=None,
...) -> TransBrief``"), and — starting Phase 2, where decompose/hypothesize
make real LLM calls — an explicit ``None`` falls back to
``config.ANTHROPIC_API_KEY`` (read from the ``ANTHROPIC_API_KEY`` process env,
BUILD_SPEC.md §0.4: "keyed by the MCP ANTHROPIC_API_KEY env"). This is BYOK's
designated key source for this standalone repo, not a forbidden second/
hardcoded "fallback key" (§0.4's "no fallback key" rule) — Phase 6's MCP
server does the exact same env read before calling this function; doing it
here too means callers that already have `ANTHROPIC_API_KEY` in their process
env (e.g. this repo's own tests) don't need to thread it through by hand. An
explicitly-passed ``user_key`` always takes precedence over the env. If
neither is set, ``create_llm`` raises its own clean, caught error (surfaced
as ``transbench.agents.TransBenchLLMError``) — never a raw exception.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from transbench import config
from transbench.graph import run_transbench_graph
from transbench.schemas import TransBrief

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TRANSBENCH_MODE (post-release addition — a small, additive demo/ops toggle;
# does not change the pipeline itself). Read from the process env INSIDE
# `run_transbench` below, never threaded as a parameter — so every existing
# caller (this repo's own tests, `mcp_server/server.py`'s `generate_experiment`
# / `search_grounded_evidence` tools, any future direct caller) gets the
# toggle automatically via whatever env the process was started with (e.g.
# the MCP connector's own registration `env` block, or a plain shell export)
# with ZERO call-site changes anywhere, including `mcp_server/**`.
#
#   TRANSBENCH_MODE=live      (default -- unset behaves identically) the
#       normal full 8-agent pipeline, completely unchanged.
#   TRANSBENCH_MODE=golden    returns a pre-captured, complete `TransBrief`
#       verbatim (see `_try_load_golden_brief`) INSTEAD of running the
#       pipeline at all -- a fully deterministic demo replay -- but ONLY when
#       the caller's `observation` matches the golden brief's own
#       `request_echo` (normalized: strip/lower/collapse-whitespace, see
#       `_normalize_for_match`). A mismatch (or a missing/invalid golden
#       file) logs a clear warning and falls back to running the live
#       pipeline instead -- this mode can never silently serve a brief for
#       the wrong question.
#   TRANSBENCH_MODE=snapshot  runs the REAL live pipeline (real decompose/
#       hypothesize/grade/entailment/novelty/design/assemble LLM calls,
#       exactly as in `live` mode) but replaces PubMed retrieval with a
#       bundled, previously-captured `retrieval_snapshot` (see
#       `_load_bundled_retrieval_snapshot`) -- fixed evidence, live LLM
#       reasoning on top of it. This reuses the EXACT SAME
#       `retrieval_snapshot` replay mechanism `TransRequest.retrieval_snapshot`
#       already exercises (`agents.run_retrieve` / `agents._replay_from_snapshot`,
#       BUILD_SPEC.md §9), which now additionally verifies each snapshot
#       entry's stored hypothesis `statement` still matches the CURRENT
#       hypothesis before ever replaying it -- a hypothesis whose statement
#       isn't present in (or doesn't match) the snapshot transparently falls
#       back to live retrieval for that one hypothesis only (logged), never
#       silently serving evidence captured for a different hypothesis. An
#       explicitly-passed `retrieval_snapshot=...` kwarg on this function
#       always takes precedence over the bundled file (mirrors `user_key`'s
#       own explicit-arg-beats-env precedence documented below) -- the
#       bundled file only ever fills in for a caller who passed nothing.
#
# Path env vars (both optional; a relative path resolves against this repo's
# own root, `config.REPO_ROOT` -- the same resolution `config.py` itself uses
# for `.env`):
#   TRANSBENCH_GOLDEN_BRIEF        (optional EXPLICIT override of the single golden file; when UNSET,
#                                   golden mode auto-selects among every snapshots/*_golden_brief.json by
#                                   matching request_echo, so per-domain goldens drop in with zero config
#                                   -- see _golden_brief_candidates below)
#   TRANSBENCH_RETRIEVAL_SNAPSHOT  (default "snapshots/flagship_retrieval_snapshot.json")
#
# Works through the MCP tools automatically: `mcp_server/server.py` calls
# `run_transbench(observation, focus_drug, user_key=...)` with no other
# kwargs and reads no mode itself -- setting `TRANSBENCH_MODE` (and, if
# needed, the two path env vars above) in the MCP connector's own
# registration `env` block (see `mcp_server/README.md`'s register-block JSON)
# is sufficient; no `mcp_server/**` code change is required or was made.
# ---------------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_for_match(text: str) -> str:
    """strip/lower/collapse-whitespace normalization for the ``golden``-mode
    ``request_echo`` safety guard below. (``agents.py`` has its own,
    independent copy of this same normalization —
    ``agents._normalize_statement`` — for the ``snapshot``-mode hypothesis
    -``statement`` guard; kept as two small, self-contained copies rather
    than a shared cross-module utility so neither module gains a dependency
    on the other purely for a two-line string helper.) Never used for
    anything citation/grading-related — purely a text-equality helper for
    deciding whether a cached artifact still applies to the CURRENT input.
    """
    return _WHITESPACE_RE.sub(" ", (text or "").strip().lower())


def _resolve_repo_path(env_var: str, default_relative: str) -> Path:
    """Resolves ``os.environ[env_var]`` (if set) or ``default_relative``
    against ``config.REPO_ROOT``. An already-absolute override (env or
    default) is returned unchanged."""
    raw = os.environ.get(env_var) or default_relative
    path = Path(raw)
    return path if path.is_absolute() else (config.REPO_ROOT / path)


def _golden_brief_path() -> Path:
    return _resolve_repo_path("TRANSBENCH_GOLDEN_BRIEF", "snapshots/flagship_golden_brief.json")


def _golden_brief_candidates() -> list[Path]:
    """The golden-brief file(s) ``golden`` mode will try, in priority order.

    - If ``TRANSBENCH_GOLDEN_BRIEF`` is explicitly set, that single file is the
      ONLY candidate -- an explicit override stays authoritative and is never
      silently widened to a directory scan (unchanged legacy behavior; every
      existing single-file test keeps its exact semantics).
    - Otherwise (the default), every ``snapshots/*_golden_brief.json`` is a
      candidate, so any number of per-domain goldens can be dropped in and
      auto-selected purely by their own ``request_echo`` with ZERO config
      (scales to new domains without touching code). The flagship file is
      tried first for a stable, deterministic order; the rest follow sorted by
      filename. Widening the candidate SET never weakens the per-candidate
      ``request_echo`` safety guard in :func:`_try_load_golden_brief` -- each
      candidate must still match the caller's observation before it is served.
    """
    if os.environ.get("TRANSBENCH_GOLDEN_BRIEF"):
        return [_golden_brief_path()]
    snapshots_dir = config.REPO_ROOT / "snapshots"
    flagship = snapshots_dir / "flagship_golden_brief.json"
    candidates: list[Path] = [flagship] if flagship.exists() else []
    if snapshots_dir.is_dir():
        for path in sorted(snapshots_dir.glob("*_golden_brief.json")):
            if path != flagship:
                candidates.append(path)
    return candidates


def _retrieval_snapshot_path() -> Path:
    return _resolve_repo_path("TRANSBENCH_RETRIEVAL_SNAPSHOT", "snapshots/flagship_retrieval_snapshot.json")


def _try_load_golden_brief(observation: str) -> Optional[TransBrief]:
    """``TRANSBENCH_MODE=golden``'s safety-guarded loader. Walks the candidate
    golden files (:func:`_golden_brief_candidates` -- one explicit file, or
    every ``snapshots/*_golden_brief.json`` when unset) in priority order and
    returns the FIRST one that exists, parses as JSON, validates against
    ``TransBrief``, AND whose own ``request_echo`` matches ``observation``
    (normalized via :func:`_normalize_for_match`). Returns ``None`` if NO
    candidate matches — no files, all unparseable/invalid, or a genuinely
    different observation than every candidate — NEVER raises, and always logs
    a clear, specific reason first, so the caller's documented behavior ("fall
    back to the live pipeline") is always safe to take unconditionally on a
    ``None`` return. A single bad candidate (missing/invalid) is skipped, not
    fatal — a later candidate can still match. This is the one function
    responsible for guaranteeing ``golden`` mode can never serve a brief for
    the wrong question, no matter how many domain goldens are bundled.
    """
    candidates = _golden_brief_candidates()
    if not candidates:
        logger.warning(
            "TRANSBENCH_MODE=golden: no golden brief file(s) found -- falling back to the live pipeline.",
        )
        return None
    target = _normalize_for_match(observation)
    tried: list[str] = []
    for path in candidates:
        if not path.exists():
            tried.append(f"{path.name} (missing)")
            continue
        try:
            brief = TransBrief.model_validate(json.loads(path.read_text()))
        except Exception:
            logger.exception(
                "TRANSBENCH_MODE=golden: failed to load/validate the golden brief at %s -- skipping this candidate.",
                path,
            )
            tried.append(f"{path.name} (invalid)")
            continue
        if _normalize_for_match(brief.request_echo) == target:
            logger.info(
                "TRANSBENCH_MODE=golden: observation matches -- serving the golden brief verbatim from %s.",
                path,
            )
            return brief
        tried.append(f"{path.name} (request_echo mismatch)")
    logger.warning(
        "TRANSBENCH_MODE=golden: observation matched none of %d candidate golden brief(s) [%s] -- "
        "falling back to the live pipeline instead (this mode can never serve a mismatched brief).",
        len(candidates),
        ", ".join(tried),
    )
    return None


def _load_bundled_retrieval_snapshot() -> Optional[dict]:
    """``TRANSBENCH_MODE=snapshot``'s loader for the bundled retrieval
    snapshot file. Returns the parsed dict iff the file exists and parses to
    a JSON object; returns ``None`` (never raises, always logs a clear
    reason first) otherwise. A ``None`` return means "no bundled snapshot
    available", which :func:`run_transbench` treats exactly like the caller
    having passed no ``retrieval_snapshot`` at all — i.e. every hypothesis
    simply retrieves live, same as ``live`` mode (a missing/corrupt bundled
    file degrades this mode to ``live``, it never crashes the run).
    """
    path = _retrieval_snapshot_path()
    if not path.exists():
        logger.warning(
            "TRANSBENCH_MODE=snapshot: no retrieval snapshot file at %s -- "
            "every hypothesis will retrieve live.",
            path,
        )
        return None
    try:
        data = json.loads(path.read_text())
    except Exception:
        logger.exception(
            "TRANSBENCH_MODE=snapshot: failed to parse the retrieval snapshot at %s -- "
            "every hypothesis will retrieve live.",
            path,
        )
        return None
    if not isinstance(data, dict):
        logger.warning(
            "TRANSBENCH_MODE=snapshot: retrieval snapshot at %s is not a JSON object -- "
            "every hypothesis will retrieve live.",
            path,
        )
        return None
    logger.info(
        "TRANSBENCH_MODE=snapshot: loaded the bundled retrieval snapshot from %s (%d hypothesis entries).",
        path,
        len(data),
    )
    return data


_VALID_MODES = frozenset({"live", "golden", "snapshot"})


async def run_transbench(
    observation: str,
    focus_drug: Optional[str] = None,
    user_key: Optional[str] = None,
    *,
    user_provider: str = "anthropic",
    model_reasoning: str = config.MODEL_REASONING,
    model_cheap: str = config.MODEL_CHEAP,
    max_hypotheses: int = config.MAX_HYPOTHESES,
    retrieval_snapshot: Optional[dict] = None,
) -> TransBrief:
    """Run the full TransBench pipeline and return a validated ``TransBrief``.

    The complete, shipped 8-agent pipeline (BUILD_SPEC.md §5; every phase
    Opus-verified) runs behind this one entrypoint: decompose (agent 1,
    Haiku) -> hypothesize (agent 2, Sonnet; up to ``max_hypotheses``
    falsifiable mechanistic hypotheses) -> per-hypothesis, concurrency
    -capped retrieve (agent 3, no LLM — PubMed support + contradiction
    passes) + grade (agent 4, Haiku, one batched call per hypothesis) ->
    dedicated batched entailment + grounding enforcement + novelty check
    (agents 5-6, Haiku + Sonnet) -> experiment design (agent 7, Sonnet — only
    for a hypothesis that clears the novelty guard: ``open_question`` AND
    ``grounded``) -> brief assembly (agent 8, Haiku). Returns a schema-valid
    ``TransBrief``: ``axes``, up to ``max_hypotheses`` graded hypotheses each
    carrying real, resolvable PubMed/ClinicalTrials.gov citations and an
    auditable novelty verdict, ``top_experiment`` (a runnable computational
    experiment naming a concrete, content-verified, resolvable dataset, or an
    honest "no eligible experiment" sentinel), deduplicated ``references``,
    any surfaced contradictions, an ``uncertainty_note``, and a
    ``run_manifest`` (models/temperature/caps/concurrency/the retrieval
    snapshot/token spend/timestamps). See ``graph.py``'s ``build_graph`` for
    the exact node topology (``START -> decompose -> hypothesize ->
    retrieve_and_grade -> rigor_and_novelty -> design -> assemble -> END``).

    ``TRANSBENCH_MODE`` env toggle (post-release addition — see the module
    -level comment block above :func:`_try_load_golden_brief` for the full
    write-up): ``live`` (default/unset) runs the pipeline above completely
    unchanged; ``golden`` serves a pre-captured ``TransBrief`` verbatim,
    guarded so it can never be served for a different ``observation``;
    ``snapshot`` runs the real pipeline above but replays PubMed retrieval
    from a bundled, per-hypothesis-statement-guarded snapshot instead of
    hitting PubMed live.
    """
    effective_user_key = user_key if user_key is not None else config.ANTHROPIC_API_KEY

    mode = (os.environ.get("TRANSBENCH_MODE") or "live").strip().lower()
    if mode not in _VALID_MODES:
        logger.warning("TRANSBENCH_MODE=%r is not one of %s -- treating this run as 'live'.", mode, sorted(_VALID_MODES))
        mode = "live"

    if mode == "golden":
        golden_brief = _try_load_golden_brief(observation)
        if golden_brief is not None:
            return golden_brief
        # Missing file / invalid JSON / schema mismatch / observation
        # mismatch -- already logged inside _try_load_golden_brief. Falls
        # through to the normal live call below, exactly as `live` mode
        # would (a caller-supplied retrieval_snapshot, if any, is still
        # honored -- this fallback does NOT also switch to `snapshot` mode).

    effective_retrieval_snapshot = retrieval_snapshot
    if mode == "snapshot" and effective_retrieval_snapshot is None:
        effective_retrieval_snapshot = _load_bundled_retrieval_snapshot()

    return await run_transbench_graph(
        observation,
        focus_drug,
        effective_user_key,
        user_provider=user_provider,
        model_reasoning=model_reasoning,
        model_cheap=model_cheap,
        max_hypotheses=max_hypotheses,
        retrieval_snapshot=effective_retrieval_snapshot,
    )


def run_transbench_sync(
    observation: str,
    focus_drug: Optional[str] = None,
    user_key: Optional[str] = None,
    **kwargs: object,
) -> TransBrief:
    """Synchronous wrapper around :func:`run_transbench`, for non-async
    callers (e.g. a synchronous MCP tool handler in Phase 6). Do not call this
    from inside a running event loop — ``asyncio.run`` raises in that case;
    use ``await run_transbench(...)`` directly in async contexts instead
    (BUILD_SPEC.md §5: "Never call llm.invoke() in the async path" applies to
    the whole engine — prefer the async entrypoint wherever the caller is
    already async, e.g. FastMCP's async tool handlers).
    """
    return asyncio.run(run_transbench(observation, focus_drug, user_key, **kwargs))  # type: ignore[arg-type]
