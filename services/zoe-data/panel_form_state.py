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


def confirm_is_misattributed(panel_id: Optional[str], confirmer_user_id: Optional[str]) -> bool:
    """Would saving this Done under ``confirmer_user_id`` put a member's entry under ``guest``?

    True when the live form was presented for a named (non-guest) member but the Done arrives
    from the guest session (the panel fell back to guest after its PIN session expired, so it
    sends no ``X-Session-ID``). The caller refuses rather than write under the wrong identity.
    False with no bound owner (a form the panel opened itself, an expired form) or whenever the
    confirmer is a real member: this is a guest-vs-member guard, not a voice-vs-PIN identity check.
    """
    if not panel_id:
        return False
    entry = get_active_form(panel_id)
    owner = str((entry or {}).get("owner_user_id") or "").strip()
    if not owner or owner.lower() == "guest":
        return False
    return str(confirmer_user_id or "").strip().lower() in {"", "guest"}


def clear_active_form(panel_id: str) -> None:
    """Remove the active form state for a panel (on confirm or cancel)."""
    _ACTIVE_FORMS.pop(panel_id, None)


def is_form_active(panel_id: str) -> bool:
    return get_active_form(panel_id) is not None
