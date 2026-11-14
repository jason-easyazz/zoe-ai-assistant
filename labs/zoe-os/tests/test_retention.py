"""Retention: full, then a skeleton, then a count; and a forget that still reaches everything.

Times are driven by a fake clock. Each test plants a sentinel and reads the files on disk.
"""
from __future__ import annotations

import glob

import pytest
from spine_helpers import AllowList

from zoe_os.events import Event, Kind
from zoe_os.retention import DAY, KEEP, OPTIONS, Retention, Rule
from zoe_os.spine import Refused, Spine

SENTINEL = "zx-sentinel-iron-orchard-8813"
A, B = "person-a", "person-b"
T0 = 1_900_000_000.0                                   # a fixed "day 0", 2030
POLICY = OPTIONS["balanced"] | {Kind.UTTERANCE: Rule(30, 60), Kind.OFFER: Rule(90, 30)}


@pytest.fixture
def spine(tmp_path):
    auth = AllowList(origins={"voice"}, skills={"skill"},
                     readers={"all": {"*"}, "skill": {"*"}, "late": {"*"}, "audit": {"kernel"}})
    s = Spine(str(tmp_path / "spine.db"), auth, queue_size=8, clock=lambda: s.now)
    s.now, s.path = T0, str(tmp_path / "spine.db")
    yield s
    s.close()


def say(spine, person, text, day=0.0):
    spine.now = T0 + day * DAY
    return spine.publish(Event(Kind.UTTERANCE, "voice", subject=person, payload={"text": text}))


def offer(spine, person, text, cause, day=0.0, **extra):
    spine.now = T0 + day * DAY
    return spine.publish(Event(Kind.OFFER, "skill", subject=person, caused_by=(cause,),
                               payload={"offer_id": f"o{cause}", "text": text, **extra}))


def sweep(spine, day, policy=POLICY):
    spine.now = T0 + day * DAY
    return Retention(spine, policy).sweep()


def on_disk(spine) -> bytes:
    return b"".join(open(p, "rb").read() for p in glob.glob(spine.path + "*"))


def texts(events) -> str:
    return " | ".join(str(e.payload) for e in events)


# ---- the three stages ------------------------------------------------------------------------------
def test_full_then_skeleton_then_a_count_and_the_bytes_leave_the_disk(spine):
    old = say(spine, A, f"old words {SENTINEL}")
    new = say(spine, A, "recent words", day=20)
    keeper = Retention(spine, POLICY)
    assert sweep(spine, 29) == {"expired": 0, "retired": 0} and keeper.stage(old.seq) == "full"
    assert SENTINEL.encode() in on_disk(spine)                         # positive control
    assert sweep(spine, 31) == {"expired": 1, "retired": 0}
    assert keeper.stage(old.seq) == "skeleton" and keeper.stage(new.seq) == "full"
    assert SENTINEL.encode() not in on_disk(spine) and SENTINEL not in texts(spine.replay("all"))
    kind, subject, people = spine._db.execute(
        "SELECT kind, subject, (SELECT COUNT(*) FROM people WHERE seq = events.seq) FROM events"
        " WHERE seq = ?", (old.seq,)).fetchone()
    assert (kind, subject, people) == ("utterance", None, 1)           # who it concerned: the link only
    assert sweep(spine, 91) == {"expired": 1, "retired": 1}            # new expires, old becomes a count
    assert keeper.stage(old.seq) == "gone" and keeper.stage(new.seq) == "skeleton"
    assert keeper.counts() == [("2030-03-17", "utterance", 1)]         # a day, a kind, a number
    assert sweep(spine, 91) == {"expired": 0, "retired": 0}            # idempotent


def test_expiry_is_not_a_forget_the_words_may_be_said_and_derived_again(spine):
    say(spine, A, SENTINEL)
    sweep(spine, 31)
    fresh = say(spine, A, "something new", day=31)
    assert offer(spine, A, SENTINEL, fresh.seq, day=31).seq             # no shield: not refused
    assert [e.kind for e in spine.replay("all") if e.kind is Kind.FORGET] == []


def test_an_expired_event_is_withdrawn_from_a_live_queue_and_cannot_be_cited(spine):
    sub = spine.subscribe("all", kinds={Kind.UTTERANCE})
    old = say(spine, A, SENTINEL)
    assert SENTINEL in texts(sub._queue)
    sweep(spine, 31)
    assert sub.poll() is None
    fresh = say(spine, A, "new", day=31)
    with pytest.raises(Refused, match="no longer kept"):
        offer(spine, A, "derived late", old.seq, day=31)
    assert offer(spine, A, "derived", fresh.seq, day=31).seq


# ---- forget and retention together ------------------------------------------------------------------
def derived_from_an_old_event(spine):
    """A's words, long expired, and something a skill built on them for B that copied them."""
    src = say(spine, A, "the original words")
    copy = offer(spine, B, "a summary", src.seq, day=25, copied=SENTINEL)
    return src, copy


