"""Phase 1 acceptance test (KICKOFF.md Phase 1 / BUILD_SPEC.md §4).

Asserts ``run_transbench(observation)`` returns a schema-valid ``TransBrief``
for all 3 ``tests/fixtures.py`` demo observations (flagship + 2 backups), and
that the Phase 1 stub carries the fixed disclaimer + a non-empty
``run_manifest``. No real LLM/retrieval calls happen yet (Phase 2+ wires
agents 1-8) — this only proves the schema contract end to end.

Uses plain ``asyncio.run(...)`` inside ordinary ``def test_...()`` functions
(rather than ``@pytest.mark.asyncio``) so this file needs zero pytest-asyncio
configuration (no ``asyncio_mode`` ini setting required).
"""
from __future__ import annotations

import asyncio

import pytest

from tests.fixtures import DEMO_OBSERVATIONS
from transbench.engine import run_transbench
from transbench.schemas import TransBrief

EXPECTED_DISCLAIMER = "Research hypothesis generation only. Not clinical, diagnostic, or prescribing advice."


@pytest.mark.parametrize(
    "fixture",
    DEMO_OBSERVATIONS,
    ids=[f["name"] for f in DEMO_OBSERVATIONS],
)
def test_run_transbench_returns_valid_transbrief(fixture: dict) -> None:
    brief = asyncio.run(
        run_transbench(fixture["observation"], focus_drug=fixture.get("focus_drug"))
    )
    assert isinstance(brief, TransBrief)
    # Re-validate from a plain dict (as the MCP tool will send it, Phase 6's
    # `model_dump()`) to prove the *shape* is schema-valid, not just that this
    # particular live Python object happens to satisfy its own class.
    revalidated = TransBrief.model_validate(brief.model_dump())
    assert revalidated == brief
    assert brief.request_echo == fixture["observation"]


def test_stub_carries_disclaimer_and_run_manifest() -> None:
    fixture = DEMO_OBSERVATIONS[0]
    brief = asyncio.run(run_transbench(fixture["observation"]))

    assert brief.disclaimer == EXPECTED_DISCLAIMER
    assert isinstance(brief.run_manifest, dict)
    assert brief.run_manifest  # non-empty
    assert brief.request_echo == fixture["observation"]


def test_run_manifest_reports_reuse_source_and_caps() -> None:
    """Sanity: the stub's manifest records which reuse path served the run
    (installed_iatronix under Path A — see reuse.py / PLAN.md) and the
    BUILD_SPEC.md caps, so later phases can rely on these keys existing."""
    from transbench import config
    from transbench.reuse import REUSE_SOURCE

    fixture = DEMO_OBSERVATIONS[0]
    brief = asyncio.run(run_transbench(fixture["observation"]))

    assert brief.run_manifest.get("reuse_source") == REUSE_SOURCE
    assert brief.run_manifest.get("max_hypotheses") == config.MAX_HYPOTHESES
    assert brief.run_manifest.get("abstract_cap") == config.ABSTRACT_CAP
    assert brief.run_manifest.get("temperature") == 0


def test_default_model_ids_are_real_registry_known_ids() -> None:
    """Guards KICKOFF.md non-negotiable rule 8: never bare claude-sonnet/
    claude-haiku or the retired claude-sonnet-4-20250514."""
    fixture = DEMO_OBSERVATIONS[0]
    brief = asyncio.run(run_transbench(fixture["observation"]))

    assert brief.run_manifest.get("model_reasoning") == "claude-sonnet-4-6"
    assert brief.run_manifest.get("model_cheap") == "claude-haiku-4-5-20251001"
