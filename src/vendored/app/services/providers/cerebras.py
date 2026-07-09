"""Cerebras adapter.

client_kind=openai_compatible (base_url from registry) -> ChatOpenAI.
cache_class=auto_prefix: the server auto-caches a stable prompt prefix with no
request flag, so ``apply_cache`` is a no-op — the pipeline must keep the static
prefix byte-identical and leading (INTEGRATION_NOTES §C / D2). The generic
``read_cache_usage`` already reads ``prompt_tokens_details.cached_tokens``.

NOTE (Phase 4 / call-site guard): Cerebras rejects frequency_penalty /
presence_penalty / logit_bias with a 400 — callers must not pass them.
"""

from __future__ import annotations

from vendored.app.services.providers.base import ProviderAdapter


class CerebrasAdapter(ProviderAdapter):
    # Generic build_client + auto_prefix (no-op apply_cache) + generic cache-usage
    # reader are all correct for Cerebras today. Subclass exists as the home for
    # the Phase 4 prefix-cache assertions and the penalty-param guard.
    pass
