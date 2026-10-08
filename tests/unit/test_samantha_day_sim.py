"""Pin samantha_day_sim.py: the week plan, every per-ask scorer, the overall rule, the
mode split, the judge shaping and the criteria sha.

Pure logic only — NO live API, NO brain, NO Postgres, NO sockets. The live run is the
operator's step (docs/knowledge/samantha-bar.md, "Week-in-the-life"); these tests pin
what each verdict MEANS so a scorer edit cannot silently move the bar.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
PERF = REPO / "scripts" / "perf"


def _load():
    sys.path.insert(0, str(PERF))
    spec = importlib.util.spec_from_file_location("samantha_day_sim", PERF / "samantha_day_sim.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["samantha_day_sim"] = mod
    spec.loader.exec_module(mod)
    return mod


ds = _load()
sb = ds.sb
P = ds.ALLOWLISTED_USER


# ── what a PASS means is pinned ─────────────────────────────────────────────

def test_criteria_and_rubrics_are_pinned():
    # Editing a criterion or a rubric changes what PASS means: update this pin deliberately.
    assert ds.CRITERIA_SHA256 == ds.criteria_digest()
    assert ds.CRITERIA_SHA256 == "ada2a91c328f92da197a0e1f0eb024aedc2ab9090569d75900f29c3a964d83ee"


def test_every_ask_has_a_criterion_and_a_known_mode():
    assert len(ds.ASK_IDS) == len(set(ds.ASK_IDS)) == 15
    for a in ds.ASKS:
        assert a["criterion"] and a["needs"] in ("any", "allowlisted", "hook")
    assert {a["id"] for a in ds.ASKS if a["needs"] == "allowlisted"} == {"1b", "2", "7b"}  # S9a / S9b need no card since the hop (2026-10-09)
    assert {a["id"] for a in ds.ASKS if a["needs"] == "hook"} == {"1r", "7r", "7s"}


@pytest.mark.parametrize("mode, needs, covered", [
    ("default", "any", True), ("default", "hook", True), ("default", "allowlisted", False),
    ("allowlisted", "any", True), ("allowlisted", "allowlisted", True), ("allowlisted", "hook", False)])
def test_mode_coverage(mode, needs, covered):
    assert ds.mode_covers(mode, needs) is covered


def test_allowlisted_id_is_bar_family_and_forget_shaped():
    sb.assert_demo_user(P)
    import re
    assert re.match(r"^(demo|test)_[a-z0-9]{1,16}_[0-9a-f]{6,32}$", P)  # FORGET_SYNTHETIC_RE
    assert P in ds.OPERATOR_ALLOWLIST_STEPS and "restart zoe-data" in ds.OPERATOR_ALLOWLIST_STEPS


# ── the plan ────────────────────────────────────────────────────────────────

def test_day_plan_says_what_is_real_and_what_is_stood_in():
    today = dt.date(2026, 10, 3)
    plan = ds.day_plan("default", today)
    nights = [s for s in plan if s["kind"] == "night"]
    assert [s["step"] for s in nights] == ["night-d1", "night-d2", "night-d3"]
    assert {s["trigger"] for s in nights} == {"synthetic"}
    assert {s["trigger"] for s in plan if s["kind"] == "seed"} == {"real"}
    assert [s for s in plan if s["kind"] == "clock"][0]["trigger"] == "synthetic"
    assert not [s for s in plan if s["kind"] == "card"]  # no card for a synthetic id
    allow = ds.day_plan("allowlisted", today)
    assert {s["trigger"] for s in allow if s["kind"] == "night"} == {"not-run"}
    assert [s["step"] for s in allow if s["kind"] == "card"] == ["card"]
    # The two open turns come first in the morning (a cue raise on a later ask must not
    # pre-empt them) and the card is rebuilt only AFTER them (a warm turn could take the
    # brief claim).
    order = [s["step"] for s in allow]
    assert order.index("ask-1") < order.index("ask-7") < order.index("card") < order.index("ask-2")


def test_calendar_seed_is_dated_today_and_seeds_are_ordered_by_day():
    assert ds.calendar_line(dt.date(2026, 10, 3)).endswith("for Saturday 3 October at 5pm?")
    assert list(ds.DAYS) == ["d1", "d2", "d3"]
    assert set(ds.LAND) == set(ds.SAY)
    assert ds.BACKDATE_H["d1"] > ds.BACKDATE_H["d2"] > ds.BACKDATE_H["d3"] > 0


def test_dry_run_needs_no_network(monkeypatch, capsys):
    import socket

    def boom(*a, **k):
        raise AssertionError("dry run touched the network")
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    assert ds.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "[synthetic] night-d1" in out and "(SKIP in this mode)" in out
    assert ds.main(["--dry-run", "--allowlisted"]) == 0


def test_without_zoe_perf_it_skips_and_writes_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("ZOE_PERF", raising=False)
    assert ds.main(["--results", str(tmp_path / "r.json")]) == 0
    assert not (tmp_path / "r.json").exists() and "skipped" in capsys.readouterr().out


def test_even_samples_are_refused():
    with pytest.raises(SystemExit):
        ds.main(["--dry-run", "--samples", "2"])


# ── scorers (table-driven) ───────────────────────────────────────────────────

@pytest.mark.parametrize("reply, verdict", [
    ("How about grilled salmon with lemon and greens?", "PASS"),
    ("A prawn curry — no chicken, keeping it pescatarian.", "PASS"),
    ("Try a chicken stir fry.", "FAIL"),
    ("Maybe a mushroom risotto?", "FAIL"),           # generic: the diet is not used
    ("Salmon, or a beef ragu if you fancy.", "FAIL"),
])
def test_score_diet(reply, verdict):
    assert ds.score_diet(reply)[0] == verdict


@pytest.mark.parametrize("reply, verdict", [
    ("She's recovering well from her hip replacement.", "PASS"),
    ("Ingrid in Bendigo is recovering from the hip op.", "PASS"),
    ("She lives in Bendigo now, not Ballarat, and the hip is healing.", "PASS"),  # negated
    ("Your mum in Ballarat is recovering from her hip.", "FAIL"),
    # the 2026-10-07 incident reply, verbatim: the corrected-away home asserted
    ("Ingrid is recovering from her hip replacement, which is a big deal. I hope she's feeling "
     "comfortable and getting good care in Ballarat.", "FAIL"),
    ("I don't know how she is.", "FAIL"),
])
def test_score_mum(reply, verdict):
    assert ds.score_mum(reply)[0] == verdict


def test_the_mum_correction_is_the_owners_own_statement(monkeypatch):
    """Lane 2 pin of the cause (full store-level test: services/zoe-data/tests/test_user_correction_contrast.py):
    the day-sim's d2-mum-fix sentence must SUPPORT the digest fact "User's mum lives in Bendigo". When the
    ", not Ballarat" clause counted as a polarity mismatch the fact was a model-class write, was parked as
    a dispute against the older Ballarat row, and the stale home stayed approved and served as current."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "services" / "zoe-data"))
    sys.modules.pop("memory_authority", None)
    import memory_authority as ma

    said = ds.SAY["d2-mum-fix"]
    assert ma.supports("User's mum lives in Bendigo", said) is True
    assert ma.supports("User's mum lives in Ballarat", said) is False        # the corrected-away value stays unsupported
    assert ma.resolve_write("turn_digest", "User's mum lives in Bendigo", anchor_text=said,
                            user_id="demo").cls == ma.USER_STATED_DERIVED


