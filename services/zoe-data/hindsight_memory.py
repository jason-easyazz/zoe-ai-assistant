"""Hindsight sidecar adapter for Zoe's memory bake-off.

The adapter is disabled by default, never performs blind auto-retain, and is
hardened for Zoe's offline-only memory rule. If enabled, the sidecar URL must be
local/private and any visible Hindsight LLM provider config must use a local
provider or a local OpenAI-compatible base URL.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlparse

import httpx

from zoe_memory_contract import MemoryEvent, memory_event_from_mapping


class HindsightMemoryError(RuntimeError):
    """Raised when Hindsight is unavailable or returns an invalid response."""


class HindsightOfflineConfigError(ValueError):
    """Raised when Hindsight config would violate Zoe's offline-only rule."""


_CLOUD_LLM_PROVIDERS = {
    "anthropic",
    "bedrock",
    "claude-code",
    "deepseek",
    "fireworks",
    "gemini",
    "groq",
    "litellm",
    "litellmrouter",
    "minimax",
    "ollama-cloud",
    "openai",
    "openai-codex",
    "openrouter",
    "opencode-go",
    "vertexai",
    "volcano",
    "zai",
}

_LOCAL_LLM_PROVIDERS = {
    "llamacpp",
    "llama.cpp",
    "lmstudio",
    "ollama",
}


@dataclass(frozen=True)
class HindsightConfig:
    enabled: bool = False
    base_url: str = "http://127.0.0.1:8888"
    auth_token: str | None = None
    bank_prefix: str = "zoe"
    timeout_seconds: float = 6.0
    auto_retain: bool = False
    async_retain: bool = True
    offline_only: bool = True
    llm_provider: str | None = None
    llm_base_url: str | None = None

    def __post_init__(self) -> None:
        self.validate_offline_policy()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "HindsightConfig":
        values = env or os.environ
        return cls(
            enabled=_env_bool(values.get("HINDSIGHT_ENABLED"), default=False),
            base_url=(values.get("HINDSIGHT_BASE_URL") or "http://127.0.0.1:8888").rstrip("/"),
            auth_token=values.get("HINDSIGHT_AUTH_TOKEN") or None,
            bank_prefix=_slug(values.get("HINDSIGHT_BANK_PREFIX") or "zoe"),
            timeout_seconds=float(values.get("HINDSIGHT_TIMEOUT_SECONDS") or 6.0),
            auto_retain=_env_bool(values.get("HINDSIGHT_AUTO_RETAIN"), default=False),
            async_retain=_env_bool(values.get("HINDSIGHT_ASYNC_RETAIN"), default=True),
            offline_only=_env_bool(values.get("HINDSIGHT_OFFLINE_ONLY"), default=True),
            llm_provider=(values.get("HINDSIGHT_API_LLM_PROVIDER") or values.get("HINDSIGHT_LLM_PROVIDER") or None),
            llm_base_url=(values.get("HINDSIGHT_API_LLM_BASE_URL") or values.get("HINDSIGHT_LLM_BASE_URL") or None),
        )

    def bank_id(self, user_id: str, scope: str) -> str:
        return "-".join((_slug(self.bank_prefix), _slug(scope), _slug(user_id)))

    def validate_offline_policy(self) -> None:
        if not self.enabled or not self.offline_only:
            return
        if not _is_local_or_private_url(self.base_url):
            raise HindsightOfflineConfigError("HINDSIGHT_BASE_URL must be localhost or private network when offline_only is enabled")

        provider = (self.llm_provider or "").strip().lower()
        if not provider:
            return
        if provider in _LOCAL_LLM_PROVIDERS:
            return
        if provider == "openai" and self.llm_base_url and _is_local_or_private_url(self.llm_base_url):
            return
        if provider in _CLOUD_LLM_PROVIDERS:
            raise HindsightOfflineConfigError(
                f"Hindsight LLM provider {provider!r} is not allowed for Zoe offline memory; use llamacpp, ollama, lmstudio, or a local OpenAI-compatible base URL"
            )
        if self.llm_base_url and _is_local_or_private_url(self.llm_base_url):
            return
        raise HindsightOfflineConfigError(
            f"Hindsight LLM provider {provider!r} is unrecognized for Zoe offline memory; set a localhost/private HINDSIGHT_API_LLM_BASE_URL or use llamacpp, ollama, or lmstudio"
        )


