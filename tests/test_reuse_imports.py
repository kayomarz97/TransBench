"""Phase 0 reuse smoke test.

Confirms Path A (BUILD_SPEC §2): the Iatronix backend, installed editable and
`--no-deps` into this repo's venv, exposes every DB-free leaf function the
TransBench engine reuses -- with NO env vars set and NO live DB/redis at
import time. Only DB-free leaves are imported here; `run_search_graph`,
`semantic_cache`, and `vector_search` are intentionally never imported
(BUILD_SPEC §0.3 / KICKOFF non-negotiable rule 3) because they require
pgvector/redis.

Run inside the transbench venv:
    PYTHONDONTWRITEBYTECODE=1 /root/projects/transbench/.venv/bin/python -m pytest -q tests/test_reuse_imports.py
"""

from __future__ import annotations

import os


def test_no_env_vars_required_at_import() -> None:
    """Guard the guard: fail loudly (not silently pass) if some other process
    leaked credentials/DB/redis env vars into this test session -- the whole
    point of this smoke test is proving the reused leaves need NONE of them.
    """
    unexpected = [
        k
        for k in (
            "DATABASE_URL",
            "REDIS_URL",
            "ENCRYPTION_KEY",
            "ANTHROPIC_API_KEY",
            "SENTRY_DSN",
        )
        if os.environ.get(k)
    ]
    assert not unexpected, f"env vars set that the import-time proof assumes absent: {unexpected}"


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
