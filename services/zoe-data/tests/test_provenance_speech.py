"""provenance_speech: "you told me on Tuesday" from the row's own pointers, and NOTHING when there is no honest pointer.

The hard rule (lane C E8): a fabricated provenance is a hard fail, so every refusal case is pinned, and the flag-off packet is pinned
byte-identical."""
from __future__ import annotations

import datetime
import time
import types

import pytest

import memory_service
import provenance_answers
import provenance_speech as ps
import routers.memories as memories_mod
from memory_service import MemoryRef
from routers.memories import _build_memory_prompt_packet

pytestmark = pytest.mark.ci_safe

D = 86400.0
NOW = datetime.datetime(2026, 10, 30, 4, 0, tzinfo=datetime.timezone.utc).timestamp()     # Fri 30 Oct, 12:00 Perth


@pytest.fixture(autouse=True)
def _perth(monkeypatch):
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    monkeypatch.delenv(ps.ENV, raising=False)


def _m(cls: str, source: str, days_ago, **extra) -> dict:
    meta = {"authority_class": cls, "source": source, **extra}
    if days_ago is not None:
        meta["added_ts"] = NOW - days_ago * D
    return meta


@pytest.mark.parametrize("meta, clause", [
    (_m("user_stated", "chat_regex", 3), "you told me on Tuesday"),
    (_m("user_stated", "voice_fact", 0), "you told me earlier today"),
    (_m("user_stated", "voice_fact", 1), "you told me yesterday"),
    (_m("user_stated", "chat_regex", 40), "you told me on 20 September"),
    (_m("user_confirmed", "review_ui", 3), "you confirmed it on Tuesday"),
    (_m("user_confirmed", "omi_review", 3, origin="omi_review"), "you confirmed it from your day review on Tuesday"),
    (_m("user_stated_derived", "turn_digest", 3), "you mentioned on Tuesday"),
    (_m("user_stated_derived", "turn_digest", 3, authority_basis="verbatim_user_span"), "you told me on Tuesday"),
    (_m("model_from_turn", "turn_digest", 3), "I picked up on Tuesday"),
    (_m("user_stated", "chat_regex", None), "you told me"),                  # a stated row with no time: no day, never a guessed one
    (_m("model_from_transcript", "digest", 3), "I picked up"),               # a night batch: its capture time is not a day anyone said it
])
def test_the_clause_follows_the_stamped_authority(meta, clause):
    assert ps.of(meta, now=NOW).clause() == clause


@pytest.mark.parametrize("meta", [
    {"source": "chat_regex", "added_ts": NOW - D},                         # unstamped legacy row: a derived class is a guess
    _m("user_unverified", "voice_regex", 1),                               # a voice the gate did not confirm: never "you"
    _m("operator", "operator", 1),
    {"authority_class": "something_new", "added_ts": NOW - D},
    {},
    None,
])
def test_no_honest_pointer_says_nothing(meta):
    assert ps.of(meta, now=NOW) is None
    assert ps.packet_suffix(meta, "User works at Acme", now=NOW) == ""
    assert ps.that_clause(meta, "User works at Acme", now=NOW) == ""


def test_the_legacy_authority_stamp_alone_is_a_pointer():
    assert ps.of({"authority": "user_stated", "added_ts": NOW - 3 * D}, now=NOW).clause() == "you told me on Tuesday"
    assert ps.of({"authority": "inferred", "added_ts": NOW - 3 * D}, now=NOW).inferred


def test_lead_and_that_clause():
    meta = _m("user_stated", "chat_regex", 3)
    assert ps.of(meta, now=NOW).lead() == "On Tuesday you told me"
    assert ps.of(_m("user_stated", "chat_regex", None), now=NOW).lead() == "You told me"
    assert ps.that_clause(meta, "User lives in Northcliff", now=NOW, lead=True) == "On Tuesday you told me that you live in Northcliff"
    assert ps.that_clause(meta, "", now=NOW) == ""


def test_packet_suffix_marks_an_inference():
    assert ps.packet_suffix(_m("user_stated", "chat_regex", 3), now=NOW) == " (you told me on Tuesday)"
    assert ps.packet_suffix(_m("model_from_turn", "turn_digest", 1), now=NOW) == " (I picked up yesterday - not confirmed)"


