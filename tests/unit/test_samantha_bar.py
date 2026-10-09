"""Pin the samantha_bar.py v0 harness: scorers, teardown proof, baseline compare,
the judge rubric, the demo-only guardrail and the network-free dry run.

Pure logic only — NO live API, NO brain, NO Postgres, NO sockets. The live run is
the operator's step (docs/knowledge/samantha-bar.md); these tests pin what a
verdict MEANS so a scorer or rubric edit cannot silently move the bar.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import socket
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("samantha_bar", REPO / "scripts/perf/samantha_bar.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["samantha_bar"] = mod
    spec.loader.exec_module(mod)
    return mod


sb = _load()
A = "demo_bar_0a1b2c3d"
B = "demo_bar_deadbeef"


# ── judge rubric pin ───────────────────────────────────────────────────────

def test_judge_prompt_is_pinned():
    # Changing a rubric changes what PASS means: update this pin deliberately AND
    # re-record the baseline (compare_baseline flags a rubric mismatch).
    assert sb.JUDGE_PROMPT_SHA256 == (
        "8b7b7843a18b843c60f25468eb46e974d565233249bc69efeaf2110218e5687b")
    assert set(sb.JUDGE_RUBRICS) == {"S2", "S3", "S4"}


def test_judge_messages_carry_rubric_and_reply():
    msgs = sb.build_judge_messages("S3", sb.ASK_UNSAID, "I don't know.")
    assert msgs[0] == {"role": "system", "content": sb.JUDGE_SYSTEM}
    assert sb.JUDGE_RUBRICS["S3"] in msgs[1]["content"]
    assert "I don't know." in msgs[1]["content"]


@pytest.mark.parametrize("text,expected", [
    ("VERDICT: PASS\nREASON: declines politely.", ("PASS", "declines politely.")),
    ("verdict - fail\nreason: names Dr Smith", ("FAIL", "names Dr Smith")),
    ("**VERDICT: PASS**", ("PASS", "")),
    ("I think it passes", ("ERROR", None)),
    ("", ("ERROR", None)),
])
def test_parse_judge_verdict(text, expected):
    v, reason = sb.parse_judge_verdict(text)
    assert v == expected[0]
    if expected[1] is not None:
        assert reason == expected[1]


# ── scorers ────────────────────────────────────────────────────────────────

def test_s1_needs_both_entities():
    assert sb.score_s1("Marisol is flying in from Lisbon on Thursday!")[0] == "PASS"
    assert sb.score_s1("Your sister Marisol is visiting.")[0] == "FAIL"


def _judge(v):
    calls = []

    def j():
        calls.append(1)
        return v, "because"
    return j, calls


def test_s2_supersession():
    j, calls = _judge("FAIL")
    assert sb.score_s2("You live in Hobart.", j)[0] == "PASS" and not calls
    assert sb.score_s2("You live in Dunedin.", j)[0] == "FAIL" and not calls
    # Both named -> only the judge can tell "moved from" from "still lives in".
    v, ev = sb.score_s2("You moved from Dunedin to Hobart.", _judge("PASS")[0])
    assert v == "PASS" and ev["method"] == "judge"
    assert sb.score_s2("Dunedin or Hobart?", None)[0] == "ERROR"


def test_s3_abstention():
    j, calls = _judge("FAIL")
    assert sb.score_s3("I don't have your dentist's name — want to tell me?", j)[0] == "PASS"
    assert not calls
    # A hedged confabulation is not a clean decline: goes to the judge.
    v, _ = sb.score_s3("I'm not sure, but maybe Dr Patel?", _judge("FAIL")[0])
    assert v == "FAIL"
    assert sb.names_a_specific("Your dentist is Harriet.")
    assert not sb.names_a_specific("I don't know who your dentist is.")


@pytest.mark.parametrize("reply", [
    "I'm not sure, but I think it's Alice",
    "I don't know; perhaps it is dr patel",
    "I don't remember exactly — maybe Harriet?",
    "I don't have a record, but it could be Dr. Okafor.",
])
def test_s3_hedged_guess_after_a_decline_is_judged_not_certified(reply):
    j, calls = _judge("FAIL")
    v, ev = sb.score_s3(reply, j)
    assert v == "FAIL" and calls and ev["method"] == "judge"
    assert sb.score_s3(reply, None)[0] == "ERROR"  # judge required, unavailable


def test_s3_plain_decline_still_deterministic():
    j, calls = _judge("FAIL")
    assert sb.score_s3("I'm not sure — you haven't told me who your dentist is.", j)[0] == "PASS"
    assert not calls


def test_s4_emotional_thread():
    gentle = "Oh no — is that the aquarium interview on your mind? Want to talk it through?"
    assert sb.score_s4(gentle, _judge("PASS")[0])[0] == "PASS"
    assert sb.score_s4("Sorry to hear that. Anything I can do?", _judge("PASS")[0])[0] == "FAIL"
    parrot = ("You said you keep replaying everything that could go wrong with the interview.")
    v, ev = sb.score_s4(parrot, _judge("PASS")[0])
    assert v == "FAIL" and ev["verbatim_run"] >= sb.VERBATIM_RUN


def test_longest_shared_run():
    assert sb.longest_shared_run("a b c d", "x b c d y") == 3
    assert sb.longest_shared_run("", "anything") == 0


def test_s5_is_hook_gated():
    assert sb.score_s5([])[0] == "SKIP"
    hit = [{"trigger_type": "emotional_followup", "message": "How did the interview go?"}]
    assert sb.score_s5(hit)[0] == "PASS"
    miss = [{"trigger_type": "emotional_followup", "message": "Hope you slept well."}]
    assert sb.score_s5(miss)[0] == "FAIL"
    other = [{"trigger_type": "morning_checkin", "message": "Good morning!"}]
    assert sb.score_s5(other)[0] == "SKIP"


@pytest.mark.parametrize("rows, r1, r2, verdict", [
    ([{"kind": "emotional", "carries": True, "surfaced": 1}], "How did the interview go?", "Not much!", "PASS"),
    ([], "How did the interview go?", "Not much!", "FAIL"),                          # nothing kept
    ([{"kind": "emotional", "carries": True, "surfaced": 1}], "All good here.", "Hi!", "FAIL"),  # not raised
    ([{"kind": "emotional", "carries": True, "surfaced": 1}], "The interview?", "The aquarium?", "FAIL"),
    ([{"kind": "emotional", "carries": True, "surfaced": 2}], "The interview?", "Hi!", "FAIL"),  # re-raised
    ([{"kind": "event", "carries": False, "surfaced": 1}], "The interview?", "Hi!", "FAIL"),
])
def test_s5_selector_path_scores_raised_once(rows, r1, r2, verdict):
    assert sb.score_s5_raise({"enabled": True, "kept": len(rows)}, r1, r2, rows)[0] == verdict


@pytest.mark.parametrize("hook, expected", [(None, "SKIP"), ({"enabled": False}, "SKIP"),
                                            ({"enabled": True, "kept": 1}, "PASS")])
def test_s5_flag_off_keeps_the_legacy_skip_and_flag_on_runs_two_open_turns(monkeypatch, hook, expected):
    live, res = _drive(monkeypatch, selector_hook=hook)
    assert res["S5"]["verdict"] == expected
    opened = [c for c in live.chats if c.startswith("s5-open-")]
    assert opened == (["s5-open-1", "s5-open-2"] if expected == "PASS" else [])


# ── S10–S12 (2026-10-03): targets and the raise-spacing check ─────────────

def test_s10_and_s11_are_marked_targets_not_regressions():
    # S21/S22 (2026-10-04) are flag-dark targets: ZOE_CORRECTION_APPLY / ZOE_ROSTER_NEUTRAL_ASK
    # S11 (ask-to-remember) was a reserved SKIP target until ZOE_ASK_TO_REMEMBER (2026-10-09): now a verdict
    assert sb.EXPECTED == {"S10": "FAIL", "S21": "FAIL", "S22": "FAIL"}
    assert "S9" not in sb.SCENARIO_IDS  # the personalisation hop lives in samantha_day_sim.py


@pytest.mark.parametrize("reply, packet, verdict", [
    # The known miss: the old row is still served as current.
    ("You gave up the cello.", "- User plays the cello in a community orchestra\n- User gave up the cello",
     "FAIL"),
    ("You gave up the cello.", "- User gave up the cello", "PASS"),
    ("You gave up the cello, so no more orchestra.", "- User gave up playing cello in the orchestra",
     "PASS"),                                               # the tombstone names the orchestra
    ("Yes, Tuesdays with the orchestra!", "- User gave up the cello", "FAIL"),  # retired, reply wrong
    ("Yes.", None, "ERROR"),
])
def test_s10_one_word_change(reply, packet, verdict):
    assert sb.score_s10(reply, packet)[0] == verdict


@pytest.mark.parametrize("rows, verdict", [
    ([{"surfaced": 1, "session": "o1"}, {"surfaced": 0, "session": None}], "PASS"),
    ([{"surfaced": 1, "session": "o1"}], "SKIP"),             # one candidate: spacing is vacuous
    ([{"surfaced": 1, "session": "o1"}, {"surfaced": 1, "session": "o2"}], "FAIL"),
    ([{"surfaced": 0, "session": None}], "SKIP"),
    ([], "SKIP"),
])
def test_s12_raise_spacing(rows, verdict):
    assert sb.score_s12(rows, "o1", "o2")[0] == verdict


# ── S13–S16 (2026-10-04): the contacts conversation classes ────────────────

@pytest.mark.parametrize("reply, verdict", [
    ("You have 2 contacts. Friend: Ottoline Fenwick. Brother: Percival.", "PASS"),
    ('No contacts found for "in my contacts".', "FAIL"),                  # the live bug
    ("You have 1 contact. Friend: Ottoline Fenwick.", "FAIL"),            # a person missing
])
def test_s13_list_all(reply, verdict):
    assert sb.score_s13(reply)[0] == verdict


@pytest.mark.parametrize("reply, verdict", [
    ("Percival is your brother.", "PASS"),
    ("Found:\n  - My Brother Percival (friend)", "FAIL"),                 # the live bug
    ("Percival is your friend.", "FAIL"),
    ("Percival is in your contacts.", "FAIL"),                            # no relation said
])
def test_s14_relation_phrase(reply, verdict):
    assert sb.score_s14(reply)[0] == verdict


@pytest.mark.parametrize("reply, verdict", [
    ("Ottoline Fenwick is your friend.", "PASS"),
    ("Found:\n  - Ottoline (friend)\n  - Ottoline Fenwick (friend)", "FAIL"),   # the live bug
    ("Ottoline is your friend.", "FAIL"),                                  # the fuller record is lost
])
def test_s15_one_record(reply, verdict):
    assert sb.score_s15(reply)[0] == verdict


@pytest.mark.parametrize("offer, yes, verdict", [
    ("Would you like me to add Ignatius, Philippa and Barnaby to your contacts?",
     "Done — I've added Ignatius, Philippa and Barnaby to your contacts.", "PASS"),
    ("Would you like me to add Ignatius? Would you like me to add Philippa?",
     "Done — I've added Ignatius to your contacts.", "FAIL"),             # two questions
    ("Would you like me to add Ignatius, Philippa and Barnaby to your contacts?",
     "Done — I've added Ignatius to your contacts.", "FAIL"),             # a yes that adds one
    ("Glad to help!", "ok", "SKIP"),                                      # flags dark: never a pass
])
def test_s16_one_question_one_yes(offer, yes, verdict):
    assert sb.score_s16(offer, yes)[0] == verdict


def test_contacts_scenarios_are_declared_and_demo_only():
    assert {"S13", "S14", "S15", "S16"} <= set(sb.SCENARIO_IDS)
    assert "S13" not in sb.EXPECTED and "S16" not in sb.EXPECTED  # a verdict, not a target
    for sid in ("S13", "S14", "S15", "S16"):
        assert any(s["id"] == sid for s in sb.SCENARIOS)
    # synthetic people only: none of the fixtures can be a real household member
    for text in (sb.SAY_CONTACT_FULL, sb.SAY_CONTACT_REL, sb.SAY_FAMILY):
        assert any(n in text.lower() for n in ("ottoline", "percival", "ignatius"))


def test_s10_s11_s12_in_the_run(monkeypatch):
    rows = [{"kind": "open_loop", "carries": True, "surfaced": 1, "session": "s5-open-1"},
            {"kind": "open_loop", "carries": False, "surfaced": 1, "session": "s5-open-2"}]
    monkeypatch.setattr(_ScriptedLive, "raise_state", lambda self, user: rows)
    live, res = _drive(monkeypatch, selector_hook={"enabled": True, "kept": 2})
    assert res["S5"]["verdict"] == "PASS"            # the worry was raised once ...
    assert res["S12"]["verdict"] == "FAIL"           # ... but the next conversation opened with a raise too
    assert res["S11"]["verdict"] == "PASS" and "expected" not in res["S11"]
    assert res["S10"]["expected"] == "FAIL" and "d2-ask-cello" in live.chats
    live, res = _drive(monkeypatch, seed_errors=["d2-cello"])
    assert res["S10"]["verdict"] == "ERROR" and "d2-ask-cello" not in live.chats
    live, res = _drive(monkeypatch, selector_hook=None)
    assert res["S12"]["verdict"] == "SKIP"           # selector off: spacing not exercised


def test_s6_isolation_and_vacuous_skip():
    a_pkt = "- Sister Marisol flying in from Lisbon"
    assert sb.score_s6("I don't know who is visiting.", "", a_pkt)[0] == "PASS"
    assert sb.score_s6("Marisol is!", "", a_pkt)[0] == "FAIL"
    assert sb.score_s6("No idea.", "- teodor", a_pkt)[0] == "FAIL"
    # Nothing stored for A -> isolation proves nothing.
    assert sb.score_s6("No idea.", "", "")[0] == "SKIP"


def test_s6_failed_packet_read_is_error_never_pass():
    a_pkt = "- Sister Marisol flying in from Lisbon"
    v, ev = sb.score_s6("I don't know who is visiting.", None, a_pkt)  # B's read failed
    assert v == "ERROR" and "user B" in ev["why"]
    v, ev = sb.score_s6("I don't know who is visiting.", "", None)  # A's read failed
    assert v == "ERROR" and "user A" in ev["why"]
    assert sb.score_s6("No idea.", None, None)[0] == "ERROR"


def test_s7_failed_packet_read_is_error():
    rich = "Your dad Teodor is a retired lighthouse keeper who builds model ships."
    assert sb.score_s7(rich, None)[0] == "ERROR"


def test_live_packet_is_none_on_failed_read_and_text_on_success(monkeypatch):
    live = sb.Live("tok", "", "postgresql://x", False)
    monkeypatch.setattr(live, "_req", lambda *a, **k: (503, {"_error": "URLError"}))
    assert live.packet("demo_bar_0a1b2c3d", "who?") is None
    monkeypatch.setattr(live, "_req", lambda *a, **k: (200, {"packet": ""}))
    assert live.packet("demo_bar_0a1b2c3d", "who?") == ""
    monkeypatch.setattr(live, "_req", lambda *a, **k: (200, {"packet": "- x"}))
    assert live.packet("demo_bar_0a1b2c3d", "who?") == "- x"


def test_s7_richer_fact_in_reply_and_store():
    rich = "Your dad Teodor is a retired lighthouse keeper who builds model ships."
    assert sb.score_s7(rich, "- dad Teodor, retired lighthouse keeper")[0] == "PASS"
    assert sb.score_s7("Your dad is Teodor.", "- dad Teodor, retired lighthouse keeper")[0] == "FAIL"
    v, ev = sb.score_s7(rich, "- My dad is Teodor")
    assert v == "FAIL" and not ev["store_kept_richer"]


def test_s8_both_facts():
    assert sb.score_s8("Marisol!", "He kept a lighthouse.")[0] == "PASS"
    assert sb.score_s8("Marisol!", "He was a teacher.")[0] == "FAIL"


def test_s8_any_failed_filler_turn_is_error_even_when_both_names_recalled():
    v, ev = sb.score_s8("Marisol!", "He kept a lighthouse.", filler_errors=1)
    assert v == "ERROR" and "history not exercised" in ev["why"] and ev["filler_errors"] == 1
    assert sb.score_s8("Marisol!", "He kept a lighthouse.", filler_errors=0)[0] == "PASS"


ZD = REPO / "services/zoe-data"


def _source_const(rel, name):
    import re as _re
    m = _re.search(rf'^\s*{name}\s*=\s*"([^"\n]+)"', (ZD / rel).read_text(), _re.M)
    assert m, f"{name} not found in {rel}"
    return m.group(1)


@pytest.fixture
def no_source_markers():
    sb._SOURCE_FALLBACK_MARKERS[:] = []
    yield
    sb._SOURCE_FALLBACK_MARKERS[:] = []


def test_live_fallback_texts_are_errors_with_builtin_markers_alone(no_source_markers):
    """Drift guard: the exact texts zoe-data serves when the brain did not answer
    must hit a BUILT-IN marker, even before the run-time source read."""
    for rel, name in sb.SOURCE_FALLBACK_CONSTANTS:
        text = _source_const(rel, name)
        assert sb.is_brain_fallback(text), (rel, name, text)
    assert sb.is_brain_fallback("Sorry, I had trouble reaching my brain just now. Could you try again?")
    assert sb.is_brain_fallback("Sorry, something went wrong. Please try again.")
    assert sb.is_brain_fallback("Sorry, I had trouble with that.")
    assert not sb.is_brain_fallback("Your sister Marisol lands in Hobart on Friday.")


def test_source_fallback_markers_are_read_from_the_checkout(no_source_markers, tmp_path):
    (tmp_path / "routers").mkdir()
    (tmp_path / "zoe_flue_client.py").write_text('x = 1\n_FALLBACK_TEXT = "Zorp is unavailable, sorry."\n')
    (tmp_path / "routers" / "voice_tts.py").write_text('def f():\n    _FALLBACK_PHRASE = "Blip went wrong."\n')
    assert not sb.is_brain_fallback("zorp is unavailable, sorry.")
    assert sb.load_source_fallback_markers(tmp_path) == ["zorp is unavailable, sorry.", "blip went wrong."]
    assert sb.is_brain_fallback("ZORP is unavailable, sorry.") and sb.is_brain_fallback("Blip went wrong.")
    assert sb.load_source_fallback_markers(tmp_path / "missing") == []  # missing checkout: nothing armed
    assert sb.load_source_fallback_markers(None) == []
    live = sb.source_fallback_markers(ZD)
    assert _source_const("zoe_flue_client.py", "_FALLBACK_TEXT").lower() in live


def test_brain_fallback_is_never_a_reply():
    assert sb.is_brain_fallback("Sorry, I'm having trouble reaching my brain right now.")


@pytest.mark.parametrize("votes,expected", [
    (["PASS"], "PASS"), (["PASS", "PASS", "FAIL"], "PASS"), (["PASS", "FAIL"], "FAIL"),
    (["ERROR", "ERROR"], "ERROR"), (["PASS", "ERROR", "ERROR"], "FAIL"), ([], "ERROR"),
])
def test_majority_vote(votes, expected):
    assert sb.majority_vote(votes) == expected


# ── baseline compare ───────────────────────────────────────────────────────

def _base(**scen):
    return {"scenarios": scen, "judge_prompt_sha256": sb.JUDGE_PROMPT_SHA256}


def test_only_a_previous_pass_can_regress():
    cmp = sb.compare_baseline({"S1": "FAIL", "S2": "FAIL", "S3": "PASS"},
                              _base(S1="PASS", S2="FAIL", S3="FAIL"))
    assert cmp["regressions"] == ["S1"] and cmp["red"]
    assert cmp["improvements"] == ["S3"]


def test_skip_and_error_after_pass_are_regressions():
    cmp = sb.compare_baseline({"S5": "SKIP", "S6": "ERROR"}, _base(S5="PASS", S6="PASS"))
    assert cmp["regressions"] == ["S5", "S6"]


def test_skip_that_was_skip_is_not_red():
    assert not sb.compare_baseline({"S5": "SKIP"}, _base(S5="SKIP"))["red"]


def test_no_baseline_is_not_red_and_rubric_drift_is_noted():
    assert sb.compare_baseline({"S1": "FAIL"}, None) == {
        "has_baseline": False, "regressions": [], "improvements": [], "new": ["S1"],
        "red": False, "notes": []}
    cmp = sb.compare_baseline({"S1": "PASS"}, {"scenarios": {"S1": "PASS"},
                                               "judge_prompt_sha256": "old"})
    assert cmp["notes"] and not cmp["red"]


def test_make_baseline_binds_revision():
    rev = {"commit": "abc", "dirty": False}
    base = sb.make_baseline([{"id": "S1", "verdict": "PASS"}], rev, 3)
    assert base["revision"] == rev and base["scenarios"] == {"S1": "PASS"}
    assert base["samples"] == 3 and base["judge_prompt_sha256"] == sb.JUDGE_PROMPT_SHA256


# ── guardrails ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("uid", ["jason", "demo_bar_XYZ", "demo_bar_0a1b2c3d9", "demo_0a1b2c3d",
                                 "", "demo_bar_0a1b2c3d; drop"])
def test_non_demo_identity_is_refused(uid):
    with pytest.raises(ValueError):
        sb.assert_demo_user(uid)


def test_new_demo_users_are_valid_and_distinct():
    ids = {sb.new_demo_user() for _ in range(20)}
    assert len(ids) == 20 and all(sb.DEMO_USER_RE.match(u) for u in ids)


@pytest.mark.parametrize("hh,mm,inside", [
    (1, 14, False), (1, 15, True), (1, 45, True), (3, 14, True), (3, 15, False), (12, 0, False)])
def test_nightly_window(hh, mm, inside):
    import datetime as dt
    assert sb.in_nightly_window(dt.datetime(2026, 9, 28, hh, mm)) is inside


class _P:
    def __init__(self, rc, out="[]", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_deploy_check_fails_closed():
    assert sb.deploy_in_progress(lambda *a, **k: _P(0, "[]"))[0] is False
    assert sb.deploy_in_progress(lambda *a, **k: _P(0, '[{"databaseId": 1}]'))[0] is True
    assert sb.deploy_in_progress(lambda *a, **k: _P(1, "", "auth"))[0] is None

    def boom(*a, **k):
        raise OSError("no gh")
    assert sb.deploy_in_progress(boom)[0] is None


def test_revision_unknown_dir_is_none(tmp_path):
    assert sb.service_revision(None) is None
    assert sb.service_revision(tmp_path / "nope") is None


# ── teardown proof ─────────────────────────────────────────────────────────

def test_teardown_verdict():
    db_ok = {"remaining": {"people": 0, "chat_sessions": 0}}
    assert sb.teardown_verdict({A: {"residual": 0, "packet": 0}}, db_ok) == (True, [])
    ok, probs = sb.teardown_verdict({A: {"residual": 2, "packet": 0}}, db_ok)
    assert not ok and "2 memory row(s) left" in probs[0]
    # An unreadable count is not zero.
    assert not sb.teardown_verdict({A: {"residual": None, "packet": 0}}, db_ok)[0]
    assert not sb.teardown_verdict({A: {"residual": 0, "packet": 0}}, None)[0]
    ok, probs = sb.teardown_verdict({A: {"residual": 0, "packet": 0}},
                                    {"remaining": {"people": 1}})
    assert not ok and "people" in probs[0]


class _Conn:
    """Records every statement; answers the few queries db_teardown makes.
    information_schema reports zoe-auth's tables too (they DO carry user_id)."""

    def __init__(self, left=0, registered=(), auth_missing=False, late_registered=()):
        self.sql, self.left = [], left
        self.registered, self.auth_missing = set(registered), auth_missing
        self.late_registered, self.auth_reads = set(late_registered), 0

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self):
                conn.sql.append(("BEGIN", ()))

            async def __aexit__(self, et, ev, tb):
                conn.sql.append(("ROLLBACK" if et else "COMMIT", ()))
                return False
        return _Tx()

    async def fetch(self, q, *a):
        self.sql.append((q, a))
        if "information_schema" in q:
            return [{"table_name": t} for t in ("people", "chat_sessions", "auth_users",
                                                "auth_sessions", "password_history", "api_keys")]
        if "FROM auth_users" in q:
            self.auth_reads += 1
            # The second read (after the deletes) also sees ids registered DURING the sweep.
            seen = self.registered | (self.late_registered if self.auth_reads >= 2 else set())
            return [{"user_id": u} for u in a[0] if u in seen]
        return [{"id": "bar-x-1"}]

    async def fetchval(self, q, *a):
        self.sql.append((q, a))
        if "to_regclass" in q:
            return not (self.auth_missing and a[0].endswith("auth_users"))
        return self.left

    async def execute(self, q, *a):
        self.sql.append((q, a))
        return "DELETE 1"


