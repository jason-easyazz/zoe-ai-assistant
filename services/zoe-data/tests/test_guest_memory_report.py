"""scripts/maintenance/guest_memory_report.py: reports guest-scoped palace rows (counts, no text by default) and never writes
unless ``--purge --execute --i-stopped-zoe-data``.

Pinned: the report finds every sentinel owner and no member; the default output and ``--json`` carry NO row text; ``--show``
does; a ``--purge`` dry run never calls the erase and never changes the palace file; ``--execute`` without the stop
acknowledgement is refused; the executed purge goes through ``MemoryService.erase_rows`` per sentinel owner only.

A throwaway sqlite file in chroma's table shape; invented text only (ci_safe).
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "guest_memory_report.py"
SECRET = "User keeps the spare key under the blue flowerpot"


def _load():
    spec = importlib.util.spec_from_file_location("guest_memory_report", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gmr = _load()


def _palace(tmp_path) -> Path:
    db = tmp_path / "chroma.sqlite3"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE collections (id TEXT, name TEXT);
        CREATE TABLE segments (id TEXT, collection TEXT, scope TEXT);
        CREATE TABLE embeddings (id INTEGER, segment_id TEXT, embedding_id TEXT);
        CREATE TABLE embedding_metadata (id INTEGER, key TEXT, string_value TEXT, int_value INTEGER, float_value REAL);
        INSERT INTO collections VALUES ('c1', 'mempalace_drawers'), ('c2', 'mempalace_audit');
        INSERT INTO segments VALUES ('s1', 'c1', 'METADATA'), ('s2', 'c2', 'METADATA');
    """)
    rows = [
        (1, "s1", "zoe_guest_a", {"user_id": "guest", "status": "approved", "memory_type": "preference",
                                  "source": "voice_turn_digest", "added_at": "2026-10-14T13:53:24Z"}, SECRET),
        (2, "s1", "zoe_guest_b", {"user_id": "voice-guest", "status": "approved", "memory_type": "profile",
                                  "source": "voice_regex", "added_at": "2026-10-13T09:00:00Z"}, "User manages a kiosk."),
        (3, "s1", "zoe_member_c", {"user_id": "demo_member_wren", "status": "approved", "memory_type": "fact",
                                   "source": "chat_regex", "added_at": "2026-10-14T10:00:00Z"}, "User likes kites."),
        (4, "s1", "zoe_ownerless", {"status": "approved"}, "ownerless row"),
        (5, "s2", "audit_guest", {"user_id": "guest"}, "audit row"),     # the audit collection is not the drawers
    ]
    for rid, seg, eid, md, doc in rows:
        con.execute("INSERT INTO embeddings VALUES (?,?,?)", (rid, seg, eid))
        for k, v in {**md, "chroma:document": doc}.items():
            con.execute("INSERT INTO embedding_metadata VALUES (?,?,?,NULL,NULL)", (rid, k, v))
    con.commit()
    con.close()
    return db


def _digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_finds_every_sentinel_owner_and_no_member(tmp_path):
    _palace(tmp_path)
    rows = gmr.load_guest_rows(str(tmp_path))
    assert sorted(r["id"] for r in rows) == ["zoe_guest_a", "zoe_guest_b"]
    s = gmr.summarise(rows)
    assert s["palace_rows"] == 2 and s["by_owner"] == {"guest": 1, "voice-guest": 1}
    assert s["by_writer"] == {"voice_regex": 1, "voice_turn_digest": 1}
    assert s["by_day"] == {"2026-10-13": 1, "2026-10-14": 1}


def test_the_default_report_and_json_carry_no_text(tmp_path, capsys):
    _palace(tmp_path)
    assert gmr.main(["--palace", str(tmp_path), "--no-pg"]) == 0
    assert gmr.main(["--palace", str(tmp_path), "--no-pg", "--json"]) == 0
    out = capsys.readouterr().out
    assert "Guest-scoped palace rows: 2" in out and SECRET not in out and "kiosk" not in out


def test_show_prints_the_text_locally(tmp_path, capsys):
    _palace(tmp_path)
    assert gmr.main(["--palace", str(tmp_path), "--no-pg", "--show"]) == 0
    assert SECRET in capsys.readouterr().out


