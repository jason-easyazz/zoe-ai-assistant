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
        # True when the bootstrap token is a pinned ZOE_AUTH_SETUP_TOKEN. A pinned
        # token survives restarts, so its one-time consumption must be enforced
        # durably and fail CLOSED; a generated token is replaced by a fresh random
        # value on rotation/restart, so the in-memory used-flag is sufficient.
        self._bootstrap_from_env = False
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
            self._bootstrap_from_env = True
            logger.info("Setup bootstrap token loaded from ZOE_AUTH_SETUP_TOKEN (one-time use)")
        else:
            self._bootstrap_token = secrets.token_urlsafe(24)
            self._bootstrap_generated = True
            self._bootstrap_from_env = False
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
        """Burn the current bootstrap token and mint+log a fresh (generated) one."""
        self._bootstrap_token = secrets.token_urlsafe(24)
        self._bootstrap_generated = True
        self._bootstrap_from_env = False  # the rotated-in value is generated, not pinned
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
        # restart, so consult the durable consumed-marker. Fail CLOSED for a
        # pinned token when the marker is already set OR cannot be read (we cannot
        # prove it was not already spent). A generated token can't be revalidated
        # this way (rotation/restart replaces it with a new random value), so an
        # undetermined read there is treated as "not consumed".
        state = self._durable_consumed_state(token)
        if state is True:
            return False
        if state is None and self._bootstrap_from_env:
            logger.error(
                "Refusing pinned bootstrap setup token: cannot confirm it is "
                "unspent (audit_logs unavailable) — failing closed."
            )
            return False
        return True

    # -- durable bootstrap-consumed marker (survives restart) ----------------
    def _durable_consumed_state(self, token: str) -> Optional[bool]:
        """True if durably marked consumed, False if not, None if undeterminable."""
        try:
            from models.database import auth_db
            with auth_db.get_connection() as conn:
                row = conn.execute(
                    "SELECT 1 FROM audit_logs WHERE action = ? AND resource = ? LIMIT 1",
                    ("setup_bootstrap_consumed", _hash(token)),
                ).fetchone()
                return row is not None
        except Exception:
            # No audit table / DB unavailable (e.g. unit tests).
            return None

    def _persist_bootstrap_consumed(self, token: str) -> bool:
        """Durably record consumption. Returns True on success, False otherwise."""
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
            return True
        except Exception as exc:
            logger.debug("Could not persist bootstrap-consumed marker: %s", exc)
            return False

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
                from_env = self._bootstrap_from_env
                persisted = self._persist_bootstrap_consumed(token)
                # A pinned (env) token survives restarts, so refuse to spend it
                # unless we durably recorded the consumption — otherwise a restart
                # could revalidate it. A generated token needs no durable record.
                if from_env and not persisted:
                    logger.error(
                        "Refusing pinned bootstrap setup: cannot durably record "
                        "consumption (audit_logs unavailable) — failing closed."
                    )
                    return None
                self._bootstrap_used = True
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
                self._bootstrap_from_env = False
            else:
                self._init_bootstrap_from_env()


# Global instance used by the API layer.
setup_token_manager = SetupTokenManager()
