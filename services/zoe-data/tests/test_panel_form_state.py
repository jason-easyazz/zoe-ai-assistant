"""Focused unit tests for ``panel_form_state``.

This module owns the per-process dictionary of currently-open action-form
panels. Voice + UI code consults it to decide whether an incoming utterance
should fill a form field or fall through to the main chat pipeline, so the
round-trip and TTL semantics are load-bearing for routing correctness.

The module is intentionally pure (no I/O, no globals beyond the dict). The
tests pin the public contract:

* ``set_active_form`` then ``get_active_form`` round-trips ``panel_type``
  and ``slots``, and substitutes an empty dict when ``slots`` is omitted.
* ``get_active_form`` returns ``None`` for an unknown panel id.
* Entries past their ``expire_at`` are evicted on read and reported as
  inactive (``is_form_active`` flips to ``False`` too).
* ``clear_active_form`` removes state for a panel.

The autouse fixture below empties the module-level ``_ACTIVE_FORMS`` dict
between tests so each case starts with a clean slate regardless of
collection order.
"""

from __future__ import annotations

import pytest

import panel_form_state

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _reset_active_forms(monkeypatch):
    """Ensure each test sees an empty ``_ACTIVE_FORMS`` registry.

    The module keeps state in a module-level dict. Swapping in a fresh dict via
    ``monkeypatch.setattr`` (rather than mutating the real one with ``.clear()``)
    isolates each test *and* lets pytest restore the original object on teardown,
    so a crashing test can't leave the module's registry permanently emptied.
    """
    monkeypatch.setattr(panel_form_state, "_ACTIVE_FORMS", {})


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_set_then_get_round_trips_panel_type_and_slots():
    """Stored panel_type and slots come back unchanged via ``get_active_form``."""
    slots = {"name": "Alice", "amount": 42}
    panel_form_state.set_active_form(
        "panel_1", panel_type="add_person", slots=slots
    )

    entry = panel_form_state.get_active_form("panel_1")

    assert entry is not None
    assert entry["panel_type"] == "add_person"
    assert entry["slots"] == slots


def test_set_with_no_slots_defaults_to_empty_dict():
    """Omitting ``slots`` stores an empty dict, not ``None`` — callers can
    safely index into ``entry['slots']`` without a None-check."""
    panel_form_state.set_active_form("panel_2", panel_type="confirm_action")

    entry = panel_form_state.get_active_form("panel_2")

    assert entry is not None
    assert entry["panel_type"] == "confirm_action"
    assert entry["slots"] == {}
    # Specifically: not None.
    assert entry["slots"] is not None


def test_set_with_explicit_none_slots_also_defaults_to_empty_dict():
    """``slots=None`` is treated the same as the default — the public API
    promises a dict on read."""
    panel_form_state.set_active_form(
        "panel_3", panel_type="set_reminder", slots=None
    )

    entry = panel_form_state.get_active_form("panel_3")

    assert entry is not None
    assert entry["slots"] == {}


def test_set_records_opened_and_expire_timestamps():
    """Each entry carries the bookkeeping timestamps used for TTL."""
    panel_form_state.set_active_form("panel_ts", panel_type="x")

    entry = panel_form_state.get_active_form("panel_ts")

    assert entry is not None
    assert "opened_at" in entry
    assert "expire_at" in entry
    # expire_at is exactly _FORM_TTL_S after opened_at.
    assert entry["expire_at"] - entry["opened_at"] == pytest.approx(
        panel_form_state._FORM_TTL_S
    )


def test_set_overwrites_existing_entry_for_same_panel():
    """Reopening a form on the same panel replaces the previous entry —
    the panel_id is the registry key, not a list element."""
    panel_form_state.set_active_form(
        "panel_dup", panel_type="first", slots={"a": 1}
    )
    panel_form_state.set_active_form(
        "panel_dup", panel_type="second", slots={"b": 2}
    )

    entry = panel_form_state.get_active_form("panel_dup")

    assert entry is not None
    assert entry["panel_type"] == "second"
    assert entry["slots"] == {"b": 2}
    assert len(panel_form_state._ACTIVE_FORMS) == 1


def test_multiple_panels_are_tracked_independently():
    """The registry is keyed by panel_id, so different panels coexist."""
    panel_form_state.set_active_form(
        "p_a", panel_type="add_person", slots={"x": 1}
    )
    panel_form_state.set_active_form("p_b", panel_type="set_reminder")

    a = panel_form_state.get_active_form("p_a")
    b = panel_form_state.get_active_form("p_b")

    assert a is not None and b is not None
    assert a["panel_type"] == "add_person"
    assert b["panel_type"] == "set_reminder"
    assert a["slots"] == {"x": 1}
    assert b["slots"] == {}


# ---------------------------------------------------------------------------
# Absent panels
# ---------------------------------------------------------------------------


def test_get_active_form_returns_none_for_absent_panel():
    """An unknown panel_id has no entry — ``get_active_form`` returns None
    rather than raising KeyError."""
    panel_form_state.set_active_form("known", panel_type="x")

    assert panel_form_state.get_active_form("known") is not None
    assert panel_form_state.get_active_form("never_set") is None
    assert panel_form_state.get_active_form("") is None


