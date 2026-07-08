"""Phase 2 acceptance test (KICKOFF.md Phase 2 / BUILD_SPEC.md §5).

REAL Anthropic API call test — requires a working ``ANTHROPIC_API_KEY`` in
the environment (skips cleanly, with a clear reason, if absent). Runs the
FLAGSHIP observation through the real Decomposer (agent 1, Haiku) +
Hypothesis Generator (agent 2, Sonnet) via the full ``run_transbench()``
pipeline — exactly ONE flagship pass (2 real LLM calls total: 1 decompose +
1 hypothesize), per the phase's spend-limiting instruction. Downstream
stages (agents 3-8: retrieve/grade/novelty/rigor/design/assemble) are still
Phase-1/2-style placeholders until Phase 3+ — this test only asserts on
``axes``/``hypotheses``.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from tests.fixtures import FLAGSHIP_OBSERVATION
from transbench.engine import run_transbench
from transbench.schemas import Axis

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="Phase 2 acceptance test makes a REAL Anthropic API call; requires a working ANTHROPIC_API_KEY.",
)

_VALID_AXES = set(Axis.__args__)  # type: ignore[attr-defined]


def test_flagship_decompose_and_hypothesize_real_llm_calls() -> None:
    """Real end-to-end call: flagship -> decompose -> hypothesize."""
    brief = asyncio.run(run_transbench(FLAGSHIP_OBSERVATION))

    # --- Agent 1: Decomposer (Haiku) ---
    axis_names = [a.axis for a in brief.axes]
    assert len(brief.axes) >= 2, f"expected >=2 axes, got {axis_names}"
    assert "immune_inflammatory" in axis_names, (
        f"flagship (resistant HTN + elevated hs-CRP) must motivate "
        f"immune_inflammatory, got {axis_names}"
    )
    for a in brief.axes:
        assert a.axis in _VALID_AXES
        assert a.rationale.strip(), f"axis {a.axis} has an empty rationale"

    # --- Agent 2: Hypothesis Generator (Sonnet) ---
    hypotheses = [gh.hypothesis for gh in brief.hypotheses]
    assert 1 <= len(hypotheses) <= 3, f"expected 1-3 hypotheses, got {len(hypotheses)}"
    for h in hypotheses:
        assert h.statement.strip(), f"hypothesis {h.id} has an empty statement"
        assert h.prediction.strip(), f"hypothesis {h.id} has an empty (non-falsifiable) prediction"
        assert h.axis in _VALID_AXES, f"hypothesis {h.id} has an invalid axis {h.axis!r}"

    # --- Downstream rigor/novelty stages must still be honestly labeled as
    # not-yet-run (Phase 4+). Note: as of Phase 3, `evidence` IS legitimately
    # populated (retrieve+grade now run in this same pipeline) -- this test
    # only gates agents 1-2's own contract, so it no longer asserts
    # `evidence == []` (that was Phase 2's placeholder-era expectation, not a
    # real invariant of decompose/hypothesize). See test_retrieval_phase3.py
    # for the agent 3-4 evidence assertions.
    for gh in brief.hypotheses:
        assert gh.grounded is False

    # Printed (not asserted) so a human can eyeball scientific sanity, per the
    # coordinator's request — visible with `pytest -q -s`.
    print("\n--- Phase 2 flagship output ---")
    print("Axes:", [(a.axis, a.rationale) for a in brief.axes])
    for h in hypotheses:
        print(f"Hypothesis {h.id} [{h.axis}, priority={h.priority}]:")
        print("  statement :", h.statement)
        print("  prediction:", h.prediction)
