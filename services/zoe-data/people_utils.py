"""Shared people data normalization helpers."""

from __future__ import annotations

import json


def row_to_person(row) -> dict:
    """Convert a people row to the canonical person dict used by UI surfaces."""
    try:
        pref = row["preferences"] if hasattr(row, "__getitem__") else getattr(row, "preferences", None)
    except Exception:
        pref = None
    d = dict(row) if not isinstance(row, dict) else row
    if pref is None:
        pref = d.get("preferences")
    if pref and isinstance(pref, str):
        try:
            pref = json.loads(pref)
        except json.JSONDecodeError:
            pref = None
    return {
        "id": d.get("id"),
        "user_id": d.get("user_id"),
        "name": d.get("name"),
        "relationship": d.get("relationship"),
        "circle": d.get("circle", "circle"),
        "context": d.get("context", "personal"),
        "email": d.get("email"),
        "phone": d.get("phone"),
        "birthday": d.get("birthday"),
        "notes": d.get("notes"),
        "preferences": pref,
        "visibility": d.get("visibility"),
        "health_score": d.get("health_score", 0.5),
        "notification_count": d.get("notification_count", 0),
        "contact_count": d.get("contact_count", 0),
        "last_contacted_at": d.get("last_contacted_at"),
        "is_partial": bool(d.get("is_partial", 0)),
        "how_we_met": d.get("how_we_met"),
        "first_met_date": d.get("first_met_date"),
        "introduced_by_person_id": d.get("introduced_by_person_id"),
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
    }
