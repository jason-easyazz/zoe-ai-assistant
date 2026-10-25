"""The single evolution proposal queue (``evolution_queue.py``, migration 0062; self-evolution Phase 0).

The store is the REAL SQL on a real SQLite file built by the real migration (0062 creates the table with its 0004 + 0027 shape when it
is missing), so the compare-and-swap writes are exercised, not mocked. Every phrase is invented (public repo).

Negative controls (run by hand when this was written; each turns the named test red):
  * an illegal move accepted        -> add "kept" to ``TRANSITIONS["proposed"]`` (test_every_illegal_move_is_refused_and_changes_nothing)
  * a stale move accepted           -> drop ``AND status = ?`` from ``transition`` (test_a_move_from_a_status_the_row_no_longer_has)
  * a decision on a closed row      -> drop the ``status IN`` guard from ``decide`` (test_a_decision_needs_an_open_proposal)
  * a second writer                 -> put a raw ``UPDATE evolution_proposals SET status`` in any live module (test_one_writer)
  * a legacy writer outside         -> make ``record_user_issue`` INSERT directly (test_a_legacy_writer_lands_in_the_queue)
  * the remap drops a row           -> delete the ``failed`` branch of ``LEGACY_TO_QUEUE`` (test_the_migration_remaps_every_legacy_row)
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import re
import sqlite3
import sys
import types
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import db_pool
import evolution_queue as eq
from db_pool import _Cursor, _ExecResult

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
MIG = SVC / "alembic/versions/0062_evolution_proposal_queue.py"
NOW = 1_790_000_000.0


class _Sqlite:
    """The compat surface the queue and the legacy writers use: ``execute(sql, params)`` (``?``) and ``fetch(sql, *args)`` (``$n``)."""

    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def execute(self, sql, *params):
        if len(params) == 1 and isinstance(params[0], (list, tuple)):
            params = params[0]
        sql = re.sub(r"\$(\d+)", r"?\1", sql)          # asyncpg $n -> sqlite ?n (numbered, order-safe)

        async def _run():
            await asyncio.sleep(0)
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            self.conn.commit()
            return _Cursor(rows, rowcount=cur.rowcount)
        return _ExecResult(_run())

    async def fetch(self, sql, *args):
        cur = self.conn.execute(re.sub(r"\$(\d+)", r"?\1", sql), tuple(args))
        return [dict(r) for r in cur.fetchall()]

    async def commit(self):
        self.conn.commit()

    def rows(self, sql, params=()):
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]


def _mod(path=MIG, name="mig_0062"):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(engine, fn_name, path=MIG):
    mod = _mod(path, "mig_" + path.name[:4])
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(mod, fn_name)()


@pytest.fixture
def db(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    _run(sa.create_engine(f"sqlite:///{path}"), "upgrade")
    store = _Sqlite(path)

    @contextlib.asynccontextmanager
    async def fake_ctx():
        yield store

    monkeypatch.setattr(db_pool, "get_db_ctx", fake_ctx)
    return store


def run(coro):
    return asyncio.run(coro)


def new(db, **kw):
    args = dict(source="test", change_class="harness", component="manner", title="Shorter evening replies", now=NOW)
    args.update(kw)
    return run(eq.propose(db, **args))


def status_of(db, pid):
    return db.rows("SELECT status, decision FROM evolution_proposals WHERE id = ?", (pid,))[0]


def walk(db, pid, path):
    """Move ``pid`` along ``path`` (a list of statuses) through the queue."""
    for to in path:
        run(eq.transition(db, pid, to, actor="test", now=NOW))


PATH_TO = {
    "proposed": [], "probed": ["probed"], "applied": ["applied"], "canary": ["applied", "canary"],
    "kept": ["applied", "kept"], "reverted": ["applied", "reverted"], "rejected": ["rejected"],
}
PAIRS = [(a, b) for a in eq.STATUSES for b in eq.STATUSES]


def test_the_transition_table_is_closed_and_complete():
    assert set(eq.TRANSITIONS) == set(eq.STATUSES)
    assert all(set(v) <= set(eq.STATUSES) for v in eq.TRANSITIONS.values())
    assert eq.TRANSITIONS["proposed"] == {"probed", "applied", "rejected"}
    assert "kept" not in eq.TRANSITIONS["proposed"] and "applied" not in eq.TRANSITIONS["canary"]
    for status in eq.STATUSES:                     # every status is reachable from proposed
        assert status == "proposed" or any(status in eq.TRANSITIONS[s] for s in eq.STATUSES if s != status)


@pytest.mark.parametrize("frm,to", [p for p in PAIRS if eq.legal(*p)])
def test_every_legal_move_is_taken_and_recorded(db, frm, to):
    pid = new(db)
    walk(db, pid, PATH_TO[frm])
    assert run(eq.transition(db, pid, to, actor="admin-1", note="why", now=NOW + 5)) == frm
    assert status_of(db, pid)["status"] == to
    last = db.rows("SELECT from_status, to_status, actor, note FROM evolution_proposal_history WHERE proposal_id = ? ORDER BY at DESC, rowid DESC",
                   (pid,))[0]
    assert (last["from_status"], last["to_status"], last["actor"], last["note"]) == (frm, to, "admin-1", "why")


@pytest.mark.parametrize("frm,to", [p for p in PAIRS if not eq.legal(*p)])
def test_every_illegal_move_is_refused_and_changes_nothing(db, frm, to):
    pid = new(db)
    walk(db, pid, PATH_TO[frm])
    before = (status_of(db, pid), len(db.rows("SELECT 1 FROM evolution_proposal_history WHERE proposal_id = ?", (pid,))))
    with pytest.raises(eq.IllegalTransition):
        run(eq.transition(db, pid, to, actor="test"))
    with pytest.raises(eq.IllegalTransition):      # also when the caller claims the from-status
        run(eq.transition(db, pid, to, actor="test", frm=frm))
    assert (status_of(db, pid), len(db.rows("SELECT 1 FROM evolution_proposal_history WHERE proposal_id = ?", (pid,)))) == before


def test_not_a_status_is_refused(db):
    pid = new(db)
    for bad in ("pending", "approved", "deployed", "validated", "failed", "", "KEPT"):
        with pytest.raises(eq.IllegalTransition):
            run(eq.transition(db, pid, bad, actor="test"))
    assert status_of(db, pid)["status"] == "proposed"


def test_a_move_from_a_status_the_row_no_longer_has(db):
    pid = new(db)
    walk(db, pid, ["applied"])
    with pytest.raises(eq.Conflict):               # the caller read "proposed"; the row is applied now (compare-and-swap)
        run(eq.transition(db, pid, "rejected", actor="test", frm="proposed"))
    with pytest.raises(eq.Conflict):               # the same, with evidence (the read path)
        run(eq.transition(db, pid, "probed", actor="test", frm="proposed", evidence={"after": {"x": 1}}))
    assert status_of(db, pid)["status"] == "applied"
    with pytest.raises(eq.NotFound):
        run(eq.transition(db, "nope", "probed", actor="test"))


def test_require_decision(db):
    pid = new(db)
    with pytest.raises(eq.Conflict):               # an undecided proposal is not applied by a lane that needs an approval
        run(eq.transition(db, pid, "applied", actor="runner", require_decision="approved"))
    run(eq.decide(db, pid, "approved", actor="admin-1"))
    run(eq.transition(db, pid, "applied", actor="runner", require_decision="approved"))
    assert status_of(db, pid) == {"status": "applied", "decision": "approved"}


def test_a_decision_needs_an_open_proposal_and_its_guard(db):
    pid = new(db, change_class="code", component="intent_router")
    run(eq.decide(db, pid, "approved", actor="admin-1", guard={"decision": ""}))
    with pytest.raises(eq.Conflict):               # the guard moved: someone decided first
        run(eq.decide(db, pid, "approved", actor="admin-2", guard={"decision": ""}))
    with pytest.raises(eq.Conflict):               # the autonomy contract the gate read is not what is stored
        run(eq.decide(db, pid, "approved", actor="admin-2", guard={"autonomy_class": "execute"}))
    with pytest.raises(ValueError):
        run(eq.decide(db, pid, "approved", actor="x", guard={"status": "proposed"}))
    with pytest.raises(ValueError):
        run(eq.decide(db, pid, "maybe", actor="x"))
    walk(db, pid, ["applied"])
    with pytest.raises(eq.Conflict):
        run(eq.decide(db, pid, "", actor="admin-1"))
    assert status_of(db, pid) == {"status": "applied", "decision": "approved"}


def test_a_reopen_starts_clean(db):
    pid = new(db)
    run(eq.decide(db, pid, "approved", actor="admin-1", note="looks right"))
    walk(db, pid, ["applied", "reverted"])
    run(eq.transition(db, pid, "proposed", actor="loop", note="recurred"))
    row = db.rows("SELECT status, decision, decided_by, reviewed_at, decision_note FROM evolution_proposals WHERE id = ?", (pid,))[0]
    assert row == {"status": "proposed", "decision": "", "decided_by": "", "reviewed_at": None, "decision_note": "recurred"}


def test_propose_validates_what_enters(db):
    with pytest.raises(ValueError):
        new(db, change_class="prompt")
    with pytest.raises(ValueError):                # a code change is only ever the maintainer PR process
        new(db, change_class="code", risk_tier=0)
    with pytest.raises(ValueError):
        new(db, risk_tier=4)
    with pytest.raises(eq.IllegalTransition):      # nothing enters the queue already applied
        new(db, status="applied")
    with pytest.raises(ValueError):
        new(db, evidence={"words": "an utterance"})
    with pytest.raises(ValueError):
        new(db, evidence={"diff": "x" * (eq.DIFF_MAX + 1)})
    with pytest.raises(ValueError):
        new(db, title="  ")
    assert db.rows("SELECT COUNT(*) AS n FROM evolution_proposals")[0]["n"] == 0
    pid = new(db, status="probed", evidence={"before": {"S3": "FAIL"}, "after": {"S3": "PASS"}, "controls": {"sycophancy": "red"}})
    row = db.rows("SELECT * FROM evolution_proposals WHERE id = ?", (pid,))[0]
    assert (row["status"], row["change_class"], row["risk_tier"], row["component"], row["source"]) == ("probed", "harness", 1, "manner", "test")
    assert json.loads(row["evidence_json"])["after"] == {"S3": "PASS"}
    assert row["type"] == "harness:manner"


def test_evidence_merges_key_by_key(db):
    pid = new(db, evidence={"before": {"S3": "FAIL"}})
    run(eq.attach_evidence(db, pid, {"after": {"S3": "PASS"}}))
    run(eq.transition(db, pid, "probed", actor="loop", evidence={"controls": {"planted": "red"}}))
    got = run(eq.get(pid))["evidence"]
    assert got == {"before": {"S3": "FAIL"}, "after": {"S3": "PASS"}, "controls": {"planted": "red"}}
    with pytest.raises(ValueError):
        run(eq.attach_evidence(db, pid, {"transcript": "..."}))


def test_the_read_api(db):
    a = new(db, title="A")
    b = new(db, title="B", change_class="code", component="intent_router")
    c = new(db, title="C", change_class="release", component="llama.cpp")
    walk(db, b, ["rejected"])
    walk(db, c, ["probed", "applied", "canary", "kept"])
    assert [p["id"] for p in run(eq.list_queue(status="proposed"))] == [a]
    assert [p["id"] for p in run(eq.list_queue(change_class="release"))] == [c]
    assert [p["id"] for p in run(eq.list_queue(component="intent_router"))] == [b]
    with pytest.raises(ValueError):
        run(eq.list_queue(status="pending"))
    one = run(eq.get(c))
    assert set(one) == {"id", "class", "risk_tier", "component", "status", "decision", "title", "source", "evidence", "lesson_id",
                        "proposed_at", "updated_at", "decided_at", "decided_by", "applied_at"}
    assert one["status"] == "kept" and one["applied_at"] == NOW
    assert [h["to"] for h in run(eq.history(c))] == ["proposed", "probed", "applied", "canary", "kept"]
    s = run(eq.summary(now=NOW + 60))
    assert s["by_class"]["harness"]["proposed"] == 1 and s["by_class"]["code"]["rejected"] == 1 and s["by_class"]["release"]["kept"] == 1
    assert s["open"] == 1 and s["total"] == 3 and s["recent"] == {"kept": 1, "reverted": 0}
    assert s["oldest_open_age_days"] == 0.0


def test_delete_respects_the_source(db):
    mine = new(db, source="struggle")
    theirs = new(db, source="evolution_notice:user_issue_report", change_class="code", component="chat")
    run(eq.remove(db, [mine, theirs], source="struggle"))
    assert [r["id"] for r in db.rows("SELECT id FROM evolution_proposals")] == [theirs]
    assert not db.rows("SELECT 1 FROM evolution_proposal_history WHERE proposal_id = ?", (mine,))


def test_legacy_status_names_round_trip():
    for legacy, (statuses, decision) in eq.LEGACY_STATUS.items():
        for status in statuses:                   # the exact inverse: every status a legacy name covers renders as that name
            assert eq.legacy_status(status, decision or "") == legacy
    for status in eq.STATUSES:
        for decision in eq.DECISIONS:
            name = eq.legacy_status(status, decision)
            statuses, want = eq.LEGACY_STATUS[name]
            assert status in statuses and want in (None, decision), (status, decision)
    assert eq.legacy_status("canary") == "deployed" and eq.legacy_status("probed") == "pending"


# -- old writers land in the queue -----------------------------------------------

def test_a_legacy_writer_lands_in_the_queue(db, monkeypatch):
    import evolution_notice

    async def no_board(**_kw):
        return None

    monkeypatch.setitem(sys.modules, "multica_client", types.SimpleNamespace(sync_evolution_proposal_to_multica=no_board))
    run(evolution_notice.record_user_issue("the timer did not ring", "member-a"))
    run(evolution_notice.record_user_issue("the timer did not ring", "member-a"))         # deduplicated by title while open
    rows = db.rows("SELECT * FROM evolution_proposals")
    assert len(rows) == 1
    row = rows[0]
    assert (row["status"], row["decision"], row["change_class"], row["risk_tier"], row["component"], row["source"]) == (
        "proposed", "", "code", 3, "chat", "evolution_notice:user_issue_report")
    assert json.loads(row["evidence_json"]) == {"signal": {"kind": "user_issue_report"}}       # counts only: the words stay in the legacy column
    assert json.loads(row["evidence"])["signal"]["source"] == "evolution_notice:user_issue_report"
    assert [h["to_status"] for h in db.rows("SELECT to_status FROM evolution_proposal_history")] == ["proposed"]
    walk(db, row["id"], ["rejected"])
    run(evolution_notice.record_user_issue("the timer did not ring", "member-a"))         # a rejected one does not block a new report
    assert len(db.rows("SELECT 1 FROM evolution_proposals")) == 2


# -- the migration -------------------------------------------------------------

LEGACY = [("pending", "intent_pattern"), ("approved", "user_frustration"), ("deployed", "agent_health"), ("validated", "intent_pattern"),
          ("failed", "user_issue_report"), ("rejected", "security_improvement"), ("weird", "code_improvement"), (None, "intent_pattern")]
STRUGGLE = [("S1", "router_example", "proposed", "", "", None), ("S2", "intent_gap", "accepted", "admin-1", "other", NOW),
            ("S3", "answer_quality", "rejected", "admin-1", "not_now", NOW), ("S4", "stt_term", "applied", "admin-1", "applied: pr1", NOW)]


def _q(engine, sql):
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.exec_driver_sql(sql)]


def _legacy_db(tmp_path):
    """A pre-0062 box: the 0004 + 0027 table with every legacy status (and two odd ones), and 0057 struggle proposals."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE evolution_proposals (id TEXT PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL, "
            "evidence TEXT, target_patterns TEXT, status TEXT DEFAULT 'pending', multica_issue_id TEXT, proposed_at REAL NOT NULL, "
            "reviewed_at REAL, deployed_at REAL, validation_result TEXT, next_review_at REAL, autonomy_class TEXT NOT NULL DEFAULT 'prepare', "
            "approval_required TEXT NOT NULL DEFAULT '[]', risk TEXT NOT NULL DEFAULT 'medium')")
        for i, (st, typ) in enumerate(LEGACY):
            conn.exec_driver_sql("INSERT INTO evolution_proposals (id, type, title, description, status, proposed_at) VALUES (?, ?, ?, ?, ?, ?)",
                                 (f"L{i}", typ, f"t{i}", "d", st, NOW + i))
    _run(engine, "upgrade", SVC / "alembic/versions/0057_struggle_proposals.py")
    with engine.begin() as conn:
        for pid, kind, st, by, note, at in STRUGGLE:
            conn.exec_driver_sql(
                "INSERT INTO struggle_proposals (id, fingerprint, kind, status, summary, decided_by, decision_note, decided_at, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (pid, "fp-" + pid, kind, st, "Three episodes on two days.", by, note, at,
                                                                      NOW - 10, NOW - 5))
    return engine


