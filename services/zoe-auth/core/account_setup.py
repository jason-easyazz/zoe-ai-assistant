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

Per-user token state is in-process (single long-lived uvicorn worker); a restart
asks the admin to re-mint those. Bootstrap *consumption* is additionally recorded
in the existing ``audit_logs`` table (best-effort) so a pinned
``ZOE_AUTH_SETUP_TOKEN`` cannot be revalidated by restarting the service. No
database schema is added (the auth schema is owned outside this service).
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
        # The bootstrap token is ONE-TIME: once it successfully sets a password it
        # is burned and a fresh one is rotated in (and logged), so a single leaked
        # bootstrap token can claim at most one account.
        self._bootstrap_used = False
        if self._bootstrap_token is None:
            self._init_bootstrap_from_env()

    # -- bootstrap token ------------------------------------------------
    def _init_bootstrap_from_env(self) -> None:
        self._bootstrap_used = False
        env_token = os.getenv("ZOE_AUTH_SETUP_TOKEN", "").strip()
        if env_token:
            self._bootstrap_token = env_token
            self._bootstrap_generated = False
            logger.info("Setup bootstrap token loaded from ZOE_AUTH_SETUP_TOKEN (one-time use)")
        else:
            self._bootstrap_token = secrets.token_urlsafe(24)
            self._bootstrap_generated = True
            self._log_bootstrap(self._bootstrap_token)

    def _log_bootstrap(self, token: str) -> None:
        logger.warning(
            "One-time bootstrap setup token for first-run password setup: %s\n"
            "Use it as the `setup_token` field of POST /api/auth/password/setup. "
            "It is consumed after one successful setup; a fresh one is then logged. "
            "Set ZOE_AUTH_SETUP_TOKEN to seed a known initial value.",
            token,
        )

    def _rotate_bootstrap(self) -> None:
        """Burn the current bootstrap token and mint+log a fresh one."""
        self._bootstrap_token = secrets.token_urlsafe(24)
        self._bootstrap_generated = True
        self._bootstrap_used = False
        self._log_bootstrap(self._bootstrap_token)

    @property
    def bootstrap_token(self) -> Optional[str]:
        """The current bootstrap token (for the local operator / tests)."""
        return self._bootstrap_token

    def _matches_bootstrap(self, token: str) -> bool:
        if not self._bootstrap_token or self._bootstrap_used:
            return False
        if not hmac.compare_digest(token, self._bootstrap_token):
            return False
        # A pinned ZOE_AUTH_SETUP_TOKEN reloads with _bootstrap_used reset after a
        # restart; consult the durable consumed-marker so an already-spent token
        # cannot be revalidated by bouncing the service.
        return not self._is_bootstrap_consumed_persisted(token)

    # -- durable bootstrap-consumed marker (best-effort, survives restart) ----
    def _is_bootstrap_consumed_persisted(self, token: str) -> bool:
        try:
            from models.database import auth_db
            with auth_db.get_connection() as conn:
                row = conn.execute(
                    "SELECT 1 FROM audit_logs WHERE action = ? AND resource = ? LIMIT 1",
                    ("setup_bootstrap_consumed", _hash(token)),
                ).fetchone()
                return row is not None
        except Exception:
            # No audit table / DB unavailable (e.g. unit tests) — fall back to the
            # in-memory flag only.
            return False

    def _persist_bootstrap_consumed(self, token: str) -> None:
        try:
            from models.database import auth_db
            with auth_db.get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO audit_logs
                    (log_id, user_id, action, resource, result, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"setupboot_{secrets.token_hex(8)}", None,
                        "setup_bootstrap_consumed", _hash(token), "success",
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
        except Exception as exc:  # pragma: no cover - best effort
            logger.debug("Could not persist bootstrap-consumed marker: %s", exc)

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

    # -- atomic claim ---------------------------------------------------
    def claim(self, user_id: str, token: Optional[str]) -> Optional[str]:
        """Atomically verify AND consume a setup credential for ``user_id``.

        Returns ``"pair"`` for a valid per-user token (which is removed here),
        ``"bootstrap"`` for the one-time bootstrap token (which is burned,
        durably marked consumed, and rotated here), or ``None``. Verify and
        consume happen under one lock so two concurrent setup requests cannot
        both pass with the same token (TOCTOU) — only the first wins.
        """
        if not token:
            return None
        with self._lock:
            if self._matches_pending(user_id, token):
                self._pending.pop(user_id, None)
                return "pair"
            if self._matches_bootstrap(token):
                self._bootstrap_used = True
                self._persist_bootstrap_consumed(token)
                self._rotate_bootstrap()
                return "bootstrap"
            return None

    def clear_pending(self, user_id: str) -> None:
        """Drop any stale per-user token for ``user_id`` (admin-session path)."""
        with self._lock:
            self._pending.pop(user_id, None)

    def reset(self, bootstrap_token: Optional[str] = None) -> None:
        """Clear all state. Intended for tests.

        If ``bootstrap_token`` is given it becomes the new (unused) bootstrap
        secret; otherwise the bootstrap token is re-initialised from the
        environment.
        """
        with self._lock:
            self._pending.clear()
            self._bootstrap_used = False
            if bootstrap_token is not None:
                self._bootstrap_token = bootstrap_token
                self._bootstrap_generated = False
            else:
                self._init_bootstrap_from_env()


# Global instance used by the API layer.
setup_token_manager = SetupTokenManager()
