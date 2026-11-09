"""The MORNING TRANSCRIPT CHECK (``transcript_check.py``, migration 0073, flag ``ZOE_TRANSCRIPT_CHECK``). Spec: docs/knowledge/transcript-check.md.

Groups (each invariant has its negative control next to it):
  rank       names / numbers / times first, chit-chat last, capped per day, whole-chunk items capped lower
  night      the re-pass result becomes queue rows; shadow stores rows nobody can see and holds no audio; an open item holds its WAV
  ask        once a day, only a VERIFIED voice turn, only after the command was answered, never emotional / unverified / typed / empty
  reply      "yes" opens the panel screen; "later" leaves the notification and is not asked again; a stray "yes" is not taken
  serve      a guest sees nothing; another member's item is 404; a forgotten span is never shown
  answer     the segment + transcript are corrected (owner_corrected), the ledger learns, the day review re-opens
  say-it     the owner's next verified utterance fills the word

Synthetic names and text only.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import omi_recordings as omi
import omi_repass as rp
import transcript_check as tc
import user_erase_gate as erase_gate
from test_omi_recordings import OWNER, _migrate, env, make_wav, rows, upload  # noqa: F401
from test_omi_repass import fake_runner, first_pass, result_for, transcribe  # noqa: F401

UTC = timezone.utc
MORNING = datetime(2026, 10, 10, 23, 30, tzinfo=UTC)          # 07:30 household time (Perth, +8)
EARLY = datetime(2026, 10, 10, 20, 0, tzinfo=UTC)             # 04:00 household time
WORDS = ("tell", "Marisol", "to", "meet", "me", "at", "seven")


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture
def tenv(env, monkeypatch, tmp_path):
    _migrate(env.engine, "0054_omi_day_review.py")
    _migrate(env.engine, "0055_omi_segments_voices.py")
    _migrate(env.engine, "0073_omi_night_repass.py")
    _migrate(env.engine, "0075_transcript_check.py")
    monkeypatch.setenv("ZOE_OMI_NIGHT_REPASS", "on")
    monkeypatch.setenv("ZOE_OMI_REPASS_DIR", str(tmp_path / "repass"))
    monkeypatch.setenv("ZOE_OMI_REPASS_ZOE_CLIPS", str(tmp_path / "no-clips"))
    monkeypatch.setenv("ZOE_TRANSCRIPT_CHECK", "on")
    monkeypatch.setattr(tc, "_now", lambda: MORNING)
    tc.reset()
    return env


def chunk(slots=None, low_conf=False, t0=0.0, t1=3.5, hyps=None, rover="tell Marisol to meet me at seven"):
    """One stored ROVER chunk row, the shape ``omi_repass_eval.check_queue`` returns: slots {pos, pick, votes, who, n}."""
    slot = {"pos": 6, "pick": "seven", "n": 3, "votes": {"seven": 2, "eleven": 1}, "who": {"seven": ["qwen3", "parakeet"], "eleven": ["whisper"]}}
    return {"i": 0, "t0": t0, "t1": t1, "rover": rover, "slots": [slot] if slots is None else slots, "low_conf": low_conf, "voters": ["qwen3", "parakeet", "whisper"],
            "hyps": hyps or {"qwen3": "tell Marisol to meet me at seven", "parakeet": "tell Marisol to meet me at seven", "whisper": "tell Marisol to meet me at eleven"}}


def night(env, monkeypatch, chunks, text=WORDS):
    """A done, re-passed recording whose fusion left ``chunks`` (contested rows); returns the recording id. The hyps store holds them too, as a real night leaves it."""
    first_pass(env)
    transcribe(env, monkeypatch)
    rid = rows(env)[0]["id"]
    res = result_for(rid, text)
    res["check_rows"] = chunks
    run(rp.run_night(budget_s=600, runner=fake_runner({rid: res})))
    import omi_repass_eval as eu
    eu.store_hyps(rp.home_dir(), "2026-10-10", [{"u": OWNER, "rid": rid, **c} for c in chunks])
    return rid


def items(env, sql="SELECT * FROM omi_check_items ORDER BY score DESC"):
    return env.db.rows(sql)


def verified_turn(session="s1", member=OWNER, panel="panel-1", verdict=True):
    tc.note_voice_turn(session, panel, scope_user=member, verdict=verdict)


@pytest.fixture
def queued(tenv, monkeypatch):
    """A re-passed recording with one contested time slot, queue ON, ask time 07:30."""
    rid = night(tenv, monkeypatch, [chunk()])
    return tenv, rid


# ── rank ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_names_numbers_and_commitments_rank_above_chat():
    alts = [{"word": "a", "votes": 2}, {"word": "b", "votes": 1}]
    assert tc.classify("we saw", "Delphine", "at the shops", [{"word": "Delfina", "votes": 1}], {"delphine"}) == "name"
    assert tc.classify("meet me at", "seven", "on Thursday", alts) == "number"
    assert tc.classify("I'll", "ring", "the plumber tomorrow", alts) in ("commitment", "number")      # 'tomorrow' is a date word
    assert tc.classify("that was a really", "nice", "film", alts) == "chat"
    s = {k: tc.score(k, alts, False) for k in ("name", "number", "commitment", "chat")}
    assert s["name"] > s["number"] > s["commitment"] > s["chat"]
    assert tc.score("chat", alts, True) > tc.score("number", alts, False) or tc.FEEDS_BONUS < 50     # feeding a review lifts an item


def test_the_day_keeps_only_the_best_ten_and_chunks_never_fill_it(tenv, monkeypatch):
    slots = [{"pos": 1, "pick": f"w{i}", "n": 3, "votes": {f"w{i}": 2, f"x{i}": 1}, "who": {f"w{i}": ["qwen3", "parakeet"], f"x{i}": ["whisper"]}} for i in range(14)]
    slots.append({"pos": 2, "pick": "7", "n": 3, "votes": {"7": 2, "eleven": 1}, "who": {"7": ["qwen3", "parakeet"], "eleven": ["whisper"]}})
    rid = night(tenv, monkeypatch, [chunk(slots=slots, rover="a w b c d e f"), chunk(slots=[], low_conf=True), chunk(slots=[], low_conf=True, t0=3.6, t1=7.0)])
    rs = items(tenv)
    assert len(rs) <= tc.max_items()
    assert rs[0]["importance"] in ("number", "name")                                  # the digit outranks the chit-chat
    assert sum(r["kind"] == "chunk" for r in rs) <= tc.MAX_CHUNK_ITEMS


# ── night ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_night_pass_queues_a_contested_slot_with_every_models_word(queued):
    env, rid = queued
    (r,) = items(env)
    assert r["recording_id"] == rid and r["status"] == "open" and r["span_text"] == "seven" and r["before_text"].endswith("at")
    alts = json.loads(r["alts"])
    assert {a["word"] for a in alts} == {"seven", "eleven"}
    assert [a for a in alts if a["word"] == "eleven"][0]["models"] == ["whisper"]       # who offered it, for the ledger
    assert 0 <= r["start_ms"] < r["end_ms"] <= 3500


def test_a_low_confidence_chunk_offers_each_models_whole_text(tenv):
    cands = tc.candidates_from_row(chunk(slots=[], low_conf=True, rover="tell Marisol", hyps={"qwen3": "tell Marisol", "parakeet": "tell Marissa", "whisper": "tell Marissa"}))
    (c,) = cands
    assert c["kind"] == "chunk" and c["span"] == "tell Marisol"
    assert {a["word"]: a["models"] for a in c["alts"]} == {"tell Marisol": ["qwen3"], "tell Marissa": ["parakeet", "whisper"]}


def test_flag_off_stores_nothing(tenv, monkeypatch):
    monkeypatch.setenv("ZOE_TRANSCRIPT_CHECK", "off")
    night(tenv, monkeypatch, [chunk()])
    assert items(tenv) == []


def test_shadow_stores_rows_nobody_can_see_and_holds_no_audio(tenv, monkeypatch, caplog):
    monkeypatch.setenv("ZOE_TRANSCRIPT_CHECK", "shadow")
    caplog.set_level("INFO")
    night(tenv, monkeypatch, [chunk()])
    (r,) = items(tenv)
    assert r["status"] == "shadow"
    assert run(tc.open_recording_ids()) == set()                                       # holds no audio
    assert run(tc.list_open(OWNER)) == {"items": [], "done": 0, "total": 0}            # serves nothing
    log = " ".join(m for m in caplog.messages if "TRANSCRIPT_CHECK_SHADOW" in m)
    assert "stored" in log and "Marisol" not in log and "seven" not in log             # counts only
    verified_turn()
    assert run(tc.prepare_tail(OWNER, "s1", "put the kettle on", "Done.")) is None      # and asks nothing
    from fastapi import FastAPI
    from routers import transcript_check as rt
    assert rt.register(FastAPI()) is False                                              # the routes are absent


def test_an_open_item_holds_its_wav_and_answering_releases_it(queued):
    env, rid = queued
    assert rows(env)[0]["audio_path"] and Path(rows(env)[0]["audio_path"]).exists()      # the pass would have released it, the item holds it
    run(omi.release_tagged_audio(OWNER))
    assert Path(rows(env)[0]["audio_path"]).exists()
    iid = items(env)[0]["id"]
    assert run(tc.skip(OWNER, iid))
    assert rows(env)[0]["audio_path"] == ""                                              # nothing holds it now


def test_control_the_wav_goes_at_retention_whatever_an_item_wants(queued):
    env, rid = queued
    assert run(omi.purge_expired_audio(now=datetime.now(UTC) + timedelta(days=8))) == 1
    assert rows(env)[0]["audio_path"] == ""
    assert run(tc.clip(OWNER, items(env)[0]["id"])) is None


def test_the_snippet_is_cut_from_the_held_audio(queued):
    env, rid = queued
    wav = run(tc.clip(OWNER, items(env)[0]["id"]))
    assert wav and wav[:4] == b"RIFF"
    assert run(tc.clip("member-b", items(env)[0]["id"])) is None                         # not theirs


# ── ask ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_ask_rides_a_verified_turn_once_after_the_command_is_answered(queued, monkeypatch):
    env, rid = queued

    async def name(uid):
        return "Rowan"
    import identity_facts
    monkeypatch.setattr(identity_facts, "preferred_name", name)
    verified_turn()
    tail = run(tc.prepare_tail(OWNER, "s1", "put the kettle on", "Done, the kettle is on."))
    assert tail.startswith("Hey Rowan, I had a few issues with last night's transcripts")      # the member's own preferred name
    run(tc.confirm_tail(OWNER, "s1"))
    assert run(tc.prepare_tail(OWNER, "s1", "what time is it", "It's half past seven.")) is None      # once a day
    assert env.db.rows("SELECT outcome FROM omi_check_asks")[0]["outcome"] == "asked"
    assert env.db.rows("SELECT type FROM notifications")[0]["type"] == "transcript_check"            # and the notification is left


def test_the_name_is_never_hard_coded(queued, monkeypatch):
    import identity_facts

    async def none(uid):
        return ""
    monkeypatch.setattr(identity_facts, "preferred_name", none)
    verified_turn()
    tail = run(tc.prepare_tail(OWNER, "s1", "put the kettle on", "Done."))
    assert tail.startswith("Hey, I had") and "Jason" not in tail


@pytest.mark.parametrize("why", ["guest", "unverified", "no-note-typed", "empty-reply", "question-reply", "too-early", "emotional", "no-queue"])
def test_the_ask_never_fires(queued, monkeypatch, why):
    env, rid = queued
    reply, said = "Done, the kettle is on.", "put the kettle on"
    if why == "guest":
        verified_turn(member="guest")
    elif why == "unverified":
        verified_turn(verdict=False)
    elif why == "no-note-typed":
        pass                                                                              # a typed turn never calls note_voice_turn
    else:
        verified_turn()
    if why == "empty-reply":
        reply = "  "
    if why == "question-reply":
        reply = "Which kettle do you mean?"
    if why == "too-early":
        monkeypatch.setattr(tc, "_now", lambda: EARLY)
    if why == "emotional":
        said = "I feel really sad and I miss my mum"
        import emotional_turn_guard
        monkeypatch.setattr(emotional_turn_guard, "is_emotional_turn", lambda m: True)
    if why == "no-queue":
        env.db.conn.execute("DELETE FROM omi_check_items")
        env.db.conn.commit()
    assert run(tc.prepare_tail(OWNER, "s1", said, reply)) is None
    assert env.db.rows("SELECT * FROM omi_check_asks") == []                              # and nothing was reserved


def test_control_the_same_turn_asks_when_every_condition_holds(queued):
    verified_turn()
    assert run(tc.prepare_tail(OWNER, "s1", "put the kettle on", "Done.")) is not None


def test_a_reserved_ask_that_was_not_spoken_is_released(queued):
    env, rid = queued
    verified_turn()
    assert run(tc.prepare_tail(OWNER, "s1", "put the kettle on", "Done."))
    run(tc.release_tail(OWNER, "s1"))
    assert run(tc.prepare_tail(OWNER, "s1", "put the kettle on", "Done."))                # an interrupted reply does not burn the day


async def _collect(gen):
    return [d async for d in gen]


def test_the_stream_wrapper_appends_after_the_reply_and_only_then(queued):
    env, rid = queued
    verified_turn()

    async def reply():
        yield "Done. "
        yield "The kettle is on."
    out = run(_collect(tc.append_stream(reply(), "put the kettle on", OWNER, "s1")))
    assert out[:2] == ["Done. ", "The kettle is on."] and "transcripts" in out[2] and len(out) == 3
    assert "s1" in tc._PENDING

    async def again():
        yield "Half past seven."
    assert run(_collect(tc.append_stream(again(), "what time is it", OWNER, "s1"))) == ["Half past seven."]      # once a day


def test_a_barge_in_gets_no_line_and_keeps_the_day(queued):
    env, rid = queued
    verified_turn()

    async def reply():
        yield "Done. "
        yield "The kettle is on."

    async def interrupted():
        g = tc.append_stream(reply(), "put the kettle on", OWNER, "s1")
        await g.__anext__()
        await g.aclose()
    run(interrupted())
    assert env.db.rows("SELECT * FROM omi_check_asks") == []


def test_non_command_text_in_a_stream_is_not_a_reply(queued):
    env, rid = queued
    verified_turn()

    async def only_tools():
        yield "__TOOL__:timer"
    assert run(_collect(tc.append_stream(only_tools(), "set a timer", OWNER, "s1"))) == ["__TOOL__:timer"]


# ── reply to the ask ─────────────────────────────────────────────────────────────────────────────────────────────────────
def asked(env):
    verified_turn()
    assert run(tc.prepare_tail(OWNER, "s1", "put the kettle on", "Done."))
    run(tc.confirm_tail(OWNER, "s1"))


def test_yes_opens_the_screen_on_this_panel(queued, monkeypatch):
    env, rid = queued
    asked(env)
    sent = []

    async def send(panel, kind, frame):
        sent.append((panel, kind, frame))
        return 1
    import voice_session
    monkeypatch.setattr(voice_session, "send_to_panel", send)
    verified_turn("s2")                                                                    # the yes is a new turn on the same session id
    tc._TURNS["s1"] = tc._TURNS["s2"]
    r = run(tc.tier("yes please", OWNER, "s1", channel="voice", allow_writes=True))
    assert r.reply == tc.OPENED_REPLY
    (panel, kind, frame), = sent
    assert panel == "panel-1" and kind == "voice_card" and frame["open"] == "checks" and frame["private"] and frame["card_token"]
    assert "Marisol" not in json.dumps(frame)                                              # the frame carries a count, never a sentence
    assert env.db.rows("SELECT outcome FROM omi_check_asks")[0]["outcome"] == "opened"


def test_later_leaves_the_notification_and_is_never_asked_again_today(queued):
    env, rid = queued
    asked(env)
    r = run(tc.tier("not now", OWNER, "s1", channel="voice", allow_writes=True))
    assert r.reply == tc.LATER_REPLY
    assert env.db.rows("SELECT outcome FROM omi_check_asks")[0]["outcome"] == "later"
    assert len(env.db.rows("SELECT * FROM notifications WHERE type = 'transcript_check'")) == 1
    assert run(tc.prepare_tail(OWNER, "s1", "put the kettle on", "Done.")) is None


def test_no_answer_just_carries_on_and_the_notification_stays(queued):
    env, rid = queued
    asked(env)
    assert run(tc.tier("what's the weather", OWNER, "s1", channel="voice", allow_writes=True)) is None         # a new command is a new command
    assert "s1" not in tc._PENDING
    assert len(env.db.rows("SELECT * FROM notifications WHERE type = 'transcript_check'")) == 1


def test_control_a_yes_with_no_ask_pending_is_not_taken(queued):
    env, rid = queued
    verified_turn()
    assert run(tc.tier("yes", OWNER, "s1", channel="voice", allow_writes=True)) is None


def test_a_yes_from_a_chat_turn_is_not_taken(queued):
    env, rid = queued
    asked(env)
    assert run(tc.tier("yes", OWNER, "s1", channel="chat", allow_writes=True)) is None


def test_say_it_takes_the_next_verified_utterance(queued):
    env, rid = queued
    iid = items(env)[0]["id"]
    verified_turn()
    tc.listen_for(OWNER, iid, "panel-1")
    r = run(tc.tier("it's eight", OWNER, "s1", channel="voice", allow_writes=True))
    assert r.reply == tc.HEARD_REPLY
    assert items(env)[0]["status"] == "answered" and items(env)[0]["answer"] == "eight" and items(env)[0]["answered_via"] == "voice"


def test_say_it_is_not_taken_from_an_unverified_voice(queued):
    env, rid = queued
    iid = items(env)[0]["id"]
    verified_turn(verdict=False)
    tc.listen_for(OWNER, iid, "panel-1")
    assert run(tc.tier("it's eight", OWNER, "s1", channel="voice", allow_writes=True)) is None
    assert items(env)[0]["status"] == "open"


# ── serve ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_owner_gets_the_item_with_progress(queued):
    env, rid = queued
    d = run(tc.list_open(OWNER))
    assert d["total"] == 1 and d["done"] == 0
    it = d["items"][0]
    assert it["before"].endswith("at") and it["span"] == "seven" and [a["word"] for a in it["alts"]] == ["seven", "eleven"]
    assert run(tc.list_open("member-b")) == {"items": [], "done": 0, "total": 0}          # another member's layer is empty


def _client(env, user):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import auth
    from routers import transcript_check as rt
    app = FastAPI()
    assert rt.register(app) is True
    if user is None:
        app.dependency_overrides[auth.get_current_user] = lambda: {"user_id": "guest", "role": "guest"}
    else:
        app.dependency_overrides[auth.get_current_user] = lambda: user
        app.dependency_overrides[auth.require_signed_in] = lambda: user
    return TestClient(app)


def test_a_guest_never_sees_it(queued, monkeypatch):
    env, rid = queued
    c = _client(env, None)
    for path in ("", "/summary", f"/{items(env)[0]['id']}/clip", "/phone-qr"):
        r = c.get("/api/transcript-check" + path)
        assert r.status_code in (401, 403) and "Marisol" not in r.text and "seven" not in r.text, path
    assert c.post(f"/api/transcript-check/{items(env)[0]['id']}/answer", json={"alt": 1}).status_code in (401, 403)
    assert items(env)[0]["status"] == "open"


def test_the_signed_in_owner_gets_it_and_a_stranger_gets_404(queued):
    env, rid = queued
    me = {"user_id": OWNER, "role": "member", "username": OWNER}
    c = _client(env, me)
    r = c.get("/api/transcript-check")
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["span"] == "seven"
    assert c.get("/api/transcript-check/summary").json() == {"open": 1}
    stranger = _client(env, {"user_id": "member-b", "role": "member", "username": "member-b"})
    # legacy mode: only the configured owner may use the omi routes at all
    assert stranger.get("/api/transcript-check").status_code in (403, 200)
    r = stranger.post(f"/api/transcript-check/{items(env)[0]['id']}/answer", json={"alt": 1})
    assert r.status_code in (403, 404) and items(env)[0]["status"] == "open"


def test_a_forgotten_span_is_never_shown(queued, monkeypatch):
    env, rid = queued
    import memory_forgotten as mf

    async def spans(uid, text, **kw):
        i = text.find("Marisol")
        return [(i, i + 7)] if i >= 0 else []
    monkeypatch.setattr(mf, "spans", spans)
    assert run(tc.list_open(OWNER))["items"] == []
    assert items(env) == []                                                               # and it was deleted, not just hidden


def test_control_a_name_that_was_not_forgotten_is_shown(queued, monkeypatch):
    env, rid = queued
    import memory_forgotten as mf

    async def spans(uid, text, **kw):
        i = text.find("Zebulon")
        return [(i, i + 7)] if i >= 0 else []
    monkeypatch.setattr(mf, "spans", spans)
    assert len(run(tc.list_open(OWNER))["items"]) == 1


def test_an_unreadable_forget_ledger_fails_closed(queued, monkeypatch):
    env, rid = queued
    import memory_forgotten as mf

    async def down(uid, text, **kw):
        raise RuntimeError("ledger down")
    monkeypatch.setattr(mf, "spans", down)
    assert run(tc.list_open(OWNER))["items"] == []
    assert len(items(env)) == 1                                                           # shown nothing, deleted nothing


def test_a_forget_of_a_name_deletes_the_items_that_name_it(queued):
    env, rid = queued
    import memory_forgotten
    import omi_voices
    rx = memory_forgotten.name_pattern("Marisol")

    async def go():
        from db_compat import get_compat_db
        async with get_compat_db() as db:
            return await omi_voices.redact(db, OWNER, rx, "[forgotten]")
    run(go())
    assert items(env) == []


def test_deleting_the_recording_or_the_member_takes_the_queue_with_it(queued):
    env, rid = queued
    assert run(omi.delete_recording(OWNER, rid))
    assert items(env) == []


def test_forgetting_the_member_erases_queue_and_lock(queued):
    env, rid = queued
    verified_turn()
    assert run(tc.prepare_tail(OWNER, "s1", "put the kettle on", "Done."))
    assert env.db.rows("SELECT * FROM omi_check_asks") and items(env)
    run(omi.forget_user(OWNER))
    for t in ("omi_check_items", "omi_check_asks"):
        assert env.db.rows(f"SELECT * FROM {t}") == [], t


# ── answer ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_an_answer_corrects_the_segment_and_transcript_and_teaches_the_ledger(queued):
    env, rid = queued
    assert "at seven" in rows(env)[0]["transcript"] or "seven" in rows(env)[0]["transcript"]
    iid = items(env)[0]["id"]
    alts = json.loads(items(env)[0]["alts"])
    pick = [i for i, a in enumerate(alts) if a["word"] == "eleven"][0]
    r = run(tc.answer(OWNER, iid, alt=pick))
    assert r["applied"] is True
    assert "eleven" in rows(env)[0]["transcript"] and "seven" not in rows(env)[0]["transcript"]
    segs = env.db.rows("SELECT text, text_provenance FROM omi_segments WHERE text LIKE '%eleven%'")
    assert segs and segs[0]["text_provenance"] == "owner_corrected"
    assert all(r["text_provenance"] == "owner_corrected" for r in env.db.rows("SELECT text_provenance FROM omi_segments WHERE text LIKE '%eleven%'"))
    led = tc.ledger()                                                                          # #2358's ledger.jsonl, scored by omi_repass_eval.note_owner_correction
    assert led["whisper"] == {"owner_hit": 1, "owner_miss": 0}                                 # the owner agreed with whisper's whole chunk
    assert led["qwen3"] == {"owner_hit": 0, "owner_miss": 1} and led["parakeet"] == {"owner_hit": 0, "owner_miss": 1}
    assert items(env)[0]["status"] == "answered" and items(env)[0]["answered_via"] == "panel"


def test_an_answer_that_none_of_the_models_had_counts_against_all_of_them(queued):
    env, rid = queued
    iid = items(env)[0]["id"]
    run(tc.answer(OWNER, iid, word="nine", via="voice"))
    assert "nine" in rows(env)[0]["transcript"]
    assert all(v == {"owner_hit": 0, "owner_miss": 1} for v in tc.ledger().values()) and len(tc.ledger()) == 3


def test_an_answer_cannot_be_given_twice_or_to_someone_elses_item(queued):
    env, rid = queued
    iid = items(env)[0]["id"]
    run(tc.answer(OWNER, iid, alt=0))
    with pytest.raises(tc.CheckError) as e:
        run(tc.answer(OWNER, iid, alt=1))
    assert e.value.code == "already_decided"
    with pytest.raises(tc.CheckError) as e:
        run(tc.answer("member-b", iid, alt=0))
    assert e.value.status == 404


def test_an_answer_naming_a_forgotten_entity_is_refused(queued, monkeypatch):
    env, rid = queued
    import memory_forgotten as mf
    iid = items(env)[0]["id"]

    async def spans(uid, text, **kw):
        i = text.find("Zebulon")
        return [(i, i + 7)] if i >= 0 else []
    monkeypatch.setattr(mf, "spans", spans)
    with pytest.raises(tc.CheckError) as e:
        run(tc.answer(OWNER, iid, word="Zebulon"))
    assert e.value.status == 410 and "Zebulon" not in rows(env)[0]["transcript"]


def test_a_correction_reopens_the_day_review_that_cited_the_recording(queued):
    env, rid = queued
    now = "2026-10-10T00:00:00Z"
    env.db.conn.execute("UPDATE omi_recordings SET day_review_id = 'rev1' WHERE id = ?", (rid,))
    env.db.conn.execute("INSERT INTO omi_day_reviews (id, user_id, day_local, created_at) VALUES ('rev1', ?, '2026-10-10', ?)", (OWNER, now))
    for cid, st in (("c-pending", "pending"), ("c-approved", "approved")):
        env.db.conn.execute("INSERT INTO omi_day_candidates (id, review_id, user_id, day_local, kind, recording_ids, dedup_key, status, created_at) "
                            "VALUES (?, 'rev1', ?, '2026-10-10', 'reminder', ?, ?, ?, ?)", (cid, OWNER, json.dumps([rid]), cid, st, now))
    env.db.conn.commit()
    assert run(tc.answer(OWNER, items(env)[0]["id"], alt=1))["reopened"] == 1
    left = {r["id"] for r in env.db.rows("SELECT id FROM omi_day_candidates")}
    assert left == {"c-approved"}                                                          # a decision stays; a guess is redone
    assert rows(env)[0]["day_review_id"] is None                                           # so the next review reads the corrected text


def test_control_a_correction_with_no_dependent_candidate_touches_no_review(queued):
    env, rid = queued
    env.db.conn.execute("UPDATE omi_recordings SET day_review_id = 'rev1' WHERE id = ?", (rid,))
    env.db.conn.commit()
    assert run(tc.answer(OWNER, items(env)[0]["id"], alt=1))["reopened"] == 0
    assert rows(env)[0]["day_review_id"] == "rev1"


def test_a_high_ranked_item_that_feeds_a_pending_candidate_outranks_an_equal_one(tenv, monkeypatch):
    first_pass(tenv)
    transcribe(tenv, monkeypatch)
    rid = rows(tenv)[0]["id"]
    tenv.db.conn.execute("INSERT INTO omi_day_reviews (id, user_id, day_local, created_at) VALUES ('rev1', ?, '2026-10-10', 'x')", (OWNER,))
    tenv.db.conn.execute("INSERT INTO omi_day_candidates (id, review_id, user_id, day_local, kind, recording_ids, dedup_key, status, created_at) "
                         "VALUES ('c1', 'rev1', ?, '2026-10-10', 'reminder', ?, 'k', 'pending', 'x')", (OWNER, json.dumps([rid])))
    tenv.db.conn.commit()
    cand = tc.candidates_from_row(chunk())
    run(tc.enqueue(OWNER, dict(rows(tenv)[0]), cand))
    (r,) = items(tenv)
    assert r["score"] >= tc.IMPORTANCE_SCORE["number"] + tc.FEEDS_BONUS


def test_expired_items_are_swept_and_never_served(queued):
    env, rid = queued
    assert run(tc.sweep(now=datetime.now(UTC) + timedelta(days=9))) == 1
    assert run(tc.list_open(OWNER))["items"] == []


def test_an_answer_that_corrected_nothing_is_refused_and_credits_nothing(queued):
    """The words the item pointed at are gone (a later pass rewrote them): no ledger credit, no 'answered', and the item stops being asked."""
    env, rid = queued
    env.db.conn.execute("UPDATE omi_segments SET text = 'something else entirely'")
    env.db.conn.execute("UPDATE omi_recordings SET transcript = 'something else entirely'")
    env.db.conn.commit()
    iid = items(env)[0]["id"]
    with pytest.raises(tc.CheckError) as e:
        run(tc.answer(OWNER, iid, alt=1))
    assert e.value.code == "text_moved"
    assert tc.ledger() == {}
    assert items(env)[0]["status"] == "expired" and items(env)[0]["answer"] is None


def test_a_failed_review_reopen_fails_the_answer_instead_of_being_swallowed(queued, monkeypatch):
    """The correction, the day-review re-open, the ledger and the status are one unit: when the re-open fails the answer fails, the ledger is
    untouched and the item is still open - and a retry after the fault clears succeeds (the text swap is idempotent)."""
    env, rid = queued
    import omi_day_review as dr
    real = dr.reopen_recording_in

    async def boom(db, uid, r):
        raise RuntimeError("review store down")
    monkeypatch.setattr(dr, "reopen_recording_in", boom)
    iid = items(env)[0]["id"]
    with pytest.raises(RuntimeError):
        run(tc.answer(OWNER, iid, alt=1))
    assert items(env)[0]["status"] == "open" and tc.ledger() == {}
    monkeypatch.setattr(dr, "reopen_recording_in", real)
    assert run(tc.answer(OWNER, iid, alt=1))["applied"] is True
    assert items(env)[0]["status"] == "answered" and "eleven" in rows(env)[0]["transcript"]


def test_the_candidates_consume_what_the_real_rover_returns():
    """Contract pin against #2358's own fusion: three voters, three different words in one slot -> one contested slot, one candidate offering all three."""
    import omi_repass_eval as eu
    hy = {"qwen3-asr": "tell Marisol to meet me at seven", "parakeet-tdt": "tell Marisol to meet me at eleven", "whisper-gpu:large-v3": "tell Marisol to meet me at nine"}
    fz = eu.rover(hy)
    row = {"i": 0, "t0": 0.0, "t1": 3.0, "hyps": hy, "rover": fz["text"], "slots": fz["slots"], "low_conf": fz["low_conf"], "voters": fz["voters"]}
    assert eu.is_contested(row)
    (c,) = tc.candidates_from_row(row)
    assert {a["word"] for a in c["alts"]} == {"seven", "eleven", "nine"} and c["before"].endswith("at")
    assert sorted(m for a in c["alts"] for m in a["models"]) == sorted(hy)
    assert c["chunk_tpl"].count("\x01") == 1


