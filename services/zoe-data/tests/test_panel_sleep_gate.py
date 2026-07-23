"""The idle screensaver must not drift to the night clock while someone is up.

The `sleep` surface is a plain INACTIVITY timer (3 min, no touch/voice). It
never knew whether a human was present, so it kept appearing while the operator
was sitting in a lit room — "the sleep card keeps coming up on zoe, even though
I'm not asleep anymore".

There is no presence sensor to ask. Verified against the live house
(2026-07-24, `GET /api/ha/entities`): 44 entities, ZERO `binary_sensor`, and
`person.jason` is `unknown`. The room's own toggles are the only honest signal,
so the operator chose "light fully blocks sleep".

FIXTURE PROVENANCE: `switch.bedroom_1_switch_1` ("Bedroom Light") is the real
entity in the real Bedroom room — the panel `zoe-touch-pi` is bound to room
73319610-63e0-4d32-a7f1-b80f80ff97f5, whose only `room_devices` row is that
switch. Only the fields the resolver reads are kept.

DIRECTION OF FAILURE is the load-bearing property: every unknown must resolve to
`block: False` (i.e. SLEEP). The panel must never latch awake because a lookup
failed — that is the same direction the client's timeout race falls, so the two
halves agree instead of fighting.
"""
import pytest

pytestmark = pytest.mark.ci_safe

from routers.panel_config import resolve_sleep_gate

LIGHT = "switch.bedroom_1_switch_1"          # the real Bedroom Light
FAN = "input_boolean.fan"                     # real domain in this house
TEMP = "input_number.thermostat_temperature"  # a READING, never occupancy
SENSOR = "sensor.current_temperature"


def idx(*pairs):
    """Build an entity index the way `_entity_index()` does: id -> entity."""
    return {eid: {"entity_id": eid, "state": state} for eid, state in pairs}


def test_room_light_on_blocks_sleep():
    """The reported bug: operator is up, light is on, panel slept anyway."""
    gate = resolve_sleep_gate({LIGHT}, idx((LIGHT, "on")))
    assert gate["block"] is True, "a lit room must keep the panel awake"
    assert gate["entities"] == [LIGHT]


def test_dark_room_sleeps():
    """The guard must stay NARROW — a dark room still sleeps, or the fix would
    just disable the screensaver."""
    gate = resolve_sleep_gate({LIGHT}, idx((LIGHT, "off")))
    assert gate["block"] is False
    assert gate["entities"] == []


def test_ha_unavailable_falls_through_to_sleeping():
    """`_entity_index()` returns None when HA is unreachable. Unknown must mean
    SLEEP, never "latch awake"."""
    assert resolve_sleep_gate({LIGHT}, None)["block"] is False
    assert resolve_sleep_gate({LIGHT}, None)["reason"] == "ha-unavailable"


def test_panel_in_no_room_sleeps():
    """`room_entity_ids_for_panel` returns an EMPTY set for every "we don't
    know" case, which must not be read as occupancy."""
    assert resolve_sleep_gate(set(), idx((LIGHT, "on")))["block"] is False
    assert resolve_sleep_gate(None, idx((LIGHT, "on")))["block"] is False


def test_readings_are_not_occupancy():
    """A temperature that happens to be non-zero is not a person. Only
    toggle-ish domains count."""
    gate = resolve_sleep_gate({TEMP, SENSOR}, idx((TEMP, "on"), (SENSOR, "on")))
    assert gate["block"] is False, "a sensor/number must never imply occupancy"


def test_a_fan_or_tv_counts_as_someone_being_up():
    """The rule is domain-based on purpose: a switch may be a light, a fan or a
    TV, and all three are equally good evidence a human is awake."""
    assert resolve_sleep_gate({FAN}, idx((FAN, "on")))["block"] is True


def test_unavailable_entity_is_not_on():
    for state in ("unavailable", "unknown", None):
        gate = resolve_sleep_gate({LIGHT}, idx((LIGHT, state)))
        assert gate["block"] is False, f"state {state!r} must not count as on"


def test_stale_room_device_is_skipped_not_crashed():
    """HA entities get renamed/deleted; a room_devices row can outlive one."""
    gate = resolve_sleep_gate({"switch.deleted_long_ago", LIGHT}, idx((LIGHT, "on")))
    assert gate["block"] is True and gate["entities"] == [LIGHT]


def test_any_one_of_several_being_on_is_enough():
    gate = resolve_sleep_gate({LIGHT, FAN}, idx((LIGHT, "off"), (FAN, "on")))
    assert gate["block"] is True and gate["entities"] == [FAN]
