"""The struggle detector's RUNTIME (``struggle_detector.py``): the hook is off the turn path and cannot break or slow it, the
notes join the router's and the brain's view of a turn, words are kept only where the transcript keeps them, a forget /
user delete erases (and fails closed), retention sweeps, the digest clusters per user, and the flag's three modes.

Every phrasing is invented (public repo). The store is the in-process backend; no DB, no network, no model.
"""
from __future__ import annotations

import asyncio
import statistics
import time

import pytest

pytestmark = pytest.mark.ci_safe

import struggle_detector as sd
import struggle_signals as sig

A, B = "member-a", "member-b"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for env in ("ZOE_STRUGGLE_DETECT", "ZOE_STRUGGLE_WINDOW_S", "ZOE_STRUGGLE_RETENTION_DAYS", "ZOE_STRUGGLE_QUEUE_MAX"):
        monkeypatch.delenv(env, raising=False)
    sd.reset()
    be = sd.MemoryBackend()
    sd.set_backend(be)
    yield be
    sd.reset()
    sd.set_backend(None)


def item(text, reply="Done.", *, user=A, session="chat-s1", channel="chat", ts=None, panel="", interrupted=False):
    return (time.time() if ts is None else ts, channel, text, reply, user, session, panel, interrupted)


async def feed(*turns):
    """Run turns through ``process`` in order (the worker's own function) and return every event."""
    out = []
    for t in turns:
        out.extend(await sd.process(t))
    return out


# ── the flag ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [(None, "shadow"), ("", "shadow"), ("shadow", "shadow"), ("off", "off"), ("0", "off"),
                                      ("false", "off"), ("on", "on"), ("1", "on"), ("bogus", "shadow")])
def test_mode(monkeypatch, raw, want):
    if raw is not None:
        monkeypatch.setenv("ZOE_STRUGGLE_DETECT", raw)
    assert sd.mode() == want


async def test_off_records_nothing_and_never_starts_a_worker(monkeypatch, fresh):
    monkeypatch.setenv("ZOE_STRUGGLE_DETECT", "off")
    assert sd.observe("chat", "what's on my list", "Done.", A, "s1") is False
    sd.note_route("what's on my list", {"domain": "lists", "score": 0.9})
    sd.note_brain("s1", "flue", "ok")
    assert sd._STATE["worker"] is None and not sd._ROUTES and not sd._BRAINS
    assert sd.stats()["dropped_off"] == 1 and fresh.data == []


async def test_shadow_records_through_the_real_queue_and_worker(fresh):
    t0 = time.time()
    assert sd.observe("chat", "what's the capital of peru", "Lima.", A, "s1") is True
    assert sd.observe("chat", "what's the capital of peru", "Lima.", A, "s1") is True
    await sd.drain()
    assert [r["signal"] for r in fresh.data] == ["RE_ASK"]
    row = fresh.data[0]
    assert row["user_id"] == A and row["channel"] == "chat" and row["mode"] == "shadow" and row["ts"] >= t0
    assert row["summary"] == "what is the capital of peru -> what is the capital of peru"
    st = sd.stats()
    assert st["enqueued"] == 2 and st["processed"] == 2 and st["events"] == 1 and st["queue_depth"] == 0


# ── the notes: the router's and the brain's view of the turn ─────────────────

async def test_route_and_brain_notes_make_a_fallthrough(fresh):
    text = "will it be windy this afternoon"
    sd.note_route(text, {"domain": "weather", "score": 0.81,
                         "two_stage": {"domain": "weather", "head_conf": 0.97}}, two_stage_active=True)
    sd.note_brain("voice-panel-hall-1a2b3c4d", "flue", "ok")
    ev = await feed(item(text, "Probably a little.", channel="voice", session="voice-panel-hall-1a2b3c4d"))
    assert [e.signal for e in ev] == ["FALLTHROUGH"]
    row = fresh.data[0]
    assert row["path_served"] == "brain:flue" and row["domain_predicted"] == "weather" and row["panel_id"] == "hall"
    assert row["fix_type"] == sig.FIX_ROUTER_TRAINING and row["latency_ms"] is not None
    assert not sd._BRAINS, "a brain note belongs to ONE turn"


