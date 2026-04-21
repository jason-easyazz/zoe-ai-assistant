"""
Calendar gateway abstraction for provider sync.

Phase 1 uses Keeper.sh as the provider bridge while preserving local-first writes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
import hashlib
import json
import logging
import os

import requests

logger = logging.getLogger(__name__)


@dataclass
class CalendarSyncResult:
    success: bool
    provider: str
    operation: str
    provider_event_id: Optional[str] = None
    status_code: Optional[int] = None
    error: Optional[str] = None
    response_payload: Optional[Dict[str, Any]] = None


class CalendarGateway:
    def create_event(self, user_id: str, event_payload: Dict[str, Any], idempotency_key: str) -> CalendarSyncResult:
        raise NotImplementedError

    def update_event(
        self,
        user_id: str,
        event_id: str,
        event_payload: Dict[str, Any],
        idempotency_key: str,
        provider_event_id: Optional[str] = None,
    ) -> CalendarSyncResult:
        raise NotImplementedError

    def delete_event(
        self,
        user_id: str,
        event_id: str,
        idempotency_key: str,
        provider_event_id: Optional[str] = None,
    ) -> CalendarSyncResult:
        raise NotImplementedError


class KeeperCalendarGateway(CalendarGateway):
    def __init__(self) -> None:
        self.base_url = os.getenv("KEEPER_BASE_URL", "http://zoe-keeper:8787").rstrip("/")
        self.auth_token = os.getenv("KEEPER_AUTH_TOKEN", "")
        self.timeout_seconds = int(os.getenv("KEEPER_TIMEOUT_SECONDS", "12"))

    def _headers(self, idempotency_key: str) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Idempotency-Key": idempotency_key,
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def _post(self, path: str, payload: Dict[str, Any], idempotency_key: str, operation: str) -> CalendarSyncResult:
        url = f"{self.base_url}{path}"
        try:
            response = requests.post(url, headers=self._headers(idempotency_key), json=payload, timeout=self.timeout_seconds)
            body: Dict[str, Any] = {}
            try:
                body = response.json()
            except Exception:
                body = {"raw": response.text[:2000]}
            provider_event_id = body.get("event_id") or body.get("provider_event_id")
            if response.ok:
                return CalendarSyncResult(
                    success=True,
                    provider="keeper",
                    operation=operation,
                    provider_event_id=provider_event_id,
                    status_code=response.status_code,
                    response_payload=body,
                )
            return CalendarSyncResult(
                success=False,
                provider="keeper",
                operation=operation,
                status_code=response.status_code,
                error=body.get("detail") or body.get("error") or "keeper request failed",
                response_payload=body,
            )
        except Exception as exc:
            logger.warning("Keeper request failed: %s", exc)
            return CalendarSyncResult(
                success=False,
                provider="keeper",
                operation=operation,
                error=str(exc),
            )

    def create_event(self, user_id: str, event_payload: Dict[str, Any], idempotency_key: str) -> CalendarSyncResult:
        return self._post(
            "/api/sync/events/create",
            {"user_id": user_id, "event": event_payload},
            idempotency_key,
            "create",
        )

    def update_event(
        self,
        user_id: str,
        event_id: str,
        event_payload: Dict[str, Any],
        idempotency_key: str,
        provider_event_id: Optional[str] = None,
    ) -> CalendarSyncResult:
        return self._post(
            "/api/sync/events/update",
            {
                "user_id": user_id,
                "event_id": event_id,
                "provider_event_id": provider_event_id,
                "event": event_payload,
            },
            idempotency_key,
            "update",
        )

    def delete_event(
        self,
        user_id: str,
        event_id: str,
        idempotency_key: str,
        provider_event_id: Optional[str] = None,
    ) -> CalendarSyncResult:
        return self._post(
            "/api/sync/events/delete",
            {
                "user_id": user_id,
                "event_id": event_id,
                "provider_event_id": provider_event_id,
            },
            idempotency_key,
            "delete",
        )


def is_calendar_sync_enabled() -> bool:
    return os.getenv("ZOE_CALENDAR_SYNC_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def get_calendar_gateway() -> CalendarGateway:
    provider = os.getenv("ZOE_CALENDAR_SYNC_PROVIDER", "keeper").lower()
    if provider == "keeper":
        return KeeperCalendarGateway()
    raise RuntimeError(f"Unsupported calendar sync provider: {provider}")


def build_idempotency_key(user_id: str, operation: str, event_id: str, payload: Optional[Dict[str, Any]] = None) -> str:
    digest_source = {
        "user_id": user_id,
        "operation": operation,
        "event_id": str(event_id),
        "payload": payload or {},
    }
    raw = json.dumps(digest_source, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
