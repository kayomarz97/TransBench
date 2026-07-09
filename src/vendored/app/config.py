from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database
    database_url: str = (
        "postgresql+asyncpg://medadmin:CHANGE_ME@iatronix-db:5432/medvectordb"
    )

    # Redis
    redis_url: str = "redis://iatronix-redis:6379/0"

    # Embedding & Vector search (BYOK — uses user's LLM key, no server-side key needed)
    embedding_dim: int = 768
    embedding_model: str = "text-embedding-3-small"  # OpenAI default; gemini uses text-embedding-004
    vector_search_enabled: bool = True
    vector_top_k: int = 5
    vector_min_similarity: float = 0.3

    # PDF upload
    max_pdf_size_bytes: int = 20_971_520  # 20MB
    chunk_size: int = 2000  # characters (~500 tokens)
    chunk_overlap: int = 400  # characters (~100 tokens)

    # Auto-indexing
    pubmed_vector_cache_enabled: bool = True

    # Semantic query cache (pgvector SWR)
    semantic_cache_enabled: bool = True
    semantic_cache_threshold: float = (
        0.92  # cosine similarity — 0.92 allows semantically similar queries to match
    )
    semantic_cache_swr_ttl_seconds: int = (
        604800  # 7 days — beyond this, revalidate in background
    )

    # BYOK
    byok_enabled: bool = True
    encryption_key: str = "CHANGE_ME"  # Fernet key for encrypting user LLM keys

    # CORS
    allowed_origins: str = "http://localhost:3100"

    # Sentry
    sentry_dsn: Optional[str] = None

    # Prompt versioning
    prompt_version: int = (
        4  # v4: grounding gate + disease-fetch crash fix — invalidates pre-fix cached answers
    )

    # Logging
    log_level: str = "INFO"

    # --- Limits (single source of truth) ---

    # Payload
    max_request_body_bytes: int = 65536  # 64KB
    max_query_length: int = 2000

    # Rate limiting — path-aware buckets
    rate_limit_ip_per_minute: int = 100
    rate_limit_key_per_minute: int = 10  # kept for backward-compat; unused in path-aware logic
    # General bucket (auth writes, documents, etc.)
    rate_limit_free_key_per_minute: int = 30   # raised from 20; queries/suggestions now in own buckets
    rate_limit_premium_key_per_minute: int = 60
    # Query bucket — /api/v1/query/* (LLM calls — the expensive resource to protect)
    rate_limit_query_free_per_minute: int = 10
    rate_limit_query_premium_per_minute: int = 30
    # Suggestions bucket — /api/v1/suggestions* (keystroke-driven autocomplete, never blocks queries)
    rate_limit_suggest_free_per_minute: int = 60
    rate_limit_suggest_premium_per_minute: int = 120

    # LLM
    llm_timeout_seconds: int = 90  # disease format at 6144 tokens needs ~55s on Sonnet
    llm_max_tokens: int = 4096
    # Low temperature: citation/extraction synthesis must be near-deterministic. The
    # provider default (~0.7-1.0) caused bimodal disease output (full answer vs empty
    # sections) on identical prompts. 0.2 stabilises grounding without flattening prose.
    llm_temperature: float = 0.2
    llm_retry_max_attempts: int = 1
    llm_retry_backoff_seconds: float = 2.0

    # Pipeline
    pipeline_timeout_seconds: int = 120
    proxy_timeout_seconds: int = 130

    # Cache TTL (seconds)
    cache_ttl_structured: int = 604800  # 7 days (reduced from 30 for medical data freshness)
    cache_ttl_general: int = 86400  # 24 hours

    # Circuit breaker
    cb_fail_max: int = 5
    cb_reset_timeout: int = 30

    # Async log queue
    log_queue_max_size: int = 1000
    log_db_retry_max: int = 2
    log_db_retry_backoff: float = 1.0

    # Response storage
    max_response_jsonb_bytes: int = 1048576  # 1MB
    truncated_response_bytes: int = 512000  # 500KB

    # Frontend display
    truncation_display_limit: int = 20

    # Drug linker
    drug_link_min_score: float = 0.90
    fuzzy_max_distance_short: int = 1  # words 5-8 chars
    fuzzy_max_distance_long: int = 2  # words >8 chars

    # Classifier
    classifier_confidence_threshold: float = 0.7

    # Model routing (RAG optimization)
    model_haiku: str = "claude-haiku-4-5-20251001"
    model_sonnet: str = "claude-sonnet-4-20250514"
    model_classify: str = "claude-haiku-4-5-20251001"
    model_generate: str = "claude-haiku-4-5-20251001"
    openai_default_model: str = "gpt-4o-mini"
    openrouter_default_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    openrouter_api_base: str = "https://openrouter.ai/api/v1"
    model_routing_enabled: bool = True

    # OpenRouter OAuth PKCE
    openrouter_oauth_url: str = "https://openrouter.ai/auth"
    openrouter_token_url: str = "https://openrouter.ai/api/v1/auth/keys"
    openrouter_callback_base: str = ""  # Set per-env in .env — e.g. https://med.debkay.com

    # Gemma 4 model routing — 3-model fallback chain
    openrouter_gemma_primary: str = "google/gemma-4-31b-it"
    openrouter_gemma_fallback: str = "google/gemma-4-26b-a4b-it:free"
    openrouter_meta_fallback: str = "meta-llama/llama-3.3-70b-instruct:free"

    # ── Cerebras BYOK (OpenAI-compatible, paid tier) ──────────────────────────────
    # Paid tier llama3.1-8b: 32,768 context, 2,000 req/min, 2M tokens/min
    # To change model if Cerebras updates: set CEREBRAS_DEFAULT_MODEL env var only
    cerebras_api_base: str = "https://api.cerebras.ai/v1"
    cerebras_default_model: str = "gpt-oss-120b"    # ← one-line change if Cerebras changes model
    # NOTE: No special token caps — paid tier 32,768 context fits full pipeline unchanged
    # Future free-tier toggle will add: cerebras_free_max_output: int = 2048
    #                                    cerebras_free_max_context_chars: int = 18000

    # BYOK-only: these fields are unused (kept for backward compat with .env files)
    # All LLM calls use the user's own key from the frontend Settings page
    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None

    # DSPy adaptive pipeline
    dspy_enabled: bool = True
    adaptive_second_pass_enabled: bool = True
    # Reconciled to True to match .env.example + docs (E2). NOTE: the branch that reads this
    # (rag_pipeline.py) only fires when prompt_mode == "generate", which is now never set —
    # the Evidence Floor is the active grounding gate. True is the fail-closed-stricter default.
    fail_closed_evidence_only: bool = True

    # KeyStore — where BYOK keys live. Postgres is authoritative; Firestore (Admin
    # SDK, server-side only) is an optional best-effort mirror for easy migration.
    # Flip keystore_primary to "firestore" once mirrored to switch read source.
    keystore_primary: str = "postgres"            # "postgres" | "firestore"
    keystore_firestore_enabled: bool = False      # dual-write to Firestore when True

    # Deep-grounded citation chasing (Phase 5). Bounds live in providers.yaml deep_search.
    # Default off until live-verified on dev; flip on to deepen thin retrieval.
    deep_search_enabled: bool = False

    # Parallel section agents — each section generated by an independent LLM call
    parallel_sections_enabled: bool = True
    parallel_sections_max_tokens: int = 8192  # per-section token budget
    parallel_bluf_max_tokens: int = 6144  # BLUF+titles+flowcharts+tables phase token budget
    parallel_sections_max_concurrent: int = 3  # max simultaneous LLM calls — keeps token/min under Anthropic limits

    # Resumable streaming jobs — query keeps running server-side even if the client
    # disconnects (mobile tab switch / screen off); the client reconnects and replays
    # missed events from the Redis event log (source of truth). When False, the legacy
    # connection-coupled streaming path is used (fully back-compatible). Dev true / prod false.
    resumable_stream_enabled: bool = False
    stream_job_ttl_seconds: int = 900             # how long a finished job's events stay replayable in Redis
    stream_job_max_runtime_seconds: int = 240     # hard cap so a detached producer can never run forever
    stream_job_idle_grace_seconds: int = 30       # tailer waits this long for the first event before giving up

    # Multi-variation retrieval (anti-sycophancy) — fetch using the DSPy search_variants
    # (phrasing-diverse forms) so the evidence base isn't anchored to one surface form.
    # Bounded, deduped, reuses existing merge/enrich plumbing. Dev true / prod false.
    multi_variation_search_enabled: bool = False
    multi_variation_max_variants: int = 2         # extra phrasings fetched per second pass

    # Per-section LangGraph re-fetch — when a section is still empty after LLM retries,
    # fetch targeted evidence for that section's topic and re-synthesize just that section.
    # Dev true / prod false.
    section_refetch_enabled: bool = False
    section_refetch_timeout_seconds: float = 10.0  # wall-clock cap for the per-section re-fetch graph

    # Citation token grounding — [REF_N] tokens for deterministic source attribution
    citation_ref_tokens_enabled: bool = True

    # Smart PubMed expansion + snowballing
    pubmed_expansion_enabled: bool = True
    snowball_enabled: bool = True
    snowball_max_refs: int = 15

    # External API fetching
    api_fetch_enabled: bool = True
    api_fetch_timeout_seconds: float = 20.0
    pubmed_api_key: Optional[str] = None
    openfda_api_key: Optional[str] = None
    nice_api_key: Optional[str] = None
    # Unpaywall requires user email — passed from authenticated user context at query time

    # Token budgets
    llm_max_tokens_format: int = 2048  # format mode — drug/procedure (Haiku)
    llm_max_tokens_format_drug_context: int = (
        6144  # drug-in-condition: synthesize drug + condition management guidelines
    )
    llm_max_tokens_format_evidence: int = (
        5120  # evidence queries need more for study tables
    )
    llm_max_tokens_format_procedure: int = (
        3072  # procedure format mode: needs room for 5+ steps + all sections
    )
    llm_max_tokens_format_disease: int = (
        8192  # format mode — disease (Sonnet, needs depth for path/dx/tx/complications)
    )
    llm_max_tokens_generate: int = (
        6144  # generate/fallback mode — disease/drug schemas need full depth
    )
    retry_on_sparse_enabled: bool = (
        True  # retry LLM call when response is critically sparse
    )

    # Stance Neutralization Layer — separate kill-switches
    stance_neutralizer_enabled: bool = True
    reference_filter_v2_enabled: bool = True

    # Evidence Floor — blocks ungrounded LLM "generate" mode answers
    # When True: every answer goes through format mode with ≥1 citable source, or returns no_evidence.
    # Set False only for emergency rollback if broadening loop misbehaves.
    evidence_floor_enabled: bool = True

    # Grounding Gate — guarantees the RENDERED answer is evidence-grounded, not training data.
    # After post-processing, ungrounded ("Expert opinion"/sourceless) claims are stripped; if too
    # few grounded claims remain the answer is replaced with the honest no_evidence terminal.
    # This is the structural backstop for silent upstream fetch failures (data_fetcher fails closed-silent).
    grounding_floor_enabled: bool = True
    grounding_floor_min_ratio: float = 0.40   # below this grounded-claim ratio → strip ungrounded
    grounding_floor_min_claims: int = 2        # fewer than this many grounded claims → honest card

    # Cloudflare R2 Storage (for PDF uploads)
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "iatronix-documents"
    r2_public_url: str = ""  # e.g. https://pub-xxx.r2.dev

    # PDF lifecycle (non-approved docs auto-deleted after N hours)
    pdf_non_approved_ttl_hours: int = 48
    pdf_cleanup_interval_minutes: int = 60

    # LLM cost estimates shown to users (USD per million tokens, Anthropic pricing)
    cost_haiku_input_per_m: float = 0.25
    cost_haiku_output_per_m: float = 1.25
    cost_sonnet_input_per_m: float = 3.0
    cost_sonnet_output_per_m: float = 15.0

    model_config = {"env_file": ".env", "extra": "ignore"}
    backend_version: str = "2.1"


settings = Settings()