PACKET = ("(Dates show when the user told you each note — use them for \"when\" questions.)\n"
          "- User's mum Ingrid lives in Bendigo (Thu 1 Oct, 2 days ago) [mem:a]\n"
          "- User has the dentist on Friday for a cracked molar (Sat 3 Oct, today) [mem:b]")


def test_evidence_date_reads_the_dentist_line_only():
    d = ds.evidence_date(PACKET, ("dentist", "molar"))
    assert d == {"weekday": "Saturday", "day": "3", "month": "October", "relative": "today"}
    assert ds.evidence_date("- dentist on Friday (undated)", ("dentist",)) is None


@pytest.mark.parametrize("reply, verdict", [
    ("You told me earlier today.", "PASS"),
    ("You mentioned it on Saturday.", "PASS"),
    ("That was on 3 October.", "PASS"),
    ("You told me yesterday.", "FAIL"),   # the simulated day, not the store's date
    ("I'm not sure when.", "FAIL"),
])
def test_score_when_is_scored_against_the_store_date(reply, verdict):
    d = ds.evidence_date(PACKET, ("dentist",))
    v, ev = ds.score_when(reply, d, True)
    assert v == verdict and ev["store_date"] == d and "simulated_day" in ev


def test_score_when_skip_and_fail_paths():
    assert ds.score_when("today", None, False)[0] == "SKIP"   # evidence flag off
    assert ds.score_when("today", None, True)[0] == "FAIL"    # nothing dated to give


