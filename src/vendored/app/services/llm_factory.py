"""LLM client factory — thin, registry-backed shim over the provider adapters.

Public contract is unchanged (``create_llm`` / ``get_provider`` /
``handle_llm_api_error`` / ``get_alternative_model``) so all existing callers
keep working. Provider routing + client construction now come from
``config/providers.yaml`` via the adapter layer (no hardcoded prefix dispatch
or per-provider client branches here).
"""

import logging
from typing import Optional

from fastapi import HTTPException, status

from vendored.app.config import settings
from vendored.app.services.provider_registry import get_registry
from vendored.app.services.providers import get_adapter, resolve_provider

logger = logging.getLogger(__name__)


def handle_llm_api_error(e: Exception, model_id: str, provider: str) -> None:
    """Convert LLM provider errors to user-friendly HTTP exceptions.

    Raises HTTPException on known errors; re-raises on unknown.
    """
    error_msg = str(e).lower()

    if provider == "anthropic":
        if "notfound" in error_msg or "404" in error_msg or "model" in error_msg and "does not exist" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "model_not_found",
                    "message": f"Model '{model_id}' not found on Anthropic. Check your API plan or update the model name via MODEL_HAIKU / MODEL_SONNET environment variables.",
                    "settings_url": "/settings",
                },
            ) from e
        elif "authentication" in error_msg or "401" in error_msg or "invalid api key" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "auth_failed",
                    "message": "Anthropic API key is invalid or expired. Check Settings → LLM API Key.",
                    "settings_url": "/settings",
                },
            ) from e
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "error": "llm_error",
            "message": f"{provider} API error: {error_msg[:100]}",
        },
    ) from e


def get_provider(model_id: str) -> str:
    """Determine provider id from a model id (registry-first, prefix fallback)."""
    return resolve_provider(model_id)


def create_llm(
    model_id: str,
    max_tokens: int | None = None,
    user_key: Optional[str] = None,
    user_provider: Optional[str] = None,
):
    """Create a BYOK LLM client for ``model_id``.

    Uses only the user's own key. Provider + client construction are delegated
    to the registry-driven adapter for the resolved provider.
    """
    effective_max_tokens = (
        max_tokens if max_tokens is not None else settings.llm_max_tokens
    )

    provider = resolve_provider(model_id, user_provider)
    api_key = user_key

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "no_api_key",
                "message": "No API key configured. Please add your Cerebras, Anthropic, OpenAI, or OpenRouter API key in Settings → LLM API Key.",
                "settings_url": "/settings",
            },
        )

    if provider not in get_registry().allowed_providers():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "unsupported_provider",
                "message": f"Provider '{provider}' is not supported. Enable it in config/providers.yaml.",
            },
        )

    adapter = get_adapter(provider)
    try:
        return adapter.build_client(model_id, api_key, effective_max_tokens)
    except ImportError as exc:
        # e.g. langchain-google-genai not installed for the gemini client_kind
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": f"{provider}_unavailable",
                "message": f"Support for provider '{provider}' is not installed. Contact support.",
            },
        ) from exc


def get_alternative_model(model_id: str) -> str | None:
    """No alternative provider in BYOK-only mode."""
    return None
