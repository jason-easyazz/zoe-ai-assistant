"""Shared people data normalization helpers."""

from __future__ import annotations

import json
import unicodedata


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


def _fold_name(name: str) -> str:
    """Case- and accent-folded, whitespace-collapsed name. Pure."""
    t = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return " ".join(t.split())


def name_covered_by_contacts(name: str, existing_names) -> bool:
    """Is ``name`` somebody the user ALREADY has a contact for? Pure, language-independent.

    The full name matches (case/accent-folded), or - for a bare single word like "Marisol" - the word is the first
    or last word of any live contact's name. "Marisol" with "Marisol Okafor" and "Marisol Vance" on the list is not a
    new person to offer to add; it is a name the owner already has (twice), and offering "Would you like me to add
    Marisol as a contact?" on an unrelated turn is what the person bench's P7.b scored as a needless question.
    A multi-word name that differs from every contact ("Marisol Quinn") is still new."""
    want = _fold_name(name)
    if not want:
        return False
    bare = " " not in want
    for existing in existing_names or ():
        have = _fold_name(str(existing or ""))
        if not have:
            continue
        if have == want:
            return True
        if bare:
            words = have.split()
            if want == words[0] or want == words[-1]:
                return True
    return False
