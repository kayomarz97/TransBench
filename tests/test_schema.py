"""Phase 1/7 acceptance test (KICKOFF.md Phase 1/7 / BUILD_SPEC.md §4, §8).

Asserts ``run_transbench(observation)`` returns a schema-valid ``TransBrief``
for all 3 ``tests/fixtures.py`` demo observations (flagship + 2 backups —
BUILD_SPEC.md §8's exact 3 demo inputs), and that ``top_experiment.
dataset_pointer`` is present (KICKOFF.md Definition of Done: "a runnable ...
experiment naming a resolvable dataset"; live, independent-network
re-verification that a specific pointer actually RESOLVES is
``test_experiment_phase5.py::test_flagship_experiment_plan_and_full_brief``'s
job, not re-done here for all 3 fixtures at extra live-network cost —
``agents.run_design_experiment``'s own verify-then-fallback gate,
Phase 5, structurally guarantees every ``dataset_pointer`` this engine ever
emits is either independently verified or the guaranteed-resolvable pinned
default).

REAL Anthropic + PubMed API call test — requires a working
``ANTHROPIC_API_KEY`` (skips cleanly, with a clear reason, if absent).

**Phase 7 fix + spend-bounding** (KICKOFF.md Phase 7: "confirm it passes
(use snapshot replay / bounded runs to limit spend)"; "keep total NEW live
runs to ≤2-3"): this file previously had NO skip guard at all (inconsistent
with — and a real robustness gap next to — every sibling live-call test file
in this suite, which all skip cleanly without a key) and called
``run_transbench`` completely fresh SIX times: once per parametrized fixture
id (3), PLUS three MORE fixture-0-only reruns across three separate test
functions. FOUR of those six were the identical flagship input, redundant
with every other live acceptance test in this suite that also independently
re-ran the flagship. It now:
  - skips cleanly without ``ANTHROPIC_API_KEY`` (bug fix);
  - reuses the shared, session-scoped ``flagship_brief`` fixture
    (``tests/conftest.py``) for every flagship-fixture assertion (the
    parametrized case AND the three single-fixture tests below) — ONE live
    call total, shared with the rest of the suite, zero incremental spend;
  - still makes exactly ONE fresh live call each for backup1/backup2 (no
    other test in this suite needs those fixtures, so there is nothing
    further to share — BUILD_SPEC.md §8 defines all 3 demo inputs
    specifically so this file can validate the schema contract against each
    of them), retried once on a transient Anthropic 500
    (``tests.conftest.run_transbench_live_with_retry`` — this file
    previously had NO retry protection at all, another gap fixed here).
Grand total NEW live calls attributable to this file: 2 (backup1, backup2) —
plus reuse of the 1 flagship call every other live test in the suite also
shares.

Uses plain ``asyncio.run(...)`` (via ``tests.conftest``'s helper) inside
ordinary ``def test_...()`` functions (rather than ``@pytest.mark.asyncio``)
so this file needs zero pytest-asyncio configuration (no ``asyncio_mode`` ini
setting required) — same convention as every other test file in this suite.
"""
from __future__ import annotations

import os

import pytest

from tests.conftest import run_transbench_live_with_retry
from tests.fixtures import DEMO_OBSERVATIONS
from transbench import config
from transbench.reuse import REUSE_SOURCE
from transbench.schemas import TransBrief

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="test_schema's acceptance tests make REAL Anthropic + PubMed API calls; requires a working ANTHROPIC_API_KEY.",
)

EXPECTED_DISCLAIMER = "Research hypothesis generation only. Not clinical, diagnostic, or prescribing advice."

# fixtures.py's own documented, frozen ordering (BUILD_SPEC.md §8: flagship
# first) — asserted once here so a future reordering of DEMO_OBSERVATIONS
# fails loudly and obviously, rather than silently making this file spend an
# extra live call on what it THINKS is the flagship but isn't.
_FLAGSHIP_NAME = "flagship_resistant_hypertension_immune_axis"
assert DEMO_OBSERVATIONS[0]["name"] == _FLAGSHIP_NAME