# ── the packet ────────────────────────────────────────────────────────────────

def _rows():
    return [MemoryRef(id="aaaa1111", text="User lives in Northcliff", metadata=_m("user_stated", "chat_regex", 3)),
            MemoryRef(id="bbbb2222", text="User enjoys kayaking", metadata=_m("model_from_turn", "turn_digest", 1)),
            MemoryRef(id="cccc3333", text="User prefers short answers", metadata={"source": "chat_regex", "added_ts": NOW - D})]


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(provenance_answers, "time", types.SimpleNamespace(time=lambda: NOW, monotonic=time.monotonic))


def test_builder_default_is_unchanged_and_speak_adds_the_clause(clock):
    plain = _build_memory_prompt_packet(_rows(), [])["packet"]
    assert "you told me" not in plain and "picked up" not in plain
    spoken = _build_memory_prompt_packet(_rows(), [], speak=True)["packet"]
    assert "- User lives in Northcliff (you told me on Tuesday) [mem:aaaa1111]" in spoken
    assert "- User enjoys kayaking (I picked up yesterday - not confirmed) [mem:bbbb2222]" in spoken
    assert "- User prefers short answers [mem:cccc3333]" in spoken            # unstamped: nothing invented
    # removing the clause gives back the plain packet line for line (nothing else moved)
    assert spoken.replace(" (you told me on Tuesday)", "").replace(" (I picked up yesterday - not confirmed)", "") == plain


class _FakeSvc:
    async def load_for_prompt(self, user_id, *, limit=20):
        return _rows()[:limit]

    async def search(self, q, *, user_id, limit=10):
        return []

    async def load_recent_for_prompt(self, user_id, **kw):
        return _rows()


@pytest.fixture
def svc(monkeypatch, clock):
    monkeypatch.setattr(memory_service, "is_guest_memory_user", lambda uid: False)
    monkeypatch.setattr(memories_mod, "_svc", lambda: _FakeSvc())


async def _packet(mode="relevance"):
    return (await memories_mod.memory_for_prompt(user_id="member-a", message="where do I live?", limit=12, mode=mode, _=None))["packet"]


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [None, "0", "off", "nonsense"])
async def test_endpoint_flag_off_never_speaks(svc, monkeypatch, flag):
    if flag is not None:
        monkeypatch.setenv(ps.ENV, flag)
    assert "you told me" not in await _packet()


@pytest.mark.asyncio
async def test_endpoint_flag_on_speaks_in_relevance_mode_only(svc, monkeypatch):
    monkeypatch.setenv(ps.ENV, "1")
    assert "(you told me on Tuesday)" in await _packet()
    assert "you told me" not in await _packet(mode="continuity")              # the continuity block is budgeted: untouched


def test_a_confirmation_is_dated_from_the_review_not_the_capture():
    iso = datetime.datetime.fromtimestamp(NOW - 1 * D, datetime.timezone.utc).isoformat()
    meta = _m("user_confirmed", "review_ui", 30, reviewed_at=iso)          # captured 30 days ago, approved yesterday
    assert ps.of(meta, now=NOW).clause() == "you confirmed it yesterday"
    assert ps.of(_m("user_confirmed", "review_ui", 30, reviewed_at="not a date"), now=NOW).clause() == "you confirmed it"


def test_second_person_provenance_binds_to_the_requesting_member(clock):
    """Codex #2301: a family-visible row member A stated is not 'you told me' to member B asking."""
    rows = [MemoryRef(id="aaaa1111", text="User lives in Northcliff",
                      metadata={**_m("user_stated", "chat_regex", 3), "user_id": "member-a"})]
    mine = _build_memory_prompt_packet(rows, [], speak=True, user_id="member-a")["packet"]
    assert "(you told me on Tuesday)" in mine
    theirs = _build_memory_prompt_packet(rows, [], speak=True, user_id="member-b")["packet"]
    assert "you told me" not in theirs and "User lives in Northcliff" in theirs          # the fact is served, the false attribution is not
    anon = _build_memory_prompt_packet(rows, [], speak=True)["packet"]
    assert "(you told me on Tuesday)" in anon                                           # control: no requesting id = as before
