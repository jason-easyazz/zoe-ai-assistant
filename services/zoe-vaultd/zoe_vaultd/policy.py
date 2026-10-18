"""Proposals, approvals, sign-in capabilities, rate limits and the U3 unlock gate (credential-vault.md sections 5.2,
7 and 10.3). Everything here lives only in the broker's memory and is wiped by `lock`.

A capability is minted by the broker only after the credential owner approved the proposal on the broker's own phone
page. It is single use and bound to (member, item, errand, step, operation, expected origin, expiry, nonce). The
caller never asserts an approval: a bare item id, errand id, proposal id or "approved" flag is never accepted in place
of the capability token (cell V6).
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field

APPROVAL_TIMEOUT_S = 120          # owner decision O11
CAPABILITY_TTL_S = 600            # a capability must be used inside the errand's approval window
UNLOCK_TOKEN_TTL_S = 600          # 5.2: the U3 unlock link expires after 10 minutes
UNLOCK_MAX_FAILS = 3              # 5.2: three failed attempts lock the verb ...
UNLOCK_LOCKOUT_S = 900            # ... for 15 minutes
OTP_TIMEOUT_S = 120
HOUR = 3600.0

# 10.3 approval fatigue: prompts per rolling hour, and refusals/timeouts that pause one errand.
LIMIT_PER_ITEM = 3
LIMIT_PER_MEMBER = 6
LIMIT_HOUSEHOLD = 12
ERRAND_PAUSE_AFTER = 3

SENSITIVE = frozenset({"money", "government", "health", "email"})
CATEGORIES = frozenset({"money", "government", "health", "email", "shopping", "utility", "school", "other"})


class PolicyRefusal(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "replace")).hexdigest()


def is_sensitive(category: str | None) -> bool:
    return category not in CATEGORIES or category in SENSITIVE


@dataclass
class Step:
    step_id: str
    item_id: str
    label: str
    category: str | None
    origin: str          # the expected (approved) top-level origin, computed by the broker from the vault
    login_url: str
    domain: str | None


@dataclass
class Proposal:
    proposal_id: str
    token: str
    member: str
    errand: str
    goal: str
    steps: list[Step]
    approver: str
    created: float
    expires: float
    state: str = "pending"   # pending | approved | denied | expired
    caps_issued: bool = False


@dataclass
class Capability:
    member: str
    item_id: str
    errand: str
    step_id: str
    operation: str
    origin: str
    login_url: str
    category: str | None
    label: str
    expires: float
    nonce: str = field(default_factory=lambda: secrets.token_hex(8))
    used: bool = False


class Policy:
    def __init__(self, *, clock=time.monotonic, approval_timeout_s: float = APPROVAL_TIMEOUT_S,
                 capability_ttl_s: float = CAPABILITY_TTL_S,
                 limits: tuple[int, int, int] = (LIMIT_PER_ITEM, LIMIT_PER_MEMBER, LIMIT_HOUSEHOLD)) -> None:
        self._clock = clock
        self.approval_timeout_s = approval_timeout_s
        self.capability_ttl_s = capability_ttl_s
        self.limit_item, self.limit_member, self.limit_household = limits
        self._lock = threading.Lock()
        self._proposals: dict[str, Proposal] = {}       # by proposal id
        self._by_token: dict[str, str] = {}             # sha256(approval token) -> proposal id
        self._caps: dict[str, Capability] = {}          # sha256(capability) -> capability
        self._prompts: deque[tuple[float, str, str]] = deque()   # (t, member, item)
        self._errand_failures: dict[str, int] = {}

    # -- proposals -----------------------------------------------------------------------------------------------
    def _expire(self, p: Proposal) -> None:
        if p.state == "pending" and self._clock() >= p.expires:
            p.state = "expired"
            self._errand_failures[p.errand] = self._errand_failures.get(p.errand, 0) + 1

    def _check_limits(self, member: str, items: list[str]) -> None:
        now = self._clock()
        while self._prompts and now - self._prompts[0][0] >= HOUR:
            self._prompts.popleft()
        if len(self._prompts) >= self.limit_household:
            raise PolicyRefusal("rate_limited")
        if sum(1 for _, m, _ in self._prompts if m == member) >= self.limit_member:
            raise PolicyRefusal("rate_limited")
        for item in set(items):
            if sum(1 for _, _, i in self._prompts if i == item) >= self.limit_item:
                raise PolicyRefusal("rate_limited")

    def new_proposal(self, *, member: str, errand: str, goal: str, steps: list[Step], approver: str) -> Proposal:
        with self._lock:
            for p in self._proposals.values():
                self._expire(p)
            if self._errand_failures.get(errand, 0) >= ERRAND_PAUSE_AFTER:
                raise PolicyRefusal("errand_paused")
            self._check_limits(member, [s.item_id for s in steps])
            now = self._clock()
            p = Proposal(proposal_id="p-" + secrets.token_hex(8), token=secrets.token_urlsafe(32), member=member,
                         errand=errand, goal=goal, steps=steps, approver=approver, created=now,
                         expires=now + self.approval_timeout_s)
            self._proposals[p.proposal_id] = p
            self._by_token[_key(p.token)] = p.proposal_id
            for s in steps:
                self._prompts.append((now, member, s.item_id))
            return p

    def by_token(self, token: str) -> Proposal | None:
        with self._lock:
            pid = self._by_token.get(_key(token or ""))
            p = self._proposals.get(pid) if pid else None
            if p is not None:
                self._expire(p)
            return p

    def decide(self, token: str, approve: bool) -> Proposal:
        with self._lock:
            pid = self._by_token.get(_key(token or ""))
            p = self._proposals.get(pid) if pid else None
            if p is None:
                raise PolicyRefusal("unknown_token")
            self._expire(p)
            if p.state != "pending":
                raise PolicyRefusal("proposal_" + p.state)
            p.state = "approved" if approve else "denied"
            # the approval token is single use either way
            self._by_token.pop(_key(token), None)
            if not approve:
                self._errand_failures[p.errand] = self._errand_failures.get(p.errand, 0) + 1
            return p

    def issue(self, proposal_id: str) -> tuple[str, list[tuple[str, str]]]:
        """('ok', [(step_id, capability)]) once per approved proposal; otherwise its state and no capability."""
        with self._lock:
            p = self._proposals.get(proposal_id or "")
            if p is None:
                return "unknown", []
            self._expire(p)
            if p.state != "approved":
                return p.state, []
            if p.caps_issued:
                return "already_issued", []
            p.caps_issued = True
            out = []
            expires = self._clock() + self.capability_ttl_s
            for s in p.steps:
                token = secrets.token_urlsafe(32)
                self._caps[_key(token)] = Capability(
                    member=p.member, item_id=s.item_id, errand=p.errand, step_id=s.step_id, operation="login",
                    origin=s.origin, login_url=s.login_url, category=s.category, label=s.label, expires=expires)
                out.append((s.step_id, token))
            return "ok", out

    def consume(self, token: str, *, member: str, errand: str, step: str, item: str,
                operation: str = "login") -> Capability:
        """Spend a capability. Every bound field must match the caller's request exactly; any use (even a refused
        one with the right token) burns it, so a capability can never be probed twice."""
        with self._lock:
            cap = self._caps.pop(_key(token or ""), None) if isinstance(token, str) and token else None
            if cap is None:
                raise PolicyRefusal("no_capability")
            if cap.used:
                raise PolicyRefusal("capability_used")
            cap.used = True
            if self._clock() >= cap.expires:
                raise PolicyRefusal("capability_expired")
            if (cap.member, cap.item_id, cap.errand, cap.step_id, cap.operation) != (member, item, errand, step,
                                                                                         operation):
                raise PolicyRefusal("capability_mismatch")
            return cap

    def pending_links(self) -> list[dict]:
        with self._lock:
            out = []
            for p in self._proposals.values():
                self._expire(p)
                if p.state == "pending" and _key(p.token) in self._by_token:
                    out.append({"kind": "approve", "member": p.approver, "proposal": p.proposal_id,
                                "path": "/v/approve/" + p.token})
            return out

    def wipe(self) -> None:
        with self._lock:
            self._proposals.clear()
            self._by_token.clear()
            self._caps.clear()


class UnlockGate:
    """U3 attended unlock: single-use tokens minted by the core while it is locked; 3 failures lock the verb."""

    def __init__(self, *, clock=time.monotonic, ttl_s: float = UNLOCK_TOKEN_TTL_S, max_fails: int = UNLOCK_MAX_FAILS,
                 lockout_s: float = UNLOCK_LOCKOUT_S) -> None:
        self._clock = clock
        self.ttl_s = ttl_s
        self.max_fails = max_fails
        self.lockout_s = lockout_s
        self._lock = threading.Lock()
        self._token_key: str | None = None
        self._token: str | None = None
        self._expires = 0.0
        self._fails = 0
        self._locked_until = 0.0

    def locked_out(self) -> bool:
        return self._clock() < self._locked_until

    def mint(self) -> str | None:
        with self._lock:
            if self._clock() < self._locked_until:
                return None
            if self._locked_until and self._clock() >= self._locked_until:
                self._locked_until = 0.0
                self._fails = 0
            self._token = secrets.token_urlsafe(32)
            self._token_key = _key(self._token)
            self._expires = self._clock() + self.ttl_s
            return self._token

    def current_link(self) -> str | None:
        with self._lock:
            if self._token is None or self._clock() >= self._expires or self._clock() < self._locked_until:
                return None
            return "/v/unlock/" + self._token

    def take(self, token: str) -> None:
        """Spend the token (single use). Raises PolicyRefusal when it is wrong, expired or the verb is locked."""
        with self._lock:
            if self._clock() < self._locked_until:
                raise PolicyRefusal("unlock_locked_out")
            if not token or self._token_key is None or _key(token) != self._token_key:
                raise PolicyRefusal("unlock_bad_token")
            expired = self._clock() >= self._expires
            self._token_key = None
            self._token = None
            if expired:
                raise PolicyRefusal("unlock_token_expired")

    def failed(self) -> bool:
        """Record a wrong password. Returns True when this failure locked the verb."""
        with self._lock:
            self._fails += 1
            if self._fails >= self.max_fails:
                self._locked_until = self._clock() + self.lockout_s
                return True
            return False

    def succeeded(self) -> None:
        with self._lock:
            self._fails = 0
            self._token_key = None
            self._token = None

    def clear(self) -> None:
        with self._lock:
            self._token_key = None
            self._token = None