def _brief_for_fixture(fixture: dict, flagship_brief: TransBrief) -> TransBrief:
    """The flagship fixture reuses the ONE shared live brief (zero
    incremental spend); a backup fixture gets its OWN single fresh live call
    (retried once on a transient 500) — nothing else in this suite needs
    those, so there is nothing further to share."""
    if fixture["name"] == _FLAGSHIP_NAME:
        return flagship_brief
    return run_transbench_live_with_retry(fixture["observation"], focus_drug=fixture.get("focus_drug"))


@pytest.mark.parametrize(
    "fixture",
    DEMO_OBSERVATIONS,
    ids=[f["name"] for f in DEMO_OBSERVATIONS],
)
def test_run_transbench_returns_valid_transbrief(fixture: dict, flagship_brief: TransBrief) -> None:
    brief = _brief_for_fixture(fixture, flagship_brief)

    assert isinstance(brief, TransBrief)
    # Re-validate from a plain dict (as the MCP tool sends it, `model_dump()`)
    # to prove the *shape* is schema-valid, not just that this particular
    # live Python object happens to satisfy its own class.
    revalidated = TransBrief.model_validate(brief.model_dump())
    assert revalidated == brief
    assert brief.request_echo == fixture["observation"]

    # KICKOFF.md Definition of Done / BUILD_SPEC.md §5 agent 7:
    # top_experiment.dataset_pointer must be present -- either a model-named,
    # independently-verified accession, or the guaranteed-resolvable pinned
    # Tabula Sapiens default (agents.run_design_experiment's verify-then
    # -fallback gate, Phase 5), or -- if genuinely no hypothesis cleared the
    # novelty guard this run -- the honest hypothesis_id="none" sentinel
    # (still schema-valid, still names the pinned default pointer;
    # BUILD_SPEC.md never allows a missing/blank dataset_pointer either way).
    assert brief.top_experiment.dataset_pointer, (
        f"[{fixture['name']}] top_experiment.dataset_pointer must be present"
    )
    assert str(brief.top_experiment.dataset_pointer).startswith("https://"), (
        f"[{fixture['name']}] top_experiment.dataset_pointer must be a well-formed https URL: "
        f"{brief.top_experiment.dataset_pointer!r}"
    )


def test_flagship_carries_disclaimer_and_run_manifest(flagship_brief: TransBrief) -> None:
    brief = flagship_brief

    assert brief.disclaimer == EXPECTED_DISCLAIMER
    assert isinstance(brief.run_manifest, dict)
    assert brief.run_manifest  # non-empty
    assert brief.request_echo == DEMO_OBSERVATIONS[0]["observation"]


def test_flagship_run_manifest_reports_reuse_source_and_caps(flagship_brief: TransBrief) -> None:
    """Sanity: the manifest records which reuse path served the run
    (installed_iatronix under Path A — see reuse.py / PLAN.md) and the
    BUILD_SPEC.md caps."""
    brief = flagship_brief

    assert brief.run_manifest.get("reuse_source") == REUSE_SOURCE
    assert brief.run_manifest.get("max_hypotheses") == config.MAX_HYPOTHESES
    assert brief.run_manifest.get("abstract_cap") == config.ABSTRACT_CAP
    assert brief.run_manifest.get("temperature") == 0


def test_flagship_default_model_ids_are_real_registry_known_ids(flagship_brief: TransBrief) -> None:
    """Guards KICKOFF.md non-negotiable rule 8: never bare claude-sonnet/
    claude-haiku or the retired claude-sonnet-4-20250514."""
    brief = flagship_brief

    assert brief.run_manifest.get("model_reasoning") == "claude-sonnet-4-6"
    assert brief.run_manifest.get("model_cheap") == "claude-haiku-4-5-20251001"
