"""Phase 2 acceptance test (KICKOFF.md Phase 2 / BUILD_SPEC.md §5).

REAL Anthropic API call test — requires a working ``ANTHROPIC_API_KEY`` in
the environment (skips cleanly, with a clear reason, if absent). Asserts on
the real Decomposer (agent 1, Haiku) + Hypothesis Generator (agent 2,
Sonnet) output of a full flagship ``run_transbench()`` pipeline run.

Phase 7 update: this file originally called
``asyncio.run(run_transbench(FLAGSHIP_OBSERVATION))`` itself. As of Phase 5
the graph is COMPLETE end to end (agents 1-8), so that was actually a full
~13-call live pipeline run every time this file ran — not the "2 calls"
this docstring originally (Phase-2-era) claimed, and redundant with the
other acceptance tests that also each independently ran the identical
flagship pipeline. It now consumes the shared, session-scoped
``flagship_brief`` fixture (``tests/conftest.py``) instead — the SAME real
brief every other live test in this suite shares, at zero incremental
spend (KICKOFF.md Phase 7 spend directive). This file's own assertions
(agents 1-2's contract) are unchanged.

Phase 8 update (domain-universalization): ``schemas.Axis`` is no longer a
fixed 8-value ``Literal`` (it is now a free-form, normalized string — any
clinical/biomedical domain names its own relevant axes; see ``schemas.py``'s
own docstring) — so this file's assertions are now domain-agnostic:
non-empty axes/rationale, not membership in a closed set, and no hardcoded
expectation that this ONE flagship scenario's axes must include
"immune_inflammatory" by name (that hardcoded assumption doesn't generalize
to any other domain's own decomposition and was never really a Phase 2
contract requirement — "the model decomposes into >=2 real, non-empty
axes" is).
"""
from __future__ import annotations

from transbench.schemas import TransBrief


def test_flagship_decompose_and_hypothesize_real_llm_calls(flagship_brief: TransBrief) -> None:
    """Real end-to-end call (shared fixture): flagship -> decompose -> hypothesize."""
    brief = flagship_brief

    # --- Agent 1: Decomposer (Haiku) ---
    axis_names = [a.axis for a in brief.axes]
    assert len(brief.axes) >= 2, f"expected >=2 axes, got {axis_names}"
    for a in brief.axes:
        assert isinstance(a.axis, str) and a.axis.strip(), f"axis has an empty/invalid label: {a.axis!r}"
        assert a.rationale.strip(), f"axis {a.axis} has an empty rationale"

    # --- Agent 2: Hypothesis Generator (Sonnet) ---
    hypotheses = [gh.hypothesis for gh in brief.hypotheses]
    assert 1 <= len(hypotheses) <= 3, f"expected 1-3 hypotheses, got {len(hypotheses)}"
    for h in hypotheses:
        assert h.statement.strip(), f"hypothesis {h.id} has an empty statement"
        assert h.prediction.strip(), f"hypothesis {h.id} has an empty (non-falsifiable) prediction"
        assert isinstance(h.axis, str) and h.axis.strip(), f"hypothesis {h.id} has an empty/invalid axis {h.axis!r}"

    # This file only gates agents 1-2's OWN contract (axes/hypotheses shape),
    # not downstream grounding -- the pipeline behind the shared
    # `flagship_brief` fixture is the REAL, COMPLETE Phase-5 graph (agents
    # 1-8), so `evidence`/`grounded` are legitimately populated real
    # signals here, not placeholders. (Phase 7 fix: this file previously
    # asserted `gh.grounded is False` for EVERY hypothesis unconditionally --
    # correct back when agents 3-8 were still stubs, but false once Phase 5
    # completed the graph: a healthy flagship run is EXPECTED to ground at
    # least one hypothesis, per test_retrieval_phase3.py's own
    # `any_grounded` assertion. See test_retrieval_phase3.py/test_cost.py
    # for the real agent 3-8 grounding/cost assertions.)

    # Printed (not asserted) so a human can eyeball scientific sanity, per the
    # coordinator's request — visible with `pytest -q -s`.
    print("\n--- Phase 2 flagship output ---")
    print("Axes:", [(a.axis, a.rationale) for a in brief.axes])
    for h in hypotheses:
        print(f"Hypothesis {h.id} [{h.axis}, priority={h.priority}]:")
        print("  statement :", h.statement)
        print("  prediction:", h.prediction)
