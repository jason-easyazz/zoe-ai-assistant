"""Shared Hermes gateway HTTP helpers (auth headers for :8642 /v1/*)."""

from __future__ import annotations

import os


def hermes_api_key() -> str:
    return (
        os.environ.get("HERMES_API_KEY")
        or os.environ.get("API_SERVER_KEY")
        or ""
    ).strip()


def hermes_auth_headers(*, session_id: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    key = hermes_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if session_id:
        headers["X-Hermes-Session-Id"] = session_id
    return headers
