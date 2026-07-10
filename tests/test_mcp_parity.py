"""tests/test_mcp_parity.py — MCP parity + retrieval-snapshot determinism
(KICKOFF.md Phase 7 report requirements; BUILD_SPEC.md §7, §9).

Both halves reuse the ONE shared, real, live ``flagship_brief`` fixture
(``tests/conftest.py``) — ZERO additional live Anthropic/PubMed calls beyond
that single flagship run.

**MCP parity.** ``mcp_server/server.py``'s tools are ASYNC (submit + poll):
``generate_experiment`` / ``search_grounded_evidence`` start a background job
and return a ``job_id``; the actual pipeline payload is computed by the
internal ``_generate_experiment_result`` / ``_search_grounded_evidence_result``
coroutines and handed back via ``get_experiment_result``. Re-running the full
live pipeline a SECOND time through the MCP layer would only prove the engine
is non-deterministic (it legitimately is, at temp=0, per BUILD_SPEC.md §9's own
"reproducibility" framing) — it would NOT prove the MCP layer is a faithful
passthrough. So instead: monkeypatch ``mcp_server.server.run_transbench`` to
return the ALREADY-COMPUTED ``flagship_brief`` and call the REAL result
coroutines directly — this exercises every line of the MCP layer's OWN payload
code (input validation, the engine call site, error-handling boundary,
``model_dump()``/reshape) against a real brief, with zero extra spend, and
proves it is schema-valid and shape-faithful. A separate lifecycle test then
drives the real submit -> poll tool path (``generate_experiment`` ->
``get_experiment_result``) to prove the job registry surfaces that same payload
faithfully. (FastMCP's ``@mcp_app.tool()`` decorator returns the original async
function unchanged and directly callable — confirmed empirically against this
repo's installed ``mcp==1.28.1``.)

**Retrieval-snapshot determinism** (BUILD_SPEC.md §9): replays
``agents.run_retrieve`` for every REAL hypothesis in the captured
``flagship_brief`` against its OWN just-captured
``run_manifest["retrieval_snapshot"]``, with ``neutralize_query``/
``fetch_evidence_data`` monkeypatched to raise if called at all — proves
IDENTICAL evidence is reproduced with ZERO PubMed/neutralize calls, and that
replaying the SAME snapshot twice is itself deterministic.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import pytest

import mcp_server.server as mcp_server_module
from tests.fixtures import FLAGSHIP_OBSERVATION
from transbench import agents
from transbench.agents import TransBenchLLMError
from transbench.schemas import TransBrief

# ---------------------------------------------------------------------------
# MCP parity
# ---------------------------------------------------------------------------


def _patch_engine_to_return(monkeypatch: pytest.MonkeyPatch, brief: TransBrief) -> None:
    async def _fake_run_transbench(observation: str, focus_drug: Optional[str] = None, **kwargs: Any) -> TransBrief:
        return brief

    monkeypatch.setattr(mcp_server_module, "run_transbench", _fake_run_transbench)


def test_mcp_generate_experiment_matches_engine_brief(
    flagship_brief: TransBrief, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The showpiece tool: with the engine call stubbed to return the ALREADY
    -COMPUTED real flagship brief, ``generate_experiment`` must return the
    exact same brief, faithfully, as a schema-valid dict — proving the MCP
    boundary (input validation -> engine call -> ``.model_dump()``) does not
    silently drop/alter anything."""
    _patch_engine_to_return(monkeypatch, flagship_brief)

    result = asyncio.run(mcp_server_module._generate_experiment_result(observation=FLAGSHIP_OBSERVATION, focus_drug=""))

    assert "error" not in result, f"unexpected error shape from a stubbed-success engine call: {result}"
    revalidated = TransBrief.model_validate(result)
    assert revalidated == flagship_brief

    assert result["top_experiment"]["claude_science_prompt"], (
        "generate_experiment's returned top_experiment.claude_science_prompt must be "
        "present -- this is literally what Claude Science runs (BUILD_SPEC.md §7)"
    )
    assert result["disclaimer"] == (
        "Research hypothesis generation only. Not clinical, diagnostic, or prescribing advice."
    )