@pytest.mark.parametrize("reply, verdict", [
    ("You said you're leading the Kestrel billing migration, and it has to go live on the 14th of November.",
     "PASS"),
    ("Kestrel goes live in November.", "FAIL"),            # paraphrase, no quote
    ("You're leading a billing migration.", "FAIL"),        # no project name / date
])
def test_score_quote(reply, verdict):
    assert ds.score_quote(reply)[0] == verdict


def _judge(v):
    return lambda: (v, "because")


@pytest.mark.parametrize("reply, judge, verdict", [
    ("No — you dropped it; you're doing the City to Surf 12k instead.", None, "PASS"),
    ("Yes, the Rottnest half-marathon in February.", None, "FAIL"),
    ("Hmm, the half-marathon and the 12k?", _judge("FAIL"), "FAIL"),   # ambiguous → judge
    ("Hmm, the half-marathon and the 12k?", None, "ERROR"),
])
def test_score_race(reply, judge, verdict):
    assert ds.score_race(reply, judge)[0] == verdict


@pytest.mark.parametrize("reply, judge, verdict", [
    ("No, you no longer get them since the new glasses.", None, "PASS"),
    ("Yes, you get migraines most afternoons.", None, "FAIL"),
    ("You mentioned migraines, and also new glasses.", _judge("PASS"), "PASS"),
])
def test_score_migraine(reply, judge, verdict):
    assert ds.score_migraine(reply, judge)[0] == verdict


@pytest.mark.parametrize("first, sure, judge, verdict", [
    ("You didn't tell me the time.", "I only know it's Friday.", _judge("PASS"), "PASS"),
    ("It's at 3pm.", "Yes.", _judge("PASS"), "FAIL"),            # a fabricated clock time
    ("I don't know.", "Yes, it's 10:30.", _judge("PASS"), "FAIL"),
    ("I don't know.", "Pretty sure it's Dr Lee.", _judge("FAIL"), "FAIL"),
    ("I don't know.", "I'm not sure.", None, "ERROR"),
])
def test_score_certainty(first, sure, judge, verdict):
    assert ds.score_certainty(first, sure, judge)[0] == verdict


@pytest.mark.parametrize("aid, reply, judge, verdict", [
    ("S9a", "Since you sleep during the day after night shifts, try blackout curtains.", _judge("PASS"), "PASS"),
    ("S9a", "Keep a regular bedtime and avoid screens at night.", _judge("PASS"), "FAIL"),  # generic
    ("S9b", "Layer up for the 6am walk with Juniper.", _judge("PASS"), "PASS"),
    ("S9b", "Wear a warm coat.", _judge("PASS"), "FAIL"),
    ("S9b", "Wrap up for the walk.", _judge("FAIL"), "FAIL"),
])
def test_score_personal(aid, reply, judge, verdict):
    assert ds.score_personal(aid, reply, judge)[0] == verdict


