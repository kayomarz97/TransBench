"""tests/test_iatronix_untouched.py — THE Iatronix-safety guard (KICKOFF.md
Phase 7 / non-negotiable rule 1; BUILD_SPEC.md §0.1). MOST IMPORTANT test in
this repo.

**Baseline-diff, not assert-empty.** Iatronix (``IATRONIX_PATH`` —
``/root/projects/med-ai-project`` by default, see ``tests/conftest.py``)
already carries unrelated untracked files (as of writing: two files under
``plans/``, unrelated to TransBench) — an absolute-"``git status --porcelain``
must be empty" assertion would FALSE-FAIL on day one, for reasons that have
nothing to do with whether TransBench actually touched anything. The correct
guard instead: snapshot ``git -C <IATRONIX_PATH> status --porcelain`` at the
TRUE start of the pytest session (``tests/conftest.py``'s
``pytest_sessionstart`` hook — ordering-independent, unlike a snapshot taken
inside this test function itself, which could run after some OTHER test file
already exercised the engine), exercise the engine, then assert the porcelain
output is BYTE-IDENTICAL to that baseline (no NEW delta, in either
direction — a file disappearing from the untracked list would be just as
real a violation as a new one appearing) AND that
``git -C <IATRONIX_PATH> diff --quiet`` exits 0 (no tracked-file edits).
Hard-fails (never skips-as-if-passing) on any new delta.

Run under ``PYTHONDONTWRITEBYTECODE=1`` (required so the editable-installed
Iatronix backend's import never drops ``.pyc``/``__pycache__`` into the
read-only Iatronix tree — BUILD_SPEC.md §0.1):

    PYTHONDONTWRITEBYTECODE=1 /root/projects/transbench/.venv/bin/python -m pytest -q tests/test_iatronix_untouched.py

Two complementary tests:

1. ``test_engine_run_leaves_iatronix_untouched`` — the PRIMARY, strongest
   form: depends on the shared ``flagship_brief`` fixture (``tests/conftest.py``),
   i.e. a REAL, live, full 8-agent-pipeline ``run_transbench(...)`` call
   (literally "a real ... run_transbench", per this phase's own brief) —
   costing ZERO incremental spend beyond the ONE flagship run every other
   live test in this suite already shares (``tests/conftest.py``'s module
   docstring). Skips cleanly without ``ANTHROPIC_API_KEY`` (matches every
   sibling live test's convention) — see test 2 for the unconditional
   companion that never skips.
2. ``test_reuse_seam_calls_leave_iatronix_untouched`` — NEVER skips (needs no
   API key, no network): directly exercises the SAME DB-free reuse-seam leaf
   functions the engine calls at runtime (``build_article_registry``,
   ``rank_article_list`` — BUILD_SPEC.md §2) against synthetic in-memory
   data, with its own self-contained local before/after snapshot. Guarantees
   the Iatronix-safety property is checked EVERY run, regardless of whether
   Anthropic credentials happen to be configured.
"""
from __future__ import annotations

import subprocess

from tests.conftest import IATRONIX_PATH, session_start_iatronix_snapshot
from transbench.reuse import EvidenceFetchResult, FetchedData, build_article_registry, rank_article_list
from transbench.schemas import TransBrief