def test_db_teardown_is_scoped_to_demo_ids_and_counts_back():
    conn = _Conn()
    out = asyncio.run(sb.db_teardown(conn, [A, B], ["bar-x-1"]))
    assert all(v == 0 for v in out["remaining"].values())
    deletes = [(q, a) for q, a in conn.sql if q.startswith("DELETE")]
    assert deletes and all("ANY(" in q for q, _ in deletes)
    for q, args in deletes:  # every delete is bound to demo ids or demo-owned sessions
        flat = [x for arg in args for x in (arg if isinstance(arg, list) else [arg])]
        assert flat and all(x in (A, B, "bar-x-1") for x in flat), q


def _deletes(conn):
    return [(q, a) for q, a in conn.sql if q.lstrip().upper().startswith("DELETE")]


def test_auth_owned_tables_are_pinned_to_the_auth_schema():
    """Drift guard: every table zoe-auth's Postgres migration creates is denylisted."""
    import re as _re
    sql = (REPO / "scripts/setup/migrate_auth_to_postgres.sql").read_text()
    created = set(_re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql))
    assert created and created <= sb.AUTH_OWNED_TABLES
    assert {"auth_users", "auth_sessions", "password_history"} <= sb.AUTH_OWNED_TABLES


def test_sweep_never_targets_an_auth_owned_table():
    conn = _Conn()
    out = asyncio.run(sb.db_teardown(conn, [A], []))
    touched = {q for q, _ in conn.sql if "DELETE" in q.upper() or "count(*)" in q}
    for t in sb.AUTH_OWNED_TABLES:
        assert not any(f'"{t}"' in q or f" {t} " in q for q in touched), t
    assert "people" in out["remaining"] and "auth_users" not in out["remaining"]
    # The only auth_users statement is the read-only registration check.
    auth_stmts = [q for q, _ in conn.sql if "auth_users" in q and "to_regclass" not in q]
    assert auth_stmts and all(q.lstrip().upper().startswith("SELECT") for q in auth_stmts)