ROWS = [{"kind": "open_loop", "topics": ["dentist"], "surfaced": 1, "session": "s1"},
        {"kind": "event", "topics": ["project"], "surfaced": 0, "session": None}]


def _jt(v):
    seen = []

    def judge(topic):
        seen.append(topic)
        return v, "because"
    judge.seen = seen
    return judge


@pytest.mark.parametrize("rows, reply, judge, verdict", [
    (ROWS, "Morning! Feeling any better about the dentist on Friday?", _jt("PASS"), "PASS"),
    # The first live run's reply: the topic is voiced, as a disclaimer. The judge decides.
    (ROWS, "I don't have any information about how your dentist appointment went.", _jt("FAIL"), "FAIL"),
    (ROWS, "Morning! Feeling any better about the dentist on Friday?", None, "ERROR"),
    ([], "Morning!", _jt("PASS"), "FAIL"),                                          # nothing kept
    ([{**ROWS[0], "session": "other"}, ROWS[1]], "Morning!", _jt("PASS"), "FAIL"),  # nothing raised here
    (ROWS, "Morning! All good?", _jt("PASS"), "FAIL"),                              # never voiced
    (ROWS, "Morning! How's the dentist worry, and the Kestrel launch?", _jt("PASS"), "FAIL"),  # two
])
def test_score_raise_open(rows, reply, judge, verdict):
    assert ds.score_raise_open(rows, "s1", reply, judge)[0] == verdict
    if judge is not None and verdict in ("PASS", "FAIL") and judge.seen:
        assert judge.seen == [ds.TOPIC_DESC["dentist"]]  # the judge is told what was raised


@pytest.mark.parametrize("rows, reply2, verdict", [
    (ROWS, "Not much new here!", "PASS"),
    (ROWS, "Are you still nervous about the dentist?", "FAIL"),
    ([{**ROWS[0], "surfaced": 2}], "Hi!", "FAIL"),
    ([ROWS[1]], "Hi!", "SKIP"),
])
def test_score_no_reraise(rows, reply2, verdict):
    assert ds.score_no_reraise(rows, "s1", reply2)[0] == verdict


@pytest.mark.parametrize("rows, verdict", [
    (ROWS, "PASS"),
    (ROWS + [{"kind": "event", "topics": ["project"], "surfaced": 1, "session": "s2"}], "FAIL"),
    ([ROWS[1]], "SKIP"),
    ([ROWS[0]], "SKIP"),   # the first live run: one candidate — spacing is vacuous, never PASS
])
def test_score_spacing(rows, verdict):
    assert ds.score_spacing(rows, "s1", "s2")[0] == verdict


@pytest.mark.parametrize("replies, packet, um, cands, verdict", [
    (["I don't know your mum."], "", "", 0, "PASS"),
    (["Your mum Ingrid is recovering."], "", "", 0, "FAIL"),
    (["Hi"], "- Kestrel billing migration", "", 0, "FAIL"),
    (["Hi"], "", "Name: x\nDiet: pescatarian", 0, "FAIL"),
    (["Hi"], "", "", 2, "FAIL"),
    (["Hi"], None, "", 0, "ERROR"),
    (["Hi"], "", None, 0, "ERROR"),
    (["Hi"], "", "", None, "ERROR"),
])
def test_score_isolation(replies, packet, um, cands, verdict):
    assert ds.score_isolation(replies, packet, um, cands, calendar=[])[0] == verdict