# ── "who said this?" ─────────────────────────────────────────────────────────────────────────────────────────────────────
WHO = {"t0": 0.0, "t1": 1.5, "text": "put the garden things away on Sunday", "first": True, "turns": 4,
       "candidates": [{"person_id": "p-quinn", "name": "Quinn", "score": 0.61}, {"person_id": "p-ines", "name": "Ines", "score": 0.52}]}


@pytest.fixture
def who(queued, monkeypatch):
    """The queued recording plus one 'who said this?' item; omi_voices' own tag / label seams are recorded (their behaviour is pinned by test_omi_voices)."""
    env, rid = queued
    import omi_voices
    calls = []

    async def own_people(db, uid):
        return [{"id": "p-quinn", "name": "Quinn"}, {"id": "p-ines", "name": "Ines"}]

    async def tag(db, uid, **kw):
        calls.append(("tag", kw))
        return {"person_id": kw.get("person_id") or "p-new", "segments": 1, "learning": "learning"}

    async def label(db, uid, seg, value):
        calls.append(("label", seg, value))
        return True

    async def rebuild(db, cid):
        calls.append(("rebuild", cid))
        return True
    monkeypatch.setattr(omi_voices, "own_people", own_people)
    monkeypatch.setattr(omi_voices, "tag", tag)
    monkeypatch.setattr(omi_voices, "set_owner_label", label)
    monkeypatch.setattr(omi_voices, "rebuild_cluster", rebuild)
    run(tc.enqueue(OWNER, dict(rows(env)[0]), tc.candidates_from_who(WHO)))
    wid = env.db.rows("SELECT id FROM omi_check_items WHERE kind = 'who'")[0]["id"]
    return env, rid, wid, calls