def _env_bool(value: str | None, *, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value).strip().lower()).strip("-")
    return slug or "default"


def _is_local_or_private_url(url: str | None) -> bool:
    parsed = urlparse(str(url or ""))
    host = parsed.hostname
    if not host:
        return False
    if host in {"localhost", "zoe", "host.docker.internal"}:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host.endswith(".local") or host.endswith(".lan")
    return ip.is_loopback or ip.is_private or ip.is_link_local


def event_to_hindsight_item(event: MemoryEvent) -> dict[str, Any]:
    """Convert a validated Zoe memory event into a Hindsight retain item."""

    event.validate()
    tags = [
        "zoe",
        f"user:{_slug(event.user_id)}",
        f"scope:{event.scope}",
        f"type:{event.event_type}",
        f"status:{event.status}",
    ]
    tags.extend(f"entity:{_slug(entity)}" for entity in event.entities[:8])
    tags.extend(f"evidence:{_slug(ref)}" for ref in event.evidence_refs[:8])

    context = {
        "source": event.source,
        "scope": event.scope,
        "event_type": event.event_type,
        "status": event.status,
        "confidence": event.confidence,
        "evidence_refs": list(event.evidence_refs),
        "relationships": [relationship.to_dict() for relationship in event.relationships],
        "supersedes": list(event.supersedes),
        "retention_policy": event.retention_policy,
    }

    return {
        "content": event.content,
        "context": _compact_context(context),
        "document_id": event.event_id,
        "timestamp": event.created_at.isoformat(),
        "tags": tags,
    }


def _compact_context(context: Mapping[str, Any]) -> str:
    parts = []
    for key, value in context.items():
        if value in (None, "", [], {}):
            continue
        serialized = json.dumps(value, separators=(",", ":")) if isinstance(value, (list, dict)) else value
        parts.append(f"{key}={serialized}")
    return "; ".join(parts)


