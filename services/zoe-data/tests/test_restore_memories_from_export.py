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


def _palace_db(path, drawers):
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE collections (id TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE segments (id TEXT PRIMARY KEY, scope TEXT, collection TEXT);
        CREATE TABLE embeddings (id INTEGER PRIMARY KEY, segment_id TEXT, embedding_id TEXT);
        CREATE TABLE embedding_metadata (id INTEGER, key TEXT, string_value TEXT, int_value INTEGER,
                                         float_value REAL, bool_value INTEGER);
        INSERT INTO collections VALUES ('c1','mempalace_drawers');
        INSERT INTO segments VALUES ('s1','METADATA','c1');
    """)
    for n, (eid, md) in enumerate(drawers, 1):
        c.execute("INSERT INTO embeddings VALUES (?,?,?)", (n, "s1", eid))
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
    assert {p["status"] for p in second} == {"refused_exists_by_text:approved"}
    assert _run(second, tmp_path, svc) == {"restored": 0, "refused": 20}
    assert len(svc._rows.store) == 20


def test_even_a_stale_plan_cannot_double_restore(export, palace, tmp_path):
    """Control for 'idempotent': re-applying the SAME (stale) plan is stopped by ingest's durable dedup."""
    svc = _svc(tmp_path)
    plan = rest.build_plan(rest.load_export(export), rest.read_palace(palace))
    _run(plan, tmp_path, svc)
    stale = rest.build_plan(rest.load_export(export), rest.read_palace(palace))        # still thinks 20 are missing
    res = _run(stale, tmp_path, svc)
    assert res["restored"] == 0 and len(svc._rows.store) == 20


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
    monkeypatch.setattr(rest.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no systemctl")))
    monkeypatch.setattr(rest.socket, "create_connection", lambda addr, timeout=1: real(("127.0.0.1", port), timeout=timeout))
    try:
        assert "accepting connections" in rest.service_up()
    finally:
        srv.close()
    assert rest.service_up() is None                                   # control: nothing listening any more
