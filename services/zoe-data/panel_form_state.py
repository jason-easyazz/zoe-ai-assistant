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

# panel_id → {panel_type, slots, opened_at, expire_at}
_ACTIVE_FORMS: dict[str, dict] = {}

# Forms auto-expire after 10 minutes of inactivity to prevent ghost state.
_FORM_TTL_S: float = 600.0


def set_active_form(panel_id: str, panel_type: str, slots: Optional[dict] = None) -> None:
    """Register an open action form for a panel."""
    now = time.monotonic()
    _ACTIVE_FORMS[panel_id] = {
        "panel_type": panel_type,
        "slots": slots or {},
        "opened_at": now,
        "expire_at": now + _FORM_TTL_S,
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


def clear_active_form(panel_id: str) -> None:
    """Remove the active form state for a panel (on confirm or cancel)."""
    _ACTIVE_FORMS.pop(panel_id, None)


def is_form_active(panel_id: str) -> bool:
    return get_active_form(panel_id) is not None