def test_a_who_row_becomes_one_candidate_with_the_people_best_first():
    (c,) = tc.candidates_from_who(WHO)
    assert c["kind"] == "who" and c["span"] == WHO["text"] and [a["word"] for a in c["alts"]] == ["Quinn", "Ines"] and c["first"] and c["pick"] == "Quinn"
    assert tc.candidates_from_who({**WHO, "text": ""}) == []


def test_what_an_answer_unlocks_ranks_it_and_it_shares_the_ten_a_day_with_the_words(tenv, monkeypatch):
    first_pass(tenv)
    transcribe(tenv, monkeypatch)
    rec = dict(rows(tenv)[0])
    words = [tc.candidates_from_row(chunk(slots=[{"pos": 1, "pick": f"w{i}", "n": 3, "votes": {f"w{i}": 2, f"x{i}": 1}, "who": {}}], rover="a w b c", t0=float(i), t1=i + 1.0))[0]
             for i in range(12)]
    run(tc.enqueue(OWNER, rec, words))
    run(tc.enqueue(OWNER, rec, tc.candidates_from_who(WHO)))
    rs = items(tenv)
    assert len(rs) == tc.max_items()                                                       # one shared cap
    assert rs[0]["kind"] == "who" and rs[0]["importance"] == "who_first"                     # a voice's first confirmation unlocks the most
    run(tc.enqueue(OWNER, rec, tc.candidates_from_who({**WHO, "t0": 5.0, "t1": 6.0, "first": False})))
    assert [r["importance"] for r in items(tenv) if r["kind"] == "who"] == ["who_first", "who"] or len(items(tenv)) == tc.max_items()