def test_mcp_search_grounded_evidence_reshapes_same_brief(
    flagship_brief: TransBrief, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The utility/fallback tool: SAME engine call (stubbed to the same real
    brief), reshaped into the lighter evidence-only projection — no
    ``axes``/``top_experiment``/``run_manifest`` — but every hypothesis id
    and its full evidence list must still be present, faithfully."""
    _patch_engine_to_return(monkeypatch, flagship_brief)

    result = asyncio.run(mcp_server_module._search_grounded_evidence_result(question=FLAGSHIP_OBSERVATION))

    assert "error" not in result, f"unexpected error shape from a stubbed-success engine call: {result}"
    for absent_key in ("axes", "top_experiment", "run_manifest"):
        assert absent_key not in result, f"search_grounded_evidence must omit {absent_key!r}"

    assert result["request_echo"] == flagship_brief.request_echo
    assert [h["hypothesis_id"] for h in result["hypotheses"]] == [
        gh.hypothesis.id for gh in flagship_brief.hypotheses
    ]
    for h_out, gh in zip(result["hypotheses"], flagship_brief.hypotheses):
        assert len(h_out["evidence"]) == len(gh.evidence)
    assert result["disclaimer"] == flagship_brief.disclaimer


def test_mcp_generate_experiment_returns_clean_structured_error_on_engine_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP error-handling contract (BUILD_SPEC.md §7 / KICKOFF.md Phase 6):
    an engine failure (here: simulating "no API key", the documented example)
    must surface as the ONE clean ``{error, message, status_code}`` shape —
    never a raw traceback/exception. Zero live cost (the engine call is
    stubbed to raise before ever reaching Anthropic)."""

    async def _fake_raising(observation: str, focus_drug: Optional[str] = None, **kwargs: Any) -> TransBrief:
        raise TransBenchLLMError(402, "no_api_key", "No API key configured.")

    monkeypatch.setattr(mcp_server_module, "run_transbench", _fake_raising)

    result = asyncio.run(mcp_server_module._generate_experiment_result(observation=FLAGSHIP_OBSERVATION))

    assert result == {"error": "no_api_key", "message": "No API key configured.", "status_code": 402}


def test_mcp_generate_experiment_rejects_too_short_observation() -> None:
    """Input-validation boundary — the submit tool rejects obviously-bad input
    INLINE (that clean error shape, not a job_id) before any job is created or
    the engine is ever called (no monkeypatch needed; zero cost either way)."""
    result = asyncio.run(mcp_server_module.generate_experiment(observation="ab"))
    assert "job_id" not in result, "invalid input must fail inline, not be accepted as a job"
    assert result["error"] == "invalid_input"
    assert result["status_code"] == 422


def test_mcp_submit_and_poll_lifecycle(
    flagship_brief: TransBrief, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The async fix (submit + poll): ``generate_experiment`` must return a
    job handle in <1s, and polling ``get_experiment_result`` with that id must
    hand back the SAME full brief once the background run finishes — proving
    the job registry is a faithful passthrough, not just the direct-call path.
    Engine stubbed to the already-computed flagship brief (zero live cost);
    submit + poll run on ONE event loop so the background task can complete."""
    _patch_engine_to_return(monkeypatch, flagship_brief)

    async def _drive() -> dict[str, Any]:
        submitted = await mcp_server_module.generate_experiment(
            observation=FLAGSHIP_OBSERVATION, focus_drug=""
        )
        assert submitted["status"] == "running"
        assert submitted["poll_tool"] == "get_experiment_result"
        assert submitted["job_id"], "submit must return a non-empty job_id"
        job_id = submitted["job_id"]

        # Poll on the SAME loop; the stubbed engine completes near-instantly, so
        # a few yields drain the background task. Bounded so a bug can't hang.
        for _ in range(10_000):
            polled = await mcp_server_module.get_experiment_result(job_id=job_id)
            if polled["status"] != "running":
                return polled
            await asyncio.sleep(0)
        raise AssertionError("job never left 'running' — background task did not complete")

    polled = asyncio.run(_drive())

    assert polled["status"] == "done", f"expected done, got {polled}"
    revalidated = TransBrief.model_validate(polled["result"])
    assert revalidated == flagship_brief


def test_mcp_get_experiment_result_unknown_job() -> None:
    """Polling an unknown/expired ``job_id`` returns the clean ``unknown_job``
    error shape (404), never a raw KeyError/traceback."""
    result = asyncio.run(mcp_server_module.get_experiment_result(job_id="does-not-exist"))
    assert result["error"] == "unknown_job"
    assert result["status_code"] == 404


# ---------------------------------------------------------------------------
# Retrieval-snapshot determinism (BUILD_SPEC.md §9)
# ---------------------------------------------------------------------------


def _boom(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("network/LLM call attempted during snapshot replay -- determinism violated")


def test_retrieval_snapshot_replay_reproduces_identical_evidence_with_zero_network_calls(
    flagship_brief: TransBrief, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replays retrieval for EVERY real hypothesis in the captured flagship
    brief, from its OWN just-captured ``run_manifest["retrieval_snapshot"]``
    — with ``neutralize_query``/``fetch_evidence_data`` monkeypatched to
    raise if invoked at all. Confirms IDENTICAL evidence (the exact same
    ranked abstract dicts, byte-for-byte) is reproduced with zero PubMed
    calls, and that replaying twice is itself deterministic (no hidden
    ordering/state)."""
    snapshot = flagship_brief.run_manifest.get("retrieval_snapshot") or {}
    assert snapshot, "flagship_brief.run_manifest['retrieval_snapshot'] must be populated"

    monkeypatch.setattr(agents, "neutralize_query", _boom)
    monkeypatch.setattr(agents, "fetch_evidence_data", _boom)

    for gh in flagship_brief.hypotheses:
        hyp_id = gh.hypothesis.id
        expected_abstracts = snapshot[hyp_id]["abstracts"]

        result_1 = asyncio.run(
            agents.run_retrieve(gh.hypothesis, user_key=None, retrieval_snapshot=snapshot)
        )
        result_2 = asyncio.run(
            agents.run_retrieve(gh.hypothesis, user_key=None, retrieval_snapshot=snapshot)
        )

        assert result_1.ranked == expected_abstracts == result_2.ranked, (
            f"hypothesis {hyp_id}: snapshot replay did not reproduce identical evidence"
        )
        assert result_1.neutral_query == snapshot[hyp_id]["neutral_query"]
        assert result_1.pubmed_query == snapshot[hyp_id]["pubmed_query"]
        # The registry is rebuilt fresh each replay (pure, in-process) -- every
        # PMID in the snapshot's abstracts must resolve in it.
        for abstract in expected_abstracts:
            pmid = abstract.get("pmid")
            if pmid:
                assert result_1.registry.by_pmid.get(str(pmid)) is not None
