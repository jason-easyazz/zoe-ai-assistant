"""Ownership scoping for biometric profiles (voiceprints + faceprints).

A biometric profile is the most personal row Zoe stores, and the household
shares one LAN. Both biometric routers (`routers/voice_tts.py` speaker
profiles, `routers/face_id.py` face profiles) authenticate through
`_require_voice_auth`, which answers "is this caller allowed to touch the
voice surface at all" — device token OR any non-guest session. That is an
AUTHENTICATION gate, not an AUTHORISATION one: on its own it lets any
signed-in household member list, re-target, or delete anybody else's
profile. These helpers are the missing authorisation half, kept in one place
so the two routers cannot drift apart.

The rules, all fail-closed:

- **A device token owns nothing.** It is a SHARED panel credential (the
  kitchen touchscreen), not a person, so it can neither enumerate nor delete
  household biometric profiles. The panel's legitimate need is the embedding
  feed for on-device matching — `GET /api/{voice,face}/profiles/sync`, which
  is already device-token-only — not personal profile management.
- **A session caller sees and deletes its own rows.** An admin may act across
  the household, explicitly and never implicitly, matching how the rest of
  the codebase treats `users.role` (see `routers/proactive.py`).
- **404 before 403.** An unknown profile id is "not found"; a known id owned
  by someone else is "not yours" — the same order `routers/proactive.py`
  `delete_schedule` and `routers/lists.py` use.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException

from auth import is_admin_role

# A device token authenticates a panel, and every panel presents this
# placeholder user_id. It is not a `users` row and must never match an owner.
DEVICE_PSEUDO_USER = "voice-daemon"


def require_person_scope(caller: dict) -> tuple[str, bool]:
    """Resolve a caller to (user_id, is_admin) for a person-scoped operation.

    Raises 403 for a device token: profile management is a personal act by a
    signed-in human, and a shared panel credential is not a person. Nothing on
    the Pi calls the management endpoints — the daemons only pull
    `/profiles/sync` — so this closes a hole without removing a capability.
    """
    caller = caller or {}
    if caller.get("source") != "session":
        raise HTTPException(
            status_code=403,
            detail="biometric profile management requires a signed-in household member",
        )
    user_id = str(caller.get("user_id") or "").strip()
    if not user_id or user_id == DEVICE_PSEUDO_USER:
        raise HTTPException(
            status_code=403,
            detail="biometric profile management requires a signed-in household member",
        )
    return user_id, is_admin_role(caller.get("role"))


def authorize_profile_access(
    owner_user_id: Optional[str],
    caller_user_id: str,
    is_admin: bool,
    *,
    kind: str,
) -> None:
    """Gate one already-fetched profile row for the caller. Never returns a value.

    `owner_user_id is None` means the row does not exist. 404-then-403 leaks
    that an opaque UUID is a live profile id, which is the deliberate trade the
    surrounding routers already make: it keeps "you asked for something that
    isn't there" distinguishable from "that isn't yours" for the settings UI,
    and the ids are unguessable UUID4s, so it grants no enumeration.
    """
    if owner_user_id is None:
        raise HTTPException(status_code=404, detail=f"{kind} profile not found")
    if owner_user_id != caller_user_id and not is_admin:
        raise HTTPException(status_code=403, detail=f"Not your {kind} profile")


def resolve_enroll_target(payload_user_id: object, caller: dict) -> Optional[str]:
    """Decide whose profile an enroll call may write.

    Enrolling under someone else's `user_id` is identity takeover of the whole
    biometric layer — enrol your own face as "jason" and the panel greets you
    as Jason — so the target is not simply whatever the payload asked for:

    - device token → the payload's `user_id` stands. The panel runs the guided
      enrolment flow on behalf of the person standing in front of it, and it
      already needs a device token to reach the endpoint.
    - session → the caller's own id, unless the caller is an admin, who may
      enrol on another member's behalf. This mirrors `routers/proactive.py`
      `create_schedule`: ``body.user_id if body.user_id and admin else caller``.

    Returns None when there is no usable id, leaving the router's existing
    "a real user_id is required" validation to produce the error.
    """
    caller = caller or {}
    requested = str(payload_user_id or "").strip()
    if caller.get("source") == "device":
        return requested or None

    caller_id = str(caller.get("user_id") or "").strip() or None
    if requested and is_admin_role(caller.get("role")):
        return requested
    return caller_id
