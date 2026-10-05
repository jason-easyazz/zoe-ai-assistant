"""scripts/maintenance/restore_memories_from_export.py — the July-2026 owner-memory restore.

Synthetic export + synthetic palace only (no household text). Each guarantee has a negative control.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts" / "maintenance"))
import restore_memories_from_export as rest  # noqa: E402
from memory_service import MemoryService  # noqa: E402

OWNER = "jason"
IDS = list(rest.LOST_IDS)
EXPORT_NAME = "jason-mem-synthetic.json"


def _text(i: int) -> str:
    return f"Synthetic restore fixture number {i} about a fictional pet"


def _row(i: int, **meta_over):
    meta = {"user_id": OWNER, "wing": OWNER, "status": "approved", "source": "conversation", "memory_type": "person",
            "confidence": 0.7, "added_at": f"2026-07-05T0{i % 9}:10:00.123456Z", "session_id": "telegram-630xxxx",
            "user_turn_id": f"turn-{i}", "visibility": "personal"}
    if i % 2:
        meta.update(entity_type="person_pending", entity_id=f"pending:fixture-{i}")
    meta.update(meta_over)
    return {"id": IDS[i], "text": _text(i), "src": meta["source"], "type": meta["memory_type"], "meta": meta}


@pytest.fixture
def export(tmp_path):
    rows = [_row(i) for i in range(20)] + [{"id": "zoe_jason_keepme", "text": "not selected", "src": "x", "type": "fact",
                                            "meta": {"user_id": OWNER}}]
    path = tmp_path / EXPORT_NAME
    path.write_text(json.dumps(rows))
    return str(path)


def _palace_db(path, drawers, audit=()):
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE collections (id TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE segments (id TEXT PRIMARY KEY, scope TEXT, collection TEXT);
        CREATE TABLE embeddings (id INTEGER PRIMARY KEY, segment_id TEXT, embedding_id TEXT);
        CREATE TABLE embedding_metadata (id INTEGER, key TEXT, string_value TEXT, int_value INTEGER,
                                         float_value REAL, bool_value INTEGER);
        INSERT INTO collections VALUES ('c1','mempalace_drawers');
        INSERT INTO segments VALUES ('s1','METADATA','c1');
        INSERT INTO collections VALUES ('c2','mempalace_audit');
        INSERT INTO segments VALUES ('s2','METADATA','c2');
    """)
    for n, (seg, eid, md) in enumerate([("s1", e, m) for e, m in drawers] + [("s2", e, m) for e, m in audit], 1):
        c.execute("INSERT INTO embeddings VALUES (?,?,?)", (n, seg, eid))
        for k, v in md.items():
            c.execute("INSERT INTO embedding_metadata VALUES (?,?,?,?,?,?)", (n, k, str(v), None, None, None))
    c.commit()
    c.close()


@pytest.fixture
def palace(tmp_path):
    d = tmp_path / "palace"
    d.mkdir()
    _palace_db(str(d / "chroma.sqlite3"), [("zoe_jason_unrelated", {"user_id": OWNER, "status": "approved",
                                                                     "chroma:document": "something else entirely"})])
    return str(d)


class _Col:
    """In-memory chroma stand-in: just enough for ingest (get by ids, upsert)."""
    def __init__(self):
        self.store = {}

    def get(self, ids=None, where=None, include=None):
        got = [i for i in (ids or []) if i in self.store]
        return {"ids": got, "documents": [self.store[i][0] for i in got], "metadatas": [self.store[i][1] for i in got]}

    def upsert(self, ids, documents, metadatas, embeddings=None):
        for i, d, m in zip(ids, documents, metadatas):
            self.store[i] = (d, dict(m))


def _svc(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path / "scratch"))
    svc._rows, svc._audit = _Col(), _Col()
    svc._collection = lambda: svc._rows
    svc._audit_collection = lambda: svc._audit
    return svc


