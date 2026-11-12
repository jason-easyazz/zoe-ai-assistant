"""account_secrets - the ONE seam a connected account's credential goes through (design ``docs/architecture/zoe-abilities.md`` section 5).

Stage 0 of the feed plumbing builds the CONTRACT, not a vault. Nothing is stored anywhere yet: the only backend shipped is
``RefusingBackend``, which fails closed. Which real backend replaces it is an owner decision (the key-encryption key's home: a 0600
key file under ``~/.zoe/keys/``, a systemd encrypted credential, or the ``zoe-vaultd`` broker per credential-vault decision O9), so a
connector cannot be built that writes a token into a plain database column, a log or a file by accident.

Contract (every backend):
  * ``put(account_id, user_id=, purpose=, secret=)`` stores one secret per (account, purpose), bound to its member: a ``get`` with a
    different ``user_id`` or ``purpose`` must fail, never return another member's secret (the design's AES-GCM associated data).
  * ``get(...)`` returns the secret bytes, or raises ``SecretNotFound``. Decrypted only inside the adapter call that uses it.
  * ``remove(account_id)`` / ``forget_user(user_id)`` remove every secret of the account / member; the count removed.
  * no backend configured = ``SecretsUnavailable`` on ``put`` / ``get`` (fail closed). ``remove`` / ``forget_user`` on the refusing
    backend return 0: nothing can have been stored through it.
  * a secret never reaches a log line, an exception message, the database or a prompt. Errors name the account id only.
"""
from __future__ import annotations

import re
from typing import Optional, Protocol

#: what a stored secret is for (the design's ``aad_purpose``); a new purpose is a reviewed change
PURPOSES = ("oauth_refresh", "app_password")
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class SecretsUnavailable(RuntimeError):
    """No secret backend is configured: the write or read is refused (fail closed)."""


class SecretNotFound(LookupError):
    """No secret for this (account, member, purpose)."""


class Backend(Protocol):
    name: str

    async def put(self, account_id: str, user_id: str, purpose: str, secret: bytes) -> None: ...
    async def get(self, account_id: str, user_id: str, purpose: str) -> bytes: ...
    async def remove(self, account_id: str) -> int: ...
    async def forget_user(self, user_id: str) -> int: ...


class RefusingBackend:
    """The default until the owner picks where the key lives: stores nothing, refuses every put and get."""

    name = "refusing"

    async def put(self, account_id: str, user_id: str, purpose: str, secret: bytes) -> None:
        raise SecretsUnavailable("no account-secret backend is configured")

    async def get(self, account_id: str, user_id: str, purpose: str) -> bytes:
        raise SecretsUnavailable("no account-secret backend is configured")

    async def remove(self, account_id: str) -> int:
        return 0

    async def forget_user(self, user_id: str) -> int:
        return 0


_backend: Optional[Backend] = None


def backend() -> Backend:
    return _backend if _backend is not None else RefusingBackend()


def configure(b: Optional[Backend]) -> None:
    """Install a backend (``None`` = back to refusing). Tests, and the future vault wiring, call this; nothing else does."""
    global _backend
    _backend = b


def configured() -> bool:
    return not isinstance(backend(), RefusingBackend)


def _check(account_id: str, user_id: str, purpose: str) -> None:
    if not (_ID.match(account_id or "") and _ID.match(user_id or "")):
        raise ValueError("account_secrets: account_id and user_id must be plain ids")
    if purpose not in PURPOSES:
        raise ValueError(f"account_secrets: unknown purpose {purpose!r}")


async def put(account_id: str, *, user_id: str, purpose: str, secret: bytes) -> None:
    _check(account_id, user_id, purpose)
    if not isinstance(secret, (bytes, bytearray)) or not secret:
        raise ValueError("account_secrets: the secret must be non-empty bytes")
    await backend().put(account_id, user_id, purpose, bytes(secret))


async def get(account_id: str, *, user_id: str, purpose: str) -> bytes:
    _check(account_id, user_id, purpose)
    return await backend().get(account_id, user_id, purpose)


async def remove(account_id: str) -> int:
    if not _ID.match(account_id or ""):
        raise ValueError("account_secrets: account_id must be a plain id")
    return await backend().remove(account_id)


async def forget_user(user_id: str) -> int:
    """Every secret of the member (``connected_accounts.forget_user`` calls it first). Raises on a backend failure: fail closed."""
    if not _ID.match(user_id or ""):
        return 0
    return await backend().forget_user(user_id)
