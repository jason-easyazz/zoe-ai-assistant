"""Shared helpers for Zoe capability governance modules."""

from __future__ import annotations

from typing import Sequence


def merge_string_refs(*groups: Sequence[str]) -> tuple[str, ...]:
    """Merge ordered string reference groups while preserving first occurrence."""

    values: list[str] = []
    for group in groups:
        values.extend(str(value) for value in group if value is not None and str(value))
    return tuple(dict.fromkeys(values))


__all__ = ["merge_string_refs"]
