"""engine.py — thin public entrypoint for the TransBench engine (BUILD_SPEC.md §1).

``run_transbench`` is the single async entrypoint every caller (tests, the
MCP server in Phase 6, Phase 2-5 internals) should import. ``run_transbench_sync``
wraps it with ``asyncio.run`` for non-async callers. Parameter names mirror
``schemas.TransRequest`` 1:1 (minus ``user_key``'s required-ness — see note
below) so a caller can do ``run_transbench(**request.model_dump(exclude={"user_key"}),
user_key=request.user_key)`` without any renaming.

Note on ``user_key``: ``TransRequest.user_key`` is a *required* field (no
default) because a real request always needs BYOK credentials for the LLM
calls it triggers. This entrypoint's own signature instead defaults
``user_key=None`` per KICKOFF.md Phase 1 ("``run_transbench(observation,
focus_drug=None, user_key=None, ...) -> TransBrief``") because the Phase 1
stub makes no LLM calls at all and must stay runnable without any key present
(e.g. this repo's own acceptance tests). Phase 2+ agents that actually need a
key raise a clean, caught ``fastapi.HTTPException`` from ``create_llm`` if one
is missing (BUILD_SPEC.md §0.4) — this entrypoint does not duplicate that
check.
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

    Phase 1 (today): delegates to the stub LangGraph pipeline in ``graph.py``,
    which echoes ``observation`` into a schema-valid placeholder brief — no
    LLM or retrieval calls happen yet. Phase 2+ wires the real 8 agents
    (BUILD_SPEC.md §5) behind this exact same signature.
    """
    return await run_transbench_graph(
        observation,
        focus_drug,
        user_key,
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
