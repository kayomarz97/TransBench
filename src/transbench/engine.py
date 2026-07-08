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
from typing import Optional

from transbench import config
from transbench.graph import run_transbench_graph
from transbench.schemas import TransBrief


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

    Phase 2 (today): decompose (agent 1, Haiku) + hypothesize (agent 2,
    Sonnet) make REAL Anthropic calls and populate ``TransBrief.axes`` +
    ``hypotheses``. Agents 3-8 (retrieve/grade/novelty/rigor/design/assemble)
    are still an accurate, clearly-labeled placeholder — Phases 3-5 wire
    those in behind this exact same signature.
    """
    effective_user_key = user_key if user_key is not None else config.ANTHROPIC_API_KEY
    return await run_transbench_graph(
        observation,
        focus_drug,
        effective_user_key,
        user_provider=user_provider,
        model_reasoning=model_reasoning,
        model_cheap=model_cheap,
        max_hypotheses=max_hypotheses,
        retrieval_snapshot=retrieval_snapshot,
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