def test_a_forget_still_reaches_what_was_derived_from_an_event_that_has_aged_out(spine):
    src, copy = derived_from_an_old_event(spine)
    assert sweep(spine, 100) == {"expired": 1, "retired": 0}           # src is a skeleton; copy lives
    keeper = Retention(spine, POLICY)
    assert keeper.stage(src.seq) == "skeleton" and SENTINEL in texts(spine.replay("all"))
    assert spine.forget(A, actor=A).count == 2                         # the skeleton and the copy
    assert SENTINEL not in texts(spine.replay("all")) and SENTINEL.encode() not in on_disk(spine)
    assert keeper.stage(src.seq) == "forgotten" and keeper.stage(copy.seq) == "forgotten"


def test_negative_control_if_expiry_dropped_the_links_the_forget_would_miss_the_copy(spine, monkeypatch):
    real = Spine._expire

    def careless(self, seqs):                              # a simpler retention: just delete old rows
        real(self, seqs)
        marks = ",".join("?" * len(seqs))
        self._db.execute(f"DELETE FROM people WHERE seq IN ({marks})", seqs)

    monkeypatch.setattr(Spine, "_expire", careless)
    derived_from_an_old_event(spine)
    sweep(spine, 100)
    spine.forget(A, actor=A)
    assert SENTINEL in texts(spine.replay("all"))                      # B's copy of A's words survives


def test_a_skeleton_is_kept_while_anything_derived_from_it_is_still_on_the_log(spine):
    src, copy = derived_from_an_old_event(spine)
    keeper = Retention(spine, POLICY)
    sweep(spine, 100)                                                  # src is 100 days old: past 30 + 60
    assert keeper.stage(src.seq) == "skeleton" and keeper.counts() == []
    sweep(spine, 160)                                                  # the copy (day 25) is past 90 + 30
    assert keeper.stage(copy.seq) == "gone" and keeper.stage(src.seq) == "skeleton"
    sweep(spine, 160)                                                  # nothing derives from it now
    assert keeper.stage(src.seq) == "gone"
    assert keeper.counts() == [("2030-03-17", "utterance", 1), ("2030-04-11", "offer", 1)]


def test_negative_control_retiring_a_skeleton_early_breaks_the_forget(spine, monkeypatch):
    monkeypatch.setattr(Retention, "_childless", lambda self, seqs: set(seqs))
    derived_from_an_old_event(spine)
    sweep(spine, 100)
    spine.forget(A, actor=A)
    assert SENTINEL in texts(spine.replay("all"))


def test_a_forgotten_event_is_never_counted_and_the_notice_never_expires(spine):
    say(spine, A, SENTINEL)
    say(spine, B, "b's words")
    spine.forget(A, actor=A)
    sweep(spine, 5000)
    keeper = Retention(spine, POLICY)
    assert keeper.counts() == [("2030-03-17", "utterance", 1)]         # B's only; A's left no count
    late = spine.subscribe("late", from_seq=0)
    assert [e.kind for e in iter(late.poll, None)] == [Kind.FORGET]    # 5000 days later, still told
    live = say(spine, B, "new", day=5000)
    with pytest.raises(Refused, match="forgotten content"):            # the shield outlives the events
        offer(spine, A, SENTINEL, live.seq, day=5000)


def test_HONEST_LIMIT_a_scoped_forget_cannot_match_an_event_whose_content_has_aged_out(spine):
    src = say(spine, A, "zorvane quillfeather rang")
    kept = offer(spine, A, "call them back", src.seq, day=25)
    sweep(spine, 31)
    assert spine.forget(A, actor=A, entity="Zorvane Quillfeather").count == 0   # nothing left to match
    assert kept.seq in [e.seq for e in spine.replay("all")]
    assert spine.forget(A, actor=A).count == 2                         # the person-forget still reaches it


# ---- no policy is built in ---------------------------------------------------------------------------
def test_a_policy_must_be_complete_and_cannot_expire_notices_or_outlive_its_proof(spine):
    with pytest.raises(ValueError, match="every kind"):
        Retention(spine, {Kind.UTTERANCE: Rule(30)})
    with pytest.raises(ValueError, match="never expire"):
        Retention(spine, POLICY | {Kind.FORGET: Rule(30)})
    with pytest.raises(ValueError, match="at least as long"):
        Retention(spine, POLICY | {Kind.ACTION_RECEIPT: Rule(30), Kind.CLAIM: Rule(90)})
    for name, policy in OPTIONS.items():
        assert Retention(spine, policy).policy[Kind.FORGET] == Rule(KEEP), name
    assert [OPTIONS[n][Kind.UTTERANCE].full_days for n in ("short", "balanced", "long")] == [7, 30, 365]
    assert sweep(spine, 9999, OPTIONS["long"]) == {"expired": 0, "retired": 0}   # an empty log
