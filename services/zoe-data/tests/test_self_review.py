"""The nightly self-review (``self_review.py``, ``scripts/maintenance/zoe-self-review.py``): Zoe reviews her own real turns.

Every rule has a negative control beside it: harness / probe / demo traffic is excluded while the same household turn is kept; each
flagging rule fires on its failure and stays quiet on the near-miss; the budget stops calls before the cap; the privacy policy keeps
non-owner words out of every file and every summary; forget erases a record naming the entity and keeps one that does not; the flag
off is a no-op. Invented household only (the repo is public). In-process fakes: no database, no network, no model.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

import capability_gap as cg
import self_review as sr

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[3]
OWNER, ADULT, CHILD = "member_owner", "member_adult", "member_kid"
ROLES = {OWNER: "owner", ADULT: "adult", CHILD: "child"}
T0 = 1_800_000_000.0


def run(coro):
    return asyncio.run(coro)


async def lookup(uid: str) -> str:
    return ROLES.get(uid, "child")


def row(i, session, role, content, ts, owner=OWNER, **meta):
    meta.setdefault("traffic_class", "household")
    if owner:
        meta.setdefault("user_id", owner)
    return {"id": f"m{i}", "session_id": session, "role": role, "content": content, "metadata": json.dumps(meta), "ts": ts, "owner": owner}


def exchange(i, session, ask, reply, ts, owner=OWNER, **meta):
    return [row(2 * i, session, "user", ask, ts, owner, **meta), row(2 * i + 1, session, "assistant", reply, ts + 2, owner, **meta)]


class FakeLlm:
    def __init__(self, reply='{"cause": "the ask was not understood", "fix": "router_training", "note": "add the phrasing"}'):
        self.calls, self.reply = [], reply

    async def __call__(self, messages, max_tokens):
        self.calls.append((messages, max_tokens))
        return self.reply(messages) if callable(self.reply) else self.reply


def inputs(rows, **kw):
    base = dict(rows=rows, log_lines=[], stt_lines=[], struggles=[], flue=[], since=T0 - 10, until=T0 + 3600)
    base.update(kw)
    return sr.NightInputs(**base)


def night(inp, *, llm=None, budget=None, run_mode="shadow", dry_run=False):
    return run(sr.run_night(inp, llm=llm, budget=budget or sr.Budget(), night="2026-11-04", run_mode=run_mode, dry_run=dry_run, lookup=lookup))


# ── assembly ─────────────────────────────────────────────────────────────────

def test_pairs_each_user_turn_with_its_reply_and_merges_the_voice_double_save():
    rows = [row(1, "voice-panel-kitchen-0a1b2c3d", "user", "turn the lights off", T0),
            row(2, "voice-panel-kitchen-0a1b2c3d", "user", "turn the lights off", T0 + 1),         # the voice lane's second save
            row(3, "voice-panel-kitchen-0a1b2c3d", "assistant", "Done, the lights are off.", T0 + 3),
            row(4, "chat-x", "user", "what's the weather", T0 + 10)]
    turns = sr.pair_rows(rows)
    assert [(t.id, t.reply, t.channel) for t in turns] == [("m1", "Done, the lights are off.", "voice"), ("m4", "", "chat")]
    assert turns[0].panel_id == "kitchen"


@pytest.mark.parametrize("why,kw", [
    ("harness", {"traffic_class": "harness"}),
    ("probe", {"traffic_class": "probe"}),
    ("harness", {"owner": "demo_bar_00000001"}),
    ("probe", {"session": "voice-panel-replay-harness-0a1b2c3d"}),
])
def test_harness_probe_and_demo_traffic_is_excluded_and_the_same_household_turn_is_kept(why, kw):
    session = kw.pop("session", "chat-1")
    owner = kw.pop("owner", OWNER)
    bad = exchange(0, session, "add milk to the list", "", T0, owner=owner, **kw)
    good = exchange(1, "chat-2", "add milk to the list", "", T0 + 100)                         # negative control: the household
    s = night(inputs(bad + good))
    assert s["turns_seen"] == 2 and s["turns_household"] == 1 and s["excluded"] == {why: 1}


def test_a_demo_id_readmitted_as_a_person_is_still_not_reviewed():
    assert sr.exclusion(sr.Turn("m", "chat", "demo_house_1", T0, "hi")) in ("harness", "demo")
    assert sr.exclusion(sr.Turn("m", "chat", OWNER, T0, "hi")) == ""


def test_the_log_receipts_join_by_session_member_and_words():
    turns = sr.pair_rows(exchange(0, "voice-panel-kitchen-0a1b2c3d", "set a timer", "Done.", T0))
    lines = [f"{time.strftime('%Y-%m-%dT%H:%M:%S+0000', time.gmtime(T0 + 3))} INFO     routers.voice_tts: VOICE TIMING turn=x "
             "session=voice-panel-kitchen-0a1b2c3d path=stream pre_brain_ms=500 memory_packet_ms=0 brain_ttft_ms=1200 packet=skipped",
             f"{time.strftime('%Y-%m-%dT%H:%M:%S+0000', time.gmtime(T0 + 4))} WARNING  write_claim_guard: WRITE_CLAIM_BLOCKED action=x lane=y "
             f"user={OWNER} lang=en kinds=timer reason=z traffic=household",
             f"{time.strftime('%Y-%m-%dT%H:%M:%S+0000', time.gmtime(T0 + 4))} WARNING  write_claim_guard: WRITE_CLAIM_BLOCKED action=x lane=y "
             f"user={OWNER} lang=en kinds=timer reason=z traffic=probe"]
    logs = sr.parse_log(lines, T0 - 10, T0 + 100)
    assert len(logs["claims"]) == 1                                   # the probe line never counts
    sr.attach_receipts(turns, logs, [], [])
    assert turns[0].latency_ms == 1700 and turns[0].path == "stream" and turns[0].claim_blocked


def test_a_lost_reply_is_read_back_from_the_flue_store_read_only(tmp_path):
    db = tmp_path / "zoe-brain.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE flue_conversation_stream_batches (path TEXT, seq INTEGER, data TEXT);"
                      "CREATE TABLE flue_conversation_stream_batch_chunks (path TEXT, seq INTEGER, chunk_index INTEGER, chunk_count INTEGER, data TEXT);")
    iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(T0 + 1))
    recs = [{"type": "user_message", "submissionId": "s1", "timestamp": iso, "content": "[context]\nwhat time is it"},
            {"type": "assistant_text_delta", "submissionId": "s1", "timestamp": iso, "sequence": 1, "delta": "It's"},
            {"type": "assistant_text_delta", "submissionId": "s1", "timestamp": iso, "sequence": 2, "delta": " noon."}]
    con.execute("INSERT INTO flue_conversation_stream_batches VALUES ('agents/zoe/a', 1, ?)", (json.dumps(recs),))
    con.commit()
    con.close()
    flue = sr.flue_replies(db, T0 - 10, T0 + 100)
    turns = sr.pair_rows([row(1, "chat-1", "user", "what time is it", T0)])
    assert sr.fill_from_flue(turns, flue) == 1 and turns[0].reply == "It's noon." and turns[0].reply_source == "flue"
    assert sr.flue_replies(tmp_path / "missing.db", 0, 1) == []


# ── the flagging rules (each with its near-miss) ─────────────────────────────

def T(text, reply="Sure.", ts=T0, **kw):
    return sr.Turn(id=kw.pop("id", f"t{ts}"), session_id=kw.pop("session_id", "chat-1"), user_id=OWNER, ts=ts, text=text, reply=reply, **kw)


def test_empty_reply_and_truncated_reply_are_flagged_and_a_real_reply_is_not():
    assert sr.EMPTY_REPLY in sr.flag_turn(T("what's on today", ""), None)
    assert sr.EMPTY_REPLY in sr.flag_turn(T("what's on today", "You have", truncated=True), None)
    assert sr.EMPTY_REPLY not in sr.flag_turn(T("what's on today", "Nothing on today."), None)


def test_a_rephrase_within_60_seconds_is_flagged_and_one_after_is_not():
    a = T("play some jazz in the kitchen", "Here's the weather.")
    assert sr.REPHRASE_RETRY in sr.flag_turn(a, T("play jazz music in the kitchen", ts=T0 + 30))
    assert sr.REPHRASE_RETRY not in sr.flag_turn(a, T("play jazz music in the kitchen", ts=T0 + 90))
    assert sr.REPHRASE_RETRY not in sr.flag_turn(a, T("thanks", ts=T0 + 10))


def test_an_ack_the_next_turn_contradicts_is_flagged_and_a_thank_you_is_not():
    a = T("turn the porch light on", "Done, I've turned the porch light on.")
    assert sr.ACK_CONTRADICTED in sr.flag_turn(a, T("it didn't, it's still off", ts=T0 + 20))
    assert sr.ACK_CONTRADICTED not in sr.flag_turn(a, T("thanks", ts=T0 + 20))


def test_a_claimed_write_with_no_tool_comes_from_the_claim_guard_receipt():
    assert sr.CLAIM_NO_TOOL in sr.flag_turn(T("add eggs", "Added.", claim_blocked=True), None)
    assert sr.CLAIM_NO_TOOL not in sr.flag_turn(T("add eggs", "Added."), None)


def test_an_emotional_misread_of_a_short_remark_is_flagged_and_real_feelings_are_not():
    comfort = "I'm sorry to hear that. I'm here for you."
    assert sr.EMOTION_MISREAD in sr.flag_turn(T("it's raining", comfort), None)
    assert sr.EMOTION_MISREAD not in sr.flag_turn(T("I feel so tired", comfort), None)
    assert sr.EMOTION_MISREAD not in sr.flag_turn(T("it's raining", "Yes, rain until noon."), None)


def test_garbled_stt_misroute_and_cant_do_come_from_receipts_and_text():
    assert sr.GARBLED_STT in sr.flag_turn(T("the the the lights", channel="voice"), None)
    assert sr.GARBLED_STT in sr.flag_turn(T("on", channel="voice", audio_s=4.0), None)
    assert sr.GARBLED_STT not in sr.flag_turn(T("turn on the lights", channel="voice", audio_s=1.5), None)
    assert sr.GARBLED_STT not in sr.flag_turn(T("the the the lights", channel="chat"), None)           # typed words are not heard
    t = T("dim the hall", id="x1", struggles=[{"signal": "FALLTHROUGH", "fix_type": "router_training", "turn_id": "x1"},
                                              {"signal": "CANT_DO", "fix_type": "capability_gap", "turn_id": "x1"}])
    assert {sr.MISROUTE, sr.CANT_DO} <= set(sr.flag_turn(t, None))
    assert not {sr.MISROUTE, sr.CANT_DO} & set(sr.flag_turn(T("dim the hall", id="x2"), None))


# ── the budget ───────────────────────────────────────────────────────────────

def test_budget_cutoff_no_call_is_made_past_the_deadline_and_the_flags_stand():
    llm = FakeLlm()
    s = night(inputs(exchange(0, "chat-1", "what's on", "", T0)), llm=llm, budget=sr.Budget(deadline=time.monotonic() - 1))
    assert llm.calls == [] and s["no_time"] == 1 and s["flagged"] == 1 and s["classes"] == {"empty_reply": 1}


def test_the_token_cap_is_charged_before_each_call():
    llm = FakeLlm()
    rows = exchange(0, "chat-1", "what's on", "", T0) + exchange(1, "chat-1", "what's next", "", T0 + 500)
    s = night(inputs(rows), llm=llm, budget=sr.Budget(tokens_left=sr.REVIEW_TOKENS))
    assert len(llm.calls) == 1 and s["reviewed"] == 1 and s["no_time"] == 1 and s["tokens_charged"] == sr.REVIEW_TOKENS


# ── privacy ──────────────────────────────────────────────────────────────────

def test_only_the_owners_turns_are_quoted_another_adult_is_counted_and_never_sent_to_the_model(tmp_path):
    llm = FakeLlm()
    rows = (exchange(0, "chat-1", "remind me about the plumber", "", T0)
            + exchange(1, "chat-2", "message the school about friday", "", T0 + 100, owner=ADULT))
    s = night(inputs(rows), llm=llm)
    recs = sr._read_records(sr.records_path("2026-11-04"))
    by = {r["user_id"]: r for r in recs}
    assert by[OWNER]["text"] == "remind me about the plumber" and by[OWNER]["review"]["fix"] == "router_training"
    assert "text" not in by[ADULT] and "reply" not in by[ADULT] and by[ADULT]["review"] is None
    assert len(llm.calls) == 1 and "plumber" in llm.calls[0][0][1]["content"] and "school" not in json.dumps(llm.calls)
    report = sr.report_path("2026-11-04").read_text()
    assert "plumber" in report and "school" not in report
    assert oct(sr.report_path("2026-11-04").stat().st_mode & 0o777) == "0o600"
    assert oct(sr.records_path("2026-11-04").stat().st_mode & 0o777) == "0o600"
    assert s["flagged"] == 2


def test_a_childs_turn_gets_the_kid_rubric_is_never_quoted_and_an_echo_is_withheld():
    ask = "why is the sky blue at night time"
    llm = FakeLlm(lambda m: json.dumps({"cause": f"it misread {ask}", "fix": "answer_quality", "note": "say why the sky is blue"}))
    s = night(inputs(exchange(0, "chat-k", ask, "", T0, owner=CHILD)), llm=llm)
    assert "CHILD" in llm.calls[0][0][0]["content"] and "NEVER quote" in llm.calls[0][0][0]["content"]
    rec = sr._read_records(sr.records_path("2026-11-04"))[0]
    assert rec["policy"] == "kid" and "text" not in rec and "withheld" in rec["review"]["cause"]
    for path in (sr.records_path("2026-11-04"), sr.summary_path("2026-11-04"), sr.report_path("2026-11-04")):
        assert "sky" not in path.read_text(), path.name
    assert "sky" not in json.dumps(s)


def test_a_guest_and_an_unverified_voice_are_counted_only():
    llm = FakeLlm()
    rows = (exchange(0, "chat-g", "what's the time", "", T0, owner="guest")
            + exchange(1, "voice-panel-kitchen-0a1b2c3d", "what's the time", "", T0 + 100, speaker_verified=False))
    s = night(inputs(rows), llm=llm)
    assert llm.calls == [] and s["flagged"] == 2
    assert all("text" not in r for r in sr._read_records(sr.records_path("2026-11-04")))


def test_the_summary_is_counts_ids_and_hashes_only():
    s = night(inputs(exchange(0, "chat-1", "book the plumber for tuesday", "", T0)), llm=FakeLlm(), run_mode="on")
    blob = sr.summary_path("2026-11-04").read_text()
    assert "plumber" not in blob and "tuesday" not in blob
    ev = json.loads(blob)["events"][0]
    assert ev["utt"] == cg.utterance_hash("book the plumber for tuesday") and ev["cls"] == "empty_reply" and s["examples"] == {"empty_reply": ["m0"]}


def test_a_turn_naming_a_forgotten_entity_is_never_reviewed(monkeypatch):
    import memory_forgotten as mf

    async def keep(uid, items, *, text_of=str, near=True, strict=False):
        items = list(items)
        kept = [i for i in items if "Dana" not in text_of(i)]
        return kept, len(items) - len(kept)
    monkeypatch.setattr(mf, "keep_unforgotten", keep)
    llm = FakeLlm()
    s = night(inputs(exchange(0, "chat-1", "did Dana call", "", T0) + exchange(1, "chat-1", "did Leo call", "", T0 + 500)), llm=llm)
    assert s["excluded"] == {"forgotten": 1} and "Dana" not in json.dumps(llm.calls) and "Leo" in json.dumps(llm.calls)


# ── forget / delete ──────────────────────────────────────────────────────────

def _seed_two():
    rows = exchange(0, "chat-1", "did Dana call about dinner", "", T0) + exchange(1, "chat-1", "did Leo fix the gate", "", T0 + 500)
    night(inputs(rows), llm=FakeLlm(), run_mode="on")


def test_forget_erases_the_record_naming_the_entity_and_keeps_the_other():
    import memory_forgotten as mf

    _seed_two()
    assert run(sr.erase_entity(OWNER, mf.name_pattern("Dana"), strict=True)) == 1
    recs = sr._read_records(sr.records_path("2026-11-04"))
    assert [r["text"] for r in recs] == ["did Leo fix the gate"]
    assert "Dana" not in sr.report_path("2026-11-04").read_text() and "Leo" in sr.report_path("2026-11-04").read_text()
    summary = json.loads(sr.summary_path("2026-11-04").read_text())
    assert "m0" not in json.dumps(summary["examples"]) and all(e["turn_id"] != "m0" for e in summary["events"])
    assert run(sr.erase_entity("someone_else", mf.name_pattern("Leo"), strict=True)) == 0      # another member's forget


def test_delete_user_removes_every_record_and_event_of_the_member():
    _seed_two()
    assert run(sr.delete_user(OWNER)) == 2
    assert sr._read_records(sr.records_path("2026-11-04")) == []
    assert json.loads(sr.summary_path("2026-11-04").read_text())["events"] == []


def test_the_struggle_detector_member_erase_reaches_the_self_review(monkeypatch):
    import struggle_detector as sd

    _seed_two()
    sd.set_backend(sd.MemoryBackend())
    try:
        run(sd.delete_user(OWNER))
    finally:
        sd.set_backend(None)
    assert sr._read_records(sr.records_path("2026-11-04")) == []


def test_the_cascade_has_a_self_review_step():
    import memory_forget_cascade as c

    assert "self_review" in c.ALL_LABELS and "self_review" not in c.DB_STEPS


# ── mode on: the struggle store, the self-improver candidates, the morning line ──

def test_mode_on_writes_wordless_struggle_rows_once(monkeypatch):
    import struggle_detector as sd

    be = sd.MemoryBackend()
    sd.set_backend(be)
    try:
        rows = exchange(0, "chat-1", "add bread", "", T0)
        night(inputs(rows), llm=FakeLlm(), run_mode="on")
        night(inputs(rows), llm=FakeLlm(), run_mode="on")                        # a re-run of the night adds nothing twice
    finally:
        sd.set_backend(None)
    assert len(be.data) == 1 and be.data[0]["signal"] == "SELF_REVIEW" and be.data[0]["summary"] == "" and be.data[0]["pattern"] == "empty_reply"
    import struggle_proposals as sp
    assert sp.kind_of(be.data[0]) is None                                      # never a proposal by itself


def test_shadow_writes_the_private_review_and_nothing_downstream():
    import struggle_detector as sd

    be = sd.MemoryBackend()
    sd.set_backend(be)
    try:
        s = night(inputs(exchange(0, "chat-1", "it's cold", "I'm sorry to hear that. I'm here for you.", T0)), llm=FakeLlm(), run_mode="shadow")
    finally:
        sd.set_backend(None)
    assert be.data == [] and s["candidate_cells"] == 0 and not (sr.improver_dir() / sr.CANDIDATES_FILE).exists()
    assert sr.records_path("2026-11-04").exists()


def test_a_manner_failure_becomes_a_neutral_synthetic_candidate_cell():
    def reply(messages):
        if "invented" in messages[0]["content"]:
            return "It's windy out there."
        return json.dumps({"cause": "comfort for a plain remark", "fix": "manner", "note": "answer plainly"})
    s = night(inputs(exchange(0, "chat-1", "it's cold today", "I'm sorry to hear that. I'm here for you.", T0)), llm=FakeLlm(reply), run_mode="on")
    lines = (sr.improver_dir() / sr.CANDIDATES_FILE).read_text().splitlines()
    assert s["candidate_cells"] == 1 and len(lines) == 1
    cell = json.loads(lines[0])
    assert cell["ask"] == "It's windy out there." and cell["status"] == "candidate" and "cold" not in lines[0] and "turn" not in cell


@pytest.mark.parametrize("cand,ok", [
    ("It's windy out there.", True),
    ("it's cold today again", False),            # three of the household's words
    ("Tell Dana it's windy", False),             # a name-like word
    ("call me on 0412345678", False),            # a number
    ("ok", False),                               # too short
])
def test_the_paraphrase_guard(cand, ok):
    assert sr.paraphrase_ok("it's cold today", cand) is ok


def test_the_paraphrase_guard_refuses_a_deny_term():
    assert not sr.paraphrase_ok("it's cold", "the harbour is windy", ["harbour"])


def test_the_morning_line_is_counts_and_classes_only_and_only_in_mode_on():
    s = {"mode": "on", "turns_household": 12, "flagged": 3, "classes": {"rephrase_retry": 2, "empty_reply": 1}}
    line = sr.morning_line(s)
    assert line.startswith("Last night I looked back over 12") and "2 had to be asked again" in line and "1 gave no reply" in line
    assert sr.morning_line({**s, "mode": "shadow"}) is None
    assert "nothing that went wrong" in sr.morning_line({**s, "flagged": 0, "classes": {}})


def test_brief_line_is_off_while_the_flag_is_off(monkeypatch):
    monkeypatch.delenv("ZOE_SELF_REVIEW", raising=False)
    assert run(sr.brief_line(OWNER)) is None


# ── capability_gap reads the summaries ───────────────────────────────────────

def test_capability_gap_reads_mode_on_summaries_only():
    rec = {"mode": "on", "events": [{"ts": T0, "cls": "rephrase_retry", "member": OWNER, "utt": "0123456789ab", "area": "music"},
                                    {"ts": T0, "cls": "garbled_stt", "member": OWNER, "utt": "0123456789ab", "area": "music"}]}
    evs = cg.parse_self_review_summary(rec)
    assert [(e.source, e.area, e.utt) for e in evs] == [(cg.SRC_SELF_REVIEW, "music", "0123456789ab")]
    assert cg.parse_self_review_summary({**rec, "mode": "shadow"}) == []
    ranked = cg.cluster_events(evs, now=T0 + 60, days=14)
    assert ranked and ranked[0].area == "music"


# ── the CLI: flag off is a no-op, the 4B is refused ──────────────────────────

def _cli():
    spec = importlib.util.spec_from_file_location("zoe_self_review_cli", REPO / "scripts" / "maintenance" / "zoe-self-review.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_flag_off_is_a_no_op(monkeypatch):
    monkeypatch.delenv("ZOE_SELF_REVIEW", raising=False)
    code, s = run(_cli().amain(["--model-url", "http://127.0.0.1:11500/v1"]))
    assert (code, s["status"]) == (0, "flag_off") and not sr.state_dir().exists()


def test_the_cli_refuses_the_daytime_4b_and_a_remote_model():
    cli = _cli()
    with pytest.raises(SystemExit, match="11434"):
        cli.check_model_url("http://127.0.0.1:11434/v1")
    with pytest.raises(SystemExit, match="non-loopback"):
        cli.check_model_url("http://10.0.0.5:11500/v1")
    assert cli.check_model_url("http://127.0.0.1:11500/v1")


def test_dry_run_writes_nothing():
    s = night(inputs(exchange(0, "chat-1", "add bread", "", T0)), llm=FakeLlm(), dry_run=True)
    assert s["flagged"] == 1 and s["written"] == [] and not sr.state_dir().exists()


# ── the night window runs it on the 12B, bounded ─────────────────────────────

def test_the_night_window_runs_the_review_on_the_12b_with_a_budget():
    sys.path.insert(0, str(REPO / "scripts" / "night"))
    import night_window as nw

    cfg = nw.NightCfg()
    assert "self_review" in cfg.jobs and cfg.jobs.index("self_review") < cfg.jobs.index("omi_day_review")
    job = next(j for j in nw.build_jobs(cfg) if j.name == "self_review")
    assert job.best_effort and job.budget_flag == "--budget-s" and job.timeout_s <= 600 and not job.fallback_4b
    assert f"127.0.0.1:{cfg.port}/v1" in " ".join(job.argv) and ":11434" not in " ".join(job.argv)
    assert nw.job_idle_evidence("self_review", "NIGHT_JOB self_review status=flag_off household=0 flagged=0 reviewed=0 no_time=0 calls=0")
    assert nw.job_idle_evidence("self_review", "NIGHT_JOB self_review status=ok household=9 flagged=0 reviewed=0 no_time=0 calls=0")
    assert not nw.job_idle_evidence("self_review", "NIGHT_JOB self_review status=ok household=9 flagged=3 reviewed=3 no_time=0 calls=3")