def _drawers_of(svc):
    return [{"eid": i, "user_id": m.get("user_id"), "status": m.get("status"), mla_doc(): d, **m}
            for i, (d, m) in svc._rows.store.items()]


def mla_doc():
    return rest.mla.DOC_KEY


# ── plan / dry run ───────────────────────────────────────────────────────────────────────────

def test_selects_exactly_the_20_listed_rows(export, palace):
    plan = rest.build_plan(rest.load_export(export), rest.read_palace(palace))
    assert [p["id"] for p in plan] == IDS and len(plan) == 20
    assert {p["status"] for p in plan} == {"restorable"}


def test_dry_run_prints_shapes_only_and_writes_nothing(export, palace, capsys):
    before = Path(palace, "chroma.sqlite3").read_bytes()
    assert rest.main(["--export", export, "--palace", palace]) == 0
    out = capsys.readouterr().out
    assert "would restore: 20" in out and "DRY RUN" in out
    assert IDS[0][-8:] in out and rest.sha10(_text(0)) in out
    assert "Synthetic restore fixture" not in out, "no memory text may be printed"
    assert Path(palace, "chroma.sqlite3").read_bytes() == before


def test_refuses_a_row_that_exists_by_id(export, tmp_path):
    d = tmp_path / "p2"
    d.mkdir()
    _palace_db(str(d / "chroma.sqlite3"), [(IDS[3], {"user_id": OWNER, "status": "approved", "chroma:document": "other words"})])
    plan = rest.build_plan(rest.load_export(export), rest.read_palace(str(d)))
    assert plan[3]["status"] == "refused_exists_by_id"
    assert sum(1 for p in plan if p["status"] == "restorable") == 19         # control: the rest are untouched


@pytest.mark.parametrize("status", ["approved", "superseded", "archived"])
def test_refuses_a_row_whose_exact_text_exists_for_the_owner_in_any_status(export, tmp_path, status):
    d = tmp_path / "p3"
    d.mkdir()
    _palace_db(str(d / "chroma.sqlite3"), [("zoe_jason_other_id", {"user_id": OWNER, "status": status,
                                                                   "chroma:document": f"  {_text(5)}  "})])
    plan = rest.build_plan(rest.load_export(export), rest.read_palace(str(d)))
    assert plan[5]["status"] == f"refused_exists_by_text:{status}"


def test_control_the_same_text_under_another_user_is_not_a_duplicate(export, tmp_path):
    d = tmp_path / "p4"
    d.mkdir()
    _palace_db(str(d / "chroma.sqlite3"), [("zoe_other_x", {"user_id": "someone-else", "status": "approved",
                                                            "chroma:document": _text(5)})])
    assert rest.build_plan(rest.load_export(export), rest.read_palace(str(d)))[5]["status"] == "restorable"


def test_a_wrong_export_is_rejected_loudly(tmp_path, palace):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([_row(0)]))                                    # 19 expected ids missing
    with pytest.raises(SystemExit, match="not in the export"):
        rest.build_plan(rest.load_export(str(bad)), rest.read_palace(palace))


def test_a_row_for_another_user_is_refused(tmp_path, palace):
    rows = [_row(i) for i in range(20)]
    rows[7]["meta"]["user_id"] = "someone-else"
    path = tmp_path / "e.json"
    path.write_text(json.dumps(rows))
    plan = rest.build_plan(rest.load_export(str(path)), rest.read_palace(palace))
    assert plan[7]["status"] == "refused_not_owner_or_empty"


# ── apply ────────────────────────────────────────────────────────────────────────────────────

def _run(plan, tmp_path, svc, **kw):
    return asyncio.run(rest.apply_plan(plan, EXPORT_NAME, str(tmp_path), svc=svc, **kw))