def test_the_migration_remaps_every_legacy_row_and_folds_the_struggle_proposals(tmp_path):
    engine = _legacy_db(tmp_path)
    _run(engine, "upgrade")
    rows = dict((r["id"], r) for r in _q(engine, "SELECT * FROM evolution_proposals"))
    expect = {"L0": ("proposed", ""), "L1": ("proposed", "approved"), "L2": ("applied", ""), "L3": ("kept", ""), "L4": ("reverted", ""),
              "L5": ("rejected", ""), "L6": ("rejected", ""), "L7": ("proposed", "")}
    assert dict((k, (rows[k]["status"], rows[k]["decision"])) for k in expect) == expect
    assert rows["L6"]["decision_note"] == "legacy status: weird"            # kept, and the odd value is recorded
    assert all((rows[k]["change_class"], rows[k]["risk_tier"], rows[k]["source"]) == ("code", 3, "legacy") for k in expect)
    assert [rows[k]["component"] for k in ("L0", "L1", "L2", "L5", "L6")] == ["intent_router", "chat", "brain", "security", "code_improvement"]
    hist = dict((r["proposal_id"], r) for r in _q(engine, "SELECT * FROM evolution_proposal_history"))
    assert (hist["L4"]["to_status"], hist["L4"]["note"], hist["L4"]["actor"]) == ("reverted", "legacy status: failed", "migration:0062")
    folded = dict((k, (rows[k]["status"], rows[k]["decision"], rows[k]["change_class"], rows[k]["risk_tier"], rows[k]["component"],
                       rows[k]["source"])) for k in ("S1", "S2", "S3", "S4"))
    assert folded == {"S1": ("proposed", "", "harness", 1, "router", "struggle"),
                      "S2": ("proposed", "approved", "code", 3, "intent_router", "struggle"),
                      "S3": ("rejected", "", "harness", 1, "brain", "struggle"),
                      "S4": ("applied", "approved", "harness", 1, "stt", "struggle")}
    assert (rows["S2"]["decided_by"], rows["S2"]["reviewed_at"], rows["S2"]["decision_note"]) == ("admin-1", NOW, "other")
    with engine.connect() as conn:
        cols = set(r[1] for r in conn.exec_driver_sql("PRAGMA table_info(struggle_proposals)"))
    assert not cols & {"status", "decided_at", "decided_by", "decision_note"}, "the status lives in ONE place"
    import struggle_proposals as sp                                      # the module view reads the folded rows back
    view = dict((r[0], dict(zip(sp._PCOLS, tuple(r)))) for r in _q_raw(engine, f"SELECT {sp._SEL} FROM {sp._FROM}"))
    assert dict((k, v["status"]) for k, v in view.items()) == {"S1": "proposed", "S2": "accepted", "S3": "rejected", "S4": "applied"}
    assert view["S2"]["decided_by"] == "admin-1" and view["S4"]["decision_note"] == "applied: pr1"
    _run(engine, "upgrade")                                               # rerun: a no-op
    assert len(_q(engine, "SELECT id FROM evolution_proposals")) == 12 and len(_q(engine, "SELECT id FROM evolution_proposal_history")) == 12


