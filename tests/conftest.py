"""tests/conftest.py — shared pytest session infrastructure (Phase 7,
KICKOFF.md Phase 7 / BUILD_SPEC.md §0.1, §9).

Two things live here, both purely to make the FULL suite (`pytest -q tests/`)
correct AND affordable to actually run, without changing what any test
asserts:

1. ``pytest_sessionstart`` captures the Iatronix baseline
   (``git -C <IATRONIX_PATH> status --porcelain``) exactly ONCE, at the TRUE
   start of the whole pytest session — before any test/fixture/collection
   runs, and REGARDLESS of which test file pytest happens to collect/execute
   first. This is what BUILD_SPEC.md §0.1 literally asks for ("snapshot ...
   at the start of the test session") and is what makes
   ``tests/test_iatronix_untouched.py`` correct independent of test
   ordering: without this, a test file that merely HAPPENS to run before
   ``test_iatronix_untouched.py`` (alphabetically, ``test_agents_phase2.py``
   does) could already have exercised the engine by the time
   ``test_iatronix_untouched.py`` took its own "before" snapshot, silently
   hiding a real regression behind an already-dirtied baseline.

2. ``flagship_brief`` — a SESSION-scoped fixture wrapping exactly ONE real,
   live, full 8-agent-pipeline ``run_transbench(FLAGSHIP_OBSERVATION)`` call,
   shared by every test in the suite that needs a real flagship
   ``TransBrief`` (KICKOFF.md Phase 7 / this phase's explicit spend
   directive: "A full flagship run is ~13 LLM calls; keep total NEW live
   runs to ≤2-3"). Before this fixture existed, FOUR separate acceptance
   tests (``test_agents_phase2.py``, ``test_retrieval_phase3.py``,
   ``test_experiment_phase5.py``'s flagship test, and
   ``test_schema.py``'s flagship-fixture assertions, the latter across FOUR
   separate test functions) each independently called
   ``run_transbench(FLAGSHIP_OBSERVATION)`` fresh — 6+ redundant ~13-call
   live runs of the IDENTICAL input for the SAME suite. This fixture
   collapses all of that into ONE live call per pytest session; every
   consumer gets the exact same already-computed ``TransBrief`` object.
   Skips cleanly (not fails) without a working ``ANTHROPIC_API_KEY``, same
   convention as every other live test in this suite.

Neither of these changes what is asserted anywhere — only how a test obtains
the thing it asserts on.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import subprocess
from typing import Optional

import anthropic
import pytest

from tests.fixtures import FLAGSHIP_OBSERVATION
from transbench.engine import run_transbench
from transbench.schemas import TransBrief

# ---------------------------------------------------------------------------
# 1. Iatronix session baseline (BUILD_SPEC.md §0.1: "baseline-diff, not
#    assert-empty" — snapshot dynamically, never hardcode the expected
#    untracked-file list, since Iatronix legitimately carries unrelated
#    untracked files that can change over time for reasons having nothing to
#    do with TransBench).
# ---------------------------------------------------------------------------

# Overridable for portability (mirrors how every other absolute path in this
# repo's docs is presented as "adjust to reality" — KICKOFF.md/BUILD_SPEC.md);
# defaults to the path this whole project has used throughout Phases 0-6.
IATRONIX_PATH: str = os.environ.get("IATRONIX_PATH", "/root/projects/med-ai-project")


@dataclasses.dataclass(frozen=True)
class IatronixSnapshot:
    """One point-in-time read of the Iatronix repo's git state.

    ``porcelain`` is ``None`` iff the snapshot itself could not be captured
    (e.g. ``git`` not on PATH, or ``IATRONIX_PATH`` doesn't exist) — this is
    surfaced as a clear, loud test failure (via
    :func:`tests.test_iatronix_untouched`'s own assertions), never silently
    treated as "clean".
    """

    porcelain: Optional[str]
    error: Optional[str] = None


def _capture_iatronix_snapshot() -> IatronixSnapshot:
    try:
        result = subprocess.run(
            ["git", "-C", IATRONIX_PATH, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return IatronixSnapshot(porcelain=None, error=f"{type(exc).__name__}: {exc}")
    if result.returncode != 0:
        return IatronixSnapshot(
            porcelain=None,
            error=f"git status --porcelain exited {result.returncode}: {result.stderr.strip()}",
        )
    return IatronixSnapshot(porcelain=result.stdout)


# Populated by pytest_sessionstart below — the TRUE, ordering-independent
# "start of the test session" baseline BUILD_SPEC.md §0.1 asks for.
_SESSION_START_IATRONIX_SNAPSHOT: Optional[IatronixSnapshot] = None


def pytest_sessionstart(session: "pytest.Session") -> None:  # noqa: ARG001 -- pytest hook signature
    """Core pytest hook: fires exactly once, immediately after the Session
    object is created and strictly BEFORE collection or any
    fixture/test runs — for ANY invocation of pytest against this
    directory, regardless of which specific test files/functions are
    selected. This guarantee (not test-file alphabetical ordering, which is
    an implementation detail, not a contract) is what makes the Iatronix
    baseline in this module trustworthy as a true pre-session snapshot.
    """
    global _SESSION_START_IATRONIX_SNAPSHOT
    _SESSION_START_IATRONIX_SNAPSHOT = _capture_iatronix_snapshot()


def session_start_iatronix_snapshot() -> IatronixSnapshot:
    """Accessor for the true session-start baseline, for
    ``tests/test_iatronix_untouched.py``. Raises (loudly, not silently) if
    somehow read before ``pytest_sessionstart`` ran — should be unreachable
    under a normal pytest invocation."""
    if _SESSION_START_IATRONIX_SNAPSHOT is None:
        raise RuntimeError(
            "session_start_iatronix_snapshot() was read before pytest_sessionstart "
            "populated it -- this should be unreachable under a normal pytest run."
        )
    return _SESSION_START_IATRONIX_SNAPSHOT


# ---------------------------------------------------------------------------
# 2. Shared retry-once-on-transient-Anthropic-500 helper (BUILD_SPEC.md's own
#    "Anthropic is 500-flaky today" operating reality for this phase).
#    Deduplicated from what was, before this file, THREE near-identical
#    private copies (``test_retrieval_phase3.py``, ``test_experiment_phase5.py``,
#    and — implicitly, via its complete ABSENCE — a gap in
#    ``test_agents_phase2.py``/``test_schema.py``, which had NO retry
#    protection at all). A non-transient exception, or a SECOND consecutive
#    failure of any kind, still propagates and fails the caller — this must
#    never mask a real regression, only real Anthropic infra flakiness.
# ---------------------------------------------------------------------------

_TRANSIENT_EXCEPTION_TYPES = (anthropic.APIConnectionError, anthropic.InternalServerError)


def is_transient_anthropic_failure(exc: BaseException) -> bool:
    """True iff ``exc`` looks like a transient Anthropic-side failure (a bare
    500 / connection drop / overload / empty-completion 502) rather than a real
    bug. Retry-once semantics mean a PERSISTENT failure still propagates and
    fails the caller — this only smooths genuinely-transient model/infra
    hiccups (incl. an occasional empty/unparseable completion surfacing as
    ``[502 llm_bad_json]``), it never masks a real regression."""
    if isinstance(exc, _TRANSIENT_EXCEPTION_TYPES):
        return True
    if getattr(exc, "status_code", None) in (500, 502):
        return True
    message = str(exc).lower()
    return (
        "internal server error" in message
        or "overloaded" in message
        or " 500 " in f" {message} "
        or " 502 " in f" {message} "
        or "llm_bad_json" in message
    )


def run_transbench_live_with_retry(observation: str, **kwargs: object) -> TransBrief:
    """``asyncio.run(run_transbench(observation, **kwargs))``, retried
    EXACTLY ONCE if the first attempt's failure looks transient (see
    :func:`is_transient_anthropic_failure`)."""
    try:
        return asyncio.run(run_transbench(observation, **kwargs))  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 -- narrowed immediately below
        if not is_transient_anthropic_failure(exc):
            raise
        return asyncio.run(run_transbench(observation, **kwargs))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 3. The shared flagship fixture.
# ---------------------------------------------------------------------------


def _print_flagship_summary(brief: TransBrief) -> None:
    """Best-effort, human-readable summary of the ONE live flagship run,
    printed to stdout so it is visible in a ``pytest -s`` (or captured-on
    -failure) run for the Phase 7 report (run_manifest: models/temps/PMIDs/
    timestamps/token spend — KICKOFF.md Phase 7). Never raises — a
    formatting hiccup here must never fail the fixture/tests that depend on
    it; the REAL assertions live in the consuming tests, not in this print.
    """
    try:
        rm = brief.run_manifest
        print("\n" + "=" * 78)
        print("SHARED flagship_brief fixture: ONE live run_transbench(FLAGSHIP) call")
        print("=" * 78)
        print(f"model_reasoning={rm.get('model_reasoning')} model_cheap={rm.get('model_cheap')} "
              f"temperature={rm.get('temperature')}")
        print(f"reuse_source={rm.get('reuse_source')}")
        print(f"run_started_at={rm.get('run_started_at')} generated_at={rm.get('generated_at')}")
        print(f"token_spend={rm.get('token_spend')}")
        print(f"selected_experiment_hypothesis_id={rm.get('selected_experiment_hypothesis_id')} "
              f"dataset_pointer={rm.get('dataset_pointer')}")
        snapshot = rm.get("retrieval_snapshot") or {}
        for hyp_id, entry in snapshot.items():
            pmids = [a.get("pmid") for a in (entry.get("abstracts") or [])]
            print(f"  hypothesis {hyp_id}: neutral_query={entry.get('neutral_query')!r} "
                  f"pubmed_query={entry.get('pubmed_query')!r} pmids={pmids}")
        print("=" * 78 + "\n")
    except Exception:  # noqa: BLE001 -- diagnostics only, must never break the fixture
        pass


@pytest.fixture(scope="session")
def flagship_brief() -> TransBrief:
    """Exactly ONE real, live, full 8-agent-pipeline
    ``run_transbench(FLAGSHIP_OBSERVATION)`` call for the entire pytest
    session (retried once on a transient Anthropic 500 — see module
    docstring). ``scope="session"`` + pytest's own fixture caching means the
    body below executes at most once no matter how many test functions
    across however many files request it; every consumer receives the
    IDENTICAL already-computed ``TransBrief`` object.

    Skips (not fails) without a working ``ANTHROPIC_API_KEY`` — every
    consumer inherits this skip automatically via pytest's normal
    fixture-skip propagation, matching the ``pytestmark =
    pytest.mark.skipif(...)`` convention every other live test in this suite
    already uses.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip(
            "flagship_brief requires a working ANTHROPIC_API_KEY for a real, live "
            "full-pipeline run_transbench(FLAGSHIP_OBSERVATION) call."
        )
    brief = run_transbench_live_with_retry(FLAGSHIP_OBSERVATION)
    _print_flagship_summary(brief)
    return brief