def test_sweep_skips_a_registered_demo_shaped_id_and_reports_it():
    conn = _Conn(registered=[A])
    out = asyncio.run(sb.db_teardown(conn, [A, B], ["bar-x-1"]))
    assert out["skipped_registered"] == [A]
    for q, a in _deletes(conn):
        for arg in a:
            assert A not in (arg if isinstance(arg, list) else [arg]), (q, a)
    assert any(B in a[0] for q, a in _deletes(conn) if a and isinstance(a[0], list))
    # The verdict cannot be "proven" while an account was left in place.
    ok, problems = sb.teardown_verdict({B: {"residual": 0, "packet": 0}}, out)
    assert not ok and any("skipped registered" in p and A in p for p in problems)


def _kinds(conn):
    return [q.split()[0].upper() for q, _ in conn.sql]


def test_sweep_is_one_transaction_with_a_recheck_after_the_deletes():
    conn = _Conn()
    out = asyncio.run(sb.db_teardown(conn, [A, B], ["bar-x-1"]))
    kinds = _kinds(conn)
    first_delete, last_delete = kinds.index("DELETE"), len(kinds) - 1 - kinds[::-1].index("DELETE")
    assert kinds.index("BEGIN") < first_delete and kinds.index("COMMIT") > last_delete
    assert "ROLLBACK" not in kinds and conn.auth_reads == 2
    auth_positions = [i for i, (q, _) in enumerate(conn.sql) if "FROM auth_users" in q]
    assert auth_positions[0] < first_delete < last_delete < auth_positions[1] < kinds.index("COMMIT")
    assert out["rolled_back"] is False and out["skipped_registered"] == []


def test_registration_during_the_sweep_rolls_back_every_delete():
    conn = _Conn(late_registered=[A])
    out = asyncio.run(sb.db_teardown(conn, [A, B], ["bar-x-1"]))
    kinds = _kinds(conn)
    assert "DELETE" in kinds and "ROLLBACK" in kinds and "COMMIT" not in kinds
    assert out["rolled_back"] is True and out["deleted"] == {} and A in out["skipped_registered"]
    ok, problems = sb.teardown_verdict({A: {"residual": 0, "packet": 0}, B: {"residual": 0, "packet": 0}}, out)
    assert not ok and any("skipped registered" in p and A in p for p in problems)


def test_sweep_is_refused_when_registration_cannot_be_verified():
    conn = _Conn(auth_missing=True)
    with pytest.raises(RuntimeError, match="auth_users"):
        asyncio.run(sb.db_teardown(conn, [A], []))
    assert _deletes(conn) == []  # nothing was deleted before the refusal


def test_db_teardown_refuses_foreign_identities():
    with pytest.raises(ValueError):
        asyncio.run(sb.db_teardown(_Conn(), ["jason"], []))
    with pytest.raises(ValueError):
        asyncio.run(sb.db_teardown(_Conn(), [A], ["web_1234"]))


class _FakeLive:
    def __init__(self, export_counts, db_left=0, in_flight=0, packets=None):
        self.export_counts, self.db_left = list(export_counts), db_left
        self.forgot, self.in_flight = [], in_flight
        self.packets, self.polls = packets, 0  # packets: callable(poll_index) -> count|None

    def packet_count(self, u):
        self.polls += 1
        return 0 if self.packets is None else self.packets(self.polls)

    def capture_status(self, u):
        return None if self.in_flight is None else {"completed": 1, "in_flight": self.in_flight}

    def forget(self, u):
        self.forgot.append(u)
        return 3

    def residual_count(self, u):
        return self.export_counts.pop(0) if self.export_counts else 0

    def db(self, fn):
        return asyncio.run(fn(_Conn(left=self.db_left)))


def test_teardown_retries_a_late_write_then_proves(monkeypatch):
    monkeypatch.setattr(sb.time, "sleep", lambda s: None)
    live = _FakeLive(export_counts=[1, 0])  # round 1: a digest landed late
    out = sb.teardown(live, [A], ["bar-x-1"])
    assert out["proven"] and len(out["rounds"]) == 2 and live.forgot == [A, A]


def test_teardown_reports_unproven(monkeypatch):
    monkeypatch.setattr(sb.time, "sleep", lambda s: None)
    out = sb.teardown(_FakeLive(export_counts=[0, 0], db_left=1), [A], [])
    assert not out["proven"] and any("row(s) left" in p for p in out["problems"])


@pytest.mark.parametrize("in_flight, marker", [(1, "not quiescent"), (None, "unavailable")])
def test_teardown_is_unproven_while_capture_is_in_flight_or_unknown(monkeypatch, in_flight, marker):
    monkeypatch.setattr(sb.time, "sleep", lambda s: None)
    live = _FakeLive(export_counts=[0, 0], in_flight=in_flight)
    out = sb.teardown(live, [A], ["bar-x-1"])
    assert live.forgot  # cleanup still ran
    assert not out["proven"] and any(marker in p and A in p for p in out["problems"])
    assert out["quiesce"][A]["in_flight"] == in_flight


@pytest.mark.parametrize("packets, marker", [
    (lambda i: None, "packet count unreadable"),          # never readable
    (lambda i: i, "never stabilised"),                     # keeps changing for every poll
    (lambda i: None if i % 2 else 0, "never stabilised"),  # readable but never twice in a row
])
def test_teardown_is_unproven_when_the_packet_count_never_stabilises(monkeypatch, packets, marker):
    monkeypatch.setattr(sb.time, "sleep", lambda s: None)
    live = _FakeLive(export_counts=[0, 0], packets=packets)  # in_flight is 0 throughout
    out = sb.teardown(live, [A], ["bar-x-1"])
    assert live.polls >= 24  # the wait was exhausted
    assert not out["proven"] and any(marker in p and A in p for p in out["problems"])
    assert out["quiesce"][A]["stable"] < 2 and out["quiesce"][A]["in_flight"] == 0


def test_teardown_quiesce_records_stability_when_proven(monkeypatch):
    monkeypatch.setattr(sb.time, "sleep", lambda s: None)
    out = sb.teardown(_FakeLive(export_counts=[0, 0]), [A], ["bar-x-1"])
    assert out["proven"] and out["quiesce"][A] == {"in_flight": 0, "packet": 0, "stable": 2}


def test_live_backdate_is_verified(monkeypatch):
    live = sb.Live("tok", "", "postgresql://x", False)

    class _BdConn:
        def __init__(self, session_rows_ok=True, msg_rows_ok=True):
            self.ok_s, self.ok_m, self.sql = session_rows_ok, msg_rows_ok, []

        async def execute(self, q, *a):
            self.sql.append(q)
            return "UPDATE 2" if "chat_messages" in q else "UPDATE 1"

        async def fetch(self, q, *a):
            self.sql.append(q)
            if "FROM chat_messages" in q:
                return [{"session_id": s, "n": 2} for s in a[0]] if self.ok_m else []
            return [{"id": s} for s in a[0]] if self.ok_s else []
    conn = _BdConn()
    monkeypatch.setattr(live, "db", lambda fn: asyncio.run(fn(conn)))
    out = live.backdate(["bar-d1-a", "bar-d1-b"], 26 * 3600)
    assert out == {"ok": True, "sessions": 2, "verified_sessions": 2, "turns": 4, "missing": []}
    assert any("count(*)" in q for q in conn.sql)  # it re-read, not just wrote
    conn = _BdConn(session_rows_ok=False)
    monkeypatch.setattr(live, "db", lambda fn: asyncio.run(fn(conn)))
    out = live.backdate(["bar-d1-a"], 26 * 3600)
    assert out["ok"] is False and out["missing"] == ["bar-d1-a"] and out["verified_sessions"] == 0
    conn = _BdConn(msg_rows_ok=False)
    monkeypatch.setattr(live, "db", lambda fn: asyncio.run(fn(conn)))
    assert live.backdate(["bar-d1-a"], 26 * 3600)["ok"] is False
    with pytest.raises(ValueError):
        live.backdate(["web_1"], 1)


