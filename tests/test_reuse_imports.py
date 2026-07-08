"""Phase 0 reuse smoke test.

Confirms Path A (BUILD_SPEC §2): the Iatronix backend, installed editable and
`--no-deps` into this repo's venv, exposes every DB-free leaf function the
TransBench engine reuses -- with NO env vars required at import time (and no
live DB/redis touched either). Only DB-free leaves are imported here;
`run_search_graph`, `semantic_cache`, and `vector_search` are intentionally
never imported (BUILD_SPEC §0.3 / KICKOFF non-negotiable rule 3) because they
require pgvector/redis.

`test_no_env_vars_required_at_import` proves the "no env var required at
import" claim by importing the leaves in a **subprocess with a scrubbed
environment**, not by asserting anything about this process's own ambient
env. `ANTHROPIC_API_KEY` is a legitimate, normally-set BYOK *runtime* var
(BUILD_SPEC §0.4, `.env.example`) -- it has nothing to do with *import-time*
requirements, so asserting it's absent from the invoking shell would be
wrong (and was the Phase 0 bug: it made this file fail under any shell that
already has a real key exported, which is the normal case, not scrub it).
This file passes regardless of whether `ANTHROPIC_API_KEY` (or
`DATABASE_URL`/`REDIS_URL`/`ENCRYPTION_KEY`/`SENTRY_DSN`) happen to be set in
the invoking shell.

Run inside the transbench venv (passes with any ambient env, e.g. with a real
`ANTHROPIC_API_KEY` exported for later phases' end-to-end runs):
    PYTHONDONTWRITEBYTECODE=1 /root/projects/transbench/.venv/bin/python -m pytest -q tests/test_reuse_imports.py
"""

from __future__ import annotations

import os
import subprocess
import sys


def test_no_env_vars_required_at_import() -> None:
    """Import every DB-free leaf in a **child process whose environment is
    scrubbed** (no `DATABASE_URL`/`REDIS_URL`/`ENCRYPTION_KEY`/
    `ANTHROPIC_API_KEY`/`SENTRY_DSN` -- not even inherited from this process)
    and assert it exits 0. This is the correct way to prove "no env var is
    required at import time": it does not care, and makes no assertion about,
    what env vars this pytest process itself happens to be running under.
    """
    import_snippet = (
        "from app.services.data_fetcher import ("
        "EvidenceFetchResult, FetchedData, fetch_drug_data, fetch_evidence_data, "
        "init_http_client, shutdown_http_client)\n"
        "from app.services.article_registry import build_article_registry\n"
        "from app.services.ranking import rank_article_list\n"
        "from app.services.grounding_gate import grounded_ratio, grounding_stats, strip_ungrounded\n"
        "from app.services.evidence_floor import EvidenceFloorError, ensure_evidence, has_minimum_evidence\n"
        "from app.services.citation_validator import validate_citations\n"
        "from app.services.llm_factory import create_llm\n"
        "from app.services.stance_neutralizer import neutralize_query\n"
        "from app.services.providers import resolve_provider\n"
        "assert resolve_provider('claude-sonnet-4-6') == 'anthropic'\n"
        "assert resolve_provider('claude-haiku-4-5-20251001') == 'anthropic'\n"
        "print('SCRUBBED_IMPORT_OK')\n"
    )

    # Deliberately minimal: only PATH (to locate shared libs/tools the
    # interpreter itself may need) and PYTHONDONTWRITEBYTECODE=1 (Iatronix
    # baseline-diff guard, BUILD_SPEC §0.1). No DATABASE_URL, REDIS_URL,
    # ENCRYPTION_KEY, ANTHROPIC_API_KEY, or SENTRY_DSN passed through.
    scrubbed_env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}

    result = subprocess.run(
        [sys.executable, "-c", import_snippet],
        env=scrubbed_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0 and "SCRUBBED_IMPORT_OK" in result.stdout, (
        "reuse leaves failed to import in a scrubbed subprocess environment "
        "(no DATABASE_URL/REDIS_URL/ENCRYPTION_KEY/ANTHROPIC_API_KEY/SENTRY_DSN):\n"
        f"returncode={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    )


def test_data_fetcher_imports() -> None:
    from app.services.data_fetcher import (
        EvidenceFetchResult,
        FetchedData,
        fetch_drug_data,
        fetch_evidence_data,
        init_http_client,
        shutdown_http_client,
    )

    assert callable(fetch_evidence_data)
    assert callable(fetch_drug_data)
    assert callable(init_http_client)
    assert callable(shutdown_http_client)
    assert FetchedData is not None
    assert EvidenceFetchResult is not None


def test_article_registry_imports() -> None:
    from app.services.article_registry import build_article_registry

    assert callable(build_article_registry)


def test_ranking_imports() -> None:
    from app.services.ranking import rank_article_list

    assert callable(rank_article_list)


def test_grounding_gate_imports() -> None:
    from app.services.grounding_gate import (
        grounded_ratio,
        grounding_stats,
        strip_ungrounded,
    )

    assert callable(grounding_stats)
    assert callable(strip_ungrounded)
    assert callable(grounded_ratio)


def test_evidence_floor_imports() -> None:
    from app.services.evidence_floor import (
        EvidenceFloorError,
        ensure_evidence,
        has_minimum_evidence,
    )

    assert callable(has_minimum_evidence)
    assert callable(ensure_evidence)
    assert issubclass(EvidenceFloorError, Exception)


def test_citation_validator_imports() -> None:
    from app.services.citation_validator import validate_citations

    assert callable(validate_citations)


def test_llm_factory_imports() -> None:
    # Needs fastapi (HTTPException) -- curated into the dependency set (BUILD_SPEC §1).
    from app.services.llm_factory import create_llm

    assert callable(create_llm)


def test_stance_neutralizer_imports() -> None:
    # Needs fastapi transitively via llm_factory -- curated into the dependency set.
    from app.services.stance_neutralizer import neutralize_query

    assert callable(neutralize_query)


def test_resolve_provider_maps_real_model_ids_to_anthropic() -> None:
    from app.services.providers import resolve_provider

    assert resolve_provider("claude-sonnet-4-6") == "anthropic"
    assert resolve_provider("claude-haiku-4-5-20251001") == "anthropic"
    # BYOK path: explicit user_provider="anthropic" (BUILD_SPEC §0.4) must also resolve cleanly.
    assert resolve_provider("claude-sonnet-4-6", user_provider="anthropic") == "anthropic"


def test_never_imports_db_or_redis_backed_leaves() -> None:
    """Documents (does not merely assert) the forbidden-import list from
    KICKOFF non-negotiable rule 3 / BUILD_SPEC §0.3. These are NOT imported
    anywhere in this test file -- this test just records why, so the ban is
    self-documenting instead of tribal knowledge.

    Parses this file's real AST (not a prose/string scan of the docstring --
    a naive substring check false-positives on wrapped prose that happens to
    contain the word "import") and asserts none of its actual `import` /
    `from ... import ...` statements reference the forbidden DB/redis-backed
    symbols.
    """
    import ast

    forbidden = {"run_search_graph", "semantic_cache", "vector_search"}
    with open(__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=__file__)

    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)

    hit = forbidden & imported_names
    assert not hit, f"{hit} must never be imported by TransBench -- they require pgvector/redis"
