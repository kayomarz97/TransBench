"""Anthropic (Claude) adapter.

client_kind=anthropic -> ChatAnthropic. cache_class=inline (Phase 4 adds the
``cache_control`` block assembly + per-model token-floor gating). This subclass
already reads Anthropic's distinct cache-accounting fields correctly.
"""

from __future__ import annotations

from typing import Any

from vendored.app.services.providers.base import ProviderAdapter, PromptBlocks, CacheUsage, _response_usage


class AnthropicAdapter(ProviderAdapter):
    def assemble_messages(self, blocks: PromptBlocks, model_id: Any = None) -> list:
        """inline cache_class: cache_control breakpoints on the static prefix (and the
        data block) as a multi-block system message. Gates each breakpoint on the
        model's real min_cache_tokens (Haiku 4.5 = 4096, NOT the old 1024-char proxy)
        so we never spend a breakpoint on a block too small for Anthropic to cache."""
        from langchain_core.messages import HumanMessage, SystemMessage

        min_tok = self._reg.min_cache_tokens(model_id) if model_id else None
        min_chars = (min_tok or 1024) * 4  # ~4 chars/token proxy for the per-model floor

        sys_content: list[dict] = []
        static_block: dict = {"type": "text", "text": blocks.static_system}
        if blocks.static_system and len(blocks.static_system) >= min_chars:
            static_block["cache_control"] = {"type": "ephemeral"}
        sys_content.append(static_block)

        if blocks.data_block:
            db: dict = {"type": "text", "text": blocks.data_block}
            if len(blocks.data_block) >= min_chars:
                db["cache_control"] = {"type": "ephemeral"}
            sys_content.append(db)

        if blocks.dynamic_system:
            sys_content.append({"type": "text", "text": blocks.dynamic_system})

        return [SystemMessage(content=sys_content), HumanMessage(content=blocks.user_text)]

    def read_cache_usage(self, response: Any) -> CacheUsage:
        usage = _response_usage(response)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        cache_creation = int(usage.get("cache_creation_input_tokens") or 0)
        # Anthropic "input_tokens" already excludes cached/created tokens.
        input_tokens = int(usage.get("input_tokens") or 0)
        return CacheUsage(
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_creation,
            uncached_input_tokens=input_tokens,
        )
