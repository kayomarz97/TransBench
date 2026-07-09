"""tests/test_snapshot_toggle.py — ``TRANSBENCH_MODE=live|golden|snapshot``
(post-release addition, extends BUILD_SPEC.md §9's own ``retrieval_snapshot``
replay mechanism; see ``engine.py``'s module-level comment block above
``_try_load_golden_brief`` for the full design write-up).

Every test in this file is fully OFFLINE and deterministic — zero live
Anthropic/PubMed calls, independent of whether ``ANTHROPIC_API_KEY`` is set —
by construction:

  - ``golden`` mode either loads the already-committed
    ``snapshots/flagship_golden_brief.json`` straight off disk (no pipeline
    call at all), or — for the mismatch-fallback case — has the live path
    itself (``engine.run_transbench_graph``) monkeypatched to a cheap
    sentinel, per the task's own suggested technique.
  - ``snapshot`` mode's STATEMENT-match safety guard is exercised directly
    against ``agents.run_retrieve`` (the actual mechanism it lives in) with
    ``neutralize_query``/``fetch_evidence_data`` monkeypatched (to raise for
    the zero-network assertion, or to cheap fakes for the live-fallback
    assertion) — the same established pattern
    ``tests/test_experiment_phase5.py``'s own snapshot-replay tests already
    use.
  - ``snapshot`` mode's env -> bundled-file -> ``retrieval_snapshot`` kwarg
    wiring at the ``engine.run_transbench`` layer is proven the same way as
    the ``golden``-mode mismatch case: ``run_transbench_graph`` monkeypatched
    to capture its kwargs and return a sentinel.
  - ``live`` mode (default, and explicitly set) is proven UNCHANGED by
    monkeypatching the golden/snapshot loaders to raise if ever called at
    all, and confirming the sentinel from a monkeypatched
    ``run_transbench_graph`` passes straight through.

This file does NOT re-prove the pre-existing (Phase 5) id-keyed
``retrieval_snapshot`` replay mechanics themselves (zero-network replay,
fall-through on a wholly-missing id, determinism across repeated calls) —
those already live in ``tests/test_experiment_phase5.py`` and
``tests/test_mcp_parity.py`` and are unchanged/still green. This file covers
ONLY what is genuinely NEW: the ``TRANSBENCH_MODE`` env toggle itself, and
the STATEMENT-match guard layered on top of the existing replay.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures import FLAGSHIP_OBSERVATION, METABOLIC_OBSERVATION
from transbench import agents, engine
from transbench.reuse import EvidenceFetchResult
from transbench.schemas import Hypothesis, TransBrief

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_STATEMENT = (
    "Effector-memory T cells drive RAAS-resistant hypertension via an "
    "IL-17-linked vascular inflammatory program."
)


def _hypothesis(hyp_id: str = "h1", statement: str = _STATEMENT) -> Hypothesis:
    return Hypothesis(
        id=hyp_id,
        axis="immune_inflammatory",
        statement=statement,
        prediction="Depleting effector-memory T cells lowers BP in resistant hypertension.",
        rationale="r",
        priority="high",
    )


def _boom(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("network/LLM call attempted -- the guard under test should have avoided it")


# ---------------------------------------------------------------------------
# golden mode
# ---------------------------------------------------------------------------


def test_golden_mode_returns_golden_brief_verbatim_for_flagship_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The core golden-mode HIT path: purely loads the committed golden-brief
    file off disk and returns it verbatim -- the live pipeline
    (``run_transbench_graph``) is never even called (monkeypatched to raise
    if it were), proving this is a zero-network, zero-LLM-call replay."""
    monkeypatch.setenv("TRANSBENCH_MODE", "golden")
    monkeypatch.delenv("TRANSBENCH_GOLDEN_BRIEF", raising=False)
    monkeypatch.setattr(engine, "run_transbench_graph", _boom)

    golden_path = engine._golden_brief_path()
    assert golden_path.exists(), f"expected the committed golden brief at {golden_path} (TASK 3 capture)"
    expected = TransBrief.model_validate(json.loads(golden_path.read_text()))

    result = asyncio.run(engine.run_transbench(FLAGSHIP_OBSERVATION))

    assert result == expected
    assert result.request_echo == FLAGSHIP_OBSERVATION