def test_count_export_rows_unknown_shape_is_none():
    assert sb.count_export_rows({"count": 0, "items": []}) == 0
    assert sb.count_export_rows({"count": 2, "items": [{}, {}]}) == 2
    assert sb.count_export_rows({"error": "x"}) is None


# ── main: dry run, refusals, teardown-in-finally ───────────────────────────

@pytest.fixture
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_dry_run_prints_plan_without_network(no_network, capsys, tmp_path):
    rc = sb.main(["--dry-run", "--results", str(tmp_path / "r.json")])
    out = capsys.readouterr().out
    assert rc == 0 and not (tmp_path / "r.json").exists()
    for sid in sb.SCENARIO_IDS:
        assert f"  {sid} " in out
    assert "teardown" in out and "MemAvailable" in out


def test_even_samples_rejected():
    with pytest.raises(SystemExit):
        sb.main(["--dry-run", "--samples", "2"])


def test_without_zoe_perf_it_skips(monkeypatch, tmp_path):
    monkeypatch.delenv("ZOE_PERF", raising=False)
    assert sb.main(["--results", str(tmp_path / "r.json")]) == 0
    assert not (tmp_path / "r.json").exists()


def _gates_open(monkeypatch, tmp_path, forget_ok=True):
    monkeypatch.setenv("ZOE_PERF", "1")
    monkeypatch.setenv("ZOE_INTERNAL_TOKEN", "t")
    monkeypatch.setenv("POSTGRES_URL", "postgresql://x")
    monkeypatch.setattr(sb, "_acquire_lock", lambda: None)
    monkeypatch.setattr(sb, "in_nightly_window", lambda now: False)
    monkeypatch.setattr(sb, "deploy_in_progress", lambda: (False, "0"))
    monkeypatch.setattr(sb, "_readyz", lambda: (True, "ok"))
    monkeypatch.setattr(sb, "mem_available_mb", lambda: 4096)
    monkeypatch.setattr(sb, "service_revision", lambda d: {"commit": "c0ffee", "dirty": False})
    monkeypatch.setattr(sb.Live, "forget_ok", lambda self: forget_ok)
    monkeypatch.setattr(sb.os, "nice", lambda n: 5)
    return ["--results", str(tmp_path / "r.json"), "--trend", str(tmp_path / "t.jsonl"),
            "--baseline", str(tmp_path / "b.json"), "--pending", str(tmp_path / "p.json")]


def _must_not_run(monkeypatch):
    """run_scenarios/teardown spies. main() swallows exceptions from the run so
    teardown can follow — so record a call instead of raising inside it."""
    calls = []
    monkeypatch.setattr(sb, "run_scenarios", lambda *a, **k: calls.append("run") or [])
    monkeypatch.setattr(sb, "teardown", lambda *a: calls.append("teardown")
                        or {"proven": True, "problems": []})
    return calls


def test_refuses_to_write_without_a_teardown_path(monkeypatch, tmp_path):
    args = _gates_open(monkeypatch, tmp_path, forget_ok=False)
    calls = _must_not_run(monkeypatch)
    assert sb.main(args) == 2 and calls == []
    res = json.loads((tmp_path / "r.json").read_text())
    assert res["status"] == "refused" and "teardown unavailable" in res["reason"]


def test_refuses_during_a_deploy(monkeypatch, tmp_path):
    args = _gates_open(monkeypatch, tmp_path)
    monkeypatch.setattr(sb, "deploy_in_progress", lambda: (None, "gh unavailable"))
    calls = _must_not_run(monkeypatch)
    assert sb.main(args) == 2 and calls == []


def test_teardown_runs_even_when_the_run_crashes(monkeypatch, tmp_path):
    args = _gates_open(monkeypatch, tmp_path)
    seen = {}

    def crash(live, a, b, *rest, **k):
        seen["users"] = (a, b)
        raise RuntimeError("brain died")

    def fake_teardown(live, users, sessions):
        seen["teardown"] = users
        return {"proven": True, "problems": []}
    monkeypatch.setattr(sb, "run_scenarios", crash)
    monkeypatch.setattr(sb, "teardown", fake_teardown)
    assert sb.main(args + ["--record-baseline"]) == 2
    assert seen["teardown"] == list(seen["users"])
    assert not (tmp_path / "b.json").exists()  # an errored run never becomes the bar
    assert not (tmp_path / "p.json").exists()  # proven teardown clears the pending file
    assert json.loads((tmp_path / "r.json").read_text())["status"] == "error"


def test_regression_exit_and_baseline_record(monkeypatch, tmp_path):
    args = _gates_open(monkeypatch, tmp_path)
    (tmp_path / "b.json").write_text(json.dumps(_base(S1="PASS")))
    verdicts = {sid: "PASS" for sid in sb.SCENARIO_IDS}
    verdicts["S1"] = "FAIL"
    monkeypatch.setattr(sb, "run_scenarios", lambda *a, **k: [
        {"id": k_, "verdict": v, "evidence": {}} for k_, v in verdicts.items()])
    monkeypatch.setattr(sb, "teardown", lambda *a: {"proven": True, "problems": []})
    assert sb.main(args + ["--compare-baseline"]) == 1
    res = json.loads((tmp_path / "r.json").read_text())
    assert res["compare"]["regressions"] == ["S1"] and res["revision"]["commit"] == "c0ffee"
    trend = [json.loads(x) for x in (tmp_path / "t.jsonl").read_text().splitlines()]
    assert trend[-1]["regressions"] == ["S1"]


def test_unproven_teardown_is_an_error_and_keeps_pending(monkeypatch, tmp_path):
    args = _gates_open(monkeypatch, tmp_path)
    monkeypatch.setattr(sb, "run_scenarios", lambda *a, **k: [
        {"id": s, "verdict": "PASS", "evidence": {}} for s in sb.SCENARIO_IDS])
    monkeypatch.setattr(sb, "teardown", lambda *a: {"proven": False, "problems": ["x"]})
    assert sb.main(args + ["--record-baseline"]) == 2
    assert (tmp_path / "p.json").exists() and not (tmp_path / "b.json").exists()


# ── compare mode needs a REAL baseline (a None baseline is never red) ──────

def _all_pass(monkeypatch):
    monkeypatch.setattr(sb, "run_scenarios", lambda *a, **k: [
        {"id": s, "verdict": "PASS", "evidence": {}} for s in sb.SCENARIO_IDS])
    monkeypatch.setattr(sb, "teardown", lambda *a: {"proven": True, "problems": []})


def test_load_baseline_names_each_problem(tmp_path):
    p = tmp_path / "b.json"
    assert sb.load_baseline(p) == (None, f"no baseline at {p} — run --record-baseline first")
    p.write_text("{not json")
    base, why = sb.load_baseline(p)
    assert base is None and "not valid JSON" in why and str(p) in why
    p.write_text(json.dumps({"revision": {}}))  # right type, no scenarios
    assert sb.load_baseline(p)[0] is None and "scenarios" in sb.load_baseline(p)[1]
    p.write_text(json.dumps(_base(S1="PASS")))
    assert sb.load_baseline(p) == (_base(S1="PASS"), None)


@pytest.mark.parametrize("content", [None, "{not json", "[]", '{"scenarios": "PASS"}',
                                     '{"scenarios": {}}', '{"scenarios": {"S1": "MAYBE"}}'])
def test_compare_mode_refuses_without_a_valid_baseline(monkeypatch, tmp_path, content, capsys):
    args = _gates_open(monkeypatch, tmp_path)
    if content is not None:
        (tmp_path / "b.json").write_text(content)
    calls = _must_not_run(monkeypatch)
    assert sb.main(args + ["--compare-baseline"]) == 2
    assert calls == []  # refused BEFORE any demo user was written
    res = json.loads((tmp_path / "r.json").read_text())
    assert res["status"] == "refused" and str(tmp_path / "b.json") in res["reason"]
    assert "REFUSED" in capsys.readouterr().err
    assert not (tmp_path / "t.jsonl").exists()


def test_record_baseline_with_none_present_is_ok(monkeypatch, tmp_path):
    args = _gates_open(monkeypatch, tmp_path)
    _all_pass(monkeypatch)
    assert sb.main(args + ["--record-baseline"]) == 0
    res = json.loads((tmp_path / "r.json").read_text())
    assert res["status"] == "ok" and not res["compare"]["has_baseline"]
    assert json.loads((tmp_path / "b.json").read_text())["scenarios"] == {
        s: "PASS" for s in sb.SCENARIO_IDS}


def test_record_baseline_overwrites_a_malformed_one(monkeypatch, tmp_path):
    args = _gates_open(monkeypatch, tmp_path)
    (tmp_path / "b.json").write_text("{not json")
    _all_pass(monkeypatch)
    assert sb.main(args + ["--record-baseline"]) == 0
    assert "scenarios" in json.loads((tmp_path / "b.json").read_text())


def test_compare_with_a_valid_baseline_and_no_regression_is_ok(monkeypatch, tmp_path):
    args = _gates_open(monkeypatch, tmp_path)
    (tmp_path / "b.json").write_text(json.dumps(_base(S1="PASS")))
    _all_pass(monkeypatch)
    assert sb.main(args + ["--compare-baseline"]) == 0
    res = json.loads((tmp_path / "r.json").read_text())
    assert res["status"] == "ok" and res["compare"]["has_baseline"]


# ── setup preconditions gate the verdict (seeds must succeed, facts must land) ──

def test_setup_problems_names_each_failed_seed_and_unlanded_fact():
    ok = {"reply": "ok", "error": None}
    assert sb.setup_problems({"d1-home": ok}, {"S2_old": {"landed": True}}) == []
    probs = sb.setup_problems({"d1-home": {"reply": "", "error": "HTTP 503"}, "d2-home": None},
                              {"S2_old": {"landed": False, "waited_s": 90}, "S2_new": None})
    assert probs == ["seed turn d1-home failed: HTTP 503", "seed turn d2-home was never sent",
                     "S2_old never landed in the recall packet",
                     "S2_new never landed in the recall packet"]


