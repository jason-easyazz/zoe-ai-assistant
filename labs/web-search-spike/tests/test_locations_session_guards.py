"""`StoreSession.preflight` — the RAM/load floors that keep Chromium off a busy box.

No browser is launched here. `preflight()` is deliberately separable from
`__aenter__` and takes its three readings through injected callables, so the
refusal logic can be tested on the exact numbers that motivated it.
"""

from __future__ import annotations

import pytest

from websearch.locations.session import (
    DEFAULT_MIN_AVAILABLE_MB,
    DEFAULT_MIN_FREE_MB,
    SessionRefused,
    StoreSession,
)


def session(free, avail, load=0.5, **kw):
    return StoreSession(
        label="test",
        mem_reader=lambda: free,
        avail_reader=lambda: avail,
        load_reader=lambda: load,
        **kw,
    )


def test_a_quiet_box_with_room_is_allowed():
    session(free=4000, avail=4000).preflight()


def test_the_2026_08_05_reading_is_refused_even_though_memfree_passed():
    """THE MEASUREMENT THAT ADDED THE SECOND FLOOR.

    The box read MemFree 532 MB / MemAvailable 301 MB. MemFree cleared the
    380 MB floor on its own, so the single-floor version would have launched a
    ~553 MB Chromium into 301 MB of real headroom, next to the mlocked voice
    brain. On this box MemFree can EXCEED MemAvailable, which makes MemFree the
    optimistic instrument rather than the conservative one.
    """
    s = session(free=532, avail=301)
    assert 532 >= DEFAULT_MIN_FREE_MB, "MemFree alone would have said yes — that is the point"
    with pytest.raises(SessionRefused, match="MemAvailable"):
        s.preflight()


def test_the_refusal_names_both_numbers():
    """An operator reading the log must be able to see WHY it disagreed."""
    with pytest.raises(SessionRefused) as exc:
        session(free=532, avail=301).preflight()
    msg = str(exc.value)
    assert "301" in msg and "532" in msg and "optimistic" in msg


def test_low_memfree_is_still_refused_first():
    with pytest.raises(SessionRefused, match="MemFree"):
        session(free=100, avail=8000).preflight()


def test_a_busy_box_is_refused_even_with_plenty_of_ram():
    """Other agents share this box; load is a courtesy floor, not a RAM proxy."""
    with pytest.raises(SessionRefused, match="load1"):
        session(free=8000, avail=8000, load=3.4).preflight()


@pytest.mark.parametrize("free,avail", [
    (DEFAULT_MIN_FREE_MB - 1, 8000),
    (8000, DEFAULT_MIN_AVAILABLE_MB - 1),
])
def test_each_floor_is_inclusive_at_the_boundary(free, avail):
    with pytest.raises(SessionRefused):
        session(free=free, avail=avail).preflight()


def test_refusal_is_not_a_site_failure():
    """`SessionRefused` must stay distinct from any page error.

    Conflating "we chose not to launch" with "the retailer defeated us" is the
    same lie the chain's blocked/thin split exists to prevent.
    """
    assert issubclass(SessionRefused, RuntimeError)
    with pytest.raises(SessionRefused):
        session(free=10, avail=10).preflight()


def test_the_readings_are_taken_fresh_every_call():
    """A cached reading would let a session launch on a snapshot from minutes ago."""
    seen = []

    def reader():
        seen.append(1)
        return 8000

    s = StoreSession(label="t", mem_reader=reader, avail_reader=reader, load_reader=lambda: 0.1)
    s.preflight()
    s.preflight()
    assert len(seen) == 4, "two calls x two memory readings, none cached"
