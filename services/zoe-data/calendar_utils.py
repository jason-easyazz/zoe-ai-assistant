"""Shared calendar data normalization helpers."""

from __future__ import annotations

import json


def row_to_event(row) -> dict:
    """Convert a calendar row to an event dict, parsing persisted metadata."""
    d = dict(row)
    if d.get("metadata") is not None and isinstance(d["metadata"], str):
        try:
            d["metadata"] = json.loads(d["metadata"]) if d["metadata"] else None
        except json.JSONDecodeError:
            d["metadata"] = None
    if "all_day" in d and d["all_day"] is not None:
        d["all_day"] = bool(d["all_day"])
    if "deleted" in d and d["deleted"] is not None:
        d["deleted"] = bool(d["deleted"])
    return d