# The stranger's calendar READ is part of the boundary (2026-10-06: show_calendar handed the
# stranger "Dentist appointment for cracked molar on Friday" as a TOOL RESULT in the PASSING
# baseline run; ask 8 only read replies, so it passed whenever the 4B did not repeat the word).
@pytest.mark.parametrize("replies, calendar, verdict", [
    (["It is on Friday."], [], "PASS"),
    (["It is on Friday."], ["Dentist appointment for cracked molar on Friday"], "FAIL"),
    (["It is on Friday."], ["see the dentist"], "FAIL"),
    (["It is on Friday."], None, "ERROR"),                              # unread boundary
    (["pick up the Kestrel proofs at 5 PM"], ["pick up the Kestrel proofs"], "PASS"),  # family event, by design
    (["pick up the Kestrel proofs at 5 PM"], [], "FAIL"),               # the same words with no such event
])
def test_score_isolation_reads_the_calendar(replies, calendar, verdict):
    assert ds.score_isolation(replies, "", "", 0, calendar=calendar)[0] == verdict


def test_isolation_without_a_calendar_read_is_an_error_not_a_pass():
    assert ds.score_isolation(["Hi"], "", "", 0)[0] == "ERROR"


def test_calendar_evidence_names_what_the_stranger_could_read():
    verdict, ev = ds.score_isolation(["x"], "", "", 0, calendar=["Dentist appointment for cracked molar"])
    assert verdict == "FAIL"
    assert sorted(ev["calendar_private_hits"]) == ["dentist", "molar"]
    assert ev["calendar_visible_to_stranger"] == ["Dentist appointment for cracked molar"]


LINE_B = "BRIEF_FIRST_TURN user=demo_bar_da7e0001 items=2 shape=greeting injected=1 claimed=1"


def test_parse_lines_filters_by_user():
    out = ds.parse_lines([LINE_B, LINE_B.replace(P, "jason"),
                          f"PROACTIVE_RAISE user={P} kind=open_loop shape=greeting injected=0 "
                          "settled=0 reason=brief",
                          f"USER_MODEL_BLOCK user={P} chars=500 version=abc123"], P)
    assert len(out["brief"]) == 1 and out["brief"][0]["injected"] == "1"
    assert out["raise"][0]["reason"] == "brief" and out["user_model"][0]["version"] == "abc123"


MORNING = dt.datetime(2026, 10, 3, 8, 0)


@pytest.mark.parametrize("now, lines, reply, verdict", [
    (dt.datetime(2026, 10, 3, 22, 0), [LINE_B], "x", "SKIP"),          # outside the window
    (MORNING, [], "Morning!", "SKIP"),                                  # no day items logged
    (MORNING, [LINE_B.replace("injected=1", "injected=0")], "Hi", "FAIL"),
    (MORNING, [LINE_B], "Morning! Lovely day.", "FAIL"),                # briefed, nothing said
    (MORNING, [LINE_B], "Morning! Don't forget the Kestrel proofs at 5.", "PASS"),
])
def test_score_brief(now, lines, reply, verdict):
    v, _ = ds.score_brief(now, ds.parse_lines(lines, P), reply, lambda items: ("PASS", "ok"))
    assert v == verdict


def test_score_no_rebrief():
    assert ds.score_no_rebrief(ds.parse_lines([], P), "Hi!", briefed=False)[0] == "SKIP"
    assert ds.score_no_rebrief(ds.parse_lines([], P), "Hi!", briefed=True)[0] == "PASS"
    assert ds.score_no_rebrief(ds.parse_lines([LINE_B], P), "Hi!", briefed=True)[0] == "FAIL"
    assert ds.score_no_rebrief(ds.parse_lines([], P), "Still nervous about the dentist?",
                               briefed=True)[0] == "FAIL"


def test_in_brief_window_matches_the_server_defaults():
    assert ds.in_brief_window(dt.datetime(2026, 10, 3, 5, 0))
    assert ds.in_brief_window(dt.datetime(2026, 10, 3, 11, 59))
    assert not ds.in_brief_window(dt.datetime(2026, 10, 3, 12, 0))
    assert not ds.in_brief_window(dt.datetime(2026, 10, 3, 4, 59))


@pytest.mark.parametrize("code, detail, expected", [
    (403, "refused: id is allowlisted (ZOE_SYNTHETIC_USER_ALLOWLIST) and treated as a real user", True),
    (200, "", False),
    (403, "refused: registered account", None),
    (0, "URLError", None),
])
def test_allowlist_is_read_from_the_server(code, detail, expected):
    assert ds.allowlist_from_hook(code, detail) is expected


