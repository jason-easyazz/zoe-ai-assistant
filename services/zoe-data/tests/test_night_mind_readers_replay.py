"""Night-mind READERS replay (owner decision 2026-11-03: ``ZOE_NIGHT_MIND=serve`` after a replay gate).

The WRITER is live and measured (the 12B window writes in ``enforce`` every night). The READERS (``night_mind.prompt_block`` / ``morning_items`` behind the
recall packet in ``routers/memories.py``) have never run against a store another process wrote. This module replays them over a seeded ``night_store`` and
proves the walls a household member relies on before the flag is flipped. Real: ``night_mind`` (pass + readers), ``night_store`` (in-process backend), the
real ``/api/memories/for-prompt`` route (guest wall, restraint, packet composition), the forget tombstones. Scripted: the nightly model (the bench's fake
brain - plumbing, never what a 4B does). Synthetic names only, no network, no live store.

Cells: R1 serve serves and never calls a model | R2 a held (hedged) quote is never served | R3 a replaced (history) quote is never served | R4 a forgotten
entity is not served (tombstone and erase) | R5 a guest / unverified speaker gets nothing | R6 the block fits its budget and sits AFTER the stable prefix |
R7 off and shadow are byte-identical to today, touching nothing | R8 planted fault: a reader that ignores the forget shield turns R4's probe red.

Operator replay: ``flock -x /tmp/zoe-test-lane.lock python3 -m pytest -q -p no:cacheprovider services/zoe-data/tests/test_night_mind_readers_replay.py``
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys
from pathlib import Path

import pytest

import memory_tombstones
import night_mind as nm
import night_store as ns

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "perf"))
from zmb import life as lifemod  # noqa: E402
from zmb.night_brain import FakeNightBrain  # noqa: E402

pytestmark = pytest.mark.ci_safe

UID = "demo_bar_00000001"
NOW = dt.datetime(2026, 10, 9, 3, 0, tzinfo=dt.timezone.utc)
LF = lifemod.life("zmb-v1")
BLOCK_TOKEN_BUDGET = 200        # measured on the live store 2026-10-18: mean 84 / max 136 tokens, +the mood ask line; a block over 200 is a regression
PREFIX_FACT = "I like my tea strong with no sugar."


def run(coro):
    return asyncio.run(coro)


def transcript(turns, prefix="t"):
    import memory_digest

    pairs = [(f"{prefix}{i:03d}", t) for i, (t, _d) in enumerate(turns)]
    times = [(NOW - dt.timedelta(days=d)).isoformat() for _t, d in turns]
    return memory_digest.Transcript("\n".join(t for _i, t in pairs), pairs, times)


def life_transcript():
    return transcript([(t["text"], 30 - t["day"]) for t in LF.turns if t["speaker"] == "typed"])


class Tripwire:
    """The model as seen by a READER process: any call is a failure, and is counted."""

    def __init__(self):
        self.calls = 0

    def __call__(self, messages, max_tokens):
        self.calls += 1
        raise AssertionError("a reader called the model")


@pytest.fixture(autouse=True)
def lab(monkeypatch):
    for k in (nm.ENV, "ZOE_DIGEST_OBSERVATION_GATE", "ZOE_MEMORY_AUTHORITY"):
        monkeypatch.delenv(k, raising=False)
    backend = ns.MemoryBackend()
    ns.set_backend(backend)
    nm._reset_state()
    nm.FAULTS.clear()
    prev = nm.set_config(nm.Config(ctx_tokens=8192, chunk_tokens=150, max_calls=7, url="http://127.0.0.1:1"))
    yield backend
    nm.set_llm(None)
    nm.set_config(prev)
    nm.FAULTS.clear()
    memory_tombstones.clear_all(UID)
    ns.set_backend(None)


def write_night(tr=None, uid=UID, prefix_now=NOW):
    """The OTHER process: the 12B window, writing in enforce with the fake brain. Returns the model calls it made."""
    brain = FakeNightBrain()
    nm.set_llm(brain)
    run(nm.run_for_user(uid, tr if tr is not None else life_transcript(), None, now=prefix_now, force_mode="enforce"))
    nm.set_llm(None)
    return brain.calls


def become_reader(monkeypatch, mode="serve"):
    """zoe-data after the flip: the flag set, a tripwire for a model, a cold cache."""
    monkeypatch.setenv(nm.ENV, mode)
    wire = Tripwire()
    nm.set_llm(wire)
    nm.invalidate()
    return wire


SUB = LF.threads[0].identity[0]
ASK = f"what's been going on with {SUB.capitalize()} lately"


# ── the route harness: the real for-prompt route over a fake memory service ──────────────────────────────────────────────

class _Svc:
    async def load_for_prompt(self, user_id, *, limit=20):
        from memory_service import MemoryRef

        return [MemoryRef(id="m1", text=PREFIX_FACT, metadata={})]

    async def search(self, q, *, user_id, limit=10, **_kw):
        return []

    async def load_recent_for_prompt(self, user_id, **kw):
        return []


@pytest.fixture
def route(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import auth
    from routers import memories as memories_mod

    monkeypatch.setattr(memories_mod, "_svc", lambda: _Svc())
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    app = FastAPI()
    app.include_router(memories_mod.router)
    client = TestClient(app)

    def call(message, user_id=UID, **extra):
        r = client.get("/api/memories/for-prompt", headers={"X-Internal-Token": "tok"},
                       params={"user_id": user_id, "message": message, "limit": "12", **extra})
        assert r.status_code == 200
        return r.json()

    return call


# ── R1 ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_R1_serve_serves_what_another_process_wrote_and_makes_zero_model_calls(lab, monkeypatch):
    assert write_night() > 0                                              # the writer really used the model (else "zero" below proves nothing)
    written = dict(lab.obs_rows)
    assert len(written) >= 5
    wire = become_reader(monkeypatch)
    served: list = []
    block = run(nm.prompt_block(UID, ASK, now=NOW, served=served))
    assert block.startswith("## What I've noticed") and SUB in block.lower() and served
    run(nm.morning_items(UID, now=NOW))
    res = run(nm.run_for_user(UID, life_transcript(), None, now=NOW))      # the digest hook under serve: no pass
    assert res["status"] == "off" and not nm.runs_pass()
    assert wire.calls == 0 and lab.obs_rows == written                     # zero model calls, nothing written
    assert all(by["authority_class"] == "user_stated_derived" and by["state"] == "current"
               for by in (lab.obs_rows[o] for o in lab.obs_rows if lab.obs_rows[o]["turn_id"] in {t for t, _a, _q in served}))


# ── R2 ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_R2_a_held_pending_quote_is_never_served(lab, monkeypatch):
    write_night()
    held = [o for o in lab.obs_rows.values() if o["state"] == "held"]
    assert any("might look around" in o["quote"] for o in held) and all(o["authority_class"] == "pending" for o in held)   # non-vacuous
    become_reader(monkeypatch)
    for ask in ("how has my week been", "should I look around for a new job", "what's going on with work", ASK):
        block = run(nm.prompt_block(UID, ask, now=NOW))
        assert not any(o["quote"] in block for o in held), ask
    assert run(nm.prompt_block(UID, ASK, now=NOW))                         # positive control: the same store does serve its current rows
    assert all(not any(o["quote"] in i["text"] for o in held) for i in run(nm.morning_items(UID, now=NOW)))


# ── R3 ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_R3_a_quote_the_owners_later_word_replaced_is_never_served(lab, monkeypatch):
    write_night(transcript([("My sister Brynja lives in Marlowby and I visit her most Sundays.", 12), ("Brynja is a nurse in Marlowby and loves her work there.", 11),
                            ("Brynja and I went walking in Marlowby on the weekend together.", 10)]))
    write_night(transcript([("Brynja has moved to Pellham and I am happy for her new start.", 1), ("Brynja is settling into Pellham very well so far this week.", 0),
                            ("Brynja found a new flat in Pellham near the river for the summer.", 0)], "b"))
    old = [o for o in lab.obs_rows.values() if "Marlowby" in o["quote"]]
    assert old and all(o["state"] == "history" and o["valid_to"] for o in old)           # non-vacuous: the old words exist, as history
    become_reader(monkeypatch)
    for ask in ("how is Brynja", "what's been going on with Brynja lately", "how has my week been", "where does Brynja live"):
        block = run(nm.prompt_block(UID, ask, now=NOW))
        assert "Marlowby" not in block, ask
    assert "Pellham" in run(nm.prompt_block(UID, "how is Brynja", now=NOW))              # positive control
    assert all("Marlowby" not in i["text"] for i in run(nm.morning_items(UID, now=NOW)))


# ── R4 / R8 ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _forget_probe(monkeypatch, lab, *, via):
    """Seed, flip to serve, forget SUB by ``via`` ('tombstone' = the 300 s shield; 'erase' = the durable erase), and report whether the name still leaks."""
    write_night()
    become_reader(monkeypatch)
    assert SUB in run(nm.prompt_block(UID, ASK, now=NOW)).lower()                        # served before the forget (else the probe is vacuous)
    if via == "tombstone":
        memory_tombstones.add(UID, SUB)
    else:
        assert run(nm.erase_entity(UID, SUB)) >= 2
    nm.invalidate()
    return SUB in run(nm.prompt_block(UID, ASK, now=NOW)).lower()


def test_R4_a_forgotten_entity_is_not_served_after_the_tombstone_or_the_erase(lab, monkeypatch):
    assert _forget_probe(monkeypatch, lab, via="tombstone") is False
    assert any(SUB in o["quote"].lower() for o in lab.obs_rows.values())                 # the shield hid it; the rows are still there until the erase
    assert run(nm.erase_entity(UID, SUB)) >= 2
    memory_tombstones.clear_all(UID)                                                     # the shield gone: only the erase stands between the name and the packet
    nm.invalidate()
    assert not any(SUB in o["quote"].lower() for o in lab.obs_rows.values())
    assert SUB not in run(nm.prompt_block(UID, ASK, now=NOW)).lower()
    assert all(SUB not in i["text"].lower() for i in run(nm.morning_items(UID, now=NOW)))


def test_R4b_the_erase_fails_closed_a_store_failure_is_raised_never_confirmed(lab, monkeypatch):
    write_night()

    async def boom(user_id, pattern):
        raise RuntimeError("store down")

    monkeypatch.setattr(lab, "erase_matching", boom)
    with pytest.raises(RuntimeError):
        run(nm.erase_entity(UID, SUB))


def test_R8_PLANTED_FAULT_a_reader_that_ignores_the_forget_shield_is_caught_by_the_R4_probe(lab, monkeypatch):
    async def no_shield(user_id, text):
        return False

    monkeypatch.setattr(nm, "_shielded", no_shield)                                      # the planted fault
    assert _forget_probe(monkeypatch, lab, via="tombstone") is True                      # R4's probe sees the leak: its first assertion would go red


# ── R5 ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("who", ["guest", "guest-3f9a1c", "voice-daemon"])
def test_R5_a_guest_or_unverified_speaker_gets_nothing_through_the_real_route(lab, monkeypatch, route, who):
    write_night()
    # plant the same rows under the guest principal, so the wall (not an empty store) is what is proven
    for o in list(lab.obs_rows.values()):
        lab.obs_rows[o["id"] + "g"] = {**o, "id": o["id"] + "g", "user_id": who}
    for t in list(lab.thread_rows.values()):
        lab.thread_rows[t["id"] + "g"] = {**t, "id": t["id"] + "g", "user_id": who}
    become_reader(monkeypatch)
    member = route(ASK)
    assert "What I've noticed" in member["packet"] and member["night_notes"] >= 1       # control: the verified member does get it
    got = route(ASK, user_id=who)
    assert got["packet"] == "" and got["count"] == 0 and "night_notes" not in got


# ── R6 ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_R6_the_block_fits_its_budget_and_sits_after_the_stable_prefix(lab, monkeypatch, route):
    write_night()
    off = route(ASK)                                                                     # flag unset: today's packet
    assert PREFIX_FACT in off["packet"]
    become_reader(monkeypatch)
    for ask in (ASK, "how has my week been", "how is my knee"):
        base = route("turn on the kitchen lights")["packet"]                             # a turn the night mind does not touch
        got = route(ask)
        block = got["packet"][len(off["packet"]):] if got["packet"].startswith(off["packet"]) else None
        if "night_notes" not in got:
            continue
        assert block is not None, "the served packet must START with the unchanged prefix"
        assert block.startswith("\n## What I've noticed")
        assert got["packet"].startswith(off["packet"]) and base == off["packet"]
        assert 1 <= got["night_notes"] <= nm.SHOW_LINES
        assert nm.est_tokens(block) <= BLOCK_TOKEN_BUDGET, nm.est_tokens(block)
    assert route(ASK).get("night_notes")                                                 # the loop above was not vacuous


# ── R7 ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────

class _Counting(ns.MemoryBackend):
    def __init__(self, inner):
        super().__init__()
        self.__dict__.update(inner.__dict__)
        self.touched = 0

    async def threads(self, user_id):
        self.touched += 1
        return await super().threads(user_id)

    async def observations(self, user_id, **kw):
        self.touched += 1
        return await super().observations(user_id, **kw)


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_R7_off_and_shadow_packets_are_byte_identical_to_today_and_touch_nothing(lab, monkeypatch, route, mode):
    empty = route(ASK)                                                                   # today: nothing seeded at all
    write_night()
    counting = _Counting(lab)
    ns.set_backend(counting)
    if mode == "shadow":
        monkeypatch.setenv(nm.ENV, "shadow")
    wire = Tripwire()
    nm.set_llm(wire)
    nm.invalidate()
    for ask in (ASK, "how has my week been", "turn on the kitchen lights"):
        got = route(ask)
        assert got["packet"] == empty["packet"] and "night_notes" not in got
        assert run(nm.prompt_block(UID, ask, now=NOW)) == ""
    assert run(nm.morning_items(UID, now=NOW)) == []
    assert counting.touched == 0 and wire.calls == 0                                    # no read, no model


def test_R7b_negative_control_serve_does_change_the_packet(lab, monkeypatch, route):
    empty = route(ASK)
    write_night()
    become_reader(monkeypatch)
    assert route(ASK)["packet"] != empty["packet"]
