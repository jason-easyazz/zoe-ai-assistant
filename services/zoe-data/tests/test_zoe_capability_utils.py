"""Focused tests for the zoe_capability_utils.merge_string_refs pure helper."""

import pytest

from zoe_capability_utils import merge_string_refs

pytestmark = pytest.mark.ci_safe


def test_module_exports_merge_string_refs():
    # __all__ is the module's public contract; downstream capability modules
    # import the helper by name, so the export must stay stable.
    import zoe_capability_utils

    assert zoe_capability_utils.__all__ == ["merge_string_refs"]


def test_no_groups_returns_empty_tuple():
    # Capability callers may forward an empty varargs list when a plan has
    # no refs to merge; the helper must degrade to an empty tuple, not None.
    result = merge_string_refs()

    assert result == ()
    assert isinstance(result, tuple)


def test_single_group_preserves_values_in_order():
    # First-occurrence ordering is the helper's whole point: it lets callers
    # pass canonical refs first and override duplicates with later groups.
    result = merge_string_refs(["pr-1", "pr-2", "pr-3"])

    assert result == ("pr-1", "pr-2", "pr-3")


def test_multiple_groups_concatenate_in_argument_order():
    # Argument order is the merge priority order used by the capability
    # governance flows (e.g. decision refs first, candidate evidence refs last).
    result = merge_string_refs(
        ["pr-1", "pr-2"],
        ["ev-1", "ev-2"],
        ["rb-1"],
    )

    assert result == ("pr-1", "pr-2", "ev-1", "ev-2", "rb-1")


def test_duplicates_within_a_single_group_are_collapsed():
    # The helper must emit each value at most once, even when a single
    # group contains repeats (common in evidence_refs aggregates).
    result = merge_string_refs(["a", "b", "a", "c", "b"])

    assert result == ("a", "b", "c")


def test_duplicates_across_groups_keep_first_occurrence():
    # First-occurrence semantics: if an approval ref and an evidence ref
    # collide, the approval ref (passed first) wins and stays in front.
    result = merge_string_refs(
        ["approval-1", "evidence-1"],
        ["evidence-1", "rollback-1"],
    )

    assert result == ("approval-1", "evidence-1", "rollback-1")


def test_none_and_empty_string_values_are_filtered():
    # Defensive normalization: SQL/JSON paths may surface None or "" values
    # that must not leak into the ref tuples used by capability gates.
    # The source's Sequence[str] annotation is overly strict; the helper
    # filters None defensively, which is the behavior this test pins.
    group_a: list = ["keep-me", None, "", "also-keep"]
    group_b: list = ["", "more", None]
    result = merge_string_refs(group_a, group_b)

    assert result == ("keep-me", "also-keep", "more")
    assert None not in result
    assert "" not in result


def test_whitespace_only_string_is_preserved():
    # The helper only filters the literal empty string; non-empty
    # whitespace survives because callers may rely on it as a sentinel.
    result = merge_string_refs(["a", "   ", "b"])

    assert result == ("a", "   ", "b")


def test_non_string_iterable_members_are_stringified():
    # Capability plans are dataclass-driven; refs may originate as ints
    # (memory ids) before being normalized — the helper must coerce safely.
    # The source's Sequence[str] annotation is overly strict; the helper
    # runs str(...) on every member, which is the behavior this test pins.
    group_a: list = [1, 2, 3]
    group_b: list = ["4", 5]
    result = merge_string_refs(group_a, group_b)

    assert result == ("1", "2", "3", "4", "5")


def test_accepts_tuples_and_lists_interchangeably():
    # Callers pass mixed sequence types (tuples from dataclasses, lists
    # from JSON-decoded payloads); both must behave identically.
    from_list = merge_string_refs(["a", "b"], ["b", "c"])
    from_tuple = merge_string_refs(("a", "b"), ("b", "c"))

    assert from_list == from_tuple == ("a", "b", "c")


def test_none_as_group_raises_type_error():
    # A None group is a programming error (not a valid sequence); the
    # helper must not silently swallow it. We assert the type so callers
    # surface a clear stack trace at the call site.
    with pytest.raises(TypeError):
        merge_string_refs(["a"], None)  # type: ignore[arg-type]