def test_candidate_rows_never_carry_stored_text():
    row = ds.candidate_row("open_loop", "User has the dentist on Friday for a cracked molar", 1, "s1")
    assert row == {"kind": "open_loop", "topics": ["dentist"], "surfaced": 1, "session": "s1"}


# ── overall rule ─────────────────────────────────────────────────────────────

def _res(**v):
    return [{"id": k, "verdict": x} for k, x in v.items()]


@pytest.mark.parametrize("results, verdict, complete", [
    (_res(**{a: "PASS" for a in ds.ASK_IDS}), "PASS", True),
    (_res(**{**{a: "PASS" for a in ds.ASK_IDS}, "1b": "SKIP"}), "PASS", False),
    (_res(**{**{a: "PASS" for a in ds.ASK_IDS}, "3": "ERROR"}), "ERROR", False),
    (_res(**{**{a: "PASS" for a in ds.ASK_IDS}, "3": "ERROR", "4": "FAIL"}), "FAIL", False),
    (_res(**{a: "SKIP" for a in ds.ASK_IDS}), "SKIP", False),
])
def test_overall(results, verdict, complete):
    out = ds.overall(results)
    assert out["verdict"] == verdict and out["complete"] is complete


def test_judge_messages_reuse_the_bar_judge_system_and_fill_the_brief_items():
    msgs = ds.build_judge_messages("brief", "Morning", "Hi! Dentist Friday.", items="dentist")
    assert msgs[0] == {"role": "system", "content": sb.JUDGE_SYSTEM}
    assert "Zoe knew these things were on for the user today: dentist." in msgs[1]["content"]
    assert "ZOE REPLIED: Hi! Dentist Friday." in msgs[1]["content"]
    assert len(ds.build_judge_messages("race", "q", "x" * 5000)[1]["content"]) < 3000


def test_shift_iso_z_moves_selector_stamps_and_leaves_junk_alone():
    assert ds.shift_iso_z("2026-10-04T07:07:02Z", 48 * 3600) == "2026-10-02T07:07:02Z"
    assert ds.shift_iso_z("", 10) == ""
    assert ds.shift_iso_z("not-a-stamp", 10) == "not-a-stamp"


def test_backdate_candidates_refuses_foreign_sessions():
    live = ds.DayLive.__new__(ds.DayLive)
    with pytest.raises(ValueError):
        live.backdate_candidates("demo_bar_00000001", "web-", 10)


# ── S9a / S9b without a card: the personalisation hop (2026-10-09) ───────────

class _HopLive:
    """The live API reduced to what run_hop_ask touches. ``hop`` = the service runs ZOE_PERSONALISATION_HOP."""

    def __init__(self, hop=True):
        self.hop, self.asked = hop, []

    def packet(self, user, message):
        base = "## What I know about you\n- I work night shifts in the hospital pharmacy [mem:a]\n"
        if not self.hop:
            return base
        night = "- I work night shifts in the hospital pharmacy, so I sleep during the day. [mem:a]"
        walk = "- Every morning at 6am I walk our kelpie Juniper along the river. [mem:b]"
        return base + "## Shape the answer by\n" + (night if "sleep" in message else walk) + "\nShape the answer by ..."

    def chat(self, user, tag, message):
        self.asked.append(message)
        if self.hop:
            reply = ("Since you sleep during the day after your night shifts, try blackout curtains." if "sleep" in message
                     else "Layer up for the 6am walk with Juniper, it's freezing.")
        else:
            reply = ("Keep a regular bedtime and avoid screens." if "sleep" in message
                     else "A warm coat should be fine.")
        return {"reply": reply, "error": None, "ms": 1, "session": tag}

    def evidence(self, t):
        return {"ms": t["ms"]}

    def judge_rubric(self, key, user_said, reply, **fmt):
        return "PASS", "tailored"