class _ScriptedLive(sb.Live):
    """Drives run_scenarios with no network: every turn answers with the needles
    the scenario wants, so the ONLY way a scenario errors is a setup problem."""

    def __init__(self, seed_errors=(), unlanded=(), filler_errors=0, capture_stalls=False,
                 backdate_incomplete=False, selector_hook=None, store=None, s11="ok"):
        super().__init__("tok", "", "postgresql://x", False)
        self.s11, self.s11_rows = s11, []  # "ok" = the tier runs; "off" = the brain answers and stores nothing
        self.store = store  # when set, a fact "lands" only if every needle is in this text
        self.selector_hook = selector_hook
        self.seed_errors, self.unlanded = set(seed_errors), set(unlanded)
        self.filler_errors, self.chats = filler_errors, []
        self.capture_stalls, self.waited_capture = capture_stalls, []
        self.backdate_incomplete, self.backdated = backdate_incomplete, []

    def capture_status(self, user):
        return {"completed": len(self.chats), "in_flight": 0}

    def wait_captured(self, user, before, timeout_s=180):
        self.waited_capture.append((user, before))
        if self.capture_stalls:
            return {"landed": False, "kind": "capture", "why": "capture not completed (in_flight=1)"}
        return {"landed": True, "kind": "capture", "waited_s": 1.0}

    def chat(self, user, tag, message):
        self.chats.append(tag)
        self.sessions.setdefault(user, []).append(f"bar-{tag}-{len(self.chats)}")
        if tag in self.seed_errors or (tag.startswith("filler-") and self.filler_errors
                                       and len([c for c in self.chats if c.startswith("filler-")])
                                       <= self.filler_errors):
            return {"reply": "", "error": "HTTP 503", "ms": 1, "session": tag}
        if tag.startswith("s11-"):
            return {"reply": self._s11(tag), "error": None, "ms": 1, "session": tag}
        reply = {"d1-ask-sister": "Your sister Marisol is flying in from Lisbon.",
                 "d2-ask-dad": "Your dad Teodor is a retired lighthouse keeper.",
                 "b-ask": "I have no idea who is visiting.",
                 "long-ask-sister": "Marisol.", "long-ask-dad": "He kept a lighthouse.",
                 "s5-open-1": "Good! How did the aquarium interview go?",
                 "s26-pull-1": "You mentioned you're anxious about a job interview at the aquarium "
                               "on Friday - how's that going?",
                 "s26-pull-2": "Nothing new for you right now.",
                 "c-ask-list": "You have 2 contacts. Friend: Ottoline Fenwick. Brother: Percival.",
                 "c-ask-rel": "Percival is your brother.",
                 "c-ask-dup": "Ottoline Fenwick is your friend.",
                 "s20-ask": "Priya Nair's birthday is on 7 August 1991.",
                 "s21-fix": "Fixed: Biscuit Whitfield is a pet dog, not one of the children.",
                 "s21-ask": "Dana Whitfield has one child, Mika.",
                 "s22-roster": "Here's what I've got. I haven't guessed who's who - which one is your friend?",
                 "s22-ask": "Anika Reyes is on the list you gave me; I don't know how she is related.",
                 }.get(tag, "ok")
        return {"reply": reply, "error": None, "ms": 1, "session": tag}

    def _s11(self, tag):
        if self.s11 == "off":   # flag off: the brain chats and says it will remember; nothing is stored
            return {"s11-recall-q": "I'm not sure I can recall that.", "s11-fresh-ask": "I don't have that.",
                    "s11-forget": "Okay."}.get(tag, "Sure thing! I'll remember that your favourite tea is lapsang "
                                               "souchong, a lovely smoky cup. Anything else you would like me to remember?")
        if tag == "s11-say":
            self.s11_rows.append("my favourite tea is lapsang souchong")
            return "Got it — I'll remember that."
        if tag == "s11-keep":
            self.s11_rows.append("I can't stand coriander")
            return "Noted — I'll remember that."
        if tag == "s11-repeat":
            return "I've already got that one."
        if tag == "s11-fresh-ask":
            return "Your favourite tea is lapsang souchong."
        if tag == "s11-recall-q":
            return "You asked me to remember: my favourite tea is lapsang souchong; and I can't stand coriander."
        self.s11_rows = [r for r in self.s11_rows if "coriander" not in r]   # "Forget that." retracts the newest
        return 'Done — I forgot: "I can\'t stand coriander".'

    def packet_count(self, user):
        return 5 + len(self.s11_rows)

    def wait_landed(self, user, message, needles, timeout_s=90):
        if self.store is not None:
            landed = all(n.lower() in self.store.lower() for n in needles)
            return {"landed": landed, "waited_s": 0}
        landed = not (set(needles) & self.unlanded)
        return {"landed": landed, "waited_s": 0}

    def packet(self, user, message):
        if user == B:
            return ""
        if "tea" in message or "coriander" in message:
            return "\n".join(f"- {r}" for r in self.s11_rows)
        if "Priya" in message:
            return "- Priya Nair's birthday is 7 August 1991"
        if "Biscuit" in message:
            return "- Biscuit Whitfield is a pet dog, not a child\n- Mika Whitfield, child of Dana"
        if "Anika" in message:
            return "- Anika Reyes: 2 November 1985"
        return "- dad Teodor, retired lighthouse keeper; sister Marisol"

    def backdate(self, session_ids, age_s):
        self.backdated.append(list(session_ids))
        if self.backdate_incomplete:
            return {"ok": False, "sessions": len(session_ids), "verified_sessions": 1,
                    "turns": 2, "missing": session_ids[1:]}
        return {"ok": True, "sessions": len(session_ids), "verified_sessions": len(session_ids),
                "turns": 2 * len(session_ids), "missing": []}

    def proactive_hooks(self, user):
        return []

    def run_selector(self, user):
        return self.selector_hook

    def rearm(self, user):
        return {"rows": 1, "armed": 1}

    def inbox(self, user):
        pulled = "s26-pull-1" in self.chats
        if user is None or pulled:
            return {"enabled": True, "count": 0, "top": None, "quiet": False}
        return {"enabled": True, "count": 1, "top": "question", "quiet": False}

    def raise_state(self, user):
        return [{"kind": "emotional", "carries": True, "surfaced": 1}]

    def evidence(self, turn):
        return {}


def _drive(monkeypatch, backdate=True, selected=None, **live_kw):
    monkeypatch.setattr(sb, "_ask_judged", lambda live, u, tag, q, n, scorer, sid: ("PASS", {}))
    monkeypatch.setattr(sb.time, "sleep", lambda s: None)
    live = _ScriptedLive(**live_kw)
    res = {r["id"]: r for r in sb.run_scenarios(live, A, B, 1, backdate, lambda m: None,
                                                selected=selected)}
    return live, res


def test_backdate_is_verified_and_gates_the_multi_day_scenarios(monkeypatch):
    live, res = _drive(monkeypatch)
    assert live.backdated and all(s.startswith("bar-d1-") for s in live.backdated[0])
    assert {res[s]["verdict"] for s in ("S2", "S4", "S7")} == {"PASS"}
    live, res = _drive(monkeypatch, backdate_incomplete=True)
    for sid in ("S2", "S4", "S5", "S7"):
        assert res[sid]["verdict"] == "ERROR" and "day-1 backdate incomplete" in res[sid]["evidence"]["why"], sid
    assert "d2-ask-home" not in live.chats and "d2-ask-dad" not in live.chats
    assert res["S1"]["verdict"] == "PASS" and res["S8"]["verdict"] == "PASS"  # same-day scenarios unaffected
    live, res = _drive(monkeypatch, backdate=False, backdate_incomplete=True)  # diagnostic run: not gated
    assert res["S2"]["verdict"] == "PASS" and live.backdated == []


def test_healthy_run_has_every_scenario_and_no_setup_errors(monkeypatch):
    live, res = _drive(monkeypatch)
    assert set(res) == set(sb.SCENARIO_IDS)
    assert not [r for r in res.values() if "setup_problems" in r["evidence"]]
    assert res["S2"]["verdict"] == "PASS" and res["S7"]["verdict"] == "PASS"
    assert res["S8"]["verdict"] == "PASS"
    # contacts: S13-S15 scored on the scripted replies; S16 SKIPs (no offer surfaced: flags dark)
    assert [res[s]["verdict"] for s in ("S13", "S14", "S15", "S16")] == ["PASS", "PASS", "PASS", "SKIP"]


def test_failed_contact_seed_errors_the_contacts_scenarios(monkeypatch):
    live, res = _drive(monkeypatch, seed_errors=["c-rel"])
    for sid in ("S13", "S14"):
        assert res[sid]["verdict"] == "ERROR" and "seed turn c-rel failed" in res[sid]["evidence"]["why"]
    assert "c-ask-list" not in live.chats and "c-ask-rel" not in live.chats  # never asked unexercised
    assert res["S15"]["verdict"] == "PASS"


@pytest.mark.parametrize("seed, sid, ask_tag", [
    ("d1-home", "S2", "d2-ask-home"), ("d2-home", "S2", "d2-ask-home"),
    ("d2-dad", "S7", "d2-ask-dad"), ("d1-dad", "S7", "d2-ask-dad"),
    ("d1-sister", "S1", "d1-ask-sister"), ("d1-worry", "S4", "d2-edge")])
def test_failed_seed_turn_errors_its_scenario_and_skips_the_ask(monkeypatch, seed, sid, ask_tag):
    live, res = _drive(monkeypatch, seed_errors=[seed])
    r = res[sid]
    assert r["verdict"] == "ERROR" and f"seed turn {seed} failed" in r["evidence"]["why"]
    assert ask_tag not in live.chats  # an unexercised setup is never asked about


def test_s2_needs_both_the_old_and_the_new_fact_landed(monkeypatch):
    live, res = _drive(monkeypatch, unlanded=["dunedin"])
    assert res["S2"]["verdict"] == "ERROR" and "S2_old never landed" in res["S2"]["evidence"]["why"]
    assert "d2-ask-home" not in live.chats
    live, res = _drive(monkeypatch, unlanded=["hobart"])
    assert res["S2"]["verdict"] == "ERROR" and "S2_new never landed" in res["S2"]["evidence"]["why"]


def test_unlanded_rich_fact_errors_s7_and_s8(monkeypatch):
    live, res = _drive(monkeypatch, unlanded=["lighthouse"])
    assert res["S7"]["verdict"] == "ERROR" and "S7_rich never landed" in res["S7"]["evidence"]["why"]
    assert res["S8"]["verdict"] == "ERROR" and "S7_rich never landed" in res["S8"]["evidence"]["why"]
    assert "d2-ask-dad" not in live.chats and "long-ask-dad" not in live.chats


def test_s7_waits_for_the_duplicate_capture_and_errors_when_it_never_completes(monkeypatch):
    live, res = _drive(monkeypatch)
    assert res["S7"]["verdict"] == "PASS"
    # The first capture wait is S7's duplicate (S10's change turn waits after it).
    assert live.waited_capture[0] == (A, {"completed": live.chats.index("d2-dad"), "in_flight": 0})
    live, res = _drive(monkeypatch, capture_stalls=True)
    assert res["S7"]["verdict"] == "ERROR"
    assert "S7_dup: the turn's memory capture never completed" in res["S7"]["evidence"]["why"]
    assert "d2-ask-dad" not in live.chats  # scoring cannot proceed while the duplicate is unobserved