def test_golden_mode_matches_with_whitespace_and_case_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    """The safety guard's match is normalized (strip/lower/collapse
    -whitespace), not byte-exact -- a cosmetically different but
    semantically identical observation still HITS."""
    monkeypatch.setenv("TRANSBENCH_MODE", "golden")
    monkeypatch.delenv("TRANSBENCH_GOLDEN_BRIEF", raising=False)
    monkeypatch.setattr(engine, "run_transbench_graph", _boom)

    noisy_observation = "  " + FLAGSHIP_OBSERVATION.upper().replace(" ", "   ") + "  "

    golden_path = engine._golden_brief_path()
    expected = TransBrief.model_validate(json.loads(golden_path.read_text()))

    result = asyncio.run(engine.run_transbench(noisy_observation))

    assert result == expected


def test_golden_mode_falls_back_to_live_for_a_different_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mismatch safety guard (TASK 2's central requirement): a
    DIFFERENT observation in golden mode must NOT return the golden brief.
    Proven by monkeypatching the live path (``run_transbench_graph``) to a
    cheap sentinel and asserting it was used instead (the task's own
    suggested technique) -- if the mismatch guard failed to trigger, this
    test would instead get back the (wrong) golden brief, not the sentinel.
    """
    monkeypatch.setenv("TRANSBENCH_MODE", "golden")
    monkeypatch.delenv("TRANSBENCH_GOLDEN_BRIEF", raising=False)

    sentinel = object()
    captured: dict[str, Any] = {}

    async def _fake_graph(observation: str, focus_drug: Any, user_key: Any, **kwargs: Any) -> Any:
        captured["observation"] = observation
        return sentinel

    monkeypatch.setattr(engine, "run_transbench_graph", _fake_graph)

    different_observation = "A totally unrelated observation about pediatric asthma inhaler adherence."
    result = asyncio.run(engine.run_transbench(different_observation))

    assert result is sentinel, "golden-mode mismatch guard did not fall back to the live path"
    assert captured["observation"] == different_observation


def test_golden_mode_missing_file_falls_back_to_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """The "file is missing" half of the safety guard: pointing
    TRANSBENCH_GOLDEN_BRIEF at a nonexistent path must ALSO fall back to the
    live path, never raise."""
    monkeypatch.setenv("TRANSBENCH_MODE", "golden")
    monkeypatch.setenv("TRANSBENCH_GOLDEN_BRIEF", "snapshots/does_not_exist.json")

    sentinel = object()

    async def _fake_graph(observation: str, focus_drug: Any, user_key: Any, **kwargs: Any) -> Any:
        return sentinel

    monkeypatch.setattr(engine, "run_transbench_graph", _fake_graph)

    result = asyncio.run(engine.run_transbench(FLAGSHIP_OBSERVATION))

    assert result is sentinel


def test_golden_mode_invalid_json_falls_back_to_live(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """The other half of "missing/invalid": a golden-brief file that exists
    but is not valid JSON/does not validate against TransBrief must ALSO
    fall back to the live path, never raise."""
    bad_file = tmp_path / "bad_golden_brief.json"
    bad_file.write_text("this is not valid json {{{")

    monkeypatch.setenv("TRANSBENCH_MODE", "golden")
    monkeypatch.setenv("TRANSBENCH_GOLDEN_BRIEF", str(bad_file))

    sentinel = object()

    async def _fake_graph(observation: str, focus_drug: Any, user_key: Any, **kwargs: Any) -> Any:
        return sentinel

    monkeypatch.setattr(engine, "run_transbench_graph", _fake_graph)

    result = asyncio.run(engine.run_transbench(FLAGSHIP_OBSERVATION))

    assert result is sentinel


def test_golden_mode_autoselects_matching_domain_golden_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Multi-golden auto-select (the NEW candidate policy): with
    TRANSBENCH_GOLDEN_BRIEF UNSET, golden mode scans every committed
    ``snapshots/*_golden_brief.json`` and serves the one whose ``request_echo``
    matches -- here a NON-flagship domain (type 2 diabetes), proving domains
    beyond the flagship auto-select with zero config. The live path is
    monkeypatched to raise, so a pass proves this is a pure zero-network
    replay of the domain golden, not a live run (and not the flagship golden,
    which is tried FIRST but must not match a diabetes observation)."""
    monkeypatch.setenv("TRANSBENCH_MODE", "golden")
    monkeypatch.delenv("TRANSBENCH_GOLDEN_BRIEF", raising=False)
    monkeypatch.setattr(engine, "run_transbench_graph", _boom)

    result = asyncio.run(engine.run_transbench(METABOLIC_OBSERVATION))

    assert engine._normalize_for_match(result.request_echo) == engine._normalize_for_match(METABOLIC_OBSERVATION)
    assert "diabetes" in result.request_echo.lower()  # the diabetes golden, not the flagship (tried first)


def test_golden_brief_candidates_scans_dir_when_unset_but_honors_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The candidate policy itself: UNSET -> multiple committed goldens with
    the flagship deterministically first; EXPLICIT env -> exactly that one
    file (legacy single-file behavior preserved, never widened to a scan)."""
    monkeypatch.delenv("TRANSBENCH_GOLDEN_BRIEF", raising=False)
    names = [p.name for p in engine._golden_brief_candidates()]
    assert names and names[0] == "flagship_golden_brief.json", "flagship must be tried first, stable order"
    assert len(names) >= 2, "expected the bundled per-domain goldens to be auto-discovered when env is unset"

    monkeypatch.setenv("TRANSBENCH_GOLDEN_BRIEF", "snapshots/metabolic_t2d_golden_brief.json")
    assert [p.name for p in engine._golden_brief_candidates()] == ["metabolic_t2d_golden_brief.json"]


# ---------------------------------------------------------------------------
# snapshot mode -- STATEMENT-match safety guard (the NEW behavior), exercised
# directly against agents.run_retrieve, the actual mechanism it lives in.
# ---------------------------------------------------------------------------


def test_statement_match_replays_offline_with_zero_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """A snapshot entry whose stored ``statement`` matches the CURRENT
    hypothesis's ``statement`` (normalized -- different case/whitespace here,
    to prove the match is normalized, not byte-exact) is replayed with ZERO
    network calls -- ``neutralize_query``/``fetch_evidence_data`` are
    monkeypatched to raise if invoked at all."""
    hypothesis = _hypothesis("h-stmt-match")
    snapshot_abstract = {
        "pmid": "10101010",
        "title": "Matched replay abstract",
        "abstract": "Full abstract text captured on a prior live run.",
        "year": 2021,
        "journal": "Journal of Matches",
    }
    snapshot = {
        hypothesis.id: {
            "neutral_query": "nq",
            "pubmed_query": "pq",
            "abstracts": [snapshot_abstract],
            "statement": "  " + _STATEMENT.upper() + "  ",  # cosmetically different, semantically identical
        }
    }
    monkeypatch.setattr(agents, "neutralize_query", _boom)
    monkeypatch.setattr(agents, "fetch_evidence_data", _boom)

    result = asyncio.run(
        agents.run_retrieve(hypothesis, user_key=None, observation="hypertension", retrieval_snapshot=snapshot)
    )

    assert result.ranked == [snapshot_abstract]
    assert result.registry.by_pmid["10101010"].title == "Matched replay abstract"


def test_statement_mismatch_falls_back_to_live_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    """The NEW guard's central case: the snapshot DOES have an entry keyed by
    this hypothesis's id (the OLD, id-only check would have replayed it) —
    but that entry's stored ``statement`` is for a DIFFERENT hypothesis. Must
    fall back to LIVE retrieval for this hypothesis (never silently serve
    the wrong evidence) -- proven by faking (not raising)
    ``neutralize_query``/``fetch_evidence_data`` and asserting they WERE
    actually called, and that the mismatched snapshot's own PMID never
    appears in the result.
    """
    hypothesis = _hypothesis("h-stmt-mismatch")
    snapshot = {
        hypothesis.id: {
            "neutral_query": "nq-old",
            "pubmed_query": "pq-old",
            "abstracts": [
                {
                    "pmid": "99999999",
                    "title": "Evidence captured for a DIFFERENT hypothesis",
                    "abstract": "x",
                    "year": 2020,
                    "journal": "J",
                }
            ],
            "statement": "This is a completely different hypothesis statement than the current one.",
        }
    }
    calls = {"neutralize": 0, "fetch": 0}

    async def _fake_neutralize(raw_query: str, model_id: str, user_key: Any, user_provider: str) -> SimpleNamespace:
        calls["neutralize"] += 1
        return SimpleNamespace(neutral_clinical_question="does X drive Y", entities=["X", "Y"])

    async def _fake_fetch(query: str, **kwargs: Any) -> EvidenceFetchResult:
        calls["fetch"] += 1
        return EvidenceFetchResult(
            clinical_trial_abstracts=[
                {"pmid": "12121212", "title": "Live fallback abstract", "abstract": "y", "year": 2022, "journal": "J2"}
            ],
            systematic_review_abstracts=[],
            guideline_abstracts=[],
            fetch_success=True,
        )

    monkeypatch.setattr(agents, "neutralize_query", _fake_neutralize)
    monkeypatch.setattr(agents, "fetch_evidence_data", _fake_fetch)

    result = asyncio.run(
        agents.run_retrieve(hypothesis, user_key=None, observation="hypertension", retrieval_snapshot=snapshot)
    )

    assert calls["neutralize"] == 1
    assert calls["fetch"] == 2  # support pass + contradiction pass -- proves live retrieval genuinely ran
    assert all(a.get("pmid") != "99999999" for a in result.ranked), (
        "must never serve evidence captured for a different hypothesis"
    )
    assert any(a.get("pmid") == "12121212" for a in result.ranked)


def test_statement_key_absent_keeps_old_id_only_replay_backward_compat(monkeypatch: pytest.MonkeyPatch) -> None:
    """A snapshot entry with NO "statement" key at all (every snapshot
    captured before this guard existed, e.g. hand-built dicts in
    ``tests/test_experiment_phase5.py``'s pre-existing tests) keeps the
    original, unconditional id-only-keyed replay -- fully backward
    compatible, zero network."""
    hypothesis = _hypothesis("h-no-statement-key")
    snapshot = {
        hypothesis.id: {
            "neutral_query": "nq",
            "pubmed_query": "pq",
            "abstracts": [{"pmid": "55555555", "title": "t", "abstract": "a", "year": 2019, "journal": "J"}],
            # deliberately no "statement" key
        }
    }
    monkeypatch.setattr(agents, "neutralize_query", _boom)
    monkeypatch.setattr(agents, "fetch_evidence_data", _boom)

    result = asyncio.run(agents.run_retrieve(hypothesis, user_key=None, retrieval_snapshot=snapshot))

    assert result.ranked[0]["pmid"] == "55555555"


# ---------------------------------------------------------------------------
# snapshot mode -- env -> bundled-file -> retrieval_snapshot kwarg wiring at
# the engine.run_transbench layer (offline: run_transbench_graph faked).
# ---------------------------------------------------------------------------


def test_snapshot_mode_env_wires_bundled_file_into_retrieval_snapshot_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TRANSBENCH_MODE=snapshot, no explicit ``retrieval_snapshot`` kwarg, no
    TRANSBENCH_RETRIEVAL_SNAPSHOT override (default path): confirms
    ``run_transbench`` loads the committed
    ``snapshots/flagship_retrieval_snapshot.json`` (TASK 3) and passes its
    parsed content straight through as ``run_transbench_graph``'s
    ``retrieval_snapshot`` kwarg -- proves the env -> file -> kwarg wiring
    end to end without ever running the real pipeline."""
    snap_path = engine._retrieval_snapshot_path()
    assert snap_path.exists(), f"expected the committed retrieval snapshot at {snap_path} (TASK 3 capture)"
    expected_snapshot = json.loads(snap_path.read_text())
    assert expected_snapshot, "the committed retrieval snapshot must be non-empty"
    for entry in expected_snapshot.values():
        assert entry.get("statement"), "every captured entry must carry a non-empty 'statement' (graph.py change)"

    monkeypatch.setenv("TRANSBENCH_MODE", "snapshot")
    monkeypatch.delenv("TRANSBENCH_RETRIEVAL_SNAPSHOT", raising=False)

    sentinel = object()
    captured: dict[str, Any] = {}

    async def _fake_graph(observation: str, focus_drug: Any, user_key: Any, **kwargs: Any) -> Any:
        captured["retrieval_snapshot"] = kwargs.get("retrieval_snapshot")
        return sentinel

    monkeypatch.setattr(engine, "run_transbench_graph", _fake_graph)

    result = asyncio.run(engine.run_transbench("anything, this observation is irrelevant here"))

    assert result is sentinel
    assert captured["retrieval_snapshot"] == expected_snapshot


def test_snapshot_mode_missing_bundled_file_degrades_to_live_retrieval_for_every_hypothesis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing/misconfigured bundled snapshot file must never crash the
    run -- it degrades ``snapshot`` mode to plain live retrieval (kwarg ends
    up ``None``, exactly like unset ``live`` mode)."""
    monkeypatch.setenv("TRANSBENCH_MODE", "snapshot")
    monkeypatch.setenv("TRANSBENCH_RETRIEVAL_SNAPSHOT", "snapshots/does_not_exist.json")

    sentinel = object()
    captured: dict[str, Any] = {}

    async def _fake_graph(observation: str, focus_drug: Any, user_key: Any, **kwargs: Any) -> Any:
        captured["retrieval_snapshot"] = kwargs.get("retrieval_snapshot")
        return sentinel

    monkeypatch.setattr(engine, "run_transbench_graph", _fake_graph)

    result = asyncio.run(engine.run_transbench(FLAGSHIP_OBSERVATION))

    assert result is sentinel
    assert captured["retrieval_snapshot"] is None


def test_snapshot_mode_explicit_kwarg_takes_precedence_over_bundled_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicitly-passed ``retrieval_snapshot=...`` kwarg always wins over
    TRANSBENCH_MODE=snapshot's bundled file (mirrors ``user_key``'s own
    explicit-arg-beats-env precedence)."""
    monkeypatch.setenv("TRANSBENCH_MODE", "snapshot")
    monkeypatch.delenv("TRANSBENCH_RETRIEVAL_SNAPSHOT", raising=False)

    explicit_snapshot = {"h-explicit": {"neutral_query": "n", "pubmed_query": "p", "abstracts": [], "statement": "s"}}
    sentinel = object()
    captured: dict[str, Any] = {}

    async def _fake_graph(observation: str, focus_drug: Any, user_key: Any, **kwargs: Any) -> Any:
        captured["retrieval_snapshot"] = kwargs.get("retrieval_snapshot")
        return sentinel

    monkeypatch.setattr(engine, "run_transbench_graph", _fake_graph)

    result = asyncio.run(engine.run_transbench("anything", retrieval_snapshot=explicit_snapshot))

    assert result is sentinel
    assert captured["retrieval_snapshot"] == explicit_snapshot


# ---------------------------------------------------------------------------
# live mode -- default/unset, and explicitly set -- must be 100% unchanged.
# ---------------------------------------------------------------------------


def test_live_mode_default_never_touches_golden_or_snapshot_loaders(monkeypatch: pytest.MonkeyPatch) -> None:
    """TRANSBENCH_MODE unset (the documented default): the golden/snapshot
    loaders must never even be called (monkeypatched to raise if they were),
    and whatever ``run_transbench_graph`` returns passes straight through,
    with ``retrieval_snapshot`` staying ``None`` (no kwarg was passed)."""
    monkeypatch.delenv("TRANSBENCH_MODE", raising=False)
    monkeypatch.setattr(engine, "_try_load_golden_brief", _boom)
    monkeypatch.setattr(engine, "_load_bundled_retrieval_snapshot", _boom)

    sentinel = object()
    captured: dict[str, Any] = {}

    async def _fake_graph(observation: str, focus_drug: Any, user_key: Any, **kwargs: Any) -> Any:
        captured["retrieval_snapshot"] = kwargs.get("retrieval_snapshot")
        return sentinel

    monkeypatch.setattr(engine, "run_transbench_graph", _fake_graph)

    result = asyncio.run(engine.run_transbench(FLAGSHIP_OBSERVATION))

    assert result is sentinel
    assert captured["retrieval_snapshot"] is None


@pytest.mark.parametrize("mode_value", ["live", "LIVE", " Live "])
def test_live_mode_explicit_value_is_case_and_whitespace_insensitive(
    monkeypatch: pytest.MonkeyPatch, mode_value: str
) -> None:
    monkeypatch.setenv("TRANSBENCH_MODE", mode_value)
    monkeypatch.setattr(engine, "_try_load_golden_brief", _boom)
    monkeypatch.setattr(engine, "_load_bundled_retrieval_snapshot", _boom)

    sentinel = object()

    async def _fake_graph(observation: str, focus_drug: Any, user_key: Any, **kwargs: Any) -> Any:
        return sentinel

    monkeypatch.setattr(engine, "run_transbench_graph", _fake_graph)

    result = asyncio.run(engine.run_transbench("anything"))

    assert result is sentinel


def test_unknown_mode_value_falls_back_to_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unrecognized TRANSBENCH_MODE value is treated as 'live' (logged),
    never raises."""
    monkeypatch.setenv("TRANSBENCH_MODE", "bogus-mode-name")
    monkeypatch.setattr(engine, "_try_load_golden_brief", _boom)
    monkeypatch.setattr(engine, "_load_bundled_retrieval_snapshot", _boom)

    sentinel = object()

    async def _fake_graph(observation: str, focus_drug: Any, user_key: Any, **kwargs: Any) -> Any:
        return sentinel

    monkeypatch.setattr(engine, "run_transbench_graph", _fake_graph)

    result = asyncio.run(engine.run_transbench("anything"))

    assert result is sentinel


def test_live_mode_passes_through_explicit_retrieval_snapshot_kwarg_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller-supplied ``retrieval_snapshot`` kwarg in plain ``live`` mode
    is unaffected by this toggle -- passed straight through, exactly as
    before this change (Phase 5 behavior)."""
    monkeypatch.delenv("TRANSBENCH_MODE", raising=False)
    explicit = {"h1": {"abstracts": []}}
    captured: dict[str, Any] = {}
    sentinel = object()

    async def _fake_graph(observation: str, focus_drug: Any, user_key: Any, **kwargs: Any) -> Any:
        captured["retrieval_snapshot"] = kwargs.get("retrieval_snapshot")
        return sentinel

    monkeypatch.setattr(engine, "run_transbench_graph", _fake_graph)

    result = asyncio.run(engine.run_transbench("anything", retrieval_snapshot=explicit))

    assert result is sentinel
    assert captured["retrieval_snapshot"] == explicit
