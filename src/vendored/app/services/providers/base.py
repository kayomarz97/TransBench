"""Base provider adapter — generic, registry-driven LLM client construction.

This concrete class is usable as-is for any provider (it builds the right
LangChain client from the registry ``client_kind`` + ``base_url``). Providers
that need bespoke behaviour (Anthropic error mapping, per-provider caching in
Phase 4) subclass it.

Caching methods (``prepare_cache`` / ``apply_cache`` / ``read_cache_usage`` /
``release_cache``) are no-ops here; Phase 4 overrides them per cache_class
(inline / auto_prefix / stateful / conditional). See INTEGRATION_NOTES §C.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from vendored.app.services.provider_registry import get_registry

logger = logging.getLogger(__name__)


@dataclass
class PromptBlocks:
    """The four ordered prompt segments the pipeline assembles.

    Order is load-bearing for auto_prefix caching: static_system -> data_block
    -> dynamic_system must stay byte-stable and leading (INTEGRATION_NOTES §C).
    """

    static_system: str = ""
    dynamic_system: str = ""
    data_block: str = ""
    user_text: str = ""


@dataclass
class CacheUsage:
    """Normalised cache accounting across providers."""

    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    uncached_input_tokens: int = 0


class ProviderAdapter:
    """Generic adapter. Construct via ``get_adapter(provider_id)``."""

    def __init__(self, provider_id: str):
        self.provider_id = provider_id
        self._reg = get_registry()
        self.meta: dict[str, Any] = self._reg.provider_meta(provider_id) or {}
        self.client_kind: str = self.meta.get("client_kind", "openai_compatible")
        self.base_url: Optional[str] = self.meta.get("base_url")
        self.cache_class: Optional[str] = self.meta.get("cache_class")

    # -- model resolution ----------------------------------------------------
    def resolve_model(self, model_id: str) -> str:
        """Pick the effective model id.

        Registry-driven replacement for the legacy ``"/"`` heuristics:
        - empty -> provider default
        - registry says it's mine, or it's unknown (BYOK passthrough) -> use it
        - registry says it belongs to a DIFFERENT provider -> defensive fallback
          to this provider's default model.
        """
        if not model_id:
            return self._reg.default_model(self.provider_id) or ""
        owner = self._reg.provider_for_model(model_id)
        if owner is None or owner == self.provider_id:
            return model_id
        return self._reg.default_model(self.provider_id) or model_id

    # -- client construction -------------------------------------------------
    def build_client(self, model_id: str, api_key: str, max_tokens: int):
        """Construct the LangChain chat client for this provider/model."""
        from vendored.app.config import settings  # lazy: keeps routing logic import-light/testable

        timeout = settings.llm_timeout_seconds
        temperature = settings.llm_temperature
        model = self.resolve_model(model_id)

        if self.client_kind == "anthropic":
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                model=model,
                api_key=api_key,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
                max_retries=2,  # LangChain handles backoff; smooths transient 429/overload
            )

        if self.client_kind == "google_genai":
            from langchain_google_genai import ChatGoogleGenerativeAI

            return ChatGoogleGenerativeAI(
                model=model,
                google_api_key=api_key,
                max_output_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            )

        # openai_compatible: Cerebras, OpenAI, OpenRouter, xAI (base_url differs)
        from langchain_openai import ChatOpenAI

        kwargs: dict[str, Any] = dict(
            model=model,
            api_key=api_key,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            max_retries=1,
        )
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return ChatOpenAI(**kwargs)

    # -- message assembly (caching lives here, per cache_class) --------------
    def assemble_messages(self, blocks: "PromptBlocks", model_id: Optional[str] = None) -> list:
        """Default (auto_prefix: Cerebras/OpenAI/xAI): stable static -> data ->
        dynamic concat as ONE SystemMessage, keeping the longest invariant (static)
        leading so the server's prefix auto-cache matches. Byte-identical ordering
        is load-bearing — do not reorder or interpolate before static."""
        from langchain_core.messages import HumanMessage, SystemMessage

        parts = [p for p in (blocks.static_system, blocks.data_block, blocks.dynamic_system) if p]
        return [
            SystemMessage(content="\n\n".join(parts)),
            HumanMessage(content=blocks.user_text),
        ]

    # -- capability flags ----------------------------------------------------
    def supports_caching(self, model_id: Optional[str] = None) -> bool:
        if model_id:
            return self._reg.supports_caching(model_id)
        return bool(self.cache_class and self.cache_class != "noop")

    def supports_vision(self) -> bool:
        return self._reg.supports_vision(self.provider_id)

    # -- cache lifecycle (no-ops here; Phase 4 specialises per cache_class) ---
    def prepare_cache(self, blocks: PromptBlocks, ttl: Optional[str] = None):
        return None

    def apply_cache(self, blocks: PromptBlocks, handle: Any = None) -> PromptBlocks:
        return blocks

    def read_cache_usage(self, response: Any) -> CacheUsage:
        """Best-effort generic reader (OpenAI/Cerebras prefix-cache shape).

        Subclasses override for Anthropic (cache_read/creation) and Gemini
        (cachedContentTokenCount). Reads ``usage.prompt_tokens_details.cached_tokens``.
        """
        usage = _response_usage(response)
        cached = 0
        details = usage.get("prompt_tokens_details") or {}
        if isinstance(details, dict):
            cached = int(details.get("cached_tokens") or 0)
        prompt_total = int(usage.get("prompt_tokens") or 0)
        return CacheUsage(
            cache_read_tokens=cached,
            cache_write_tokens=0,
            uncached_input_tokens=max(prompt_total - cached, 0),
        )

    def release_cache(self, handle: Any) -> None:
        return None


def _response_usage(response: Any) -> dict[str, Any]:
    """Extract a usage dict from a LangChain AIMessage-like response, defensively."""
    if response is None:
        return {}
    meta = getattr(response, "response_metadata", None) or {}
    if isinstance(meta, dict):
        usage = meta.get("usage") or meta.get("token_usage")
        if isinstance(usage, dict):
            return usage
    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, dict):
        return usage
    return {}
