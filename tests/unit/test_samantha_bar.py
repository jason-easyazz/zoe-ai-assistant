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
    def __init__(self, export_counts, db_left=0, in_flight=0):
        self.export_counts, self.db_left = list(export_counts), db_left
        self.forgot, self.in_flight = [], in_flight

    def packet_count(self, u):
        return 0

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
                 backdate_incomplete=False):
        super().__init__("tok", "", "postgresql://x", False)
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
        reply = {"d1-ask-sister": "Your sister Marisol is flying in from Lisbon.",
                 "d2-ask-dad": "Your dad Teodor is a retired lighthouse keeper.",
                 "b-ask": "I have no idea who is visiting.",
                 "long-ask-sister": "Marisol.", "long-ask-dad": "He kept a lighthouse."}.get(tag, "ok")
        return {"reply": reply, "error": None, "ms": 1, "session": tag}

    def wait_landed(self, user, message, needles, timeout_s=90):
        landed = not (set(needles) & self.unlanded)
        return {"landed": landed, "waited_s": 0}

    def packet(self, user, message):
        return "" if user == B else "- dad Teodor, retired lighthouse keeper; sister Marisol"

    def backdate(self, session_ids, age_s):
        self.backdated.append(list(session_ids))
        if self.backdate_incomplete:
            return {"ok": False, "sessions": len(session_ids), "verified_sessions": 1,
                    "turns": 2, "missing": session_ids[1:]}
        return {"ok": True, "sessions": len(session_ids), "verified_sessions": len(session_ids),
                "turns": 2 * len(session_ids), "missing": []}

    def proactive_hooks(self, user):
        return []

    def evidence(self, turn):
        return {}


def _drive(monkeypatch, backdate=True, **live_kw):
    monkeypatch.setattr(sb, "_ask_judged", lambda live, u, tag, q, n, scorer, sid: ("PASS", {}))
    monkeypatch.setattr(sb.time, "sleep", lambda s: None)
    live = _ScriptedLive(**live_kw)
    res = {r["id"]: r for r in sb.run_scenarios(live, A, B, 1, backdate, lambda m: None)}
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
    assert live.waited_capture == [(A, {"completed": live.chats.index("d2-dad"), "in_flight": 0})]
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

    def run(live, a, b, samples, backdate, log):
        seen["samples"], seen["backdate"] = samples, backdate
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