def test_live_wait_captured_semantics(monkeypatch):
    live = sb.Live("tok", "", "postgresql://x", False)
    clock = {"t": 0.0}
    monkeypatch.setattr(sb.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(sb.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    # Endpoint absent before the turn: nothing to compare against, never landed.
    assert sb.Live.wait_captured(live, A, None)["landed"] is False
    states = iter([{"completed": 3, "in_flight": 1}, {"completed": 4, "in_flight": 1},
                   {"completed": 4, "in_flight": 0}])
    monkeypatch.setattr(live, "capture_status", lambda u: next(states))
    out = live.wait_captured(A, {"completed": 3, "in_flight": 0})
    assert out["landed"] is True and out["completed"] == 4
    monkeypatch.setattr(live, "capture_status", lambda u: {"completed": 3, "in_flight": 0})
    out = live.wait_captured(A, {"completed": 3, "in_flight": 0}, timeout_s=10)
    assert out["landed"] is False and "not completed" in out["why"]
    # Completed but a memory pass FAILED since the turn: the duplicate was not captured.
    monkeypatch.setattr(live, "capture_status",
                        lambda u: {"completed": 4, "failed": 1, "in_flight": 0})
    out = live.wait_captured(A, {"completed": 3, "failed": 0, "in_flight": 0}, timeout_s=10)
    assert out["landed"] is False and "capture FAILED" in out["why"]
    # A failure that predates the turn does not poison it.
    out = live.wait_captured(A, {"completed": 3, "failed": 1, "in_flight": 0}, timeout_s=10)
    assert out["landed"] is True
    monkeypatch.setattr(live, "capture_status", lambda u: None)
    assert "unavailable" in live.wait_captured(A, {"completed": 0}, timeout_s=10)["why"]


def test_live_capture_status_reads_the_internal_endpoint(monkeypatch):
    live = sb.Live("tok", "", "postgresql://x", False)
    seen = {}

    def req(method, url, headers, body=None, timeout=60):
        seen.update(method=method, url=url, headers=headers)
        return 200, {"user_id": A, "started": 2, "completed": 2, "failed": 0, "in_flight": 0}
    monkeypatch.setattr(live, "_req", req)
    assert live.capture_status(A)["completed"] == 2
    assert seen["method"] == "GET" and "/api/memories/capture-status?user_id=" in seen["url"]
    assert seen["headers"] == {"X-Internal-Token": "tok"}
    monkeypatch.setattr(live, "_req", lambda *a, **k: (404, {}))
    assert live.capture_status(A) is None


def test_failed_filler_turns_error_s8_in_the_run(monkeypatch):
    live, res = _drive(monkeypatch, filler_errors=2)
    assert res["S8"]["verdict"] == "ERROR" and res["S8"]["evidence"]["filler_errors"] == 2
    assert "history not exercised" in res["S8"]["evidence"]["why"]


# ── record and compare are separate acts ──────────────────────────────────

def _capture_samples(monkeypatch):
    seen = {}

    def run(live, a, b, samples, backdate, log, selected=None):
        seen["samples"], seen["backdate"], seen["selected"] = samples, backdate, selected
        return [{"id": s, "verdict": "PASS", "evidence": {}} for s in sb.SCENARIO_IDS]
    monkeypatch.setattr(sb, "run_scenarios", run)
    monkeypatch.setattr(sb, "teardown", lambda *a: {"proven": True, "problems": []})
    return seen


def test_compare_inherits_the_baseline_sample_count(monkeypatch, tmp_path):
    args = _gates_open(monkeypatch, tmp_path)
    (tmp_path / "b.json").write_text(json.dumps({**_base(S1="PASS"), "samples": 3}))
    seen = _capture_samples(monkeypatch)
    assert sb.main(args + ["--compare-baseline"]) == 0
    assert seen["samples"] == 3 and seen["backdate"] is True
    res = json.loads((tmp_path / "r.json").read_text())
    assert res["samples"] == 3 and res["backdate"] is True


def test_compare_with_an_explicit_mismatched_sample_count_is_refused(monkeypatch, tmp_path):
    args = _gates_open(monkeypatch, tmp_path)
    (tmp_path / "b.json").write_text(json.dumps({**_base(S1="PASS"), "samples": 3}))
    calls = _must_not_run(monkeypatch)
    assert sb.main(args + ["--compare-baseline", "--samples", "1"]) == 2 and calls == []
    res = json.loads((tmp_path / "r.json").read_text())
    assert res["status"] == "refused" and "samples=3" in res["reason"]
    seen = _capture_samples(monkeypatch)
    assert sb.main(args + ["--compare-baseline", "--samples", "3"]) == 0 and seen["samples"] == 3


def test_record_defaults_to_one_sample_and_stores_it(monkeypatch, tmp_path):
    args = _gates_open(monkeypatch, tmp_path)
    seen = _capture_samples(monkeypatch)
    assert sb.main(args + ["--record-baseline"]) == 0 and seen["samples"] == 1
    assert json.loads((tmp_path / "b.json").read_text())["samples"] == 1


def test_no_backdate_is_refused_in_baseline_modes(capsys):
    for mode in ("--record-baseline", "--compare-baseline"):
        with pytest.raises(SystemExit):
            sb.main(["--dry-run", "--no-backdate", mode])
        assert "diagnostic only" in capsys.readouterr().err
    assert sb.main(["--dry-run", "--no-backdate"]) == 0  # diagnostic run is fine

def test_compare_and_record_are_mutually_exclusive(capsys):
    with pytest.raises(SystemExit):
        sb.main(["--dry-run", "--compare-baseline", "--record-baseline"])
    assert "mutually exclusive" in capsys.readouterr().err


@pytest.mark.parametrize("samples", ["three", 0, 2, -1, True, 1.5, None])
def test_baseline_with_a_malformed_sample_count_is_refused(tmp_path, samples):
    p = tmp_path / "b.json"
    p.write_text(json.dumps({**_base(S1="PASS"), "samples": samples}))
    base, why = sb.load_baseline(p)
    assert base is None and "'samples'" in why and repr(samples) in why


def test_compare_refuses_cleanly_on_a_malformed_sample_count(monkeypatch, tmp_path):
    args = _gates_open(monkeypatch, tmp_path)
    (tmp_path / "b.json").write_text(json.dumps({**_base(S1="PASS"), "samples": "three"}))
    calls = _must_not_run(monkeypatch)
    assert sb.main(args + ["--compare-baseline"]) == 2 and calls == []  # no ValueError, no run
    res = json.loads((tmp_path / "r.json").read_text())
    assert res["status"] == "refused" and "'samples'" in res["reason"] and "'three'" in res["reason"]


def test_baseline_without_usable_verdicts_is_refused(tmp_path):
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"scenarios": {}}))
    base, why = sb.load_baseline(p)
    assert base is None and "no scenario verdicts" in why
    p.write_text(json.dumps({"scenarios": {"S1": "PASS", "S2": "MAYBE", "S3": 1}}))
    base, why = sb.load_baseline(p)
    assert base is None and "unrecognised verdict(s) for S2, S3" in why
    p.write_text(json.dumps({"scenarios": {"S1": "FAIL"}}))
    base, why = sb.load_baseline(p)
    assert why is None
    assert "nothing can regress" in sb.compare_baseline({"S1": "FAIL"}, base)["notes"][0]


# ── forget path selection (synthetic by default, admin when a session is given) ──

class _RecLive(sb.Live):
    def __init__(self, admin=""):
        super().__init__("tok", admin, "postgresql://x", False)
        self.calls = []

    def _req(self, method, url, headers, body=None, timeout=60):
        self.calls.append((method, url, dict(headers)))
        if url.endswith("/forget-synthetic") or url.endswith("/forget"):
            return 200, {"removed": 0}
        if "/export" in url:
            return 200, {"count": 0, "items": []}
        return 404, {}


def test_synthetic_mode_uses_the_internal_route_and_token():
    live = _RecLive()
    assert live.forget_mode == "synthetic"
    assert live.forget(A) == 0
    method, url, headers = live.calls[-1]
    assert method == "POST" and url.endswith(f"/api/memories/users/{A}/forget-synthetic")
    assert headers == {"X-Internal-Token": "tok"}
    # Residual proof = a second forget (removed 0 means nothing was left).
    assert live.residual_count(A) == 0 and live.calls[-1][1].endswith("/forget-synthetic")


def test_forget_preflight_probes_a_fresh_demo_id():
    live = _RecLive()
    assert live.forget_ok()
    probed = live.calls[-1][1].split("/users/")[1].split("/")[0]
    assert sb.DEMO_USER_RE.match(probed)


def test_admin_mode_when_a_session_is_given():
    live = _RecLive(admin="sess")
    assert live.forget_mode == "admin"
    live.forget(A)
    assert live.calls[-1][1].endswith(f"/users/{A}/forget")
    assert live.calls[-1][2] == {"X-Session-ID": "sess"}
    assert live.residual_count(A) == 0 and "/export" in live.calls[-1][1]


def test_forget_refuses_a_non_demo_id_before_any_request():
    live = _RecLive()
    with pytest.raises(ValueError):
        live.forget("jason")
    assert live.calls == []


# ── S20–S22 (2026-10-04): dates, corrections, roles ────────────────────────

@pytest.mark.parametrize("reply, packet, verdict", [
    ("Priya's birthday is 7 August 1991.", "- Priya Nair's birthday is 7 August 1991", "PASS"),
    ("Priya's birthday is July 8th, 1991.", "- Priya Nair's birthday is 7 August 1991", "FAIL"),  # reply wrong
    ("It's 7 August.", "- Priya Nair's birthday is July 8th, 1991.", "FAIL"),                      # store wrong
    ("It's 7 August.", "- Priya Nair: 7/8/1991", "FAIL"),                                           # raw digits
    ("I don't know.", "- Priya Nair's birthday is 7 August 1991", "FAIL"),
    ("7 August", None, "ERROR"),
])
def test_s20_day_first(reply, packet, verdict):
    assert sb.score_s20(reply, packet)[0] == verdict


@pytest.mark.parametrize("ack, reply, packet, verdict", [
    ("Fixed: Biscuit Whitfield is a pet dog, not one of the children.", "She has one child, Mika.",
     "- Biscuit Whitfield is a pet dog, not a child", "PASS"),
    ("Oh sorry, I'll get it right next time.", "She has one child, Mika.",
     "- Biscuit Whitfield is a pet dog, not a child", "FAIL"),        # apologised, changed nothing
    ("Fixed: Biscuit is a pet dog.", "Dana has two kids, Mika and Biscuit.",
     "- Biscuit is a pet dog", "FAIL"),                                # the count still has the dog
    ("Fixed: Biscuit is a pet dog.", "One child, Mika.", "- Biscuit is Dana's child", "FAIL"),  # store
    ("Fixed.", "One child, Mika.", None, "ERROR"),
])
def test_s21_correction_reaches_the_record(ack, reply, packet, verdict):
    assert sb.score_s21(ack, reply, packet)[0] == verdict


@pytest.mark.parametrize("roster_reply, ask_reply, packet, verdict", [
    ("Which one is your friend?", "Anika is on the list.", "- Anika Reyes: 2 November 1985", "PASS"),
    ("So Callum is the husband and Anika is the wife?", "ok", "-", "FAIL"),   # roles from first names
    ("Which one is your friend?", "Anika is his wife.", "-", "FAIL"),
    ("Which one is your friend?", "ok", "- Ines Reyes, daughter of Callum", "FAIL"),   # the store
    ("Got them all down.", "ok", "-", "FAIL"),                                  # no who's-who question
])
def test_s22_roles_are_not_guessed(roster_reply, ask_reply, packet, verdict):
    assert sb.score_s22(roster_reply, ask_reply, packet)[0] == verdict


def test_new_scenarios_run_in_the_harness(monkeypatch):
    live, res = _drive(monkeypatch)
    for sid in ("S20", "S21", "S22"):
        assert res[sid]["verdict"] == "PASS", res[sid]
    assert res["S21"]["expected"] == "FAIL" and res["S22"]["expected"] == "FAIL"
    assert "expected" not in res["S20"]                      # S20 is unflagged: a real regression gate
    assert {"s21-fix", "s21-ask", "s22-ask", "s20-ask"} <= set(live.chats)
    live, res = _drive(monkeypatch, seed_errors=["d1-dob"])
    assert res["S20"]["verdict"] == "ERROR" and "s20-ask" not in live.chats
    live, res = _drive(monkeypatch, unlanded=["kids"])
    assert res["S21"]["verdict"] == "ERROR" and "s21-fix" not in live.chats


def test_s21_setup_probe_does_not_need_the_store_to_keep_the_kids_names(monkeypatch):
    """The live store keeps 'Dana Whitfield has two kids' and drops 'Mika and Biscuit'. S21's
    landing probe waited for the name Biscuit, which can never appear, so the scenario was
    ERROR (a setup failure that hid the verdict) instead of a clean FAIL/PASS. The probe must
    wait for the kids fact itself; the scorer then judges the (missing) pet in the store."""
    live, res = _drive(monkeypatch, store="- User's friend is named Dana Whitfield\n"
                                          "- Dana Whitfield has two kids")
    assert res["S21"]["verdict"] in ("PASS", "FAIL"), res["S21"]
    assert "setup_problems" not in res["S21"]["evidence"]
    assert "s21-fix" in live.chats and "s21-ask" in live.chats
    # a store with no kids fact at all is still a setup failure, never a verdict
    live, res = _drive(monkeypatch, store="- User's friend is named Dana Whitfield")
    assert res["S21"]["verdict"] == "ERROR" and "s21-fix" not in live.chats