async def test_a_router_timeout_is_recorded_as_such(fresh):
    text = "how chilly will it get tonight"
    sd.note_route(text, {"domain": "weather", "score": 0.72}, two_stage_active=True, two_stage_failed=True)
    sd.note_brain("s9", "flue", "ok")
    ev = await feed(item(text, "Around eight degrees.", session="s9"))
    assert ev[0].signal == "FALLTHROUGH" and "two_stage_failed" in ev[0].reasons
    assert fresh.data[0]["fix_type"] == sig.FIX_ROUTER_LATENCY


async def test_a_stale_brain_note_from_another_session_is_not_borrowed(fresh):
    sd.note_brain("other-session", "flue", "ok")
    text = "what's the weather like"
    sd.note_route(text, {"domain": "weather", "score": 0.95, "two_stage": {"domain": "weather"}}, two_stage_active=True)
    assert await feed(item(text, "It's 14 degrees and clear.", session="s1")) == []


def test_note_hooks_never_raise(monkeypatch):
    sd.note_route("x", {"domain": object(), "score": "nan", "two_stage": "garbage"})
    sd.note_route("x", None)
    sd.note_brain(None, None, None)  # type: ignore[arg-type]
    monkeypatch.setattr(sd, "mode", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    sd.note_route("x", {"domain": "weather"})
    sd.note_brain("s", "flue", "ok")
    assert sd.stats.__name__  # reached: nothing propagated


# ── failure isolation: the turn never feels the detector ─────────────────────

async def test_a_detector_bug_is_counted_and_swallowed(monkeypatch, fresh):
    monkeypatch.setattr(sig, "detect", lambda *_a, **_k: (_ for _ in ()).throw(ZeroDivisionError("bug")))
    assert sd.observe("chat", "hello there", "Hi!", A, "s1") is True
    await sd.drain()
    assert sd.stats()["process_errors"] == 1 and fresh.data == []
    # and the worker survived the bug: the next turn is still processed
    monkeypatch.undo()
    sd.set_backend(fresh)
    assert sd.observe("chat", "what's the capital of peru", "Lima.", A, "s1") is True
    await sd.drain()
    assert sd.stats()["processed"] >= 1


async def test_db_down_is_counted_never_raised(fresh):
    class Down(sd.MemoryBackend):
        async def add(self, rows):
            raise ConnectionError("db down")

    sd.set_backend(Down())
    sd.observe("chat", "what's the capital of peru", "Lima.", A, "s1")
    sd.observe("chat", "what's the capital of peru", "Lima.", A, "s1")
    await sd.drain()
    st = sd.stats()
    assert st["store_errors"] == 1 and st["processed"] == 2 and st["process_errors"] == 0


async def test_a_slow_store_times_out_instead_of_backing_up(monkeypatch, fresh):
    class Slow(sd.MemoryBackend):
        async def add(self, rows):
            await asyncio.sleep(30)

    monkeypatch.setattr(sd, "STORE_TIMEOUT_S", 0.05)
    sd.set_backend(Slow())
    sd.observe("chat", "what's the capital of peru", "Lima.", A, "s1")
    sd.observe("chat", "what's the capital of peru", "Lima.", A, "s1")
    await sd.drain(timeout=5)
    assert sd.stats()["store_errors"] == 1


async def test_a_full_queue_drops_and_counts_and_never_blocks(monkeypatch, fresh):
    monkeypatch.setenv("ZOE_STRUGGLE_QUEUE_MAX", "8")
    gate = asyncio.Event()
    real = sd.process

    async def held(it):
        await gate.wait()
        return await real(it)

    monkeypatch.setattr(sd, "process", held)
    results = []
    t0 = time.perf_counter()
    for i in range(20):
        results.append(sd.observe("chat", f"question number {i}", "Answer.", A, "s1"))
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5, "a full queue must never make the hook wait"
    # the worker took one item off the queue before blocking, so 8 + (at most) 1 are accepted
    assert sum(results) in (8, 9) and sd.stats()["dropped_full"] == 20 - sum(results)
    gate.set()
    await sd.drain()


def test_the_hook_without_an_event_loop_is_a_counted_no_op():
    assert sd.observe("chat", "hi", "Hello.", A, "s1") is False
    assert sd.stats()["hook_errors"] == 1


async def test_hook_errors_never_propagate(monkeypatch):
    monkeypatch.setattr(sd, "_queue", lambda: (_ for _ in ()).throw(MemoryError("no room")))
    assert sd.observe("chat", "hi", "Hello.", A, "s1") is False
    assert sd.stats()["hook_errors"] == 1


async def test_hook_overhead_on_the_turn_path_is_tiny(fresh):
    """The measured cost the TURN pays: ``note_route`` + ``note_brain`` (in the turn) and ``observe`` (in the post-turn
    task). Target < 2 ms per turn; asserted with a generous CI bound on the p99."""
    decision = {"domain": "weather", "score": 0.8, "two_stage": {"domain": "weather", "head_conf": 0.9}}
    samples = []
    for i in range(2000):
        t0 = time.perf_counter()
        sd.note_route(f"what's the weather like {i}", decision, two_stage_active=True)
        sd.note_brain(f"s{i % 7}", "flue", "ok")
        sd.observe("chat", f"what's the weather like {i}", "Mild.", A, f"s{i % 7}")
        samples.append((time.perf_counter() - t0) * 1000.0)
        if i % 200 == 199:
            await sd.drain(timeout=10)   # keep the queue from filling: we measure the hook, not drops
    samples.sort()
    p50, p99 = statistics.median(samples), samples[int(len(samples) * 0.99)]
    print(f"struggle hook overhead: p50={p50:.4f}ms p99={p99:.4f}ms max={samples[-1]:.3f}ms")
    assert p50 < 0.5 and p99 < 2.0, (p50, p99)


# ── privacy ──────────────────────────────────────────────────────────────────

async def test_a_guest_row_holds_no_words(fresh):
    await feed(item("what's on my list", "Your list is empty.", user="guest", channel="voice",
                    session="voice-panel-hall-0000aaaa"),
               item("what's on my list", "Your list is empty.", user="guest", channel="voice",
                    session="voice-panel-hall-0000aaaa"))
    row = fresh.data[0]
    assert row["user_id"] == "guest" and row["is_guest"] == 1 and row["summary"] == "" and row["session_id"] == ""
    assert "list" not in row["detail"] and row["pattern"].startswith("unrouted|what:")


async def test_a_member_row_keeps_the_scrubbed_words(fresh):
    await feed(item("my card is 4111 1111 1111 1111 please remember"), item("my card is 4111 1111 1111 1111 please remember"))
    assert fresh.data and "4111" not in fresh.data[0]["summary"]


async def test_off_the_record_and_forgotten_turns_keep_no_words(monkeypatch, fresh):
    import memory_provenance

    monkeypatch.setattr(memory_provenance, "is_off_record", lambda _u, t: "secret" in t)
    await feed(item("tell me the secret plan again"), item("tell me the secret plan again"))
    assert fresh.data[-1]["summary"] == ""

    import memory_forgotten

    async def names_it(_u, t):
        return "quillon" in t.lower()

    monkeypatch.setattr(memory_forgotten, "matches", names_it)
    await feed(item("when is Quillon visiting", session="s2"), item("when is Quillon visiting", session="s2"))
    assert fresh.data[-1]["summary"] == ""
    await feed(item("what's the capital of chad", session="s3"), item("what's the capital of chad", session="s3"))
    assert fresh.data[-1]["summary"], "an ordinary member turn keeps its words"


async def test_a_slow_forget_lookup_keeps_no_words_and_does_not_stall(monkeypatch, fresh):
    import memory_forgotten

    async def hangs(*_a, **_k):
        await asyncio.sleep(30)

    monkeypatch.setattr(memory_forgotten, "matches", hangs)
    monkeypatch.setattr(sd, "STORE_TIMEOUT_S", 0.05)
    t0 = time.perf_counter()
    await feed(item("what's the capital of chad"), item("what's the capital of chad"))
    assert time.perf_counter() - t0 < 2.0
    assert fresh.data and fresh.data[0]["summary"] == ""


async def test_an_identity_flip_on_a_panel_keeps_no_words(fresh):
    await feed(item("set a time for five minutes", user=A, channel="voice", session="voice-panel-hall-11112222"),
               item("set a timer for five minutes", user=B, channel="voice", session="voice-panel-hall-33334444"))
    row = fresh.data[0]
    assert row["signal"] == "MISHEAR" and row["user_id"] == B and row["summary"] == ""


# ── forget / delete ──────────────────────────────────────────────────────────

async def _seed_named(fresh):
    await feed(item("when is Marlowe coming over", session="s1"), item("when is Marlowe coming over", session="s1"),
               item("what's the capital of chad", session="s2"), item("what's the capital of chad", session="s2"),
               item("when is Marlowe coming over", user=B, session="s3"), item("when is Marlowe coming over", user=B, session="s3"))
    assert len(fresh.data) == 3


async def test_erase_entity_removes_only_that_users_rows_naming_it(fresh):
    await _seed_named(fresh)
    assert await sd.erase_entity(A, "Marlowe") == 1
    left = {(r["user_id"], "marlowe" in r["summary"]) for r in fresh.data}
    assert left == {(A, False), (B, True)}


async def test_control_erase_entity_depends_on_the_name_pattern(monkeypatch, fresh):
    """Negative control: with the forget pattern broken, the erase removes nothing - so the test above is testing it."""
    await _seed_named(fresh)
    import re

    import memory_forgotten
    monkeypatch.setattr(memory_forgotten, "name_pattern", lambda _n: re.compile(r"(?!)"))
    assert await sd.erase_entity(A, "Marlowe") == 0


async def test_erase_and_delete_fail_closed(fresh):
    class Down(sd.MemoryBackend):
        async def matching(self, *_a):
            raise ConnectionError("pool lost")

        async def delete_user(self, *_a):
            raise RuntimeError("db_pool not initialised - call init_pool() first")

    sd.set_backend(Down())
    with pytest.raises(ConnectionError):
        await sd.erase_entity(A, "Marlowe")
    with pytest.raises(RuntimeError):
        await sd.delete_user(A)


async def test_a_missing_table_is_nothing_stored(fresh):
    class NoTable(sd.MemoryBackend):
        async def delete_user(self, *_a):
            raise Exception('relation "struggle_events" does not exist')

    sd.set_backend(NoTable())
    assert await sd.delete_user(A) == 0


async def test_delete_user(fresh):
    await _seed_named(fresh)
    assert await sd.delete_user(A) == 2
    assert {r["user_id"] for r in fresh.data} == {B}


async def test_memory_service_delete_user_erases_struggle_rows_and_fails_closed(monkeypatch, fresh):
    from memory_service import MemoryService, MemoryServiceError

    await _seed_named(fresh)
    svc = MemoryService(data_dir="/nonexistent/zoe-test-struggle")
    monkeypatch.setattr(svc, "_list_ids_for_user", lambda uid: [])
    monkeypatch.setattr(svc, "_delete_audit_for_user_sync", lambda uid: 0)
    await svc.delete_user(A, actor="admin", reason="rtbf")
    assert {r["user_id"] for r in fresh.data} == {B}

    class Down(sd.MemoryBackend):
        async def delete_user(self, *_a):
            raise ConnectionError("pool lost")

    sd.set_backend(Down())
    with pytest.raises(MemoryServiceError, match="struggle-row erasure failed"):
        await svc.delete_user(B, actor="admin", reason="rtbf")


# ── the real forget handler ──────────────────────────────────────────────────

from forgotten_support import USER, ledger_env, no_offers, svc  # noqa: E402,F401 - fixtures


async def test_the_real_forget_handler_erases_struggle_rows_and_refuses_when_it_cannot(fresh, svc, no_offers):
    import intent_router

    await feed(item("when is Marlowe coming over", user=USER), item("when is Marlowe coming over", user=USER),
               item("what's the capital of chad", user=USER, session="s2"),
               item("what's the capital of chad", user=USER, session="s2"))
    assert len(fresh.data) == 2
    reply = await intent_router.execute_intent(intent_router.Intent("memory_forget_entity", {"name": "Marlowe"}), USER)
    assert reply and "can't say it's forgotten yet" not in reply
    assert ["marlowe" in r["summary"] for r in fresh.data] == [False]

    class Down(sd.MemoryBackend):
        async def matching(self, *_a):
            raise ConnectionError("pool lost")

    sd.set_backend(Down())
    refused = await intent_router.execute_intent(intent_router.Intent("memory_forget_entity", {"name": "Brindle"}), USER)
    assert "can't say it's forgotten yet" in refused


# ── retention ────────────────────────────────────────────────────────────────

async def test_retention_sweep(monkeypatch, fresh):
    monkeypatch.setenv("ZOE_STRUGGLE_RETENTION_DAYS", "2")
    now = time.time()
    fresh.data = [{"id": "old", "ts": now - 3 * 86400, "user_id": A}, {"id": "new", "ts": now - 86400, "user_id": A}]
    await sd._maybe_sweep(now)
    assert [r["id"] for r in fresh.data] == ["new"]
    fresh.data.insert(0, {"id": "old2", "ts": now - 9 * 86400, "user_id": A})
    await sd._maybe_sweep(now + 60)          # at most once an hour
    assert len(fresh.data) == 2
    await sd._maybe_sweep(now + sd.SWEEP_EVERY_S + 1)
    assert [r["id"] for r in fresh.data] == ["new"]
    assert sd.stats()["swept"] == 2


# ── synthetic traffic ────────────────────────────────────────────────────────

async def test_harness_and_test_traffic_is_never_recorded(fresh):
    assert sd.observe("voice", "what time is it", "Noon.", A, "voice-panel-replay-harness-1234abcd") is False
    assert sd.observe("voice", "what time is it", "Noon.", A, "s", panel_id="test-panel") is False
    assert sd.observe("chat", "what time is it", "Noon.", "test-user-1", "s") is False
    assert sd.stats()["skipped_synthetic"] == 3


# ── the hooks in the real modules ────────────────────────────────────────────

def test_semantic_router_route_notes_its_decision(monkeypatch):
    import numpy as np

    import semantic_router as sr

    monkeypatch.setattr(sr, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(sr, "ROUTES", {"weather": ["x"], "chat": ["y"]})
    monkeypatch.setattr(sr, "_MATRIX", np.eye(2, dtype=np.float32))
    monkeypatch.setattr(sr, "_DOM_IDX", {"weather": np.array([0]), "chat": np.array([1])})
    monkeypatch.setattr(sr, "embed", lambda _t: np.array([0.9, 0.2], dtype=np.float32))
    monkeypatch.setattr(sr, "head_mode", lambda: "active")
    monkeypatch.setattr(sr, "_two_stage_active", lambda *_a: None)        # the sidecar timed out
    out = sr.route("is it going to be stormy later")
    assert out["domain"] == "weather"
    ts, text, rec = sd._ROUTES[-1]
    assert text == "is it going to be stormy later" and rec["domain"] == "weather"
    assert rec["two_stage_active"] is True and rec["two_stage_failed"] is True


def test_brain_dispatch_lane_line_notes_the_brain():
    import brain_dispatch

    brain_dispatch._log_lane(attempted="flue", served="flue", outcome="ok", session_id="voice-panel-hall-9")
    assert sd._BRAINS[-1][1:] == ("voice-panel-hall-9", "flue", "ok")


async def test_voice_memory_passes_call_the_hook_first_and_survive_it_raising(monkeypatch):
    import routers.voice_tts as vt

    seen = []
    monkeypatch.setattr(sd, "observe", lambda *a, **k: seen.append((a, k)) or (_ for _ in ()).throw(RuntimeError("bug")))
    import hold_the_fact
    monkeypatch.setattr(hold_the_fact, "is_own_reply", lambda _r: True)     # stop right after the hook: no extractors
    await vt._run_voice_memory_passes("what's the capital of chad", "N'Djamena.", A, "voice-panel-hall-1", interrupted=True)
    assert seen and seen[0][0][:5] == ("voice", "what's the capital of chad", "N'Djamena.", A, "voice-panel-hall-1")
    assert seen[0][1] == {"interrupted": True}


async def test_chat_memory_hook_calls_the_detector_and_survives_it_raising(monkeypatch):
    from routers import chat as chat_router

    seen = []
    monkeypatch.setattr(sd, "observe", lambda *a, **k: seen.append(a) or (_ for _ in ()).throw(RuntimeError("bug")))

    async def impl(*_a, **_k):
        return True

    monkeypatch.setattr(chat_router, "_persist_memory_candidates_impl", impl)
    await chat_router._persist_memory_candidates(A, "s1", "what's the capital of chad", "N'Djamena.")
    assert seen == [("chat", "what's the capital of chad", "N'Djamena.", A, "s1")]


# ── the operator tools (scripts/maintenance) ─────────────────────────────────

def _script(name):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / name
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_LOG = """\
2026-01-05T07:00:00+0000 INFO     routers.voice_tts: voice/turn_stream panel=hall-panel STT=1.10s transcript="Hey zoe. What's it going to be like outside today?"
2026-01-05T07:00:01+0000 INFO     zoe.router_head_shadow: router_two_stage {"actual_routed": "weather", "failed": false, "gated": false, "head_conf": 0.99, "two_stage_domain": "weather"}
2026-01-05T07:00:01+0000 WARNING  routers.voice_tts: ROUTER_SHADOW text="What's it going to be like outside today?" -> routed=weather (best=weather 0.86) 900.0ms scores={'weather': 0.86, 'chat': 0.7}
2026-01-05T07:00:01+0000 INFO     routers.voice_tts: voice/command panel=hall-panel session=voice-panel-hall-panel-0a0b0c0d user=guest len=40 [identity: identified=None]
2026-01-05T07:00:02+0000 INFO     routers.voice_tts: SKYBRIDGE TIMING resolve=1.20s broadcast=0.03s synth=0.00s stream=True reply="It's 16 degrees and sunny."
2026-01-05T07:00:15+0000 INFO     routers.voice_tts: voice/turn_stream panel=hall-panel STT=0.90s transcript='How warn is it going to get today?'
2026-01-05T07:00:17+0000 WARNING  router_two_stage: two_stage decide failed (non-fatal, → fallback): timed out
2026-01-05T07:00:17+0000 WARNING  routers.voice_tts: ROUTER_SHADOW text='How warn is it going to get today?' -> routed=weather (best=weather 0.70) 90.0ms scores={'weather': 0.7, 'time': 0.68}
2026-01-05T07:00:17+0000 INFO     routers.voice_tts: voice/command panel=hall-panel session=voice-panel-hall-panel-0a0b0c0d user=guest len=33 [identity: identified=None]
2026-01-05T07:00:22+0000 INFO     brain_dispatch: BRAIN_LANE lane_attempted=flue lane_served=flue outcome=ok reason=- session=voice-panel-hall-panel-0a0b0c0d
2026-01-05T07:00:31+0000 INFO     routers.voice_tts: voice/turn_stream panel=hall-panel STT=1.00s transcript='How warm is it going to get today?'
2026-01-05T07:00:33+0000 WARNING  router_two_stage: two_stage decide failed (non-fatal, → fallback): timed out
2026-01-05T07:00:33+0000 WARNING  routers.voice_tts: ROUTER_SHADOW text='How warm is it going to get today?' -> routed=weather (best=weather 0.79) 50.0ms scores={'weather': 0.79, 'time': 0.7}
2026-01-05T07:00:33+0000 INFO     routers.voice_tts: voice/command panel=hall-panel session=voice-panel-hall-panel-0a0b0c0d user=guest len=33 [identity: identified=None]
2026-01-05T07:00:38+0000 INFO     brain_dispatch: BRAIN_LANE lane_attempted=flue lane_served=flue outcome=ok reason=- session=voice-panel-hall-panel-0a0b0c0d
2026-01-05T07:01:00+0000 INFO     routers.voice_tts: voice/turn_stream panel=replay-harness STT=1.00s transcript='How warm is it going to get today?'
"""


def test_offline_replay_parses_the_log_and_catches_the_forecast_episode(tmp_path):
    from datetime import datetime

    ev = _script("struggle_replay_eval.py")
    log = tmp_path / "zoe-data.app.log"
    log.write_text(_LOG, encoding="utf-8")
    turns = ev.parse([log], datetime(2026, 1, 1))
    assert len(turns) == 3, "the replay-harness panel must be dropped"
    assert turns[0].path == sig.PATH_FAST and turns[0].reply.startswith("It's 16") and turns[0].two_stage_domain == "weather"
    assert turns[1].path == sig.PATH_BRAIN and turns[1].two_stage_failed and turns[1].predicted_score == 0.7
    got = [[e.signal for e in evs] for _p, _t, evs in ev.run(turns)]
    assert got == [[], ["FALLTHROUGH", "REPHRASE"], ["FALLTHROUGH", "MISHEAR"]]
    labels = {ev.key_of(turns[1]): {"FALLTHROUGH", "REPHRASE"}, ev.key_of(turns[2]): {"FALLTHROUGH", "MISHEAR"},
              ev.key_of(turns[0]): set()}
    rep = ev.report(ev.run(turns), labels)
    assert rep["per_signal"]["REPHRASE"] == {"tp": 1, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0}
    assert rep["per_signal"]["FALLTHROUGH"]["tp"] == 2


def test_offline_replay_refuses_to_write_the_sheet_inside_the_repo(tmp_path):
    from pathlib import Path

    ev = _script("struggle_replay_eval.py")
    repo = Path(ev.__file__).resolve().parents[2]
    assert ev.main(["--log-dir", str(tmp_path), "--dump", "--out-dir", str(repo / "tmp-sheet")]) == 2
    assert not (repo / "tmp-sheet").exists()