def test_round_trip_restores_through_ingest_with_provenance(export, palace, tmp_path):
    svc = _svc(tmp_path)
    plan = rest.build_plan(rest.load_export(export), rest.read_palace(palace))
    assert _run(plan, tmp_path, svc) == {"restored": 20, "refused": 0}
    assert len(svc._rows.store) == 20
    doc, md = next(v for v in svc._rows.store.values() if v[0] == _text(1))
    assert md["source"] == "operator_restore" and md["added_by"] == "operator_restore"
    assert md["status"] == "approved" and md["user_id"] == OWNER and md["memory_type"] == "person"
    assert md["added_at"] == "2026-07-05T01:10:00.123456Z", "the ORIGINAL capture instant, not now"
    import datetime
    assert md["added_ts"] == pytest.approx(
        datetime.datetime(2026, 7, 5, 1, 10, 0, 123456, tzinfo=datetime.timezone.utc).timestamp(), abs=1e-3)
    assert md["candidate_origin"] == "july_export_restore" and md["candidate_authority_class"] == "user_stated"
    assert md["candidate_restored_from_export"] == EXPORT_NAME and md["candidate_restored_from_id"] == IDS[1]
    assert md["candidate_restored_from_source"] == "conversation"
    assert md["candidate_restored_from_added_at"] == "2026-07-05T01:10:00.123456Z"
    assert md["candidate_restored_from_user_turn_id"] == "turn-1"
    assert md["session_id"] == "telegram-630xxxx" and md["user_turn_id"] == "turn-1"
    assert md["entity_type"] == "person_pending" and md["entity_id"] == "pending:fixture-1"
    # one ingest audit row + one explicit restore audit row per memory, actor operator_restore
    meta = [m for _d, m in svc._audit.store.values()]
    assert sum(1 for m in meta if m["action"] == "ingest" and m["actor"] == "operator_restore") == 20
    restores = [m for m in meta if m["action"] == "restore"]
    assert len(restores) == 20 and all(m["actor"] == "operator_restore" and EXPORT_NAME in m["reason"] for m in restores)
    assert all("Synthetic restore fixture" not in json.dumps(m) for m in restores), "restore rows carry no text"


def test_a_second_apply_restores_zero(export, palace, tmp_path):
    svc = _svc(tmp_path)
    exp = rest.load_export(export)
    first = rest.build_plan(exp, rest.read_palace(palace))
    assert _run(first, tmp_path, svc)["restored"] == 20
    # re-plan against what is now in the palace (exact text + user exists for every row)
    second = rest.build_plan(exp, _drawers_of(svc))
    assert {p["status"] for p in second} == {"refused_exists_by_origin_id:approved"}
    assert _run(second, tmp_path, svc) == {"restored": 0, "refused": 20}
    assert len(svc._rows.store) == 20


class _DupCol(_Col):
    """A collection that, unlike chroma, APPENDS on every upsert: if a guard does not stop the second write,
    the row count (and the row's status) shows it."""
    def __init__(self):
        super().__init__()
        self.log = []

    def upsert(self, ids, documents, metadatas, embeddings=None):
        for i, d, m in zip(ids, documents, metadatas):
            self.log.append(i)
            self.store[i] = (d, dict(m))


def _fresh_svc(tmp_path, rows, audit):
    """A new MemoryService (EMPTY ``_seen_keys``, as after a restart) over existing collections."""
    svc = MemoryService(data_dir=str(tmp_path / "scratch"))
    svc._rows, svc._audit = rows, audit
    svc._collection = lambda: svc._rows
    svc._audit_collection = lambda: svc._audit
    return svc


def _stale_plan_then_reapply(export, palace, tmp_path, disable_guard):
    rows, audit = _DupCol(), _DupCol()
    first = rest.build_plan(rest.load_export(export), rest.read_palace(palace))
    assert _run(first, tmp_path, _fresh_svc(tmp_path, rows, audit))["restored"] == 20
    victim = next(iter(rows.store))
    doc, md = rows.store[victim]
    rows.store[victim] = (doc, {**md, "status": "archived", "reviewed_by": "jason", "review_note": "kept out"})
    stale = rest.build_plan(rest.load_export(export), rest.read_palace(palace))   # still thinks 20 are missing
    svc = _fresh_svc(tmp_path, rows, audit)
    assert not svc._seen_keys, "a fresh service has no in-memory idempotency cache"
    if disable_guard:
        svc._get_sync = lambda _id: None
    return victim, rows, _run(stale, tmp_path, svc)