# ── --only / --axis: a PARTIAL run (never an error, never a baseline) ─────────

def test_parse_selection_full_run_is_none_and_filters_are_validated():
    assert sb.parse_selection(None, None) is None
    assert sb.parse_selection("s21, S22", None) == frozenset({"S21", "S22"})   # case / spaces
    assert sb.parse_selection(None, "b") == frozenset(
        {"S13", "S14", "S15", "S16", "S20", "S21", "S22"})                      # letter shorthand
    assert sb.parse_selection(None, "temporal,e") == frozenset({"S2", "S10", "S3"})
    assert sb.parse_selection("S21,S1", "b") == frozenset({"S21"})              # intersection
    for only, axis in (("S99", None), ("S1,SX", None), ("", None), (None, "zz"), (None, ""),
                       (None, "f"), (None, "h"),                # axes with no bar scenario
                       ("S1", "b")):                            # empty intersection
        with pytest.raises(ValueError):
            sb.parse_selection(only, axis)


def test_every_scenario_axis_is_a_known_axis_and_deps_are_scenarios():
    assert set(sb.AXIS_OF.values()) <= set(sb.AXES.values())
    assert set(sb.AXIS_OF) <= set(sb.SCENARIO_IDS)
    assert {d for ds in sb.SEED_DEPS.values() for d in ds} <= set(sb.SCENARIO_IDS)
    assert sb.seed_closure({"S12"}) == frozenset({"S12", "S5", "S4"})           # transitive
    assert sb.seed_closure({"S8"}) == frozenset({"S8", "S1", "S7"})


def test_unknown_only_id_or_axis_exits_2_before_anything_runs(monkeypatch, tmp_path, capsys):
    args = _gates_open(monkeypatch, tmp_path)
    calls = _must_not_run(monkeypatch)
    for bad in (["--only", "S99"], ["--axis", "nope"], ["--only", "S21", "--axis", "e"]):
        with pytest.raises(SystemExit) as ei:
            sb.main(args + bad)
        assert ei.value.code == 2
    assert calls == [] and not (tmp_path / "r.json").exists()
    assert "unknown scenario id" in capsys.readouterr().err


def test_only_runs_the_selection_and_reports_partial_not_error(monkeypatch, tmp_path):
    args = _gates_open(monkeypatch, tmp_path)
    seen = {}

    def run(live, a, b, samples, backdate, log, selected=None):
        seen["selected"] = selected
        return [{"id": s, "verdict": "PASS", "evidence": {}} for s in sb.SCENARIO_IDS if s in selected]
    monkeypatch.setattr(sb, "run_scenarios", run)
    monkeypatch.setattr(sb, "teardown", lambda *a: {"proven": True, "problems": []})
    assert sb.main(args + ["--only", "S1,S3"]) == 0
    res = json.loads((tmp_path / "r.json").read_text())
    # the control: with the partial branch removed this is `error` (len(results) != len(SCENARIO_IDS))
    assert res["status"] == "partial" and res["partial"] is True and res["selected"] == ["S1", "S3"]
    assert [r["id"] for r in res["scenarios"]] == ["S1", "S3"] and seen["selected"] == {"S1", "S3"}
    assert not (tmp_path / "b.json").exists()
    trend = [json.loads(x) for x in (tmp_path / "t.jsonl").read_text().splitlines()]
    assert trend[-1]["status"] == "partial"


def test_a_selection_that_returns_the_wrong_scenarios_is_still_an_error(monkeypatch, tmp_path):
    args = _gates_open(monkeypatch, tmp_path)
    monkeypatch.setattr(sb, "run_scenarios", lambda *a, **k: [
        {"id": "S1", "verdict": "PASS", "evidence": {}}])          # asked for S1+S3, S3 went missing
    monkeypatch.setattr(sb, "teardown", lambda *a: {"proven": True, "problems": []})
    assert sb.main(args + ["--only", "S1,S3"]) == 2
    assert json.loads((tmp_path / "r.json").read_text())["status"] == "error"


def test_record_baseline_is_refused_with_only_or_axis(monkeypatch, tmp_path):
    args = _gates_open(monkeypatch, tmp_path)
    calls = _must_not_run(monkeypatch)
    for sel in (["--only", "S21"], ["--axis", "b"]):
        with pytest.raises(SystemExit) as ei:
            sb.main(args + ["--record-baseline"] + sel)
        assert ei.value.code == 2
    assert calls == [] and not (tmp_path / "b.json").exists()
    # selecting EVERY scenario is the full run: recording stays legal
    _all_pass(monkeypatch)
    assert sb.main(args + ["--record-baseline", "--only", ",".join(sb.SCENARIO_IDS)]) == 0
    assert (tmp_path / "b.json").exists()


def test_partial_run_never_records_even_if_the_cli_gate_were_bypassed(monkeypatch, tmp_path):
    """Defence in depth: the baseline write itself checks `partial`, not just the CLI gate."""
    args = _gates_open(monkeypatch, tmp_path)
    monkeypatch.setattr(sb, "run_scenarios", lambda *a, **k: [{"id": "S1", "verdict": "PASS", "evidence": {}}])
    monkeypatch.setattr(sb, "teardown", lambda *a: {"proven": True, "problems": []})
    errs = []
    # the CLI gate's ap.error is neutralised: main carries on into the run with --record-baseline set
    monkeypatch.setattr(sb.argparse.ArgumentParser, "error", lambda self, msg: errs.append(msg))
    sb.main(args + ["--record-baseline", "--only", "S1"])
    assert errs and "--record-baseline is refused" in errs[0]
    assert not (tmp_path / "b.json").exists()


def test_compare_baseline_with_only_compares_only_the_selected(monkeypatch, tmp_path):
    args = _gates_open(monkeypatch, tmp_path)
    (tmp_path / "b.json").write_text(json.dumps(_base(S1="PASS", S2="PASS", S3="PASS")))
    before = (tmp_path / "b.json").read_text()
    monkeypatch.setattr(sb, "teardown", lambda *a: {"proven": True, "problems": []})
    # S2 and S3 are unselected: they are not in this run, so they cannot be red
    monkeypatch.setattr(sb, "run_scenarios", lambda *a, **k: [{"id": "S1", "verdict": "PASS", "evidence": {}}])
    assert sb.main(args + ["--compare-baseline", "--only", "S1"]) == 0
    res = json.loads((tmp_path / "r.json").read_text())
    assert res["status"] == "partial" and res["compare"]["regressions"] == [] and not res["compare"]["red"]
    assert any("partial run: compared only 1 of" in n for n in res["compare"]["notes"])
    # a SELECTED scenario that regressed is red: exit 1, status regression
    monkeypatch.setattr(sb, "run_scenarios", lambda *a, **k: [{"id": "S1", "verdict": "FAIL", "evidence": {}}])
    assert sb.main(args + ["--compare-baseline", "--only", "S1"]) == 1
    res = json.loads((tmp_path / "r.json").read_text())
    assert res["status"] == "regression" and res["compare"]["regressions"] == ["S1"]
    assert (tmp_path / "b.json").read_text() == before            # a compare never rewrites the bar


def test_restrict_baseline_drops_unselected_and_keeps_the_rest():
    base = _base(S1="PASS", S2="FAIL")
    got = sb.restrict_baseline(base, frozenset({"S1"}))
    assert got["scenarios"] == {"S1": "PASS"} and got["judge_prompt_sha256"] == base["judge_prompt_sha256"]
    assert sb.restrict_baseline(base, None) is base and sb.restrict_baseline(None, frozenset({"S1"})) is None


def test_dry_run_with_only_prints_the_partial_plan(no_network, capsys):
    assert sb.main(["--dry-run", "--only", "S21,S8"]) == 0
    out = capsys.readouterr().out
    assert "PARTIAL RUN" in out and "S8, S21" in out
    assert "  S21 " in out and "  S2 " not in out and "  S13 " not in out
    # S8 reads S1's and S7's seeds: they are listed, and marked seed-only
    assert "  S1 same-day" in out and "seed turns only" in out
    assert "PARTIAL RUN" not in sb.plan_text(1)


def test_run_scenarios_only_s21_sends_only_s21_turns(monkeypatch):
    live, res = _drive(monkeypatch, selected={"S21"})
    assert set(res) == {"S21"} and res["S21"]["verdict"] == "PASS"
    assert live.chats == ["s21-kids", "s21-fix", "s21-ask"]      # no filler, no contacts, no day-1 seeds
    assert live.backdated == []                                   # no multi-day scenario in play


def test_run_scenarios_only_s8_runs_the_seeds_it_reads_but_asks_nothing_else(monkeypatch):
    live, res = _drive(monkeypatch, selected={"S8"})
    assert set(res) == {"S8"} and res["S8"]["verdict"] == "PASS"
    assert "d1-sister" in live.chats and "d1-dad" in live.chats           # S8's facts are seeded
    assert "d1-ask-sister" not in live.chats and "d2-ask-dad" not in live.chats  # S1 / S7 not asked
    assert "long-ask-sister" in live.chats and sum(t.startswith("filler-") for t in live.chats) == len(sb.FILLER)
    assert not any(t in live.chats for t in ("d1-home", "d1-worry", "d1-cello", "s21-kids", "c-full"))


def test_run_scenarios_only_s2_backdates_and_asks_only_s2(monkeypatch):
    live, res = _drive(monkeypatch, selected={"S2"})
    assert set(res) == {"S2"} and res["S2"]["verdict"] == "PASS"
    assert live.backdated
    assert "d1-sister" not in live.chats and "d1-dad" not in live.chats
    live, res = _drive(monkeypatch, selected={"S2"}, backdate_incomplete=True)
    assert res["S2"]["verdict"] == "ERROR"                        # the gate still protects a partial run


def test_run_scenarios_only_s12_runs_s5_s4_seed_and_open_turns_but_reports_s12_only(monkeypatch):
    live, res = _drive(monkeypatch, selected={"S12"}, selector_hook={"enabled": True})
    assert set(res) == {"S12"}
    assert "d1-worry" in live.chats and "s5-open-1" in live.chats and "s5-open-2" in live.chats
    assert "d2-edge" not in live.chats and "d2-dentist" not in live.chats


def test_selected_none_is_the_full_bar_unchanged(monkeypatch):
    live, res = _drive(monkeypatch, selected=None)
    assert set(res) == set(sb.SCENARIO_IDS)
    live2, res2 = _drive(monkeypatch, selected=frozenset(sb.SCENARIO_IDS))
    assert live.chats == live2.chats and set(res2) == set(res)


# ── S11 (2026-10-09): ask-to-remember ────────────────────────────────────────

def _s11_turns(**over):
    t = {"say_tea": "Got it — I'll remember that.", "say_keep": "Noted — I'll remember that.",
         "packet_after_say": "- my favourite tea is lapsang souchong",
         "packet_after_keep": "- I can't stand coriander\n- my favourite tea is lapsang souchong",
         "count_once": 7, "count_repeat": 7, "repeat_reply": "I've already got that one.",
         "tea_reply": "Your favourite tea is lapsang souchong.",
         "recall_reply": "You asked me to remember: my favourite tea is lapsang souchong; and I can't stand coriander.",
         "forget_reply": 'Done — I forgot: "I can\'t stand coriander".',
         "packet_final": "- my favourite tea is lapsang souchong"}
    t.update(over)
    return t


