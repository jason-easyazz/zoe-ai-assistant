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
- **The SERVER picks whose biometric an enrolment writes** (2026-10-19):
  a session enrols itself (admins excepted); a device token only a member
  the server verified at that panel (`resolve_enroll_target`, below).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException

from auth import is_admin_role

logger = logging.getLogger(__name__)

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


# ── Enrolment target: decided by the SERVER, never by a device payload (security fix 2026-10-19) ──────────────────────────
#
# Voice is a password now (``ZOE_VOICE_SESSION``, ResNet34 acting): a voiceprint enrolled onto a member IS that member's key.
# A panel device token is a shared credential in a file on the Pi, so "whoever holds the token" is not "the member at the
# panel". Before this fix ``/api/voice/enroll`` and ``/api/face/enroll`` took the member from ``payload.user_id`` for a device
# caller, so anyone with a copy of the token could enrol THEIR voice onto any member. Now a device-token enrolment may only
# write a member the SERVER already verified at THAT panel (the token's own ``panel_id``, never the payload's):
#
#   1. an ENROLMENT GRANT, minted by ``voice_enrollment.start_enrollment`` when it queues the ``voice_enroll`` command for a
#      member the server resolved from a verified turn ("learn my voice", a "yes" to the offer). Voice only, time-boxed to
#      the command's life, closed by the daemon's ``/api/enrollment/result``; or
#   2. the panel's FRESH PIN sign-in (``routers.voice_tts._resolve_fresh_panel_member``: the freshness-gated
#      ``ui_panel_sessions`` authority every voice lane uses; a stale or lapsed sign-in is nobody). This keeps the owner's
#      guided ``zoe_enroll_flow.py`` (face and voice) working: sign in with the PIN at the panel, then run it.
#
# A payload ``user_id`` may only NAME one of those members (checked, never trusted); naming anyone else is a 403. No
# server-verified member at the panel is a 403. A voice session alone does not authorise an enrolment (biometrics are a
# step-up: the PIN). Grants are in-process state (zoe-data is one uvicorn process): a restart mid-enrolment drops the grant
# and the upload is refused (fail closed) unless the member is PIN-signed-in at the panel.

ENROLL_GRANT_KINDS = frozenset({"voice"})

NO_VERIFIED_MEMBER = ("enrolling at the panel needs the member signed in at this panel with their PIN, "
                      "or Zoe's own enrolment prompt")
NOT_THE_PANEL_MEMBER = "that member is not the one signed in at this panel"


@dataclass
class _Grant:
    user_id: str
    kind: str
    expires: float


_GRANTS: dict = {}


def grant_enrolment(panel_id: str, user_id: str, *, ttl_s: float, kind: str = "voice") -> None:
    """Record that the server asked ``panel_id`` to enrol ``user_id`` (a member it resolved from a verified turn)."""
    pid, uid = str(panel_id or "").strip(), str(user_id or "").strip()
    if not pid or not uid or uid in (DEVICE_PSEUDO_USER, "guest") or kind not in ENROLL_GRANT_KINDS:
        return
    _GRANTS[pid] = _Grant(user_id=uid, kind=kind, expires=time.monotonic() + max(1.0, float(ttl_s)))
    logger.info("zoe.enroll_grant opened panel=%s member=%s kind=%s ttl=%ds", pid, uid, kind, int(ttl_s))


def granted_member(panel_id: str, kind: str = "voice") -> Optional[str]:
    """The member of ``panel_id``'s live enrolment grant of ``kind``, else None (an expired grant is dropped)."""
    pid = str(panel_id or "").strip()
    g = _GRANTS.get(pid)
    if g is None:
        return None
    if g.expires <= time.monotonic():
        _GRANTS.pop(pid, None)
        return None
    return g.user_id if g.kind == kind else None