def _current_iatronix_porcelain() -> str:
    result = subprocess.run(
        ["git", "-C", IATRONIX_PATH, "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"'git -C {IATRONIX_PATH} status --porcelain' itself failed "
        f"(exit={result.returncode}): {result.stderr.strip()}"
    )
    return result.stdout


def _assert_tracked_tree_clean(context: str) -> None:
    """``git -C <IATRONIX_PATH> diff --quiet`` — exit 0 iff there are no
    edits to TRACKED files (BUILD_SPEC.md §0.1's second, complementary
    check; the porcelain-status comparison alone would miss an in-place edit
    to a file git already tracks, since that edit doesn't add a NEW
    untracked-file line to ``--porcelain`` — it shows a distinct existing
    line's status character change instead, which the line-set comparison
    below also independently catches, but this is the direct, canonical
    check for exactly this failure mode)."""
    result = subprocess.run(["git", "-C", IATRONIX_PATH, "diff", "--quiet"], timeout=30)
    assert result.returncode == 0, (
        f"git -C {IATRONIX_PATH} diff --quiet exited {result.returncode} ({context}) -- "
        f"Iatronix has tracked-file edits. Iatronix must NEVER be modified (KICKOFF.md "
        f"non-negotiable rule 1)."
    )


def _describe_new_delta(baseline_porcelain: str, current_porcelain: str) -> str:
    """A precise, line-level diagnostic for a test-failure message: which
    ``git status --porcelain`` lines are NEW (present now, absent from the
    session-start baseline) and which DISAPPEARED (present at baseline,
    absent now — e.g. a baseline untracked file that got deleted or
    unexpectedly staged/committed) — either direction is a real violation of
    "no new delta"."""
    baseline_lines = set(baseline_porcelain.splitlines())
    current_lines = set(current_porcelain.splitlines())
    added = sorted(current_lines - baseline_lines)
    removed = sorted(baseline_lines - current_lines)
    parts = []
    if added:
        parts.append(f"NEW lines (not in the session-start baseline): {added}")
    if removed:
        parts.append(f"MISSING lines (present at baseline, gone now): {removed}")
    return "; ".join(parts) if parts else "(no line-level delta detected -- see raw porcelain output above)"


def test_engine_run_leaves_iatronix_untouched(flagship_brief: TransBrief) -> None:
    """PRIMARY guard. ``flagship_brief`` (``tests/conftest.py``) is a REAL
    live full-pipeline engine run — by the time this test body executes, the
    engine has genuinely: imported the editable-installed Iatronix backend;
    called ``create_llm``, ``neutralize_query``, ``fetch_evidence_data``,
    ``build_article_registry``, ``rank_article_list``, ``grounding_stats``/
    ``strip_ungrounded``, ``has_minimum_evidence``/``ensure_evidence``, and
    ``validate_citations`` for real, for up to 3 hypotheses. This is the
    strongest exercise of "does running the engine ever write into the
    Iatronix tree" this suite can offer.

    Compares against ``session_start_iatronix_snapshot()`` — the TRUE
    beginning-of-pytest-session baseline (``tests/conftest.py``'s
    ``pytest_sessionstart`` hook, which fires before ANY test/fixture in
    this whole run) — not a baseline taken locally inside this function,
    which could already be post-engine-activity if some other test file
    happened to consume ``flagship_brief`` first (pytest's default
    collection order runs ``test_agents_phase2.py`` etc. before this file
    alphabetically). This ordering-independence is exactly why the baseline
    lives in ``conftest.py``'s session hook rather than here.
    """
    baseline = session_start_iatronix_snapshot()
    assert baseline.porcelain is not None, (
        f"Could not capture the Iatronix baseline at session start: {baseline.error}. "
        f"This guard cannot run without a readable baseline -- fix IATRONIX_PATH "
        f"({IATRONIX_PATH}) / git availability rather than treating this as a pass."
    )

    assert isinstance(flagship_brief, TransBrief)  # the engine genuinely ran (fixture's own contract)

    current = _current_iatronix_porcelain()
    assert current == baseline.porcelain, (
        f"Iatronix ({IATRONIX_PATH}) gained/lost a git-status delta after exercising the "
        f"TransBench engine -- Iatronix must NEVER be modified (KICKOFF.md non-negotiable "
        f"rule 1). {_describe_new_delta(baseline.porcelain, current)}\n"
        f"baseline (session start):\n{baseline.porcelain!r}\n"
        f"current (after the engine run):\n{current!r}"
    )
    _assert_tracked_tree_clean("after the flagship engine run")


def test_reuse_seam_calls_leave_iatronix_untouched() -> None:
    """UNCONDITIONAL companion — never skips (no ``ANTHROPIC_API_KEY``, no
    network needed at all). Directly calls the SAME DB-free reuse-seam leaf
    functions (``transbench.reuse.build_article_registry``,
    ``transbench.reuse.rank_article_list`` — BUILD_SPEC.md §2) the engine
    calls at runtime, against synthetic in-memory data, with its OWN local
    before/after snapshot (self-contained; does not need the session-start
    baseline, since it takes both ends of its comparison itself, immediately
    around its own calls).

    Purpose: the Iatronix-untouched guarantee should not be contingent on
    Anthropic credentials being configured — reading/importing/calling the
    editable-installed Iatronix package is what could theoretically write
    into its tree (e.g. an errant cache/log write), and that risk exists
    independent of whether the LLM-calling agents ever run.
    """
    before = _current_iatronix_porcelain()

    abstracts = [
        {
            "pmid": "18772860",
            "title": "Synthetic test abstract for the Iatronix-untouched guard",
            "abstract": "Synthetic abstract text -- no real network call.",
            "year": 2010,
            "journal": "J Synthetic",
            "pub_types": ["Journal Article"],
        }
    ]
    merged = EvidenceFetchResult(
        clinical_trial_abstracts=abstracts,
        systematic_review_abstracts=[],
        guideline_abstracts=[],
        fetch_success=True,
    )
    fd = FetchedData(query_type="evidence", evidence_data=merged)
    registry = build_article_registry(fd)
    assert registry.by_pmid.get("18772860") is not None  # the calls genuinely ran, not a no-op
    ranked = rank_article_list(abstracts, entities=["hypertension"], query_text="hypertension")
    assert ranked

    after = _current_iatronix_porcelain()
    assert after == before, (
        f"Iatronix ({IATRONIX_PATH}) gained/lost a git-status delta after calling the "
        f"DB-free reuse-seam leaves directly (no API key/network involved) -- Iatronix "
        f"must NEVER be modified (KICKOFF.md non-negotiable rule 1). "
        f"{_describe_new_delta(before, after)}"
    )
    _assert_tracked_tree_clean("after direct reuse-seam calls")
