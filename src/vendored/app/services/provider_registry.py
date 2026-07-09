"""Provider registry — the single source of truth for all LLM providers & models.

Loads ``config/providers.yaml`` once at startup and exposes typed lookups so that
NO provider/model string is hardcoded anywhere else in the backend. The frontend
receives the enabled subset via ``GET /api/providers`` (built from ``public_view``).

Resolution order for the YAML path:
    1. ``PROVIDERS_CONFIG_PATH`` env var (e.g. a volume-mounted file for hot-reload)
    2. ``<backend>/config/providers.yaml`` resolved relative to this module — works
       both in-container (``/app/config/providers.yaml``) and in the repo
       (``backend/config/providers.yaml``).

This module is intentionally dependency-light (PyYAML only) so it can be imported
during early app startup before heavier services initialise.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

# backend/app/services/provider_registry.py -> parents[2] == backend/ (repo) or /app (container)
_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "providers.yaml"

_VALID_CACHE_CLASSES = {"inline", "auto_prefix", "stateful", "conditional"}
_VALID_CLIENT_KINDS = {"anthropic", "openai_compatible", "google_genai"}


class ProviderRegistryError(RuntimeError):
    """Raised when the registry file is missing or fails validation (fail-fast at startup)."""


class ProviderRegistry:
    """Read-only view over the parsed providers.yaml with O(1) model lookups."""

    def __init__(self, data: dict[str, Any]):
        self._data = data
        self._providers: dict[str, dict] = data.get("providers", {})
        # id -> (provider_id, model_dict)
        self._model_index: dict[str, tuple[str, dict]] = {}
        for pid, p in self._providers.items():
            for m in p.get("models", []) or []:
                self._model_index[m["id"]] = (pid, m)

    # -- top-level -----------------------------------------------------------
    @property
    def version(self) -> int:
        return int(self._data.get("version", 1))

    @property
    def default_provider(self) -> str:
        return self._data.get("default_provider", "")

    @property
    def deep_search(self) -> dict[str, Any]:
        return dict(self._data.get("deep_search", {}))

    # -- providers -----------------------------------------------------------
    def all_providers(self) -> dict[str, dict]:
        return self._providers

    def enabled_providers(self) -> dict[str, dict]:
        return {k: v for k, v in self._providers.items() if v.get("enabled")}

    def allowed_providers(self) -> list[str]:
        """Every provider id the system knows about (enabled or not) — replaces hardcoded allowlists."""
        return list(self._providers.keys())

    def is_enabled(self, provider_id: str) -> bool:
        return bool(self._providers.get(provider_id, {}).get("enabled"))

    def provider_meta(self, provider_id: str) -> Optional[dict]:
        return self._providers.get(provider_id)

    def key_column(self, provider_id: str) -> Optional[str]:
        return (self._providers.get(provider_id) or {}).get("key_column")

    def cache_class(self, provider_id: str) -> Optional[str]:
        return (self._providers.get(provider_id) or {}).get("cache_class")

    def base_url(self, provider_id: str) -> Optional[str]:
        return (self._providers.get(provider_id) or {}).get("base_url")

    def supports_vision(self, provider_id: str) -> bool:
        return bool((self._providers.get(provider_id) or {}).get("supports_vision"))

    def embedding_model(self, provider_id: str) -> Optional[str]:
        return (self._providers.get(provider_id) or {}).get("embedding_model")

    # -- models --------------------------------------------------------------
    def provider_for_model(self, model_id: str) -> Optional[str]:
        hit = self._model_index.get(model_id)
        return hit[0] if hit else None

    def model_meta(self, model_id: str) -> Optional[dict]:
        hit = self._model_index.get(model_id)
        return hit[1] if hit else None

    def pricing(self, model_id: str) -> dict[str, float]:
        m = self.model_meta(model_id) or {}
        return {
            "input": float(m.get("input", 0.0)),
            "output": float(m.get("output", 0.0)),
            "cache_read": float(m.get("cache_read", 0.0)),
        }

    def supports_caching(self, model_id: str) -> bool:
        """Per-MODEL caching capability (e.g. Cerebras caches only gpt-oss-120b)."""
        m = self.model_meta(model_id)
        return bool(m and m.get("supports_caching"))

    def min_cache_tokens(self, model_id: str) -> Optional[int]:
        m = self.model_meta(model_id) or {}
        v = m.get("min_cache_tokens")
        return int(v) if v is not None else None

    def default_model(self, provider_id: str) -> Optional[str]:
        return (self._providers.get(provider_id) or {}).get("default_model")

    def default_model_for_role(self, provider_id: str, role: str) -> Optional[str]:
        """First model under ``provider_id`` whose ``roles`` list contains ``role``."""
        p = self._providers.get(provider_id) or {}
        for m in p.get("models", []) or []:
            if role in (m.get("roles") or []):
                return m["id"]
        return None

    # -- public (frontend) view ---------------------------------------------
    def public_view(self) -> dict[str, Any]:
        """Enabled providers/models only, with NO secrets — feeds GET /api/providers."""
        providers = {}
        for pid, p in self.enabled_providers().items():
            providers[pid] = {
                "id": pid,
                "display": p.get("display", pid),
                "supports_vision": bool(p.get("supports_vision")),
                "key_prefix": p.get("key_prefix", ""),
                "signup_url": p.get("signup_url"),
                "blurb": p.get("blurb"),
                "default_model": p.get("default_model"),
                "models": [
                    {
                        "id": m["id"],
                        "display": m.get("display", m["id"]),
                        "input": m.get("input", 0.0),
                        "output": m.get("output", 0.0),
                        "context_window": m.get("context_window"),
                        "tier": m.get("tier"),
                    }
                    for m in (p.get("models") or [])
                ],
            }
        return {
            "default_provider": self.default_provider if self.is_enabled(self.default_provider) else next(iter(providers), None),
            "providers": providers,
        }


# ---------------------------------------------------------------------------
# Loading & validation
# ---------------------------------------------------------------------------

def _validate(data: dict[str, Any], source: str) -> None:
    if not isinstance(data, dict):
        raise ProviderRegistryError(f"{source}: top-level YAML must be a mapping")
    providers = data.get("providers")
    if not isinstance(providers, dict) or not providers:
        raise ProviderRegistryError(f"{source}: 'providers' must be a non-empty mapping")

    default_provider = data.get("default_provider")
    if default_provider not in providers:
        raise ProviderRegistryError(
            f"{source}: default_provider '{default_provider}' is not a defined provider"
        )
    if not providers.get(default_provider, {}).get("enabled"):
        raise ProviderRegistryError(
            f"{source}: default_provider '{default_provider}' must be enabled"
        )

    seen_models: set[str] = set()
    for pid, p in providers.items():
        if not isinstance(p, dict):
            raise ProviderRegistryError(f"{source}: provider '{pid}' must be a mapping")
        for req in ("display", "client_kind", "key_column", "cache_class", "default_model"):
            if req not in p:
                raise ProviderRegistryError(f"{source}: provider '{pid}' missing required key '{req}'")
        if p["client_kind"] not in _VALID_CLIENT_KINDS:
            raise ProviderRegistryError(
                f"{source}: provider '{pid}' client_kind '{p['client_kind']}' not in {_VALID_CLIENT_KINDS}"
            )
        if p["cache_class"] not in _VALID_CACHE_CLASSES:
            raise ProviderRegistryError(
                f"{source}: provider '{pid}' cache_class '{p['cache_class']}' not in {_VALID_CACHE_CLASSES}"
            )
        models = p.get("models") or []
        model_ids = {m["id"] for m in models if isinstance(m, dict) and "id" in m}
        if p.get("enabled") and not models:
            raise ProviderRegistryError(f"{source}: enabled provider '{pid}' has no models")
        if p["default_model"] not in model_ids:
            raise ProviderRegistryError(
                f"{source}: provider '{pid}' default_model '{p['default_model']}' not in its models {sorted(model_ids)}"
            )
        for m in models:
            mid = m.get("id")
            if not mid:
                raise ProviderRegistryError(f"{source}: provider '{pid}' has a model with no id")
            if mid in seen_models:
                raise ProviderRegistryError(f"{source}: duplicate model id '{mid}' across providers")
            seen_models.add(mid)


def load_registry(path: str | os.PathLike | None = None) -> ProviderRegistry:
    """Parse + validate providers.yaml. Raises ProviderRegistryError on any problem (fail-fast)."""
    p = Path(path or os.getenv("PROVIDERS_CONFIG_PATH") or _DEFAULT_PATH)
    if not p.exists():
        raise ProviderRegistryError(f"providers.yaml not found at {p}")
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:  # pragma: no cover - defensive
        raise ProviderRegistryError(f"{p}: invalid YAML: {exc}") from exc
    _validate(data, str(p))
    return ProviderRegistry(data)


@lru_cache(maxsize=1)
def get_registry() -> ProviderRegistry:
    """Process-wide singleton. Call ``get_registry.cache_clear()`` to force a reload."""
    reg = load_registry()
    logger.info(
        "Provider registry loaded: enabled=%s default=%s",
        list(reg.enabled_providers().keys()),
        reg.default_provider,
    )
    return reg