def close_enrolment_grant(panel_id: str, reason: str = "result") -> Optional[str]:
    """Close ``panel_id``'s grant. Returns the member it was for while it was still live, else None."""
    pid = str(panel_id or "").strip()
    live = granted_member(pid, _GRANTS[pid].kind) if pid in _GRANTS else None
    if _GRANTS.pop(pid, None) is not None:
        logger.info("zoe.enroll_grant closed panel=%s reason=%s", pid, reason)
    return live


def reset_enrolment_grants() -> None:
    """Tests only."""
    _GRANTS.clear()


def require_local_panel(caller: dict, what: str) -> None:
    """403 unless a DEVICE caller presented its token on the home LAN: not over the Cloudflare tunnel, not over the relay.

    A device token proves which panel, not that anyone stands at it. Copied off the Pi it works from anywhere, so the two
    things it must never do remotely are dump the household's biometric embeddings (``/api/{voice,face}/profiles/sync``)
    and write a biometric (enrolment). The panel itself reaches zoe-data on the LAN (``ZOE_URL`` = the box's LAN address),
    so this costs it nothing. A missing marker reads as remote (fail closed)."""
    c = caller or {}
    if c.get("source") != "device":
        return
    if c.get("via_relay") is not False or c.get("via_tunnel", True) is not False:
        logger.warning("zoe.biometric refused %s panel=%s reason=not_on_the_lan", what, c.get("panel_id") or "-")
        raise HTTPException(status_code=403, detail=f"{what} is only available to a panel on the home network")


async def _fresh_panel_member(panel_id: str) -> Optional[str]:
    """The panel's FRESH PIN sign-in member (late import: the voice router imports this module)."""
    from routers.voice_tts import _resolve_fresh_panel_member

    return await _resolve_fresh_panel_member(panel_id)


async def resolve_enroll_target(payload_user_id: object, caller: dict, *, kind: str) -> Optional[str]:
    """Decide whose profile an enroll call may write. ``kind`` is ``"voice"`` or ``"face"``.

    Enrolling under someone else's ``user_id`` is identity takeover of the whole biometric layer, so the target is never
    simply whatever the payload asked for:

    - session -> the caller's own id, unless the caller is an admin, who may enrol on another member's behalf. This mirrors
      ``routers/proactive.py`` ``create_schedule``: ``body.user_id if body.user_id and admin else caller``. None when there
      is no usable id, leaving the router's "a real user_id is required" validation to produce the error.
    - device token -> ONLY a member the server verified at the token's own panel (the block comment above); 403 otherwise.
    """
    caller = caller or {}
    requested = str(payload_user_id or "").strip()
    if caller.get("source") != "device":
        caller_id = str(caller.get("user_id") or "").strip() or None
        if requested and is_admin_role(caller.get("role")):
            return requested
        return caller_id

    require_local_panel(caller, "biometric enrolment")
    panel = str(caller.get("panel_id") or "").strip()
    allowed: list = []
    via_grant = None
    if panel:
        via_grant = granted_member(panel, kind)
        if via_grant:
            allowed.append(via_grant)
        try:
            pin_member = await _fresh_panel_member(panel)
        except Exception as exc:  # noqa: BLE001 - an unreadable sign-in is nobody (fail closed)
            logger.warning("zoe.enroll fresh sign-in lookup failed (%s) - no PIN member", type(exc).__name__)
            pin_member = None
        pin_member = str(pin_member or "").strip()
        if pin_member and pin_member not in allowed and pin_member not in (DEVICE_PSEUDO_USER, "guest"):
            allowed.append(pin_member)
    if not allowed:
        logger.warning("zoe.enroll refused kind=%s panel=%s reason=no_verified_member", kind, panel or "-")
        raise HTTPException(status_code=403, detail=NO_VERIFIED_MEMBER)
    if requested and requested not in allowed:
        logger.warning("zoe.enroll refused kind=%s panel=%s reason=payload_names_another_member", kind, panel)
        raise HTTPException(status_code=403, detail=NOT_THE_PANEL_MEMBER)
    target = requested or allowed[0]
    logger.info("zoe.enroll allowed kind=%s panel=%s member=%s by=%s", kind, panel, target,
                "grant" if target == via_grant else "pin")
    return target