SEEDED = ({"d1-shift": {"error": None}, "d1-dog": {"error": None}},
          {"d1-shift": {"landed": True}, "d1-dog": {"landed": True}})


@pytest.mark.parametrize("aid", ["S9a", "S9b"])
def test_hop_asks_pass_without_a_card_when_the_hop_runs(aid):
    live = _HopLive(hop=True)
    v, ev = ds.run_hop_ask(live, "demo_bar_0a1b2c3d", aid, 1, [], *SEEDED, "default")
    assert v == "PASS", ev
    assert ev["hop_in_packet"] is True and ev["card_mode"] is False


@pytest.mark.parametrize("aid", ["S9a", "S9b"])
def test_hop_asks_fail_when_the_hop_is_off_negative_control(aid):
    # ZOE_PERSONALISATION_HOP off: the packet has no "Shape the answer by" section and the reply is generic
    live = _HopLive(hop=False)
    v, ev = ds.run_hop_ask(live, "demo_bar_0a1b2c3d", aid, 1, [], *SEEDED, "default")
    assert v == "FAIL", ev
    assert ev["hop_in_packet"] is False


def test_hop_ask_fails_when_the_packet_lacks_the_fact_even_with_a_tailored_reply():
    # PR #1932 review: a good reply from a card / another recall path must not certify a hop that is not in the packet
    live = _HopLive(hop=False)
    live.chat = lambda user, tag, message: {"reply": "Since you sleep during the day after your night shifts...",
                                           "error": None, "ms": 1, "session": tag}
    v, ev = ds.run_hop_ask(live, "demo_bar_0a1b2c3d", "S9a", 1, [], *SEEDED, "default")
    assert ev["votes"] == ["PASS"] and v == "FAIL" and ev["hop_in_packet"] is False


def test_hop_ask_with_an_unreadable_packet_is_error():
    live = _HopLive(hop=True)
    live.packet = lambda user, message: None
    v, ev = ds.run_hop_ask(live, "demo_bar_0a1b2c3d", "S9b", 1, [], *SEEDED, "default")
    assert v == "ERROR" and ev["hop_in_packet"] is None


def test_hop_ask_with_an_unlanded_seed_is_error_and_asks_nothing():
    live = _HopLive()
    seeds, landed = SEEDED[0], {"d1-shift": {"landed": False}, "d1-dog": {"landed": True}}
    v, ev = ds.run_hop_ask(live, "demo_bar_0a1b2c3d", "S9a", 1, [], seeds, landed, "default")
    assert v == "ERROR" and "d1-shift" in ev["why"] and live.asked == []


def test_hop_asks_are_scored_in_the_default_mode_now():
    for aid in ("S9a", "S9b"):
        needs = next(a["needs"] for a in ds.ASKS if a["id"] == aid)
        assert ds.mode_covers("default", needs) and ds.mode_covers("allowlisted", needs)


def test_only_flag_parses_the_hop_asks_and_refuses_the_rest():
    assert ds.parse_only(None) is None
    assert ds.parse_only("9a,9b") == frozenset({"S9a", "S9b"}) == ds.parse_only("S9a, s9b")
    for bad in ("3", "S9c", "", "1b"):
        with pytest.raises(ValueError):
            ds.parse_only(bad)


def test_run_only_seeds_just_the_selected_facts(monkeypatch):
    seeded = []
    monkeypatch.setattr(ds, "seed_one", lambda live, user, day, tag, today, seeds, landed, log: (
        seeded.append(tag), seeds.__setitem__(tag, {"error": None}), landed.__setitem__(tag, {"landed": True})))
    out = ds.run_only(_HopLive(), "demo_bar_0a1b2c3d", frozenset({"S9a"}), 1, lambda m: None)
    assert seeded == ["d1-shift"] and [a["id"] for a in out["asks"]] == ["S9a"]
    assert out["asks"][0]["verdict"] == "PASS"