class HindsightMemoryClient:
    """Small async client for Hindsight's HTTP API."""

    def __init__(self, config: HindsightConfig | None = None, *, client: httpx.AsyncClient | None = None):
        self.config = config or HindsightConfig.from_env()
        self._client = client

    def enabled_status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "base_url": self.config.base_url,
            "bank_prefix": self.config.bank_prefix,
            "auto_retain": self.config.auto_retain,
            "async_retain": self.config.async_retain,
            "offline_only": self.config.offline_only,
            "llm_provider": self.config.llm_provider,
            "llm_base_url": self.config.llm_base_url,
        }

    async def health(self) -> dict[str, Any]:
        if not self.config.enabled:
            return {"enabled": False, "healthy": False, "reason": "disabled"}
        return await self._request("GET", "/health")

    async def retain_event(self, event_or_payload: MemoryEvent | Mapping[str, Any], *, allow_auto: bool = False) -> dict[str, Any]:
        """Submit an evidence-backed event to Hindsight.

        By default this refuses to write unless the caller explicitly allows a
        retain attempt and the Hindsight config is enabled. Zoe's normal path
        should create retain candidates first, then call this only after an
        evidence/admission gate.
        """

        event = event_or_payload if isinstance(event_or_payload, MemoryEvent) else memory_event_from_mapping(event_or_payload)
        if not self.config.enabled:
            return {"enabled": False, "retained": False, "reason": "disabled", "event_id": event.event_id}
        if not allow_auto and not self.config.auto_retain:
            return {"enabled": True, "retained": False, "reason": "auto_retain_disabled", "event_id": event.event_id}

        bank_id = self.config.bank_id(event.user_id, event.scope)
        payload = {"async": self.config.async_retain, "items": [event_to_hindsight_item(event)]}
        result = await self._request("POST", f"/v1/default/banks/{bank_id}/memories", json=payload)
        result.setdefault("event_id", event.event_id)
        result.setdefault("bank_id", bank_id)
        return result

    async def operation_status(self, *, bank_id: str, operation_id: str) -> dict[str, Any]:
        """Fetch a Hindsight async operation status."""

        if not self.config.enabled:
            return {"enabled": False, "status": "disabled", "reason": "disabled"}
        return await self._request("GET", f"/v1/default/banks/{bank_id}/operations/{operation_id}")

    async def wait_for_operation(
        self,
        *,
        bank_id: str,
        operation_id: str,
        timeout_seconds: float = 120.0,
        poll_seconds: float = 1.0,
    ) -> dict[str, Any]:
        """Poll a Hindsight async operation until it completes, fails, or times out."""

        if not self.config.enabled:
            return {"enabled": False, "status": "disabled", "reason": "disabled"}

        deadline = asyncio.get_running_loop().time() + timeout_seconds
        last_status: dict[str, Any] | None = None
        while True:
            last_status = await self.operation_status(bank_id=bank_id, operation_id=operation_id)
            status = str(last_status.get("status") or "").lower()
            if status in {"completed", "failed", "cancelled", "canceled"}:
                return last_status
            if asyncio.get_running_loop().time() >= deadline:
                return {
                    "operation_id": operation_id,
                    "bank_id": bank_id,
                    "status": "timeout",
                    "last_status": last_status,
                }
            await asyncio.sleep(max(0.1, poll_seconds))

    async def wait_for_retain_results(
        self,
        retained: list[Mapping[str, Any]],
        *,
        timeout_seconds: float = 120.0,
        poll_seconds: float = 1.0,
    ) -> list[dict[str, Any]]:
        """Wait concurrently for all async retain operations that expose operation IDs."""

        async def _poll_one(item: Mapping[str, Any]) -> dict[str, Any]:
            operation_id = str(item.get("operation_id") or "").strip()
            bank_id = str(item.get("bank_id") or "").strip()
            if not operation_id or not bank_id:
                return {"status": "not_async", "retain_result": dict(item)}
            return await self.wait_for_operation(
                bank_id=bank_id,
                operation_id=operation_id,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
            )

        return list(await asyncio.gather(*(_poll_one(item) for item in retained)))

    async def recall(
        self,
        *,
        user_id: str,
        scope: str,
        query: str,
        budget: str = "mid",
        max_tokens: int = 1024,
        types: tuple[str, ...] = ("world", "experience"),
        tags: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        if not self.config.enabled:
            return {"enabled": False, "results": [], "reason": "disabled"}
        bank_id = self.config.bank_id(user_id, scope)
        payload: dict[str, Any] = {
            "query": query,
            "budget": budget,
            "max_tokens": max_tokens,
            "types": list(types),
            "trace": True,
        }
        if tags:
            payload["tags"] = list(tags)
            payload["tags_match"] = "all_strict"
        result = await self._request("POST", f"/v1/default/banks/{bank_id}/memories/recall", json=payload)
        result.setdefault("bank_id", bank_id)
        return result

    async def reflect(
        self,
        *,
        user_id: str,
        scope: str,
        query: str,
        budget: str = "low",
        max_tokens: int = 1024,
        include_facts: bool = True,
    ) -> dict[str, Any]:
        if not self.config.enabled:
            return {"enabled": False, "text": "", "reason": "disabled"}
        bank_id = self.config.bank_id(user_id, scope)
        include = {"facts": {}} if include_facts else {}
        payload = {"query": query, "budget": budget, "max_tokens": max_tokens, "include": include}
        result = await self._request("POST", f"/v1/default/banks/{bank_id}/reflect", json=payload)
        result.setdefault("bank_id", bank_id)
        return result

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.config.base_url}{path}"
        headers = dict(kwargs.pop("headers", {}) or {})
        if self.config.auth_token:
            headers["Authorization"] = f"Bearer {self.config.auth_token}"
        close_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.config.timeout_seconds)
        try:
            response = await client.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                return {"data": data}
            return data
        except httpx.HTTPError as exc:
            raise HindsightMemoryError(f"Hindsight request failed: {method} {path}: {exc}") from exc
        except ValueError as exc:
            raise HindsightMemoryError(f"Hindsight response was not valid JSON: {method} {path}: {exc}") from exc
        finally:
            if close_client:
                await client.aclose()


__all__ = [
    "HindsightConfig",
    "HindsightMemoryClient",
    "HindsightMemoryError",
    "HindsightOfflineConfigError",
    "event_to_hindsight_item",
]
