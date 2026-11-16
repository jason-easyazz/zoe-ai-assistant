"""The carried verified speaker (services/zoe-data/carried_speaker.py): carried only for a verified member, never a guest; gone
on expiry, any wake, a session close, forget; capped at the voice-session ttl; the frame carries a boolean, never a name."""
from __future__ import annotations

import pytest

import carried_speaker as cs

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _clean():
    cs.reset()
    yield
    cs.reset()


def test_a_verified_member_is_carried_until_the_ttl():
    assert cs.note_verified("panel-a", "member-1", ttl_s=30, now=100.0)
    assert cs.current("panel-a", now=129.9) == "member-1"
    assert cs.current("panel-a", now=130.0) is None
    assert cs.current("panel-b", now=101.0) is None


@pytest.mark.parametrize("who", ["", "guest", "guest-42", "GUEST-x", None])
def test_a_guest_or_nobody_is_never_carried_and_clears(who):
    cs.note_verified("panel-a", "member-1", now=0.0)
    assert not cs.note_verified("panel-a", who, now=1.0)
    assert cs.current("panel-a", now=2.0) is None


def test_wake_session_close_unverified_and_forget_invalidate():
    for drop in (cs.on_wake, cs.on_session_close, cs.note_unverified):
        cs.note_verified("panel-a", "member-1", now=0.0)
        drop("panel-a")
        assert cs.current("panel-a", now=1.0) is None
    cs.note_verified("panel-a", "member-1", now=0.0)
    cs.note_verified("panel-b", "member-1", now=0.0)
    cs.note_verified("panel-c", "member-2", now=0.0)
    assert cs.forget_user("member-1") == 2
    assert cs.current("panel-c", now=1.0) == "member-2"


def test_the_ttl_is_capped_at_the_voice_session_ttl(monkeypatch):
    monkeypatch.setenv("ZOE_VOICE_SESSION_TTL_S", "60")
    cs.note_verified("panel-a", "member-1", ttl_s=10_000, now=0.0)
    assert cs.current("panel-a", now=59.0) == "member-1" and cs.current("panel-a", now=60.5) is None
    assert not cs.note_verified("panel-a", "member-1", ttl_s=float("nan"), now=0.0)
    assert not cs.note_verified("panel-a", "member-1", ttl_s=0, now=0.0)


def test_a_clock_that_ran_backwards_drops_it():
    cs.note_verified("panel-a", "member-1", ttl_s=30, now=1000.0)
    assert cs.current("panel-a", now=0.0) is None


def test_a_small_backward_step_drops_it_even_when_the_entry_is_shorter_than_the_session(monkeypatch):
    """Review of #2606: the check compared the time left with the SESSION's ttl, so an entry recorded at 1000 for 30 s and
    read at 999 (session ttl 120) still named the member, with 31 s "left"."""
    monkeypatch.setattr(cs, "_session_ttl", lambda: 120.0)
    cs.note_verified("panel-a", "member-1", ttl_s=30, now=1000.0)
    assert cs.frame_field("panel-a", now=999.0) == {"v": 1, "verified": False, "ttl_s": 0}
    assert cs.current("panel-a", now=1001.0) is None          # dropped, not merely hidden for that one read


def test_a_backward_step_between_two_reads_drops_it(monkeypatch):
    monkeypatch.setattr(cs, "_session_ttl", lambda: 120.0)
    cs.note_verified("panel-a", "member-1", ttl_s=30, now=1000.0)
    assert cs.current("panel-a", now=1010.0) == "member-1"
    assert cs.current("panel-a", now=1005.0) is None           # later than the record, earlier than the last read
    assert cs.current("panel-a", now=1011.0) is None


def test_a_forward_clock_still_carries_and_reports_the_entrys_own_time_left(monkeypatch):
    monkeypatch.setattr(cs, "_session_ttl", lambda: 120.0)
    cs.note_verified("panel-a", "member-1", ttl_s=30, now=1000.0)
    assert cs.frame_field("panel-a", now=1010.0) == {"v": 1, "verified": True, "ttl_s": 20}
    assert cs.current("panel-a", now=1030.0) is None


def test_the_frame_field_is_a_boolean_and_a_ttl_never_a_name():
    assert cs.frame_field("panel-a", now=0.0) == {"v": 1, "verified": False, "ttl_s": 0}
    cs.note_verified("panel-a", "member-1", ttl_s=30, now=0.0)
    f = cs.frame_field("panel-a", now=10.0)
    assert f == {"v": 1, "verified": True, "ttl_s": 20} and "member-1" not in repr(f)


def test_bounded():
    for i in range(cs.MAX_PANELS + 10):
        cs.note_verified(f"panel-{i}", "member-1", now=0.0)
    assert sum(cs.current(f"panel-{i}", now=1.0) is not None for i in range(cs.MAX_PANELS + 10)) == cs.MAX_PANELS
