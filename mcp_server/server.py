"""mcp_server/server.py — FastMCP connector for TransBench (BUILD_SPEC.md §7,
KICKOFF.md Phase 6).

Exposes the engine's single async entrypoint (``transbench.engine.
run_transbench``) as two MCP tools over stdio (Claude Science's transport,
CLAUDE_SCIENCE_SETUP.md Step 4) and, via ``run_http.sh``, over
``streamable-http`` (the demo-day fallback in CLAUDE_SCIENCE_SETUP.md's
"Fallback demo" section):

- ``generate_experiment`` — the showpiece: a full grounded ``TransBrief``
  (BUILD_SPEC.md §4), including ``top_experiment.claude_science_prompt``.
- ``search_grounded_evidence`` — a utility/fallback: the SAME engine run,
  reshaped into a lighter, evidence-focused projection (no ``axes``/
  ``top_experiment``/``run_manifest`` noise) for a quick grounded-literature
  lookup.
- ``get_experiment_result`` — poll tool. Both runs above are ASYNC: a full
  pipeline is ~60-120s (longer cold), longer than an MCP client will wait on a
  single call, so ``generate_experiment``/``search_grounded_evidence`` START a
  background job and return a ``job_id`` in <1s; the caller polls
  ``get_experiment_result(job_id)`` (also <1s) until ``status`` is ``"done"``/
  ``"error"``. No single call ever nears the client's wait-for-result ceiling
  — the reason the earlier progress-heartbeat keepalive could not help: that
  ceiling is hard and is not reset by progress notifications. See
  :func:`_submit_job`.

Both tools call ``transbench.engine.run_transbench`` — the ONLY engine entry
point either one ever touches (KICKOFF.md Phase 6: "Both call the engine —
no duplicated logic"). Neither tool re-implements retrieval, grounding, or
grading; ``search_grounded_evidence``'s "lighter projection" is a pure,
local reshape of the SAME ``TransBrief`` the full pipeline already produced
(see :func:`_grounded_evidence_projection`).

BYOK (BUILD_SPEC.md §0.4): the Anthropic key is read from the
``ANTHROPIC_API_KEY`` process env — freshly, on every call (see
:func:`_anthropic_api_key`) — and feeds the ENGINE's own Anthropic calls via
``create_llm(..., user_key=...)``. This is independent of Claude Science,
which is only this tool's MCP *client*; Claude Science never sees or needs
this key.

Non-blocking (BUILD_SPEC.md §5/§7): both tools are ``async def`` and only
ever ``await`` the async engine (which itself only ever ``await``s
``llm.ainvoke(...)``, never the blocking ``llm.invoke()`` — enforced inside
``agents.py``) — so a single TransBench run never blocks this process's
event loop, and FastMCP's stdio/HTTP transports keep serving other
requests/protocol messages concurrently.

Error handling (BUILD_SPEC.md §7 / KICKOFF.md Phase 6): every failure mode
— a bare ``fastapi.HTTPException`` (defensive: ``create_llm`` raises this on
a missing/invalid key or bad model, and today it is already caught+converted
inside ``agents.build_llm`` before it would ever reach here, but this
boundary catches it too in case a future engine code path ever calls
``create_llm`` directly), the engine's own clean
``transbench.agents.TransBenchLLMError``, or literally any other unexpected
exception (PubMed/GEO network errors, a schema-validation edge case, ...) —
is caught and turned into ONE clean, structured error dict
(:func:`_clean_error`). A raw traceback is NEVER returned to the MCP client.

stdio hygiene: this module NEVER calls ``print()`` — stdout is the JSON-RPC
transport channel for the ``stdio`` transport, so any stray text on stdout
would corrupt the protocol stream. All diagnostics go through the stdlib
``logging`` module, explicitly configured to stream to stderr below.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Optional

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP

# Importing `transbench.engine` (which imports `transbench`, which imports
# `transbench.config` first — see config.py's own module docstring) runs
# `load_dotenv(<repo>/.env, override=True)` as an import-time side effect,
# BEFORE this module ever reads `ANTHROPIC_API_KEY`/`PUBMED_API_KEY` off
# `os.environ` itself (see `_anthropic_api_key` below). This ordering is
# what makes a *present* `.env` win over a stale ambient value in this
# process's environment — the same guarantee every other TransBench entry
# point (tests, the engine itself) already relies on.
from transbench import config  # noqa: F401 -- import-time side effect only (see above)
from transbench.agents import TransBenchLLMError
from transbench.engine import run_transbench
from transbench.schemas import TransBrief

# Never write .pyc/__pycache__ for this process's own imports — belt #2
# alongside the `PYTHONDONTWRITEBYTECODE=1` env var every run script /
# register block sets (KICKOFF.md Phase 6: "Set PYTHONDONTWRITEBYTECODE=1 in
# the run scripts + register block"). Setting the env var is what actually
# governs the *interpreter startup* behavior (it must be set before the
# `python` process launches to have any effect on this module's own
# compilation), but this line is a harmless, redundant confirmation for any
# code imported *after* this point in the same process (e.g. a lazy import
# inside a tool call) and costs nothing.
sys.dont_write_bytecode = True

# Explicit stderr stream — NEVER the (unconfigured) default, and never
# stdout, which is the stdio transport's JSON-RPC channel (see module
# docstring "stdio hygiene"). `force=True` so a second import (e.g. under a
# test runner that already called `basicConfig`) still lands on stderr
# rather than silently no-op'ing against someone else's earlier config.
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    force=True,
)
logger = logging.getLogger("transbench.mcp_server")

# ---------------------------------------------------------------------------
# Transport / bind config (env-overridable; defaults match CLAUDE_SCIENCE_SETUP.md
# and this package's run_http.sh -- localhost:8500). Host/port are inert for
# the "stdio" transport (FastMCP only reads them when serving sse/streamable-http)
# so it is safe to always construct the app with them set.
# ---------------------------------------------------------------------------
MCP_HTTP_HOST: str = os.environ.get("MCP_HTTP_HOST", "127.0.0.1")
MCP_HTTP_PORT: int = int(os.environ.get("MCP_HTTP_PORT", "8500"))

mcp_app = FastMCP("transbench", host=MCP_HTTP_HOST, port=MCP_HTTP_PORT)

# ---------------------------------------------------------------------------
# Async submit-and-poll. A full run is ~13 LLM calls (Opus on hypothesize +
# experiment-design), plus live PubMed and a GEO content fetch, so it
# legitimately takes ~60-120s -- and MORE on a cold first call. That is longer
# than the wait-for-result timeout an MCP client (e.g. Claude Science's
# host.mcp()) imposes on a SINGLE tool call (~60s, a hard ceiling that a
# progress heartbeat does NOT reset -- an earlier keepalive attempt here proved
# useless for exactly that reason). So neither tool blocks on the engine:
# `generate_experiment` / `search_grounded_evidence` START the run as a
# background job and return a job_id in <1s; the caller polls
# `get_experiment_result(job_id)` (also <1s) until it is done. No single call
# ever approaches the client ceiling, however long or cold the run is. See
# _submit_job / get_experiment_result.
#
# Scale guards: at most MCP_MAX_CONCURRENT_JOBS engine runs execute at once (a
# semaphore; excess jobs wait, still pollable), and finished jobs are evicted
# MCP_JOB_TTL_SECONDS after completion so the registry stays bounded. The
# in-memory registry is correct for this single-process server; a multi-worker
# deployment would move it to shared storage (e.g. Redis).
# ---------------------------------------------------------------------------
_MAX_CONCURRENT_JOBS: int = int(os.environ.get("MCP_MAX_CONCURRENT_JOBS", "4"))
_JOB_TTL_SECONDS: float = float(os.environ.get("MCP_JOB_TTL_SECONDS", "1800"))

# ---------------------------------------------------------------------------
# MCP-boundary input guard. schemas.TransRequest.observation declares
# Field(min_length=3, max_length=8000) (BUILD_SPEC.md §4, frozen verbatim) --
# but TransRequest itself is never instantiated anywhere in this codebase
# today (run_transbench takes a bare `observation: str`, no length gate).
# These two constants mirror that Field's own bounds (not a new, invented
# rule) so obviously-empty/oversized MCP input is rejected here, for free,
# before ever spending a ~13-LLM-call engine run on it.
# ---------------------------------------------------------------------------
_MIN_TEXT_LEN = 3
_MAX_TEXT_LEN = 8000


def _clean_error(status_code: int, error: str, message: str) -> dict[str, Any]:
    """The ONE structured error shape both tools ever return on failure
    (BUILD_SPEC.md §7: "return a clean structured error instead of leaking
    the exception"). Always JSON-serializable, always these exact 3 keys."""
    return {"error": error, "message": message, "status_code": status_code}


def _validate_text(value: str, field_name: str) -> Optional[dict[str, Any]]:
    """Returns a :func:`_clean_error` dict if ``value`` is empty/whitespace
    -only or exceeds the mirrored ``TransRequest.observation`` bounds, else
    ``None`` (input is acceptable, caller proceeds to the engine)."""
    text = (value or "").strip()
    if len(text) < _MIN_TEXT_LEN:
        return _clean_error(
            422,
            "invalid_input",
            f"{field_name!r} must be at least {_MIN_TEXT_LEN} non-whitespace characters.",
        )
    if len(value) > _MAX_TEXT_LEN:
        return _clean_error(
            422,
            "invalid_input",
            f"{field_name!r} exceeds the {_MAX_TEXT_LEN}-character limit.",
        )
    return None


def _anthropic_api_key() -> Optional[str]:
    """Fresh ``ANTHROPIC_API_KEY`` read (BYOK, BUILD_SPEC.md §0.4) on EVERY
    call, not a module-level-frozen snapshot — deliberately a plain
    ``os.environ.get`` rather than a cached constant, so a key rotated in
    the MCP server's own environment between calls (e.g. an operator
    updating the connector's env and reloading, or a test that monkeypatches
    ``os.environ`` for one call) is always honored immediately. Correct
    because ``transbench.config`` (imported above, at module load) already
    ran ``load_dotenv(<repo>/.env, override=True)`` as a side effect, which
    mutates ``os.environ`` itself in place -- so this read, however much
    later it happens, still reflects the de-staled, .env-authoritative value.
    Feeds the ENGINE's own Anthropic calls; Claude Science (the MCP client)
    never sees this key.

    TEST GOTCHA (do not repeat this mistake -- confirmed the hard way while
    building this file): ``engine.run_transbench``'s own signature treats an
    explicitly-passed ``user_key=None`` as "fall back to
    ``config.ANTHROPIC_API_KEY``" (its module docstring: "an explicit None
    falls back to config.ANTHROPIC_API_KEY"), where ``config.ANTHROPIC_API_KEY``
    is a module-level constant captured ONCE at ``transbench.config`` import
    time. So merely popping ``ANTHROPIC_API_KEY`` out of ``os.environ`` mid
    -process (after this module has already imported ``transbench.config``
    with a real key present) does NOT reproduce a genuine "no key" condition
    end to end -- this function's own fresh read correctly returns ``None``,
    but ``run_transbench`` still silently falls back to the earlier-cached
    real key and makes REAL Anthropic/PubMed calls. This is not a bug here
    (it matches ``engine.run_transbench``'s documented, intentional
    contract, and a real deployment that genuinely never sets
    ``ANTHROPIC_API_KEY`` at process start behaves correctly end to end --
    ``config.ANTHROPIC_API_KEY`` would then ALSO correctly be ``None`` from
    the start) -- it only bites an attempt to simulate key-removal inside an
    already-running, already-keyed process. To test the error path safely
    and deterministically, mock ``mcp_server.server.run_transbench`` itself
    (e.g. ``unittest.mock.patch.object(server, "run_transbench",
    side_effect=TransBenchLLMError(402, "no_api_key", ...))``) rather than
    mutating live env/config state -- zero network risk, and it directly
    exercises :func:`_call_engine_safely`'s own catch/clean-error logic."""
    return os.environ.get("ANTHROPIC_API_KEY")


async def _run_engine(observation: str, focus_drug: Optional[str]) -> TransBrief:
    """The SINGLE call site either tool ever uses to invoke the engine
    (KICKOFF.md Phase 6: "Both call the engine — no duplicated logic").
    Always ``await``s the async entrypoint directly (never the blocking
    ``run_transbench_sync``, which internally calls ``asyncio.run`` and
    would raise inside FastMCP's already-running event loop) — keeps this
    process's event loop non-blocking end to end."""
    return await run_transbench(observation, focus_drug, user_key=_anthropic_api_key())


async def _call_engine_safely(observation: str, focus_drug: Optional[str]) -> tuple[Optional[TransBrief], Optional[dict[str, Any]]]:
    """Runs :func:`_run_engine` and converts EVERY failure mode into a clean
    :func:`_clean_error` dict — never lets a raw exception (of any kind)
    escape into a tool function's return value. Returns ``(brief, None)`` on
    success or ``(None, error_dict)`` on failure; callers just check which
    slot is populated.

    Catch order (BUILD_SPEC.md §7 / KICKOFF.md Phase 6, "catch
    fastapi.HTTPException ... and any engine exception"):
      1. ``TransBenchLLMError`` — the REAL, already-clean exception type that
         crosses out of the engine today (``agents.build_llm`` catches
         ``fastapi.HTTPException`` from ``create_llm`` and converts it to
         this before it ever leaves ``agents.py``/``graph.py``/``engine.py``).
      2. ``fastapi.HTTPException`` — defensive: catches a bare HTTPException
         too, in case any future engine code path ever calls ``create_llm``
         (or raises HTTPException some other way) without going through
         ``agents.build_llm``'s existing conversion.
      3. Any other ``Exception`` — last-resort catch-all (a PubMed/NCBI
         network error, an unexpected schema issue, ...). Logged with a full
         traceback (server-side, stderr) for operator debugging, but the
         MCP client only ever sees the clean dict — never the traceback
         itself.
    """
    try:
        brief = await _run_engine(observation, focus_drug)
        return brief, None
    except TransBenchLLMError as exc:
        logger.warning("engine LLM error (status=%s error=%s): %s", exc.status_code, exc.error, exc.message)
        return None, _clean_error(exc.status_code, exc.error, exc.message)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        error = str(detail.get("error", "llm_error"))
        message = str(detail.get("message", exc.detail))
        logger.warning("uncaught fastapi.HTTPException from engine (status=%s): %s", exc.status_code, message)
        return None, _clean_error(exc.status_code, error, message)
    except Exception as exc:  # noqa: BLE001 -- deliberate last-resort boundary; see docstring.
        logger.exception("unexpected engine failure for observation=%r focus_drug=%r", observation, focus_drug)
        return None, _clean_error(500, "internal_error", f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Job registry (async submit-and-poll; see the "Async submit-and-poll" note
# above). One long-lived event loop backs the server, so a module-level dict
# plus a loop-bound semaphore is the right, race-free structure here: asyncio
# is single-threaded, so every mutation below happens on that one loop.
# ---------------------------------------------------------------------------
@dataclass
class _Job:
    """One background engine run. ``result`` holds the FINAL dict either tool
    would have returned synchronously — a brief/projection on success, or a
    :func:`_clean_error` dict on failure — surfaced verbatim by the poll tool."""

    status: str  # "queued" -> "running" -> "done" | "error" (poll maps queued->running)
    label: str  # which tool started it (for logs / the submit message)
    created_at: float
    finished_at: Optional[float] = None
    result: Optional[dict[str, Any]] = None
    task: Optional["asyncio.Task[None]"] = None


_JOBS: dict[str, _Job] = {}

_JOB_SEMAPHORE: Optional[asyncio.Semaphore] = None
_JOB_SEMAPHORE_LOOP: Optional[asyncio.AbstractEventLoop] = None


def _job_semaphore() -> asyncio.Semaphore:
    """The concurrency cap, lazily bound to the *running* loop. Rebinds if the
    loop changed: the server has one long-lived loop (bound once, reused), but
    the offline tests drive each case under a fresh ``asyncio.run`` — a
    semaphore captured from a now-closed loop must never leak across."""
    global _JOB_SEMAPHORE, _JOB_SEMAPHORE_LOOP
    loop = asyncio.get_running_loop()
    if _JOB_SEMAPHORE is None or _JOB_SEMAPHORE_LOOP is not loop:
        _JOB_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT_JOBS)
        _JOB_SEMAPHORE_LOOP = loop
    return _JOB_SEMAPHORE


def _evict_expired_jobs() -> None:
    """Drop FINISHED jobs whose result has been retrievable for longer than
    ``_JOB_TTL_SECONDS`` — keeps the registry bounded. Never evicts a job that
    is still queued/running (a slow cold run must stay pollable); in-flight
    runs are capped instead by the concurrency semaphore."""
    if not _JOBS:
        return
    now = time.monotonic()
    for jid in [
        j for j, job in _JOBS.items()
        if job.finished_at is not None and now - job.finished_at > _JOB_TTL_SECONDS
    ]:
        _JOBS.pop(jid, None)


def _submit_job(coro: Awaitable[dict[str, Any]], label: str) -> dict[str, Any]:
    """Start ``coro`` (a ``_*_result`` coroutine that computes the full tool
    payload) as a registry-owned background task and return a job handle in
    <1s. The task is owned by the REGISTRY, not by this submit request — so if
    the client drops the (fast) submit call, the run still completes and its
    result waits in the registry for the poll, instead of being orphaned."""
    _evict_expired_jobs()
    job_id = uuid.uuid4().hex
    job = _Job(status="queued", label=label, created_at=time.monotonic())
    _JOBS[job_id] = job

    async def _runner() -> None:
        try:
            async with _job_semaphore():
                job.status = "running"
                job.result = await coro
        except Exception as exc:  # noqa: BLE001 -- background boundary; never lose the failure
            logger.exception("job %s (%s) crashed", job_id, label)
            job.result = _clean_error(500, "internal_error", f"{type(exc).__name__}: {exc}")
        # A _clean_error dict (from the result coro OR the except above) is the
        # only thing that carries a top-level "error"; a brief/projection never
        # does. finished_at gates TTL eviction.
        job.status = "error" if (job.result or {}).get("error") is not None else "done"
        job.finished_at = time.monotonic()

    job.task = asyncio.ensure_future(_runner())
    return {
        "job_id": job_id,
        "status": "running",
        "poll_tool": "get_experiment_result",
        "message": (
            f"TransBench {label} started (full grounded pipeline, ~60-120s, longer on a "
            f"cold first call). Call get_experiment_result with job_id='{job_id}' every "
            f"~5s until status is 'done' or 'error'."
        ),
    }


async def _generate_experiment_result(observation: str, focus_drug: str = "") -> dict[str, Any]:
    """The full, synchronous payload ``generate_experiment`` ultimately
    delivers: validate -> run the engine -> a ``TransBrief`` dict, or a
    :func:`_clean_error` dict on any failure. Run as a background job by
    :func:`_submit_job` and surfaced by :func:`get_experiment_result`; also the
    direct call site the offline parity tests exercise (no job layer needed to
    prove the MCP boundary is a faithful passthrough)."""
    bad = _validate_text(observation, "observation")
    if bad is not None:
        return bad
    brief, error = await _call_engine_safely(observation, focus_drug or None)
    if error is not None:
        return error
    assert brief is not None  # _call_engine_safely's own contract: exactly one of (brief, error) is set
    return brief.model_dump()


@mcp_app.tool()
async def generate_experiment(observation: str, focus_drug: str = "") -> dict[str, Any]:
    """Generate a grounded translational research brief from ANY clinical or
    biomedical observation — a disease's drug response/resistance, a drug's
    adverse effect/toxicity, or any mechanism (not limited to any one domain).

    ASYNC (submit + poll): the full pipeline (decompose -> hypothesize ->
    retrieve -> grade -> entail -> novelty-check -> design -> assemble) runs
    ~60-120s, longer on a cold first call — longer than an MCP client will wait
    on one call. So this tool does NOT block: it STARTS the run and returns
    immediately with a job handle. You MUST then poll
    ``get_experiment_result(job_id)`` every few seconds until ``status`` is
    ``"done"`` (the full ``TransBrief`` is in ``result``) or ``"error"``. The
    finished brief has decomposed biological axes, up to 3 falsifiable
    hypotheses each graded against real PubMed evidence with an auditable
    novelty verdict, and ONE runnable computational experiment
    (``top_experiment``) naming a concrete public dataset plus a
    ``claude_science_prompt`` ready to run in Claude Science.

    Args:
        observation: A free-text clinical/biomedical observation (3-8000
            characters) — any disease, drug response/resistance, adverse
            effect, or mechanism. Examples: "58F, resistant hypertension
            despite ACEi + CCB + thiazide; elevated hs-CRP" or "30M on
            amiodarone for AF, developed neutropenia".
        focus_drug: Optional drug name to focus the analysis on. Omit
            ("") to let the pipeline infer relevant drugs from the
            observation itself.

    Returns:
        Immediately: ``{"job_id", "status": "running", "poll_tool":
        "get_experiment_result", "message"}``. Poll
        ``get_experiment_result(job_id)`` for the outcome — on ``"done"`` its
        ``result`` is the full ``TransBrief`` (carrying the fixed research-only
        disclaimer, BUILD_SPEC.md §0.5; never diagnosis, drug selection, or
        dosing), on ``"error"`` its ``result`` is ``{"error", "message",
        "status_code"}`` (e.g. no/invalid ANTHROPIC_API_KEY). Obviously invalid
        input is rejected inline here (no job) as that same error shape.
    """
    bad = _validate_text(observation, "observation")
    if bad is not None:
        return bad
    return _submit_job(_generate_experiment_result(observation, focus_drug), "generate_experiment")


def _grounded_evidence_projection(brief: TransBrief) -> dict[str, Any]:
    """Reshapes an already-computed ``TransBrief`` into ``search_grounded_
    evidence``'s lighter, evidence-focused projection. Pure, local, zero
    I/O, zero engine logic of its own — every field below is copied straight
    off ``brief`` (KICKOFF.md Phase 6: "reuse the engine, don't reimplement
    retrieval"). Drops ``axes``/``top_experiment``/``run_manifest`` (the
    experiment-design/run-bookkeeping fields that ``generate_experiment``'s
    full brief carries but a quick grounded-literature lookup doesn't need).

    Kept per hypothesis: id/axis/statement, the novelty verdict + its
    PMID-citing reason, confidence, the ``grounded`` flag, both counts, and
    the FULL per-item evidence list (not just ``supports`` — a caller doing
    literature lookup legitimately wants to see contradicting/unclear items
    too, each already carrying its own ``entailment`` field so the caller
    can tell them apart).
    """
    return {
        "request_echo": brief.request_echo,
        "hypotheses": [
            {
                "hypothesis_id": gh.hypothesis.id,
                "axis": gh.hypothesis.axis,
                "statement": gh.hypothesis.statement,
                "novelty": gh.novelty,
                "novelty_reason": gh.novelty_reason,
                "confidence": gh.confidence,
                "grounded": gh.grounded,
                "supporting_count": gh.supporting_count,
                "contradicting_count": gh.contradicting_count,
                "evidence": [ev.model_dump() for ev in gh.evidence],
            }
            for gh in brief.hypotheses
        ],
        "references": [ref.model_dump() for ref in brief.references],
        "contradictions_surfaced": brief.contradictions_surfaced,
        "uncertainty_note": brief.uncertainty_note,
        "disclaimer": brief.disclaimer,
    }


async def _search_grounded_evidence_result(question: str) -> dict[str, Any]:
    """The full, synchronous payload ``search_grounded_evidence`` delivers:
    validate -> run the SAME engine -> the lighter evidence projection, or a
    :func:`_clean_error` dict on failure. Background-run by :func:`_submit_job`,
    surfaced by :func:`get_experiment_result`; also the direct call site the
    parity test exercises."""
    bad = _validate_text(question, "question")
    if bad is not None:
        return bad
    brief, error = await _call_engine_safely(question, None)
    if error is not None:
        return error
    assert brief is not None
    return _grounded_evidence_projection(brief)


@mcp_app.tool()
async def search_grounded_evidence(question: str) -> dict[str, Any]:
    """Look up PubMed-grounded mechanistic evidence for ANY clinical,
    pharmacological, or mechanistic question (utility / fallback tool — a
    lighter-weight sibling of ``generate_experiment``, not limited to any
    one domain).

    ASYNC (submit + poll): runs the SAME full TransBench pipeline as
    ``generate_experiment`` (not a separate, cheaper retrieval path), so it is
    just as slow (~60-120s) and is likewise non-blocking — it STARTS the run
    and returns a job handle immediately. You MUST then poll
    ``get_experiment_result(job_id)`` until ``status`` is ``"done"``/
    ``"error"``. On ``"done"``, ``result`` is the smaller, evidence-focused
    projection: for each hypothesis its novelty verdict and every retrieved
    evidence item (each with a resolvable citation, an entailment verdict, and
    an evidence grade), plus the deduplicated reference list, any
    contradictions surfaced, and the uncertainty note. Omits ``axes``/
    ``top_experiment``/``run_manifest``.

    Args:
        question: A free-text clinical/pharmacological/mechanistic question
            (3-8000 characters), any domain.

    Returns:
        Immediately: ``{"job_id", "status": "running", "poll_tool":
        "get_experiment_result", "message"}``. Poll
        ``get_experiment_result(job_id)`` — on ``"done"`` its ``result`` is the
        grounded-evidence projection (carrying the fixed research-only
        disclaimer), on ``"error"`` its ``result`` is ``{"error", "message",
        "status_code"}``. Obviously invalid input is rejected inline (no job).
    """
    bad = _validate_text(question, "question")
    if bad is not None:
        return bad
    return _submit_job(_search_grounded_evidence_result(question), "search_grounded_evidence")


@mcp_app.tool()
async def get_experiment_result(job_id: str) -> dict[str, Any]:
    """Poll for the result of a run started by ``generate_experiment`` or
    ``search_grounded_evidence`` (both are async: they return a ``job_id`` and
    run the ~60-120s pipeline in the background). Call this every ~5s with that
    ``job_id`` until it is no longer ``"running"``.

    Args:
        job_id: The ``job_id`` returned by ``generate_experiment`` /
            ``search_grounded_evidence``.

    Returns:
        - still working: ``{"status": "running", "job_id"}`` — poll again.
        - finished ok:   ``{"status": "done", "job_id", "result": <the full
          TransBrief, or the evidence projection>}``.
        - failed:        ``{"status": "error", "job_id", "result": {"error",
          "message", "status_code"}}``.
        - unknown/expired ``job_id``: ``{"error": "unknown_job", "message",
          "status_code": 404}`` — finished results are retained ~30 min
          (``MCP_JOB_TTL_SECONDS``) after completion, then evicted.
    """
    _evict_expired_jobs()
    job = _JOBS.get(job_id)
    if job is None:
        return _clean_error(
            404,
            "unknown_job",
            f"No job with id {job_id!r}. It may have finished and been evicted after "
            f"{int(_JOB_TTL_SECONDS)}s, or the id is wrong. Start a new run with "
            f"generate_experiment or search_grounded_evidence.",
        )
    if job.status in ("queued", "running"):
        return {"status": "running", "job_id": job_id}
    return {"status": job.status, "job_id": job_id, "result": job.result}


def main() -> None:
    """Process entrypoint (``run_stdio.sh`` / ``run_http.sh`` both invoke
    ``python -m mcp_server.server``, which runs this via ``if __name__ ==
    "__main__"`` below). Transport is selected by the ``MCP_TRANSPORT`` env
    var (default ``"stdio"`` — KICKOFF.md Phase 6: "default
    mcp.run(transport='stdio')"); the run scripts set it explicitly so this
    default only matters for an ad hoc/manual invocation.
    ``FastMCP.run(...)`` is itself synchronous (it wraps ``anyio.run(...)``
    internally) — this is the one place in this package where that's
    correct, since it's the top-level process entrypoint, not a tool
    handler."""
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport not in ("stdio", "sse", "streamable-http"):
        logger.warning("Unknown MCP_TRANSPORT=%r; falling back to 'stdio'.", transport)
        transport = "stdio"
    logger.info(
        "Starting TransBench MCP server: transport=%s host=%s port=%s",
        transport,
        MCP_HTTP_HOST,
        MCP_HTTP_PORT,
    )
    mcp_app.run(transport=transport)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