def test_is_form_active_is_false_for_absent_panel():
    """``is_form_active`` mirrors ``get_active_form is not None`` for an
    unknown panel_id."""
    assert panel_form_state.is_form_active("never_set") is False


# ---------------------------------------------------------------------------
# TTL expiry
# ---------------------------------------------------------------------------


class _Clock:
    """Test double that yields a controllable monotonic value."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def test_get_returns_none_after_ttl_expiry_and_evicts(monkeypatch):
    """An entry past its ``expire_at`` is reported as missing and is
    removed from the registry on read."""
    clock = _Clock(start=1000.0)
    monkeypatch.setattr(panel_form_state.time, "monotonic", clock)

    panel_form_state.set_active_form("panel_ttl", panel_type="add_person")

    # Just before expiry: still present.
    clock.now = 1000.0 + panel_form_state._FORM_TTL_S - 1
    assert panel_form_state.get_active_form("panel_ttl") is not None
    assert "panel_ttl" in panel_form_state._ACTIVE_FORMS

    # Past expiry: returned as None and evicted from the registry.
    clock.now = 1000.0 + panel_form_state._FORM_TTL_S + 1
    assert panel_form_state.get_active_form("panel_ttl") is None
    assert "panel_ttl" not in panel_form_state._ACTIVE_FORMS


def test_is_form_active_flips_false_after_ttl_expiry(monkeypatch):
    """``is_form_active`` reflects the same TTL check as ``get_active_form`` —
    it must go from True to False once the entry expires."""
    clock = _Clock(start=2000.0)
    monkeypatch.setattr(panel_form_state.time, "monotonic", clock)

    panel_form_state.set_active_form("panel_active", panel_type="x")

    clock.now = 2000.0 + 1
    assert panel_form_state.is_form_active("panel_active") is True

    clock.now = 2000.0 + panel_form_state._FORM_TTL_S + 1
    assert panel_form_state.is_form_active("panel_active") is False


def test_get_does_not_evict_when_clock_equals_expire_at(monkeypatch):
    """The TTL comparison is strict (``>``, not ``>=``) — an entry at
    exactly ``expire_at`` is still considered live."""
    clock = _Clock(start=3000.0)
    monkeypatch.setattr(panel_form_state.time, "monotonic", clock)

    panel_form_state.set_active_form("panel_edge", panel_type="x")

    clock.now = 3000.0 + panel_form_state._FORM_TTL_S  # exactly expire_at
    entry = panel_form_state.get_active_form("panel_edge")

    assert entry is not None
    assert "panel_edge" in panel_form_state._ACTIVE_FORMS


# ---------------------------------------------------------------------------
# clear_active_form
# ---------------------------------------------------------------------------


def test_clear_active_form_removes_entry():
    """After ``clear_active_form`` the panel reports no active form and is
    gone from the registry."""
    panel_form_state.set_active_form("panel_clear", panel_type="x")

    assert panel_form_state.is_form_active("panel_clear") is True

    panel_form_state.clear_active_form("panel_clear")

    assert panel_form_state.get_active_form("panel_clear") is None
    assert panel_form_state.is_form_active("panel_clear") is False
    assert "panel_clear" not in panel_form_state._ACTIVE_FORMS


def test_clear_active_form_is_noop_on_absent_panel():
    """Clearing an unknown panel id does not raise and does not mutate
    other panels' state."""
    panel_form_state.set_active_form("panel_keep", panel_type="x")

    # Should not raise even though the panel was never registered.
    panel_form_state.clear_active_form("never_set")

    assert panel_form_state.is_form_active("panel_keep") is True
    assert panel_form_state.get_active_form("panel_keep") is not None


def test_clear_only_targets_the_named_panel():
    """``clear_active_form`` is per-panel — siblings are untouched."""
    panel_form_state.set_active_form("p_one", panel_type="x")
    panel_form_state.set_active_form("p_two", panel_type="y")

    panel_form_state.clear_active_form("p_one")

    assert panel_form_state.is_form_active("p_one") is False
    assert panel_form_state.is_form_active("p_two") is True


# ---------------------------------------------------------------------------
# is_form_active parity
# ---------------------------------------------------------------------------


def test_is_form_active_matches_get_active_form_for_present_panel():
    """``is_form_active`` is a thin wrapper around ``get_active_form`` and
    must agree for a freshly-opened entry."""
    panel_form_state.set_active_form(
        "panel_parity", panel_type="add_person", slots={"a": 1}
    )

    assert panel_form_state.is_form_active("panel_parity") is True
    assert (
        panel_form_state.is_form_active("panel_parity")
        == (panel_form_state.get_active_form("panel_parity") is not None)
    )


def test_is_form_active_remains_true_until_clear():
    """``is_form_active`` is True for as long as the entry is in the
    registry — only clear or TTL expiry flips it back to False."""
    panel_form_state.set_active_form("panel_life", panel_type="x")

    assert panel_form_state.is_form_active("panel_life") is True

    panel_form_state.clear_active_form("panel_life")

    assert panel_form_state.is_form_active("panel_life") is False