def test_even_a_stale_plan_cannot_double_restore(export, palace, tmp_path):
    """The DURABLE dedup guard (not the in-memory ``_seen_keys`` cache, which a fresh service lacks) stops a
    stale plan from re-writing a restored, since-reviewed row."""
    victim, rows, res = _stale_plan_then_reapply(export, palace, tmp_path, disable_guard=False)
    assert res["restored"] == 0
    assert len(rows.log) == 20, "no second write reached the collection"
    status = rows.store[victim][1]
    assert status["status"] == "archived" and status["reviewed_by"] == "jason" and status["review_note"] == "kept out"


def test_negative_control_without_the_durable_guard_the_stale_plan_clobbers_the_reviewed_row(export, palace, tmp_path):
    """Proves the test above can go red: with ``_get_sync`` disabled the second apply writes 20 more rows and
    resets the reviewed row to approved."""
    victim, rows, res = _stale_plan_then_reapply(export, palace, tmp_path, disable_guard=True)
    assert res["restored"] == 20 and len(rows.log) == 40
    assert rows.store[victim][1]["status"] == "approved" and "reviewed_by" not in rows.store[victim][1]


def test_skip_gate_rejects_leaves_rows_the_gate_would_reject(export, palace, tmp_path):
    svc = _svc(tmp_path)
    plan = rest.build_plan(rest.load_export(export), rest.read_palace(palace))
    plan[2]["gate"] = "would_reject:weather_report"
    assert _run(plan, tmp_path, svc, skip_gate_rejects=True) == {"restored": 19, "refused": 1}
    assert _run(plan[2:3], tmp_path, _svc(tmp_path))["restored"] == 1          # control: default restores all