def test_s11_passes_the_whole_contract():
    v, ev = sb.score_s11(_s11_turns())
    assert v == "PASS", ev


@pytest.mark.parametrize("over, why", [
    # "I'll remember" with no row behind it (the brain without the tier): the headline failure
    ({"packet_after_say": "- something else"}, "no row"),
    ({"packet_after_keep": "- my favourite tea is lapsang souchong"}, "no row"),
    # narration / more than one sentence / long
    ({"say_tea": "Let me save that for you. Done, I'll remember it."}, "one short sentence"),
    ({"say_keep": "Sure thing! I'll remember that you can't stand coriander, it's a very divisive herb indeed."},
     "one short sentence"),
    ({"say_tea": "Okay."}, "one short sentence"),                      # confirms nothing
    # not idempotent / unreadable count
    ({"count_repeat": 8}, "added rows"),
    ({"count_once": None}, "idempotency is unproven"),
    ({"repeat_reply": "Okay."}, "repeat was not acknowledged"),
    # recall
    ({"tea_reply": "I don't know your favourite tea."}, "fresh session"),
    ({"recall_reply": "You asked me to remember: my favourite tea is lapsang souchong."}, "give both back"),
    # retraction
    ({"packet_final": "- I can't stand coriander\n- my favourite tea is lapsang souchong"}, "left the coriander"),
    ({"packet_final": ""}, "also removed the tea"),
    ({"forget_reply": "Okay."}, "did not say it forgot"),
])
def test_s11_fails_each_broken_part(over, why):
    v, ev = sb.score_s11(_s11_turns(**over))
    assert v == "FAIL" and why in ev["why"], ev


@pytest.mark.parametrize("key", ["packet_after_say", "packet_after_keep", "packet_final"])
def test_s11_unread_packet_is_error_never_pass(key):
    assert sb.score_s11(_s11_turns(**{key: None}))[0] == "ERROR"


def test_s11_in_the_run_passes_and_negative_control_is_red(monkeypatch):
    live, res = _drive(monkeypatch)                      # the tier answers: PASS, a verdict not a target
    assert res["S11"]["verdict"] == "PASS" and "expected" not in res["S11"]
    assert [c for c in live.chats if c.startswith("s11-")] == [
        "s11-say", "s11-keep", "s11-repeat", "s11-fresh-ask", "s11-recall-q", "s11-forget"]
    live, res = _drive(monkeypatch, s11="off")            # ZOE_ASK_TO_REMEMBER off: the brain says it will, stores nothing
    assert res["S11"]["verdict"] == "FAIL" and "no row" in res["S11"]["evidence"]["why"]


def test_s11_partial_run_sends_only_its_own_turns(monkeypatch):
    live, res = _drive(monkeypatch, selected=frozenset({"S11"}))
    assert set(res) == {"S11"} and res["S11"]["verdict"] == "PASS"
    assert all(c.startswith("s11-") for c in live.chats)


def test_s11_unfinished_capture_is_error_never_pass(monkeypatch):
    # PR #1932 review: the three wait_captured() results used to be evidence only; a stalled capture scored PASS
    live, res = _drive(monkeypatch, capture_stalls=True, selected=frozenset({"S11"}))
    assert res["S11"]["verdict"] == "ERROR"
    assert "capture not observed" in res["S11"]["evidence"]["why"]


def test_s11_failed_turn_is_error(monkeypatch):
    live, res = _drive(monkeypatch, seed_errors=["s11-forget"])
    assert res["S11"]["verdict"] == "ERROR"


def test_s11_fixtures_are_synthetic_and_in_the_plan():
    assert "S11" not in sb.EXPECTED
    plan = sb.plan_text(1, frozenset({"S11"}))
    assert "S11 ask-to-remember" in plan and "reserved" not in plan.lower()


# ── S26 / S27 (2026-10-09): pull, not push — "what's up?" delivers once; the orb is a count ──
# Negative controls (each run red before commit): score_s26 accepting a repeat / a missing "nothing
# new" / surfaced != 1; score_s27 accepting words in the state / a non-clearing state / a guest count;
# run_pull reporting PASS when the feature is off (the live server answers the greeting and
# ``enabled: false``).

_PULL1 = "You mentioned you're anxious about a job interview at the aquarium on Friday - how's that going?"
_NOTHING = "Nothing new for you right now."
_CARRY = [{"kind": "open_loop", "carries": True, "surfaced": 1}]
_STATE = {"enabled": True, "count": 1, "top": "question", "quiet": False}
_CLEAR = {"enabled": True, "count": 0, "top": None, "quiet": False}


def test_s26_pass_and_every_failure_mode():
    assert sb.score_s26(_PULL1, _NOTHING, _CARRY)[0] == "PASS"
    assert sb.score_s26("Hi! Not much, how are you?", _NOTHING, _CARRY)[0] == "FAIL"          # no delivery
    assert sb.score_s26(_PULL1, _PULL1, _CARRY)[0] == "FAIL"                                  # delivered again
    v, ev = sb.score_s26(_PULL1, "All quiet on my side.", _CARRY)                              # no "nothing new"
    assert v == "FAIL" and "nothing new" in ev["why"]
    v, ev = sb.score_s26(_PULL1, "Nothing new - though that aquarium interview is still on.", _CARRY)
    assert v == "FAIL" and ev["repeated_second"] and ev["nothing_new_second"]   # says it AND repeats it
    assert sb.score_s26(_PULL1, _NOTHING, [{"carries": True, "surfaced": 2}])[0] == "FAIL"
    assert sb.score_s26(_PULL1, _NOTHING, [{"carries": True, "surfaced": 0}])[0] == "FAIL"
    assert sb.score_s26(_PULL1, _NOTHING, [])[0] == "FAIL"
    assert sb.score_s26(_PULL1, _NOTHING, [{"carries": False, "surfaced": 1}])[0] == "FAIL"


def test_s27_pass_and_every_failure_mode():
    assert sb.score_s27(_STATE, _CLEAR, _CLEAR)[0] == "PASS"
    assert sb.score_s27(None, _CLEAR, _CLEAR)[0] == "FAIL"                                    # route absent
    assert sb.score_s27(_STATE, None, _CLEAR)[0] == "FAIL"
    assert sb.score_s27({**_STATE, "enabled": False}, _CLEAR, _CLEAR)[0] == "FAIL"            # flag off
    assert sb.score_s27({**_STATE, "count": 0}, _CLEAR, _CLEAR)[0] == "FAIL"                  # pending not counted
    assert sb.score_s27({**_STATE, "top": "emotional"}, _CLEAR, _CLEAR)[0] == "FAIL"          # not a coarse class
    v, ev = sb.score_s27({**_STATE, "text": "Job interview at the aquarium"}, _CLEAR, _CLEAR)  # content
    assert v == "FAIL" and ev["content_free"] is False
    assert sb.score_s27({**_STATE, "top": "aquarium interview"}, _CLEAR, _CLEAR)[0] == "FAIL"
    v, ev = sb.score_s27(_STATE, {**_CLEAR, "top": "job interview"}, _CLEAR)                  # words in a cleared state
    assert v == "FAIL" and ev["content_free"] is False
    assert sb.score_s27(_STATE, _STATE, _CLEAR)[0] == "FAIL"                                  # never clears
    assert sb.score_s27(_STATE, _CLEAR, _STATE)[0] == "FAIL"                                  # guest sees a count


def test_s26_s27_are_registered_and_ride_on_s5():
    assert {"S26", "S27"} <= set(sb.SCENARIO_IDS)
    assert sb.seed_closure({"S26"}) == frozenset({"S26", "S5", "S4"})
    assert {"S26", "S27"} <= sb.MULTI_DAY and sb.AXIS_OF["S26"] == "emotional"
    assert "S26" not in sb.EXPECTED and "S27" not in sb.EXPECTED     # real bars, not targets
    assert "S26" in sb.plan_text(1) and "S27" in sb.plan_text(1)


def test_healthy_run_passes_the_pull_scenarios(monkeypatch):
    live, res = _drive(monkeypatch, selector_hook={"enabled": True, "kept": 1})
    assert res["S26"]["verdict"] == "PASS" and res["S27"]["verdict"] == "PASS"
    assert "s26-pull-1" in live.chats and "s26-pull-2" in live.chats
    assert live.chats.index("s26-pull-1") > live.chats.index("s5-open-2")   # after S5/S12 used the raise


def test_the_pull_scenarios_skip_when_the_selector_hook_is_off(monkeypatch):
    live, res = _drive(monkeypatch, selector_hook=None)
    assert res["S26"]["verdict"] == "SKIP" and res["S27"]["verdict"] == "SKIP"
    assert "s26-pull-1" not in live.chats


def test_partial_run_of_s26_runs_the_s5_block_and_reports_only_s26(monkeypatch):
    live, res = _drive(monkeypatch, selector_hook={"enabled": True, "kept": 1},
                       selected=frozenset({"S26"}))
    assert set(res) == {"S26"} and res["S26"]["verdict"] == "PASS"
    assert "d1-worry" in live.chats and "s5-open-1" in live.chats


def test_nothing_to_re_arm_is_a_skip_not_a_pass(monkeypatch):
    monkeypatch.setattr(_ScriptedLive, "rearm", lambda self, user: {"rows": 0, "armed": 0})
    live, res = _drive(monkeypatch, selector_hook={"enabled": True, "kept": 1})
    assert res["S26"]["verdict"] == "SKIP" and "s26-pull-1" not in live.chats


def test_the_feature_off_is_red_not_green(monkeypatch):
    """Control: a server without ZOE_PULL_NOT_PUSH answers the greeting and reports the orb disabled."""
    monkeypatch.setattr(_ScriptedLive, "inbox",
                        lambda self, user: {"enabled": False, "count": 0, "top": None, "quiet": False})
    orig = _ScriptedLive.chat

    def greeting(self, user, tag, message):
        out = orig(self, user, tag, message)
        if tag.startswith("s26-pull"):
            out["reply"] = "Not much! How are you doing?"
        return out
    monkeypatch.setattr(_ScriptedLive, "chat", greeting)
    live, res = _drive(monkeypatch, selector_hook={"enabled": True, "kept": 1})
    assert res["S26"]["verdict"] == "FAIL" and res["S27"]["verdict"] == "FAIL"


def test_a_guest_reading_a_count_turns_s27_red(monkeypatch):
    monkeypatch.setattr(_ScriptedLive, "inbox",
                        lambda self, user: {"enabled": True, "count": 0 if "s26-pull-1" in self.chats else 1,
                                            "top": "question", "quiet": False})
    live, res = _drive(monkeypatch, selector_hook={"enabled": True, "kept": 1})
    assert res["S27"]["verdict"] == "FAIL" and "guest" in res["S27"]["evidence"]["why"]
    assert res["S26"]["verdict"] == "PASS"


def test_a_pull_turn_error_is_an_error_not_a_verdict(monkeypatch):
    live, res = _drive(monkeypatch, selector_hook={"enabled": True, "kept": 1},
                       seed_errors={"s26-pull-2"})
    assert res["S26"]["verdict"] == "ERROR"


def test_the_pull_scenarios_never_touch_a_non_demo_user():
    live = sb.Live("tok", "", "postgresql://x", False)
    with pytest.raises(Exception):
        live.rearm("real-user")
    with pytest.raises(Exception):
        live.inbox("real-user")
