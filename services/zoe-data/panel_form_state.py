"""
Shared in-memory state for active action-form panels.

Tracks which panels currently have a full-screen action form overlay open
so that incoming voice utterances can be routed to field-filling instead
of the main chat pipeline.

Both voice_tts.py and routers/ui_actions.py import from this module.
State is per-process (fine for single-process uvicorn deployments).
"""
from __future__ import annotations

import time
from typing import Optional

# panel_id → {panel_type, slots, opened_at, expire_at, owner_user_id}
_ACTIVE_FORMS: dict[str, dict] = {}

# Forms auto-expire after 10 minutes of inactivity to prevent ghost state.
_FORM_TTL_S: float = 600.0


def set_active_form(
    panel_id: str,
    panel_type: str,
    slots: Optional[dict] = None,
    owner_user_id: Optional[str] = None,
) -> None:
    """Register an open action form for a panel.

    ``owner_user_id`` is the member the form was presented FOR (set server-side by the voice
    path). It is the authority ``confirm_action_form`` checks, because the push socket that
    delivers the form is unauthenticated: whoever is standing at a guest-recovered panel
    sees the form, and their Done must not write the owner's data under ``guest``. The panel's
    own ``/panel/form/open`` call re-registers the form without an owner; that must not erase
    one already bound for the same live form, so it is carried over.
    """
    now = time.monotonic()
    prev = _ACTIVE_FORMS.get(panel_id)
    if (
        owner_user_id is None
        and prev is not None
        and prev.get("panel_type") == panel_type
        and now <= prev["expire_at"]
    ):
        owner_user_id = prev.get("owner_user_id")
    _ACTIVE_FORMS[panel_id] = {
        "panel_type": panel_type,
        "slots": slots or {},
        "opened_at": now,
        "expire_at": now + _FORM_TTL_S,
        "owner_user_id": owner_user_id,
    }


def get_active_form(panel_id: str) -> Optional[dict]:
    """Return the active form data for a panel, or None if expired/absent."""
    entry = _ACTIVE_FORMS.get(panel_id)
    if entry is None:
        return None
    if time.monotonic() > entry["expire_at"]:
        del _ACTIVE_FORMS[panel_id]
        return None
    return entry


def adopt_owner(panel_id: Optional[str], user_id: Optional[str]) -> bool:
    """Bind ``user_id`` as the owner of a live form that has none yet (never overwrites one).

    Forms the panel opens itself (chat-driven ``panel_show_action_form``) reach
    ``/panel/form/open`` with no voice-bound owner; the authenticated caller there IS who the
    panel is acting as, so a guest-opened form becomes the guest's to save and a member-opened
    one stays the member's after a guest downgrade. Returns True when it bound an owner.
    """
    entry = get_active_form(panel_id) if panel_id else None
    uid = str(user_id or "").strip()
    if entry is None or entry.get("owner_user_id") or not uid:
        return False
    entry["owner_user_id"] = uid
    return True


def confirm_is_misattributed(panel_id: Optional[str], confirmer_user_id: Optional[str]) -> bool:
    """Must a Done from ``confirmer_user_id`` be REFUSED rather than saved as ``guest``?

    Fails CLOSED for a guest: a guest may save only a form whose bound owner is ``guest``. A
    member's form (the panel fell back to guest after the PIN session expired), a form with no
    bound owner, and no form at all (past ``_FORM_TTL_S``, a zoe-data restart, another panel, no
    panel id; the browser overlay has no matching expiry) are all refused, since the write would
    otherwise land under ``guest`` while the panel says it saved. A signed-in member is
    authenticated and is never refused here, even for a form this process no longer knows.
    """
    if str(confirmer_user_id or "").strip().lower() not in {"", "guest"}:
        return False
    entry = get_active_form(panel_id) if panel_id else None
    owner = str((entry or {}).get("owner_user_id") or "").strip().lower()
    return owner != "guest"


def clear_active_form(panel_id: str) -> None:
    """Remove the active form state for a panel (on confirm or cancel)."""
    _ACTIVE_FORMS.pop(panel_id, None)


def is_form_active(panel_id: str) -> bool:
    return get_active_form(panel_id) is not None