def test_purge_without_execute_is_a_dry_run_that_writes_nothing(tmp_path, monkeypatch, capsys):
    db = _palace(tmp_path)
    before = _digest(db)

    async def boom(*_a, **_k):
        raise AssertionError("a dry run reached the erase")

    monkeypatch.setattr(gmr, "purge", boom)
    assert gmr.main(["--palace", str(tmp_path), "--no-pg", "--purge"]) == 0
    assert "DRY RUN" in capsys.readouterr().out and _digest(db) == before


@pytest.mark.parametrize("argv", [["--purge", "--execute"], ["--execute"]])
def test_execute_without_the_acknowledgement_is_refused(tmp_path, monkeypatch, argv):
    db = _palace(tmp_path)
    before = _digest(db)
    monkeypatch.setattr(gmr, "purge", None)       # any call would raise
    assert gmr.main(["--palace", str(tmp_path), "--no-pg", *argv]) == 2
    assert _digest(db) == before


def test_execute_refuses_while_zoe_data_looks_up(tmp_path, monkeypatch):
    _palace(tmp_path)
    monkeypatch.setattr(gmr, "_service_listening", lambda: True)
    monkeypatch.setattr(gmr, "purge", None)
    assert gmr.main(["--palace", str(tmp_path), "--no-pg", "--purge", "--execute", "--i-stopped-zoe-data"]) == 2


def test_the_executed_purge_erases_only_guest_rows_through_the_audited_path(tmp_path):
    _palace(tmp_path)
    rows = gmr.load_guest_rows(str(tmp_path))
    calls = []

    class _Svc:
        async def erase_rows(self, user_id, ids, *, actor, reason):
            calls.append((user_id, sorted(ids), actor))
            return {"rows_removed": len(ids)}

    done = asyncio.run(gmr.purge(rows, palace=str(tmp_path), svc=_Svc()))
    assert calls == [("guest", ["zoe_guest_a"], gmr.PURGE_ACTOR), ("voice-guest", ["zoe_guest_b"], gmr.PURGE_ACTOR)]
    assert json.loads(json.dumps(done)) == {"guest": {"asked": 1, "rows_removed": 1, "complete": True},
                                            "voice-guest": {"asked": 1, "rows_removed": 1, "complete": True}}



def test_the_purge_passes_each_owner_exactly_as_stored(tmp_path):
    """A legacy row stored as "Guest" or " guest " is erased under THAT id (erase_rows matches the owner exactly); normalising it
    to "guest" erased nothing and still reported success."""
    db = _palace(tmp_path)
    con = sqlite3.connect(db)
    for rid, eid, owner in ((10, "zoe_Guest_m", "Guest"), (11, "zoe_sp_guest_m", " guest ")):
        con.execute("INSERT INTO embeddings VALUES (?,?,?)", (rid, "s1", eid))
        for k, v in {"user_id": owner, "status": "approved", "chroma:document": "User likes kites."}.items():
            con.execute("INSERT INTO embedding_metadata VALUES (?,?,?,NULL,NULL)", (rid, k, v))
    con.commit()
    con.close()
    rows = gmr.load_guest_rows(str(tmp_path))
    calls = []

    class _Svc:
        async def erase_rows(self, user_id, ids, *, actor, reason):
            calls.append((user_id, sorted(ids)))
            return {"rows_removed": len(ids)}

    asyncio.run(gmr.purge(rows, palace=str(tmp_path), svc=_Svc()))
    assert (" guest ", ["zoe_sp_guest_m"]) in calls and ("Guest", ["zoe_Guest_m"]) in calls
    assert ("guest", ["zoe_guest_a"]) in calls


def test_an_incomplete_purge_is_not_reported_as_success(tmp_path, monkeypatch):
    _palace(tmp_path)

    async def short(rows, *, palace, svc=None):
        return {"guest": {"asked": 1, "rows_removed": 0, "complete": False}}

    monkeypatch.setattr(gmr, "purge", short)
    monkeypatch.setattr(gmr, "_service_listening", lambda: False)
    monkeypatch.setattr(gmr, "_service_active", lambda: False)
    assert gmr.main(["--palace", str(tmp_path), "--no-pg", "--purge", "--execute", "--i-stopped-zoe-data"]) == 2
