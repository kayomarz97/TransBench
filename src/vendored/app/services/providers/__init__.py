"""Provider adapters — one common interface over every LLM provider.

The adapter hides per-provider differences (client class, base_url, model
resolution, and — from Phase 4 — caching strategy) behind a single contract,
all driven by ``config/providers.yaml`` via ``provider_registry``.

Public API:
    get_adapter(provider_id) -> ProviderAdapter
    resolve_provider(model_id, user_provider=None) -> str   # registry-first routing
"""

from __future__ import annotations

from vendored.app.services.provider_registry import get_registry
from vendored.app.services.providers.base import (
    ProviderAdapter,
    PromptBlocks,
    CacheUsage,
)
from vendored.app.services.providers.anthropic import AnthropicAdapter
from vendored.app.services.providers.cerebras import CerebrasAdapter

# Provider ids with a dedicated subclass. Everything else uses the generic
# ProviderAdapter (correct for client construction; caching specialised later).
_ADAPTERS: dict[str, type[ProviderAdapter]] = {
    "anthropic": AnthropicAdapter,
    "cerebras": CerebrasAdapter,
}


def get_adapter(provider_id: str) -> ProviderAdapter:
    """Return the adapter for a provider id (generic adapter if no dedicated subclass)."""
    cls = _ADAPTERS.get(provider_id, ProviderAdapter)
    return cls(provider_id)


# Legacy provider-name aliases -> canonical registry ids (defensive; the codebase
# already uses these canonical ids, but callers may pass variants).
_PROVIDER_ALIASES = {"google": "gemini", "grok": "xai", "claude": "anthropic"}


def _legacy_prefix_provider(model_id: str) -> str:
    """Fallback routing for model ids NOT present in the registry (arbitrary BYOK ids).

    Mirrors the historical prefix heuristic so unknown models still route sanely.
    The ``gpt-oss`` check MUST precede ``gpt-`` (gpt-oss is Cerebras-exclusive).
    """
    if "/" in model_id:
        return "openrouter"
    if model_id.startswith("gemini") or model_id.startswith("models/gemini"):
        return "gemini"
    if (
        model_id.startswith("gpt-oss")
        or model_id.startswith("llama")
        or model_id.startswith("qwen")
        or model_id.startswith("mistral")
    ):
        return "cerebras"
    if model_id.startswith("grok"):
        return "xai"
    if model_id.startswith("gpt-") or model_id.startswith("o1") or model_id.startswith("o3"):
        return "openai"
    return "anthropic"


def resolve_provider(model_id: str, user_provider: str | None = None) -> str:
    """Resolve the provider id for a request.

    Priority: explicit ``user_provider`` (aliased) -> registry model ownership ->
    legacy prefix heuristic. Returns a canonical registry provider id.
    """
    if user_provider:
        return _PROVIDER_ALIASES.get(user_provider, user_provider)
    reg = get_registry()
    owner = reg.provider_for_model(model_id or "")
    if owner:
        return owner
    return _legacy_prefix_provider(model_id or "")


__all__ = [
    "ProviderAdapter",
    "PromptBlocks",
    "CacheUsage",
    "AnthropicAdapter",
    "CerebrasAdapter",
    "get_adapter",
    "resolve_provider",
]