def test_restore_never_writes_a_raw_collection_row(export, palace, tmp_path):
    """Every drawer in the store arrived through MemoryService.ingest: it carries ingest's idempotency key."""
    svc = _svc(tmp_path)
    _run(rest.build_plan(rest.load_export(export), rest.read_palace(palace)), tmp_path, svc)
    assert all(m.get("idempotency_key") for _d, m in svc._rows.store.values())
    import ast
    tree = ast.parse(Path(rest.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            assert not any("chromadb" in (getattr(n, "name", "") or "") for n in node.names), "no direct chroma access"
            assert "chromadb" not in (getattr(node, "module", "") or "")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in ("upsert", "add", "update", "delete", "delete_collection", "executescript"), node.func.attr


# ── ingest(captured_at) itself ───────────────────────────────────────────────────────────────

def test_captured_at_sets_the_capture_time_and_garbage_falls_back_to_now(tmp_path):
    md = MemoryService._build_metadata(user_id=OWNER, source="s", session_id=None, user_turn_id=None,
                                       memory_type="fact", confidence=0.7, status="approved", tags=[],
                                       entity_type=None, entity_id=None, expires_at=None,
                                       captured_at="2026-07-05T02:03:04+08:00")
    assert md["added_at"] == "2026-07-04T18:03:04Z" and md["last_accessed"] == md["added_at"]
    now_md = MemoryService._build_metadata(user_id=OWNER, source="s", session_id=None, user_turn_id=None,
                                           memory_type="fact", confidence=0.7, status="approved", tags=[],
                                           entity_type=None, entity_id=None, expires_at=None,
                                           captured_at="not a date")
    assert now_md["added_at"].startswith("20") and not now_md["added_at"].startswith("2026-07-04T18")


# ── the CLI refusals ─────────────────────────────────────────────────────────────────────────

def test_apply_needs_both_acknowledgements(export, palace, capsys, monkeypatch):
    monkeypatch.setattr(rest, "service_up", lambda: None)
    monkeypatch.setattr(rest, "apply_plan", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    assert rest.main(["--export", export, "--palace", palace, "--apply"]) == 2
    assert rest.main(["--export", export, "--palace", palace, "--apply", "--i-have-reviewed"]) == 2
    assert rest.main(["--export", export, "--palace", palace, "--apply", "--i-stopped-zoe-data"]) == 2
    assert "refusing --apply without" in capsys.readouterr().err


def test_apply_refuses_while_zoe_data_is_up(export, palace, capsys, monkeypatch):
    monkeypatch.setattr(rest, "service_up", lambda: "127.0.0.1:8000 is accepting connections")

    async def boom(*a, **k):
        raise AssertionError("wrote while the service was up")

    monkeypatch.setattr(rest, "apply_plan", boom)
    code = rest.main(["--export", export, "--palace", palace, "--apply", "--i-have-reviewed", "--i-stopped-zoe-data"])
    assert code == 3 and "accepting connections" in capsys.readouterr().err


def test_control_apply_proceeds_when_the_service_is_down(export, palace, capsys, monkeypatch):
    monkeypatch.setattr(rest, "service_up", lambda: None)
    calls = []

    async def fake_apply(plan, name, pal, **k):
        calls.append(len(plan))
        return {"restored": 20, "refused": 0}

    monkeypatch.setattr(rest, "apply_plan", fake_apply)
    assert rest.main(["--export", export, "--palace", palace, "--apply", "--i-have-reviewed", "--i-stopped-zoe-data"]) == 0
    assert calls == [20] and "APPLIED: restored=20" in capsys.readouterr().out


def test_service_up_detects_a_listening_port(monkeypatch):
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    real = socket.create_connection
    _unit(monkeypatch, "inactive", 3)
    monkeypatch.setattr(rest.socket, "create_connection", lambda addr, timeout=1: real(("127.0.0.1", port), timeout=timeout))
    try:
        assert "accepting connections" in rest.service_up()
    finally:
        srv.close()
    assert rest.service_up() is None                                   # control: nothing listening any more


# ── fail-closed service check (finding 1) ────────────────────────────────────────────────────

def _unit(monkeypatch, state, rc, *, port_open=False):
    """Stub ``systemctl --user is-active`` (stdout ``state``, exit ``rc``) and the 127.0.0.1:8000 probe."""
    class _R:
        returncode, stdout, stderr = rc, state + ("\n" if state else ""), ""

    monkeypatch.setattr(rest.subprocess, "run", lambda *a, **k: _R())
    if not port_open:
        monkeypatch.setattr(rest.socket, "create_connection",
                            lambda *a, **k: (_ for _ in ()).throw(ConnectionRefusedError("closed")))


@pytest.mark.parametrize("state,rc", [("activating", 3), ("deactivating", 3), ("reloading", 0), ("active", 0),
                                      ("", 1), ("unknown", 4)])
def test_service_check_fails_closed_on_every_state_but_inactive_or_failed(monkeypatch, state, rc):
    _unit(monkeypatch, state, rc)                                            # port closed in every case
    assert rest.service_up(), f"unit state {state!r} with the port closed must still refuse"


@pytest.mark.parametrize("exc", [OSError("no systemctl"), rest.subprocess.TimeoutExpired("systemctl", 10)])
def test_service_check_refuses_when_systemctl_cannot_answer(monkeypatch, exc):
    monkeypatch.setattr(rest.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(exc))
    monkeypatch.setattr(rest.socket, "create_connection",
                        lambda *a, **k: (_ for _ in ()).throw(ConnectionRefusedError("closed")))
    assert "cannot be read" in rest.service_up()


@pytest.mark.parametrize("state,rc", [("inactive", 3), ("failed", 3)])
def test_control_service_check_passes_only_when_inactive_or_failed_and_the_port_is_closed(monkeypatch, state, rc):
    _unit(monkeypatch, state, rc)
    assert rest.service_up() is None


def test_apply_refuses_through_main_while_the_unit_is_activating(export, palace, capsys, monkeypatch):
    _unit(monkeypatch, "activating", 3)

    async def boom(*a, **k):
        raise AssertionError("wrote while the unit was activating")

    monkeypatch.setattr(rest, "apply_plan", boom)
    code = rest.main(["--export", export, "--palace", palace, "--apply", "--i-have-reviewed", "--i-stopped-zoe-data"])
    assert code == 3 and "'activating'" in capsys.readouterr().err


# ── undated / unparseable / future rows (findings 2 and 5) ───────────────────────────────────

def _export_with(tmp_path, index, **meta_over):
    rows = [_row(i) for i in range(20)]
    rows[index]["meta"].update(meta_over)
    for k in [k for k, v in meta_over.items() if v is None]:
        rows[index]["meta"].pop(k)
    path = tmp_path / "e.json"
    path.write_text(json.dumps(rows))
    return rest.load_export(str(path))


@pytest.mark.parametrize("added_at", [None, "", "5 July 2026 10:41", "yesterday"])
def test_an_undated_row_is_refused_not_restored_as_today(tmp_path, palace, added_at):
    exp = _export_with(tmp_path, 6, added_at=added_at)
    svc = _svc(tmp_path)
    plan = rest.build_plan(exp, rest.read_palace(palace))
    assert plan[6]["status"] == "refused_undated"
    assert sum(1 for p in plan if p["status"] == "restorable") == 19         # control: dated rows are untouched
    assert _run(plan, tmp_path, svc) == dict(restored=19, refused=1)
    assert all(m["candidate_restored_from_id"] != IDS[6] for _d, m in svc._rows.store.values())


def test_allow_undated_restores_the_row_and_the_provenance_says_so(tmp_path, palace):
    exp = _export_with(tmp_path, 6, added_at=None)
    svc = _svc(tmp_path)
    plan = rest.build_plan(exp, rest.read_palace(palace), allow_undated=True)
    assert plan[6]["status"] == "restorable"
    assert _run(plan, tmp_path, svc) == dict(restored=20, refused=0)
    marked = [m for _d, m in svc._rows.store.values() if m.get("candidate_restored_undated") is True]
    assert [m["candidate_restored_from_id"] for m in marked] == [IDS[6]], "exactly the undated row is marked"
    assert sum(1 for _d, m in svc._rows.store.values() if "candidate_restored_undated" in m) == 1


def test_the_allow_undated_flag_reaches_the_plan_through_main(tmp_path, palace, capsys):
    rows = [_row(i) for i in range(20)]
    rows[2]["meta"].pop("added_at")
    path = tmp_path / "e2.json"
    path.write_text(json.dumps(rows))
    assert rest.main(["--export", str(path), "--palace", palace]) == 0
    assert "would restore: 19" in capsys.readouterr().out
    assert rest.main(["--export", str(path), "--palace", palace, "--allow-undated"]) == 0
    assert "would restore: 20" in capsys.readouterr().out


def test_a_row_ingest_would_date_wrongly_is_not_counted_as_restored(export, palace, tmp_path):
    """Post-ingest assertion: if the stored ``added_at`` is not the planned instant the row is reported."""
    plan = rest.build_plan(rest.load_export(export), rest.read_palace(palace))
    plan[0]["captured_at"] = "2099-01-01T00:00:00"                          # ingest ignores a future instant
    res = _run(plan, tmp_path, _svc(tmp_path))
    assert res == dict(restored=19, refused=0, misdated=1) and plan[0]["status"] == "restored_misdated"


def test_a_future_dated_row_is_refused_and_the_bound_is_five_minutes(tmp_path, palace):
    import datetime
    now = datetime.datetime(2026, 10, 5, 12, 0, 0)
    for delta, expected in [(datetime.timedelta(minutes=4), "restorable"),
                            (datetime.timedelta(minutes=6), "refused_future_date"),
                            (datetime.timedelta(days=30), "refused_future_date")]:
        exp = _export_with(tmp_path, 4, added_at=(now + delta).isoformat() + "Z")
        assert rest.build_plan(exp, rest.read_palace(palace), now=now)[4]["status"] == expected, delta
    # --allow-undated is about MISSING dates only: a future date stays refused
    exp = _export_with(tmp_path, 4, added_at="2099-01-01T00:00:00Z")
    assert rest.build_plan(exp, rest.read_palace(palace), allow_undated=True)[4]["status"] == "refused_future_date"


def _build(captured_at):
    return MemoryService._build_metadata(user_id=OWNER, source="s", session_id=None, user_turn_id=None,
                                         memory_type="fact", confidence=0.7, status="approved", tags=[],
                                         entity_type=None, entity_id=None, expires_at=None, captured_at=captured_at)


def test_ingest_warns_naming_the_shape_never_the_value_when_captured_at_is_unusable(caplog):
    import logging
    secret_like = "5th of July 2026 about my secret pet"
    for bad in (secret_like, "2099-01-01T00:00:00Z"):
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="memory_service"):
            md = _build(bad)
        msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert len(msgs) == 1 and "captured_at ignored" in msgs[0] and f"len={len(bad)}" in msgs[0]
        assert bad not in msgs[0] and "secret pet" not in msgs[0], "the value itself is never logged"
        assert not md["added_at"].startswith("2099"), "an unusable instant falls back to now"


def test_control_a_usable_captured_at_logs_nothing(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="memory_service"):
        md = _build("2026-07-05T02:03:04Z")
    assert md["added_at"] == "2026-07-05T02:03:04Z" and not caplog.records


def test_ingest_end_to_end_ignores_a_future_captured_at_with_a_warning(tmp_path, caplog):
    import logging
    svc = _svc(tmp_path)
    with caplog.at_level(logging.WARNING, logger="memory_service"):
        ref = asyncio.run(svc.ingest("Synthetic future-dated fixture", user_id=OWNER, source="operator_restore",
                                     captured_at="2099-01-01T00:00:00Z"))
    assert ref is not None and not ref.metadata["added_at"].startswith("2099")
    assert any("future_date" in r.getMessage() for r in caplog.records)


# ── re-run protection by origin id, and the forget tombstone (finding 4) ─────────────────────

def test_a_row_restored_earlier_is_refused_by_its_origin_id_even_after_its_text_was_edited(export, palace, tmp_path):
    svc = _svc(tmp_path)
    exp = rest.load_export(export)
    assert _run(rest.build_plan(exp, rest.read_palace(palace)), tmp_path, svc)["restored"] == 20
    drawers = _drawers_of(svc)
    for d in drawers:                                                        # every restored row was later edited
        d[mla_doc()] = "edited later: " + d[mla_doc()]
    plan = rest.build_plan(exp, drawers)
    assert set(p["status"] for p in plan) == set(["refused_exists_by_origin_id:approved"])
    assert _run(plan, tmp_path, svc) == dict(restored=0, refused=20)


def test_control_an_unrelated_row_with_a_different_origin_id_does_not_block(export, tmp_path):
    d = tmp_path / "p5"
    d.mkdir()
    md = dict(user_id=OWNER, status="approved", candidate_restored_from_id="zoe_jason_some_other_row")
    md["chroma:document"] = "different words"
    _palace_db(str(d / "chroma.sqlite3"), [("zoe_jason_x", md)])
    plan = rest.build_plan(rest.load_export(export), rest.read_palace(str(d)))
    assert set(p["status"] for p in plan) == set(["restorable"])


def _forgotten_palace(tmp_path, *, name, user=OWNER, action="delete_user_done", when="2026-09-01T10:00:00.5Z"):
    d = tmp_path / name
    d.mkdir()
    row = dict(user_id=user, action=action, timestamp=when, mempalace_id=f"{action}:abc")
    _palace_db(str(d / "chroma.sqlite3"), [], audit=[(f"{action}:abc", row)])
    return str(d)


def test_a_forget_newer_than_the_export_is_detected_and_blocks_apply(export, tmp_path, monkeypatch, capsys):
    pal = _forgotten_palace(tmp_path, name="f1")
    assert rest.forgotten_since_export(rest.read_audit(pal), rest.load_export(export)) == 1
    monkeypatch.setattr(rest, "service_up", lambda: None)

    async def boom(*a, **k):
        raise AssertionError("restored after a forget")

    monkeypatch.setattr(rest, "apply_plan", boom)
    code = rest.main(["--export", export, "--palace", pal, "--apply", "--i-have-reviewed", "--i-stopped-zoe-data"])
    out = capsys.readouterr()
    assert code == 4 and "--after-forget" in out.err and "FORGET TOMBSTONE" in out.out


def test_after_forget_lets_the_operator_proceed_deliberately(export, tmp_path, monkeypatch):
    pal = _forgotten_palace(tmp_path, name="f2")
    monkeypatch.setattr(rest, "service_up", lambda: None)
    calls = []

    async def fake_apply(plan, name, pal_, **k):
        calls.append(len(plan))
        return dict(restored=20, refused=0)

    monkeypatch.setattr(rest, "apply_plan", fake_apply)
    args = ["--export", export, "--palace", pal, "--apply", "--i-have-reviewed", "--i-stopped-zoe-data"]
    assert rest.main(args + ["--after-forget"]) == 0
    assert calls == [20]


@pytest.mark.parametrize("kw", [
    dict(when="2026-07-01T00:00:00Z"),          # forgotten BEFORE the export's newest row: nothing to resurrect
    dict(user="someone-else"),                  # another user's forget
    dict(action="delete_user"),                 # an intent row only: the delete never completed
    dict(action="ingest"),
])
def test_control_other_audit_rows_do_not_block(export, tmp_path, kw):
    pal = _forgotten_palace(tmp_path, name="f3", **kw)
    assert rest.forgotten_since_export(rest.read_audit(pal), rest.load_export(export)) == 0


def test_control_no_audit_collection_at_all_does_not_block(export, palace):
    assert rest.forgotten_since_export(rest.read_audit(palace), rest.load_export(export)) == 0


def test_a_forget_cannot_be_compared_without_an_export_date_so_it_blocks():
    exp = dict(x=dict(id="x", meta=dict()))
    audit = [dict(action="delete_user_done", user_id=OWNER, timestamp="2026-01-01T00:00:00Z")]
    assert rest.forgotten_since_export(audit, exp) == 1


# ── importance (the "also check") ────────────────────────────────────────────────────────────

def test_a_restored_row_carries_importance_exactly_as_a_normal_ingest_does(tmp_path):
    """Missing ``importance`` is NOT a disadvantage: ``_build_metadata`` writes it only when score > 0 and the
    restore goes through the same ingest, so restored and ordinary rows are identical on this key."""
    high = "Synthetic fixture: the fictional pet has a penicillin allergy"
    plain = "Synthetic fixture: the fictional pet likes long walks"
    rows = [_row(i) for i in range(20)]
    rows[0]["text"], rows[1]["text"] = high, plain
    path = tmp_path / "imp.json"
    path.write_text(json.dumps(rows))
    restored, normal = _svc(tmp_path), _svc(tmp_path)
    _run(rest.build_plan(rest.load_export(str(path)), []), tmp_path, restored)
    for t in (high, plain):
        asyncio.run(normal.ingest(t, user_id=OWNER, source="conversation", memory_type="person"))
    got = dict((d, m.get("importance")) for d, m in restored._rows.store.values() if d in (high, plain))
    want = dict((d, m.get("importance")) for d, m in normal._rows.store.values())
    assert got == want and got[high] == pytest.approx(0.9) and got[plain] is None