def test_a_person_who_is_gone_or_forgotten_is_never_offered(who, monkeypatch):
    env, rid, wid, calls = who
    import memory_forgotten as mf
    import omi_voices

    async def spans(uid, text, **kw):
        i = text.find("Ines")
        return [(i, i + 4)] if i >= 0 else []
    monkeypatch.setattr(mf, "spans", spans)                                                 # Ines was forgotten
    it = [i for i in run(tc.list_open(OWNER))["items"] if i["kind"] == "who"][0]
    assert [a["word"] for a in it["alts"]] == ["Quinn"] and it["span"] == WHO["text"]
    with pytest.raises(tc.CheckError) as e:
        run(tc.answer(OWNER, wid, alt=1))                                                   # a stale tap on the forgotten one
    assert e.value.code == "person_gone" and not [c for c in calls if c[0] == "tag"]

    async def only_quinn(db, uid):
        return [{"id": "p-quinn", "name": "Quinn"}]
    monkeypatch.setattr(mf, "spans", lambda *a, **k: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(omi_voices, "own_people", only_quinn)                               # Ines was deleted
    assert [a["word"] for a in [i for i in run(tc.list_open(OWNER))["items"] if i["kind"] == "who"][0]["alts"]] == ["Quinn"]


def test_choosing_a_person_relabels_the_turn_and_teaches_their_print(who):
    env, rid, wid, calls = who
    r = run(tc.answer(OWNER, wid, alt=0))
    (kind, kw), = [c for c in calls if c[0] == "tag"]
    assert kw["person_id"] == "p-quinn" and kw["segment_id"] and r["applied"]                 # the seam: omi_voices.tag = the confirmed tag + the voiceprint
    assert env.db.rows("SELECT status FROM omi_check_items WHERE id = ?", (wid,))[0]["status"] == "answered"
    assert env.db.rows("SELECT * FROM omi_check_items WHERE kind = 'who' AND answer LIKE '%Quinn%'") == []          # no name is kept in the item


def test_someone_new_leaves_a_persistent_unnamed_voice_and_can_be_named_by_voice_later(who):
    env, rid, wid, calls = who
    run(tc.answer(OWNER, wid, new=True))
    assert ("label", calls[0][1], "other") == calls[0] and env.db.rows("SELECT status, answer FROM omi_check_items WHERE id = ?", (wid,))[0]["answer"] == ""
    assert not [c for c in calls if c[0] == "tag"]                                          # nothing named yet
    verified_turn()
    tc.listen_for(OWNER, wid, "panel-1")
    r = run(tc.tier("that's Quinn", OWNER, "s1", channel="voice", allow_writes=True))
    assert r.reply == tc.HEARD_REPLY
    (kind, kw), = [c for c in calls if c[0] == "tag"]
    assert kw["name"] == "Quinn" and kw["segment_id"]
    assert env.db.rows("SELECT answer FROM omi_check_items WHERE id = ?", (wid,))[0]["answer"] == "set"


def test_a_name_the_owner_forgot_cannot_be_given_and_an_answered_item_is_not_renamed(who, monkeypatch):
    env, rid, wid, calls = who
    import memory_forgotten as mf

    async def spans(uid, text, **kw):
        i = text.find("Zebulon")
        return [(i, i + 7)] if i >= 0 else []
    monkeypatch.setattr(mf, "spans", spans)
    with pytest.raises(tc.CheckError) as e:
        run(tc.answer(OWNER, wid, word="Zebulon"))
    assert e.value.code == "forgotten" and env.db.rows("SELECT status FROM omi_check_items WHERE id = ?", (wid,))[0]["status"] == "open"
    run(tc.answer(OWNER, wid, alt=0))
    with pytest.raises(tc.CheckError):
        run(tc.answer(OWNER, wid, word="Dana"))                                              # decided with a person: not re-opened by a word


def test_two_unnamed_voices_that_are_the_same_person_can_be_merged(who):
    env, rid, wid, calls = who
    now = "2026-10-10T00:00:00Z"
    cols = [r[1] for r in env.db.rows("PRAGMA table_info(omi_voice_clusters)")]
    vals = {"id": "c1", "user_id": OWNER, "model_id": "m", "seq": 1, "centroid": b"\x00", "n_segments": 1, "status": "open", "first_heard_at": now, "last_heard_at": now, "created_at": now, "expires_at": "2026-12-01T00:00:00Z"}
    for cid in ("c1", "c2"):
        use = {k: v for k, v in {**vals, "id": cid, "seq": 1 if cid == "c1" else 2}.items() if k in cols}
        env.db.conn.execute(f"INSERT INTO omi_voice_clusters ({', '.join(use)}) VALUES ({', '.join('?' * len(use))})", tuple(use.values()))
    env.db.conn.execute("UPDATE omi_segments SET voice_cluster_id = 'c2'")
    env.db.conn.commit()
    alts = json.dumps([{"word": "Voice 1", "person_id": "", "cluster_id": "c1", "votes": 70, "models": []}])
    env.db.conn.execute("UPDATE omi_check_items SET alts = ? WHERE id = ?", (alts, wid))
    env.db.conn.commit()
    it = [i for i in run(tc.list_open(OWNER))["items"] if i["kind"] == "who"][0]
    assert it["alts"][0]["cluster"] is True
    run(tc.answer(OWNER, wid, alt=0))
    assert {r["voice_cluster_id"] for r in env.db.rows("SELECT voice_cluster_id FROM omi_segments")} == {"c1"}
    assert [r["id"] for r in env.db.rows("SELECT id FROM omi_voice_clusters")] == ["c1"] and ("rebuild", "c1") in calls


def test_a_who_item_whose_turn_has_gone_corrects_nothing(who):
    env, rid, wid, calls = who
    env.db.conn.execute("DELETE FROM omi_segments")
    env.db.conn.commit()
    with pytest.raises(tc.CheckError) as e:
        run(tc.answer(OWNER, wid, new=True))
    assert e.value.code == "text_moved" and not calls


def test_heard_name_takes_only_a_name():
    assert tc.heard_name("that's quinn") == "Quinn" and tc.heard_name("it's Quinn Hart.") == "Quinn Hart"
    assert tc.heard_name("this is a really long sentence about nothing") != "" and len(tc.heard_name("this is a really long sentence").split()) <= 2
    assert tc.heard_name("123 !!") == ""
