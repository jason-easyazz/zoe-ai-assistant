"""The owner's decide surface (``evolution_inbox.py`` + ``routers/evolution_inbox.py`` + ``zoe-ui/dist/evolution.html``), the honest
MEASURE (``evolution_notice.run_measure_phase``) and the board mirror being opt-in. Real SQL on a SQLite file built by migration 0062
(the ``db`` fixture of ``test_evolution_queue``). Every phrase and id is invented (public repo).

Negative controls (run by hand when this was written; each turned the named test red):
  * drop the ``unauthenticated_override`` refusal in ``_owner``            -> test_only_a_signed_in_admin_person_decides
  * put ``r["title"]`` into a queue card's ``noticed``                     -> test_no_member_words_leave_the_api
  * restore "no target patterns -> validated" in ``measure_verdict``      -> test_measure_never_validates_what_it_did_not_measure
  * drop the after-window coverage proof (``alive_after``)                 -> test_measure_records_unmeasured_..[log_stopped]
  * compare raw counts instead of rates                                    -> test_far_less_traffic_after_is_not_an_improvement
  * drop the ``decision == "approved"`` idempotence branch                -> test_a_verdict_is_recorded_once
  * make ``board_mirror_on`` default true                                  -> test_the_board_mirror_is_off_by_default
  * select approved rows again in ``_backlog``                             -> test_decided_cards_leave_the_list_and_later_comes_back
  * the old past-decisions query (approved / measured only)              -> test_every_verdict_leaves_a_visible_trail
  * cap the key path at ``MAX_IDS`` (500)                                  -> test_a_big_group_is_decided_by_its_key
The page's own controls are mutations of ``js/evolution-page.js`` below (test_page_negative_controls_turn_the_harness_red).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

import evolution_inbox as ei
import evolution_notice
import evolution_queue as eq
from test_evolution_queue import NOW, db, run  # noqa: F401 - ``db`` is the shared fixture

pytestmark = pytest.mark.ci_safe

DAY = 86400.0
WORDS = "please remind me about the dentist on tuesday"
TOKEN = "zoe_tok_" + "planted" * 2          # built at run time: a fixture literal that looks like a credential trips the secret scan
DIST = Path(__file__).resolve().parents[2] / "zoe-ui" / "dist"


def add(db, title, *, age_days=1.0, component="intent_router", kind="intent_miss_cluster", count=3, cls="code", **kw):
    t = NOW - age_days * DAY
    return run(eq.propose(db, source="evolution_notice:" + kind, change_class=cls, component=component, title=title,
                          description=f"User member-a said: {WORDS} {TOKEN}", legacy_evidence=json.dumps({"examples": [WORDS, TOKEN]}),
                          target_patterns=json.dumps([WORDS]), evidence={"signal": {"kind": kind, "miss_count": count}}, now=t, **kw))


@pytest.fixture
def quiet(monkeypatch):
    """No struggle store / failed turns unless a test plants them."""
    import struggle_proposals as sp
    import turn_outcomes

    async def none(*_a, **_k):
        return []

    async def no_turns(*_a, **_k):
        return {"items": []}

    monkeypatch.setattr(sp, "list_proposals", none)
    monkeypatch.setattr(turn_outcomes, "review", no_turns)


# -- the read model --------------------------------------------------------------

def test_grouping_ranking_cap_and_the_stale_bucket(db, quiet):
    a = add(db, "Intent gap: 'Turn on the lights'", count=2)
    b = add(db, "Intent gap: 'turn the lights on!'", count=2)          # same words, other order and case: one card
    strong = add(db, "Intent gap: 'what is on the radio'", count=40)
    for i in range(30):
        add(db, f"Intent gap: 'synthetic request {chr(97 + i % 26)}{chr(97 + i // 26)}'", count=1, age_days=3)
    old = [add(db, f"Intent gap: 'ancient thing {i}'", age_days=90) for i in range(4)]
    out = run(ei.inbox("admin-1", now=NOW))
    assert len(out["cards"]) == ei.CARD_CAP and out["more_cards"] == 32 - ei.CARD_CAP
    ids_of = {c["key"]: run(ei._resolve(c["key"], NOW)) for c in out["cards"]}
    assert ids_of[out["cards"][0]["key"]] == [strong]                               # the strongest evidence first
    pair = next(c for c in out["cards"] if a in ids_of[c["key"]])
    assert ids_of[pair["key"]] == sorted([a, b]) and pair["count"] == 2 and pair["evidence"]["signal_count"] == 4
    assert all("ids" not in c for c in out["cards"])                                 # a card ships its count and key, not ids
    assert out["stale"] == {"count": 4, "days": ei.STALE_DAYS} and out["open"] == 37
    assert not any(set(old) & set(v) for v in ids_of.values())
    assert "only records your decision" in out["approval_does"]


def test_struggle_and_failed_turn_cards_are_included(db, monkeypatch):
    import struggle_proposals as sp
    import turn_outcomes

    async def struggles(**_k):
        return [{"id": "s1", "kind": "router_example", "summary": "Weather questions went to chat 4 times on 3 days", "episodes": 4,
                 "events": 6, "user_days": 3, "first_seen": NOW - 3 * DAY, "last_seen": NOW - DAY}]

    async def turns(viewer, **_k):
        assert viewer == "admin-1"                                                   # the owner's OWN turns only
        return {"items": [{"label_status": "proposed", "proposed_label": "weather", "ts": NOW - DAY, "words": WORDS},
                          {"label_status": "confirmed", "proposed_label": "weather", "ts": NOW, "words": WORDS}]}

    monkeypatch.setattr(sp, "list_proposals", struggles)
    monkeypatch.setattr(turn_outcomes, "review", turns)
    cards = {c["source"]: c for c in run(ei.inbox("admin-1", now=NOW))["cards"]}
    assert cards["struggle"]["key"] == "s-s1" and "router training" in cards["struggle"]["next"]
    assert cards["failed_turns"]["count"] == 1 and cards["failed_turns"]["actions"] == []
    assert WORDS not in json.dumps(cards)


# -- the routes --------------------------------------------------------------------

@pytest.fixture
def client(db, quiet, monkeypatch):
    import auth
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routers import evolution_inbox as router_mod

    who = {"user": {"user_id": "admin-1", "username": "owner", "role": "admin"}}
    app = FastAPI()
    app.include_router(router_mod.router)
    app.dependency_overrides[auth.get_current_user] = lambda: who["user"]
    c = TestClient(app)
    c.who, c.app = who, app
    return c


ROUTES = [("get", "/api/system/evolution/inbox", None), ("post", "/api/system/evolution/decide", {"ids": ["x"], "action": "approve"}),
          ("post", "/api/system/evolution/dismiss-stale", {"stale_days": 30})]


@pytest.mark.parametrize("user", [
    {"user_id": "member-a", "role": "user"}, {"user_id": "kid-1", "role": "child"}, {"user_id": "guest", "role": "guest"},
    {"user_id": "panel-1", "role": "admin", "username": "panel:kitchen", "device_token_role": "panel"},
    {"user_id": "family-admin", "role": "admin", "unauthenticated_override": True},
], ids=["member", "child", "guest", "panel", "unauthenticated-override"])
def test_only_a_signed_in_admin_person_decides(client, db, user):
    pid = add(db, "Intent gap: 'synthetic'")
    client.who["user"] = user
    for method, path, body in ROUTES[:2] + [("post", ROUTES[1][1], {"ids": [pid], "action": "approve"}), ROUTES[2]]:
        r = getattr(client, method)(path, json=body) if body else getattr(client, method)(path)
        assert r.status_code in (401, 403), (path, r.status_code)
    assert db.rows("SELECT status, decision FROM evolution_proposals WHERE id = ?", (pid,))[0] == {"status": "proposed", "decision": ""}


def test_no_session_is_refused(client, monkeypatch):
    import auth

    client.app.dependency_overrides.clear()
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "guest")
    for method, path, body in ROUTES:
        r = getattr(client, method)(path, json=body) if body else getattr(client, method)(path)
        assert r.status_code in (401, 403), (path, r.status_code)


def test_a_verdict_is_recorded_once(client, db):
    a, b = add(db, "Intent gap: 'lights on'"), add(db, "Intent gap: 'on lights'")
    ids = sorted([a, b])
    hist = lambda: db.rows("SELECT decision, actor FROM evolution_proposal_history WHERE decision <> '' ORDER BY at, rowid")
    approve = lambda: client.post("/api/system/evolution/decide", json={"ids": ids, "action": "approve"})
    assert approve().json() == {"action": "approve", "changed": 2, "unchanged": 0, "missing": 0}
    assert approve().json()["changed"] == 0 and len(hist()) == 2                      # idempotent: no second history row
    assert {r["actor"] for r in hist()} == {"admin-1"}
    assert {r["status"] for r in db.rows("SELECT status FROM evolution_proposals")} == {"proposed"}   # approving applies nothing
    later = client.post("/api/system/evolution/decide", json={"ids": ids, "action": "later"}).json()
    assert later["changed"] == 2 and client.post("/api/system/evolution/decide", json={"ids": ids, "action": "later"}).json()["changed"] == 0
    assert client.get("/api/system/evolution/inbox").json()["later"] == 2
    rej = lambda: client.post("/api/system/evolution/decide", json={"ids": ids, "action": "reject", "reason": "not_now"}).json()
    assert rej()["changed"] == 2 and rej()["changed"] == 0
    assert client.post("/api/system/evolution/decide", json={"ids": ["nope"], "action": "reject"}).status_code == 404
    assert client.post("/api/system/evolution/decide", json={"ids": ids, "action": "reject", "reason": WORDS}).status_code == 422


def test_decided_cards_leave_the_list_and_later_comes_back(db, quiet):
    """30 distinct groups, the top 20 decided by key (approve / reject / later): the next read shows the other 10 and none of the
    decided ones (an approved row lives only in ``decided``; a decision does not refresh a card's rank); a "later" card returns only
    after its review date. Negative control: drop ``COALESCE(decision, '') = ''`` from ``_backlog`` -> red."""
    for i in range(30):
        add(db, f"Intent gap: 'synthetic {chr(97 + i % 26)}{chr(97 + i // 26)}x'", count=30 - i, age_days=1)
    first = run(ei.inbox("admin-1", now=NOW))["cards"]
    assert len(first) == 20
    for n, c in enumerate(first):
        action = ("approve", "reject", "later")[n % 3]
        assert run(ei.decide(action, "admin-1", key=c["key"], now=NOW))["changed"] == 1
    top = {c["key"] for c in first}
    again = run(ei.inbox("admin-1", now=NOW + 60, stale_days=365))
    assert len(again["cards"]) == 10 and not top & {c["key"] for c in again["cards"]}
    assert again["later"] == 6 and len(again["decided"]) == 10                       # 7 approved + 7 rejected, newest 10 shown
    assert {d["decision"] or d["status"] for d in again["decided"]} == {"approved", "rejected"}
    back = run(ei.inbox("admin-1", now=NOW + (ei.LATER_DAYS + 1) * DAY, stale_days=365))["cards"]
    later_keys = {c["key"] for n, c in enumerate(first) if n % 3 == 2}
    assert later_keys <= {c["key"] for c in back} and len(back) == 16


def test_a_big_group_is_decided_by_its_key(client, db):
    """600 near-identical proposals are ONE card (count 600, no ids shipped); approving by key changes all 600 (more than the
    500-id body limit), a repeat changes nothing, and the card is gone. Negative control: cap the key path at ``MAX_IDS`` -> red."""
    db.conn.executemany(
        "INSERT INTO evolution_proposals (id, type, title, description, status, proposed_at, change_class, risk_tier, component, "
        "evidence_json, source, decision, decided_by, decision_note, updated_at) VALUES (?, 'intent_pattern', ?, '', 'proposed', ?, "
        "'code', 3, 'intent_router', '{}', 'evolution_notice:intent_miss_cluster', '', '', '', ?)",
        [(f"big-{i:04d}", "Intent gap: 'the same synthetic thing'", time.time(), time.time()) for i in range(600)])   # fresh: not stale
    db.conn.commit()
    (card,) = client.get("/api/system/evolution/inbox").json()["cards"]
    assert card["count"] == 600 and "ids" not in card and len(json.dumps(card)) < 2000
    approve = lambda: client.post("/api/system/evolution/decide", json={"key": card["key"], "action": "approve"}).json()
    assert approve() == {"action": "approve", "changed": 600, "unchanged": 0, "missing": 0}
    assert approve()["changed"] == 0
    assert client.get("/api/system/evolution/inbox").json()["cards"] == []
    assert len(db.rows("SELECT 1 FROM evolution_proposals WHERE decision = 'approved'")) == 600
    bad = client.post("/api/system/evolution/decide", json={"key": "q-' OR 1=1 --", "action": "reject"})
    assert bad.status_code == 422                                                    # a key is a hash, never SQL or free text
    assert client.post("/api/system/evolution/decide", json={"key": "q-" + "0" * 16, "action": "reject"}).json()["changed"] == 0


def test_every_verdict_leaves_a_visible_trail(client, db, monkeypatch):
    """No -> gone from the cards and listed in past decisions as rejected, with who and when; "dismiss old" of 5 rows -> ONE summary
    line; yes -> listed as approved; later -> neither a card nor a past decision while it is put off. Negative control: restore the
    old past-decisions query (approved or measured only) -> the rejected row is lost, red."""
    monkeypatch.setattr(ei.time, "time", lambda: NOW)
    rej, yes, wait = (add(db, f"Intent gap: '{w}'") for w in ("alpha first", "beta second", "gamma third"))
    for i in range(5):
        add(db, f"Intent gap: 'old {chr(97 + i)}x'", age_days=60)
    key = {i: c["key"] for c in client.get("/api/system/evolution/inbox").json()["cards"] for i in run(ei._resolve(c["key"], NOW))}
    for pid, action in ((rej, "reject"), (yes, "approve"), (wait, "later")):
        assert client.post("/api/system/evolution/decide", json={"key": key[pid], "action": action}).json()["changed"] == 1
    assert client.post("/api/system/evolution/dismiss-stale", json={"stale_days": 30}).json()["dismissed"] == 5
    out = client.get("/api/system/evolution/inbox").json()
    assert out["cards"] == [] and out["later"] == 1
    past = {d["id"]: d for d in out["decided"]}
    assert past[rej]["status"] == "rejected" and past[rej]["decided_by"] == "admin-1" and past[rej]["at"] and past[rej]["measured"] is None
    assert past[yes]["decision"] == "approved" and wait not in past
    (swept,) = [d for d in out["decided"] if d.get("kind") == "dismissed_stale"]
    assert swept["count"] == 5 and swept["at"] and len(out["decided"]) == 3


def test_dismiss_old_rejects_only_the_stale_bucket(client, db, monkeypatch):
    monkeypatch.setattr(ei.time, "time", lambda: NOW)
    fresh = add(db, "Intent gap: 'fresh'")
    old = [add(db, f"Intent gap: 'old {i}'", age_days=60) for i in range(3)]
    assert client.post("/api/system/evolution/dismiss-stale", json={"stale_days": 30}).json() == {"dismissed": 3, "days": 30.0}
    st = {r["id"]: r["status"] for r in db.rows("SELECT id, status FROM evolution_proposals")}
    assert st[fresh] == "proposed" and {st[o] for o in old} == {"rejected"}
    notes = db.rows("SELECT actor, note FROM evolution_proposal_history WHERE to_status = 'rejected'")
    assert len(notes) == 3 and {(n["actor"], n["note"]) for n in notes} == {("admin-1", "dismissed: stale")}
    assert client.post("/api/system/evolution/dismiss-stale", json={"stale_days": 30}).json()["dismissed"] == 0


def test_no_member_words_leave_the_api(client, db, monkeypatch):
    monkeypatch.setattr(ei.time, "time", lambda: NOW)
    for k in ("intent_miss_cluster", "user_frustration", "user_issue_report"):
        add(db, f"User said: '{WORDS}' {TOKEN}", kind=k, component="chat")
        add(db, f"User said: '{WORDS}' {TOKEN} again", kind=k, component="chat", age_days=90)
    pid = add(db, f"Intent gap: '{WORDS}'")
    bodies = [client.get("/api/system/evolution/inbox").text,
              client.post("/api/system/evolution/decide", json={"ids": [pid], "action": "approve"}).text,
              client.get("/api/system/evolution/inbox").text,
              client.post("/api/system/evolution/dismiss-stale", json={"stale_days": 30}).text]
    for body in bodies:
        assert "dentist" not in body and TOKEN not in body and "member-a" not in body, body[:300]


# -- MEASURE -----------------------------------------------------------------------

T = evolution_notice.MEASURE_MIN_TURNS


@pytest.mark.parametrize("patterns,before,after,turns,want", [
    ([], 9, 1, (T, T), ("unmeasured", "no_target_signal")),
    (["x"], None, 1, (T, T), ("unmeasured", "no_before_data")),
    (["x"], 9, 1, (T - 1, T), ("unmeasured", "no_before_data")),
    (["x"], 9, None, (T, T), ("unmeasured", "no_after_coverage")),          # the miss log has no record in the after window
    (["x"], 9, 0, (T, None), ("unmeasured", "no_after_coverage")),          # no router coverage after
    (["x"], 9, 0, (T, T - 1), ("unmeasured", "no_after_coverage")),         # too little traffic after
    (["x"], evolution_notice.MEASURE_MIN_BEFORE - 1, 0, (T, T), ("unmeasured", "sample_too_small")),
    (["x"], 10, 8, (100, 100), ("validated", "fewer_misses_per_turn")),
    (["x"], 10, 12, (100, 100), ("regressed", "more_misses_per_turn")),
    (["x"], 10, 10, (100, 100), ("no_change", "within_band")),
    (["x"], 10, 4, (100, 40), ("no_change", "within_band")),               # 4 <= 8 raw, but the same RATE: not an improvement
])
def test_measure_never_validates_what_it_did_not_measure(patterns, before, after, turns, want):
    got = evolution_notice.measure_verdict(patterns, before, after, turns_before=turns[0], turns_after=turns[1])
    assert got == want


W = evolution_notice._MEASURE_WINDOW_S


def _turns(before=100, after=100):
    """Router records: household turns spread over each window, plus one old record so the log covers the before window."""
    recs = [{"ts": NOW - W - 120, "traffic": "harness"}]
    recs += [{"ts": NOW - W + 60 + i * (W - 120) / max(1, before), "traffic": "household"} for i in range(before)]
    recs += [{"ts": NOW + 60 + i * (W - 120) / max(1, after), "traffic": "household"} for i in range(after)]
    return recs


def _applied(db, monkeypatch, tmp_path, lines, *, patterns=(WORDS,), kind="intent_pattern", router=None):
    for name, env, recs in (("misses.jsonl", "ZOE_INTENT_MISS_LOG", lines), ("router.jsonl", "ZOE_ROUTER_HEAD_LOG",
                                                                             _turns() if router is None else router)):
        (tmp_path / name).write_text("".join(json.dumps(x) + "\n" for x in recs))
        monkeypatch.setenv(env, str(tmp_path / name))
    pid = run(eq.propose(db, source="evolution_notice:intent_miss_cluster", change_class="code", component="intent_router",
                         title="Intent gap", legacy_type=kind, target_patterns=json.dumps(list(patterns)), now=NOW - 10 * DAY))
    run(eq.transition(db, pid, "applied", actor="test", now=NOW))
    monkeypatch.setitem(sys.modules, "multica_client", types.SimpleNamespace())          # nothing reaches the board
    out = run(evolution_notice.run_measure_phase())
    row = db.rows("SELECT status, validation_result FROM evolution_proposals WHERE id = ?", (pid,))[0]
    return out, row["status"], json.loads(row["validation_result"])


def miss(ts, text=WORDS):
    return {"ts": ts, "text": text, "traffic_class": "household"}


BEFORE = [miss(NOW - W - 60, "an unrelated old line")] + [miss(NOW - W / 2 + i) for i in range(10)]


def test_measure_end_to_end(db, monkeypatch, tmp_path):
    """Traffic after and ZERO target misses after (the miss writer still alive: an unrelated miss in the window) -> validated."""
    out, status, vr = _applied(db, monkeypatch, tmp_path, BEFORE + [miss(NOW + 60, "an unrelated new line")])
    assert (status, vr["verdict"], vr["misses_before"], vr["misses_after"]) == ("kept", "validated", 10, 0)
    assert (vr["turns_before"], vr["turns_after"]) == (100, 100) and out["applied_due"] == 1 and out["validated"] == 1


@pytest.mark.parametrize("case,reason", [
    ("no_patterns", "no_target_signal"), ("other_kind", "no_target_signal"), ("swept_log", "no_before_data"),
    ("few", "sample_too_small"),
    ("log_stopped", "no_after_coverage"),      # the miss log stops before the deployment: (10, 0) is NOT an improvement
    ("router_stopped", "no_after_coverage"),   # misses still logged, but no household traffic after: nothing to compare
])
def test_measure_records_unmeasured_and_leaves_the_row_applied(db, monkeypatch, tmp_path, case, reason):
    alive = [miss(NOW + 60, "an unrelated new line")]
    lines = {"swept_log": [miss(NOW + 10)], "few": [miss(NOW - W - 60, "x"), miss(NOW - 60)] + alive,
             "log_stopped": BEFORE}.get(case, BEFORE + alive)
    kw = {"no_patterns": {"patterns": ()}, "other_kind": {"kind": "agent_health"},
          "router_stopped": {"router": _turns(after=0)}}.get(case, {})
    out, status, vr = _applied(db, monkeypatch, tmp_path, lines, **kw)
    assert (status, vr["verdict"], vr["reason"], vr["schema"]) == ("applied", "unmeasured", reason, 2)
    assert out["validated"] == 0 and out["unmeasured"] == 1


def test_far_less_traffic_after_is_not_an_improvement(db, monkeypatch, tmp_path):
    """10 misses in 100 turns before, 4 in 40 after: raw counts fell 60 %, the rate did not move -> no_change, never validated."""
    after = [miss(NOW + 600 + i) for i in range(4)]
    out, status, vr = _applied(db, monkeypatch, tmp_path, BEFORE + after, router=_turns(100, 40))
    assert (status, vr["verdict"], vr["misses_after"], vr["turns_after"]) == ("applied", "no_change", 4, 40)


def test_the_decide_page_shows_the_measurement(client, db, monkeypatch, tmp_path):
    _applied(db, monkeypatch, tmp_path, [], kind="agent_health")
    past = client.get("/api/system/evolution/inbox").json()["decided"]
    assert past[0]["measured"] == {"result": "not_measurable", "reason": "no_target_signal"}


# -- PART C: the board mirror and the double run -----------------------------------------

def test_the_board_mirror_is_off_by_default(db, monkeypatch):
    calls = []

    async def sync(**kw):
        calls.append(kw)
        return "issue-1"

    monkeypatch.setitem(sys.modules, "multica_client", types.SimpleNamespace(sync_evolution_proposal_to_multica=sync))
    monkeypatch.delenv(evolution_notice.BOARD_MIRROR_ENV, raising=False)
    run(evolution_notice.record_user_issue("the synthetic timer did not ring", "member-a"))
    assert calls == [] and db.rows("SELECT multica_issue_id FROM evolution_proposals")[0]["multica_issue_id"] is None
    monkeypatch.setenv(evolution_notice.BOARD_MIRROR_ENV, "1")
    run(evolution_notice.record_user_issue("another synthetic report", "member-a"))
    assert len(calls) == 1


def test_the_notice_runs_once_a_night(monkeypatch):
    import multica_autopilot_sync as mas

    async def boom():
        raise AssertionError("the board autopilot ran the notice pass")

    monkeypatch.setitem(sys.modules, "evolution_notice", types.SimpleNamespace(run_evolution_notice=boom))
    assert mas._zoe_task_for_autopilot("Evolution Nightly Notice") is mas._run_evolution_notice
    run(mas._run_evolution_notice())                                                   # a no-op, not the pass
    src = (Path(__file__).resolve().parents[3] / "scripts/night/jobs/night_digest.py").read_text()
    assert "run_evolution_notice" in src                                               # the one caller kept


# -- the page harness ----------------------------------------------------------------

def _node() -> str:
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("node is required on CI to run the decide page harness")
        pytest.skip("Node.js is not installed on this host")
    return node


def _harness(src: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ, **({"EVOLUTION_SRC": str(src)} if src else {}))
    return subprocess.run([_node(), str(DIST / "test_evolution_page.js")], capture_output=True, text=True, timeout=60, env=env)


def test_page_harness_passes():
    proc = _harness()
    assert proc.returncode == 0 and "checks passed" in proc.stdout, proc.stdout + proc.stderr


@pytest.mark.parametrize("old,new", [
    ("if (text !== undefined && text !== null) n.textContent = String(text);",
     "if (text !== undefined && text !== null) n.innerHTML = String(text);"),          # string-built DOM
    ("api('POST', BASE + '/decide', { key, action })", "api('POST', BASE + '/decide', { ids: [key], action })"),
    ("const ACTION_LABEL = { approve: 'Yes, do it', reject: 'No', later: 'Later' };",
     "const ACTION_LABEL = { approve: 'Yes, do it', reject: 'Yes, do it', later: 'Later' };"),
    ("const acts = (c.actions || []).filter(a => ACTION_LABEL[a]);\n        if (acts.length && c.key) {",
     "const acts = Object.keys(ACTION_LABEL);\n        if (acts.length) {"),               # buttons on a card that cannot be decided here
])
def test_page_negative_controls_turn_the_harness_red(tmp_path, old, new):
    src = (DIST / "js" / "evolution-page.js").read_text(encoding="utf-8")
    assert src.count(old) == 1, old
    mutated = tmp_path / "evolution-page.js"
    mutated.write_text(src.replace(old, new), encoding="utf-8")
    assert _harness(mutated).returncode != 0, new