def _q_raw(engine, sql):
    with engine.connect() as conn:
        return [tuple(r) for r in conn.exec_driver_sql(sql)]


def test_the_downgrade_gives_every_status_back(tmp_path):
    engine = _legacy_db(tmp_path)
    _run(engine, "upgrade")
    _run(engine, "downgrade")
    _run(engine, "downgrade")                                             # rerun: a no-op
    rows = dict((r["id"], r["status"]) for r in _q(engine, "SELECT id, status FROM evolution_proposals"))
    assert rows == {"L0": "pending", "L1": "approved", "L2": "deployed", "L3": "validated", "L4": "failed", "L5": "rejected",
                    "L6": "weird", "L7": None}
    sp_rows = dict((r["id"], (r["status"], r["decided_by"], r["decision_note"], r["decided_at"]))
                   for r in _q(engine, "SELECT * FROM struggle_proposals"))
    assert sp_rows == {"S1": ("proposed", "", "", None), "S2": ("accepted", "admin-1", "other", NOW),
                       "S3": ("rejected", "admin-1", "not_now", NOW), "S4": ("applied", "admin-1", "applied: pr1", NOW)}
    with engine.connect() as conn:
        cols = set(r[1] for r in conn.exec_driver_sql("PRAGMA table_info(evolution_proposals)"))
        tables = set(r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'"))
    assert not cols & {"change_class", "risk_tier", "component", "evidence_json", "lesson_id", "source", "decision"}
    assert "evolution_proposal_history" not in tables


def test_the_migration_and_the_module_agree():
    mig = _mod()
    assert mig.revision == "0062" and tuple(mig.STATUSES) == eq.STATUSES and tuple(mig.CLASSES) == eq.CLASSES
    assert dict(mig.LEGACY_COMPONENT) == eq.LEGACY_COMPONENT
    import struggle_proposals as sp
    assert dict((k, v[0]) for k, v in sp.QUEUE_CLASS.items()) == {"router_example": "harness", "intent_gap": "code",
                                                                  "answer_quality": "harness", "stt_term": "harness"}
    assert "WHEN 'router_example' THEN 'router'" in MIG.read_text() and sp.QUEUE_CLASS["router_example"][1] == "router"


def test_one_head():
    revs, downs = set(), set()
    for f in (SVC / "alembic/versions").glob("*.py"):
        src = f.read_text()
        r = re.search(r'^revision = "(\w+)"', src, re.M)
        d = re.search(r'^down_revision = "(\w+)"', src, re.M)
        if r:
            revs.add(r.group(1))
        if d:
            downs.add(d.group(1))
    assert _mod().down_revision in revs
    assert len(revs - downs) == 1, f"more than one head: {sorted(revs - downs)}"


# -- one writer -----------------------------------------------------------------

_WRITE = re.compile(r"(INSERT\s+INTO\s+evolution_proposals\b|DELETE\s+FROM\s+evolution_proposals\b|INSERT\s+INTO\s+evolution_proposal_history"
                    r"|UPDATE\s+evolution_proposals\s+SET\b[^\"']*?\b(status|decision)\s*=)", re.I)


def _live_sources():
    repo = SVC.parents[1]
    for base in (SVC, repo / "scripts", repo / "tools"):
        for path in base.rglob("*.py"):
            parts = set(path.relative_to(base).parts)
            if parts & {"tests", "alembic", "__pycache__", "node_modules"}:
                continue
            yield path


def test_one_writer():
    """Only ``evolution_queue.py`` inserts a proposal, deletes one, writes a status or a decision, or records history. Other modules
    may still set data columns (``multica_issue_id``, ``next_review_at``, ``validation_result``)."""
    offenders = []
    for path in _live_sources():
        if path == SVC / "evolution_queue.py":
            continue
        text = path.read_text(errors="replace")
        for m in _WRITE.finditer(text):
            offenders.append(f"{path.name}: {m.group(0)[:60]}")
    assert offenders == []
    assert _WRITE.search('"UPDATE evolution_proposals SET status=$1 WHERE id=$2"'), "control: the pattern catches a raw status write"
    assert _WRITE.search('"""UPDATE evolution_proposals\n   SET decision=\'approved\'"""'), "control: across lines"
    assert not _WRITE.search('"UPDATE evolution_proposals SET multica_issue_id=$1 WHERE id=$2"')


# -- the routes ------------------------------------------------------------------

@pytest.fixture
def client(db, monkeypatch):
    system = pytest.importorskip("routers.system")
    import auth
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    who = {"user": {"user_id": "admin-1", "username": "admin", "role": "admin"}}
    app = FastAPI()
    app.include_router(system.router)
    app.include_router(system._agent_card_router)
    app.dependency_overrides[auth.require_admin] = lambda: who["user"]
    app.dependency_overrides[auth.get_current_user] = lambda: who["user"]
    c = TestClient(app)
    c.who = who
    return c


def test_the_read_api_route(client, db):
    pid = new(db)
    r = client.get("/api/system/evolution/queue?status=proposed")
    assert r.status_code == 200 and [p["id"] for p in r.json()["proposals"]] == [pid] and r.json()["summary"]["open"] == 1
    assert client.get("/api/system/evolution/queue?status=pending").status_code == 400      # the queue words only
    assert client.get("/api/system/evolution/queue/summary").json()["by_class"]["harness"]["proposed"] == 1
    item = client.get(f"/api/system/evolution/queue/{pid}").json()
    assert item["status"] == "proposed" and [h["to"] for h in item["history"]] == ["proposed"]
    assert client.get("/api/system/evolution/queue/nope").status_code == 404
    client.who["user"] = {"user_id": "panel-1", "username": "panel:kitchen", "device_token_role": "panel"}
    assert client.get("/api/system/evolution/queue").status_code == 403                  # a panel is not a person


def test_the_legacy_routes_move_through_the_queue(client, db):
    a = new(db, change_class="code", component="chat", source="evolution_notice:user_issue_report")
    b = new(db, change_class="code", component="chat", source="evolution_notice:user_issue_report")
    s = new(db, source="struggle", component="router")
    listed = client.get("/api/agent/evolution/proposals?status=pending").json()
    assert sorted(p["id"] for p in listed["proposals"]) == sorted([a, b])                 # struggle rows are decided elsewhere
    assert all(p["status"] == "pending" and p["queue_status"] == "proposed" for p in listed["proposals"])
    walk(db, b, ["probed"])                                                                 # a probed row is still "pending"
    assert sorted(p["id"] for p in client.get("/api/agent/evolution/proposals?status=pending").json()["proposals"]) == sorted([a, b])
    walk(db, b, ["applied"])
    act = lambda pid, action: client.post(f"/api/agent/evolution/proposals/{pid}/action", json={"action": action})
    assert act(a, "reject").status_code == 200 and status_of(db, a)["status"] == "rejected"
    assert act(a, "reject").status_code == 409                                             # rejected is not open
    assert act(a, "deploy").status_code == 409                                             # rejected -> applied is illegal
    assert act(b, "deploy").status_code == 409 and status_of(db, b)["status"] == "applied"    # already applied
    assert act(b, "defer").status_code == 409                                              # applied is not open
    assert act(s, "reject").status_code == 409 and status_of(db, s)["status"] == "proposed"
    assert client.get("/api/agent/evolution/proposals?status=deployed").json()["count"] == 1
    assert client.get("/api/agent/evolution/proposals?status=nonsense").json() == {"proposals": [], "count": 0}


def test_approve_is_a_decision_and_a_retry_reopens(client, db, monkeypatch):
    """Approving changes no status (a decision on an open row); a rejected (board-cancelled) proposal comes back only with the
    explicit ``retry: true``, as a REOPEN through the queue. Nothing reaches Multica or Hermes (both faked)."""
    import routers.system as system

    async def no_board(*_a, **_k):
        return None

    async def reviewed(_proposal):
        return True, "APPROVED"

    monkeypatch.setitem(sys.modules, "multica_client", types.SimpleNamespace(
        sync_evolution_proposal_to_multica=no_board, update_multica_issue_on_proposal_status_change=no_board))
    monkeypatch.setattr(system, "_hermes_review_proposal", reviewed)
    pid = new(db, change_class="code", component="chat", source="evolution_notice:user_issue_report")
    act = lambda body: client.post(f"/api/agent/evolution/proposals/{pid}/action", json=body)
    assert act({"action": "approve"}).status_code == 200
    assert status_of(db, pid) == {"status": "proposed", "decision": "approved"}                  # approving applies nothing
    assert act({"action": "reject"}).status_code == 200 and status_of(db, pid)["status"] == "rejected"
    assert act({"action": "approve"}).status_code == 409                                          # no silent un-reject
    assert act({"action": "approve", "retry": True}).status_code == 200
    assert status_of(db, pid) == {"status": "proposed", "decision": "approved"}
    notes = [r["note"] for r in db.rows("SELECT note FROM evolution_proposal_history WHERE proposal_id = ? ORDER BY at, rowid", (pid,))]
    assert "reopened for retry" in notes


def test_a_move_and_its_history_are_one_transaction():
    """A status move, a decision or an insert and its history row commit together: on a connection with transactions the queue opens
    one around both, so a failed history insert takes the move back with it (cross-review of #2165). Negative control: make
    ``_atomic`` return ``contextlib.nullcontext()`` -> the move is left outside any transaction -> red."""
    log = []

    class _Tx:
        async def __aenter__(self):
            log.append("begin")

        async def __aexit__(self, et, ev, tb):
            log.append("rollback" if et else "commit")
            return False

    class _Cur:
        rowcount = 1

        async def fetchall(self):
            return []

    class _Db:
        def transaction(self):
            return _Tx()

        async def execute(self, sql, params=()):
            if sql.startswith("INSERT INTO evolution_proposal_history"):
                log.append("history")
                raise RuntimeError("history write failed")
            log.append(sql.split()[0].lower())
            return _Cur()

    with pytest.raises(RuntimeError):
        run(eq.propose(_Db(), source="t", change_class="harness", component="manner", title="x"))
    assert log == ["begin", "insert", "history", "rollback"]
    log.clear()
    with pytest.raises(RuntimeError):
        run(eq.transition(_Db(), "p1", "probed", actor="a", frm="proposed"))
    assert log == ["begin", "update", "history", "rollback"]
    log.clear()
    with pytest.raises(RuntimeError):
        run(eq.decide(_Db(), "p1", "approved", actor="a"))
    assert log == ["begin", "update", "history", "rollback"]


def test_a_retry_that_is_deployed_again_is_measured_again(db):
    """A measured failure leaves a validation_result; the retry's next deploy must clear it, else run_measure_phase (which selects
    ``validation_result IS NULL``) never measures the second attempt. Negative control: drop the reset in transition() -> red."""
    pid = new(db)
    run(eq.decide(db, pid, "approved", actor="admin-1"))
    walk(db, pid, ["applied"])
    db.conn.execute("UPDATE evolution_proposals SET validation_result = ? WHERE id = ?", ('{"verdict": "failed"}', pid))
    db.conn.commit()
    walk(db, pid, ["reverted", "proposed"])
    run(eq.decide(db, pid, "approved", actor="admin-1"))
    walk(db, pid, ["applied"])
    row = db.rows("SELECT status, validation_result FROM evolution_proposals WHERE id = ?", (pid,))[0]
    assert row == {"status": "applied", "validation_result": None}


def test_the_legacy_miss_rate_evaluator_leaves_struggle_proposals_alone(db, monkeypatch):
    """A struggle proposal has no target_patterns; the no-pattern branch would auto-'keep' it with zero misses, reporting an
    unmeasured change as validated. Negative control: drop the source filter in run_measure_phase -> red."""
    import evolution_notice
    monkeypatch.setitem(sys.modules, "multica_client", types.SimpleNamespace(
        update_multica_issue_on_proposal_status_change=lambda *a, **k: None))
    ids = {}
    for src in ("struggle", "evolution_notice:intent_miss_cluster"):
        pid = new(db, source=src)
        run(eq.decide(db, pid, "approved", actor="admin-1"))
        walk(db, pid, ["applied"])
        ids[src] = pid
    db.conn.execute("UPDATE evolution_proposals SET deployed_at = 1000.0")
    db.conn.commit()
    got = run(evolution_notice.run_measure_phase())
    assert got["evaluated"] == 1
    assert status_of(db, ids["struggle"])["status"] == "applied"
    assert status_of(db, ids["evolution_notice:intent_miss_cluster"])["status"] == "kept"
