"""Hindsight sidecar adapter for Zoe's Samantha memory bake-off.

The adapter is disabled by default and never performs blind auto-retain. It
translates Zoe's evidence-backed Samantha memory events into Hindsight's retain,
recall, and reflect HTTP API so the sidecar can be measured before live chat
integration.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

import httpx

from samantha_memory_contract import MemoryEvent, memory_event_from_mapping


class HindsightMemoryError(RuntimeError):
    """Raised when Hindsight is unavailable or returns an invalid response."""


@dataclass(frozen=True)
class HindsightConfig:
    enabled: bool = False
    base_url: str = "http://127.0.0.1:8888"
    auth_token: str | None = None
    bank_prefix: str = "zoe"
    timeout_seconds: float = 6.0
    auto_retain: bool = False
    async_retain: bool = True

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
        )

    def bank_id(self, user_id: str, scope: str) -> str:
        return "-".join((_slug(self.bank_prefix), _slug(scope), _slug(user_id)))


def _env_bool(value: str | None, *, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value).strip().lower()).strip("-")
    return slug or "default"


def event_to_hindsight_item(event: MemoryEvent) -> dict[str, Any]:
    """Convert a validated Samantha event into a Hindsight retain item."""

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
        parts.append(f"{key}={value}")
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
        finally:
            if close_client:
                await client.aclose()


__all__ = [
    "HindsightConfig",
    "HindsightMemoryClient",
    "HindsightMemoryError",
    "event_to_hindsight_item",
]
