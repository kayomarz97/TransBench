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

import logging
import os
import sys
from typing import Any, Optional

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


@mcp_app.tool()
async def generate_experiment(observation: str, focus_drug: str = "") -> dict[str, Any]:
    """Generate a grounded translational research brief from ANY clinical or
    biomedical observation — a disease's drug response/resistance, a drug's
    adverse effect/toxicity, or any mechanism (not limited to any one domain).

    Runs the full TransBench pipeline (decompose -> hypothesize -> retrieve
    -> grade -> entail -> novelty-check -> design -> assemble) and returns a
    schema-valid ``TransBrief`` (JSON): decomposed biological axes, up to 3
    falsifiable mechanistic hypotheses each graded against real PubMed
    evidence with an auditable novelty verdict, and ONE runnable
    computational experiment (``top_experiment``) naming a concrete,
    resolvable public dataset plus a ``claude_science_prompt`` ready to run
    in Claude Science to produce a reproducible figure.

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
        A ``TransBrief`` dict on success, or ``{"error", "message",
        "status_code"}`` on failure (e.g. no/invalid ANTHROPIC_API_KEY).
        Every successful response carries a fixed research-only disclaimer
        (BUILD_SPEC.md §0.5) — this tool never emits diagnosis, drug
        selection, or dosing guidance.
    """
    bad = _validate_text(observation, "observation")
    if bad is not None:
        return bad
    brief, error = await _call_engine_safely(observation, focus_drug or None)
    if error is not None:
        return error
    assert brief is not None  # _call_engine_safely's own contract: exactly one of (brief, error) is set
    return brief.model_dump()


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


@mcp_app.tool()
async def search_grounded_evidence(question: str) -> dict[str, Any]:
    """Look up PubMed-grounded mechanistic evidence for ANY clinical,
    pharmacological, or mechanistic question (utility / fallback tool — a
    lighter-weight sibling of ``generate_experiment``, not limited to any
    one domain).

    Runs the SAME full TransBench pipeline as ``generate_experiment`` (it is
    not a separate, cheaper retrieval path) but returns a smaller,
    evidence-focused projection: for each generated hypothesis, its novelty
    verdict and every retrieved evidence item (each carrying a resolvable
    citation, an entailment verdict, and an evidence grade), plus the
    deduplicated reference list, any contradictions surfaced, and the
    uncertainty note. Omits ``axes``/``top_experiment``/``run_manifest``.

    Args:
        question: A free-text clinical/pharmacological/mechanistic question
            (3-8000 characters), any domain.

    Returns:
        The grounded-evidence projection dict on success, or
        ``{"error", "message", "status_code"}`` on failure. Always carries
        the fixed research-only disclaimer.
    """
    bad = _validate_text(question, "question")
    if bad is not None:
        return bad
    brief, error = await _call_engine_safely(question, None)
    if error is not None:
        return error
    assert brief is not None
    return _grounded_evidence_projection(brief)


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
