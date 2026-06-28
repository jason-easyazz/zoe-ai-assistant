"""
First-password setup tokens.

``/api/auth/password/setup`` lets an account whose ``password_hash`` is NULL or
``SETUP_REQUIRED`` choose its first password. Without a gate, anyone who knows a
pre-provisioned username (the household seed creates ``jason``/``andrew``/… as
``SETUP_REQUIRED``) could race the real person and claim the account by setting a
password first. This module supplies the proof-of-authorization the endpoint now
requires.

Two token kinds, both verified in constant time:

* **Bootstrap token** — a single service-wide secret read from
  ``ZOE_AUTH_SETUP_TOKEN``. If the operator does not set one, a random token is
  generated at startup and written to the service log (WARNING) so a *local*
  operator can read it from ``journalctl`` and complete genuine first-run setup
  on the box. A remote attacker can neither read the box's log nor guess the
  token, so the race is closed without locking out the real first-run.
* **Per-user one-time tokens** — minted by an authenticated admin via
  ``POST /api/admin/users/{user_id}/setup-token`` for a specific pending user,
  with a short TTL, consumed on first successful use. This is how additional
  users are onboarded after the box has an admin.

State is in-process (single long-lived uvicorn worker); setup is a rare action,
and a restart simply re-issues the bootstrap token / asks the admin to re-mint.
No database schema is touched (the auth schema is owned outside this service).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# How long an admin-minted per-user setup token stays valid.
DEFAULT_TOKEN_TTL_MINUTES = 30


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SetupTokenManager:
    """Issues and verifies first-password setup tokens."""

    def __init__(self, bootstrap_token: Optional[str] = None):
        self._lock = threading.RLock()
        # user_id -> (token_hash, expires_at)
        self._pending: Dict[str, Tuple[str, datetime]] = {}
        self._bootstrap_token = bootstrap_token
        self._bootstrap_generated = False
        if self._bootstrap_token is None:
            self._init_bootstrap_from_env()

    # -- bootstrap token ------------------------------------------------
    def _init_bootstrap_from_env(self) -> None:
        env_token = os.getenv("ZOE_AUTH_SETUP_TOKEN", "").strip()
        if env_token:
            self._bootstrap_token = env_token
            self._bootstrap_generated = False
            logger.info("Setup bootstrap token loaded from ZOE_AUTH_SETUP_TOKEN")
        else:
            self._bootstrap_token = secrets.token_urlsafe(24)
            self._bootstrap_generated = True
            logger.warning(
                "ZOE_AUTH_SETUP_TOKEN is not set. Generated a one-time bootstrap "
                "setup token for first-run password setup: %s\n"
                "Use it as the `setup_token` field of POST /api/auth/password/setup. "
                "Set ZOE_AUTH_SETUP_TOKEN to pin a stable value across restarts.",
                self._bootstrap_token,
            )

    @property
    def bootstrap_token(self) -> Optional[str]:
        """The current bootstrap token (for the local operator / tests)."""
        return self._bootstrap_token

    def _matches_bootstrap(self, token: str) -> bool:
        if not self._bootstrap_token:
            return False
        return hmac.compare_digest(token, self._bootstrap_token)

    # -- per-user one-time tokens --------------------------------------
    def issue_token(self, user_id: str, ttl_minutes: int = DEFAULT_TOKEN_TTL_MINUTES) -> str:
        """Mint a one-time setup token for ``user_id`` and return the plaintext."""
        token = secrets.token_urlsafe(24)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        with self._lock:
            self._pending[user_id] = (_hash(token), expires_at)
        return token

    def _matches_pending(self, user_id: str, token: str) -> bool:
        with self._lock:
            entry = self._pending.get(user_id)
            if not entry:
                return False
            token_hash, expires_at = entry
            if datetime.now(timezone.utc) >= expires_at:
                # Expired — drop it so it cannot be used.
                self._pending.pop(user_id, None)
                return False
            return hmac.compare_digest(_hash(token), token_hash)

    def consume(self, user_id: str) -> None:
        """Invalidate any per-user token for ``user_id`` (after successful use)."""
        with self._lock:
            self._pending.pop(user_id, None)

    # -- verification ---------------------------------------------------
    def verify(self, user_id: str, token: Optional[str]) -> bool:
        """Return True if ``token`` authorizes first-password setup for ``user_id``.

        Accepts either a valid (unexpired) per-user token minted for this user,
        or the service-wide bootstrap token. Empty/absent tokens never pass.
        """
        if not token:
            return False
        # Per-user token is preferred; fall back to the bootstrap secret.
        if self._matches_pending(user_id, token):
            return True
        return self._matches_bootstrap(token)

    def reset(self, bootstrap_token: Optional[str] = None) -> None:
        """Clear all state. Intended for tests.

        If ``bootstrap_token`` is given it becomes the new bootstrap secret;
        otherwise the bootstrap token is re-initialised from the environment.
        """
        with self._lock:
            self._pending.clear()
            if bootstrap_token is not None:
                self._bootstrap_token = bootstrap_token
                self._bootstrap_generated = False
            else:
                self._init_bootstrap_from_env()


# Global instance used by the API layer.
setup_token_manager = SetupTokenManager()
