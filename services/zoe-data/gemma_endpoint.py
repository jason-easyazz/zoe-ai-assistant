"""Centralized resolution of the local llama-server (Gemma) base URL.

`GEMMA_SERVER_URL` is shared across many zoe-data modules, but two conventions
exist for it:

  * The `zoe_agent` convention — the value INCLUDES a trailing `/v1` and call
    sites append `/chat/completions`. The live systemd unit sets it this way
    (``GEMMA_SERVER_URL=http://127.0.0.1:11434/v1``).
  * The "bare base" convention — the value omits `/v1` and call sites append
    `/v1/chat/completions`.

Mixing them produces `/v1/v1/chat/completions` → 404, which silently breaks LLM
calls in prod. To stop that drift, every "bare base" call site should resolve
its base through :func:`gemma_base` (which strips a trailing `/v1`) and then
append `/v1/chat/completions`. The result is always exactly one `/v1`,
regardless of whether the env value already had one.
"""
from __future__ import annotations

import os

_DEFAULT_BASE = "http://127.0.0.1:11434"


def normalize_gemma_base(raw: str) -> str:
    """Return ``raw`` as a llama-server base URL WITHOUT a trailing ``/v1``.

    Strips surrounding whitespace and any trailing slashes, then a single
    trailing ``/v1`` segment if present. Falls back to the local default when
    the input is empty.
    """
    base = (raw or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")].rstrip("/")
    return base or _DEFAULT_BASE


def gemma_base() -> str:
    """Resolve the Gemma base URL from ``GEMMA_SERVER_URL`` (env), normalized.

    Read at call time so tests and runtime env overrides both take effect.
    Append ``/v1/chat/completions`` to the result.
    """
    return normalize_gemma_base(os.environ.get("GEMMA_SERVER_URL", _DEFAULT_BASE))
