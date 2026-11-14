"""scripts/maintenance/forget_command_rows_audit.py: finds rows MINTED FROM A FORGET COMMAND's own turn (QA round 10a) and forgets
them only through the owner-forget path.

Pinned: the audit finds a planted minted row (by excerpt and by turn id), ignores an ordinary row and a row from an ordinary turn that
merely says "forget"; output and logs carry no text and no name; the id file is 0600; the palace file is never written; the cleanup's
dry run changes nothing; --confirm without the stop acknowledgement is refused; the cleanup forgets exactly the audited ids through
``MemoryService.review(archive)`` + ``MemoryService.erase_rows`` and nothing else. Negative control: with the detector blind the planted
row is not found. A throwaway sqlite in chroma's table shape; invented names only (ci_safe)."""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import sqlite3
from pathlib import Path

import pytest

import turn_forget
from forgotten_support import svc  # noqa: F401 - the MemoryService over the where-honouring Chroma stand-in

pytestmark = pytest.mark.ci_safe

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "forget_command_rows_audit.py"
UID = "demo_bar_00000001"
OTHER = "demo_bar_00000002"
CMD = "forget that i told you about my brother"
ORDINARY_FORGET = "i always forget my keys at the gym"
MINTED = "User told Zoe about their brother before"
SECRET_WORDS = ("brother", "keys", "Rowan", "librarian")


def _load():
    spec = importlib.util.spec_from_file_location("forget_command_rows_audit", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fca = _load()


def _turns():
    return [
        {"id": "t1", "session_id": "sess-a", "at": "2026-11-14T08:24:15+08:00", "user_id": UID, "content": CMD},
        {"id": "t2", "session_id": "sess-a", "at": "2026-11-14T08:25:00+08:00", "user_id": UID, "content": ORDINARY_FORGET},
        {"id": "t3", "session_id": "sess-b", "at": "2026-11-14T08:26:00+08:00", "user_id": OTHER, "content": "forget everything about rowan"},
    ]


def _rows():
    def r(rid, text, user=UID, **md):
        return {"id": rid, "user_id": user, "text": text, "meta": {"user_id": user, "status": "approved", **md}}
    return [
        r("minted_excerpt", MINTED, source="idle_consolidation", source_excerpt=CMD, session_id="sess-a"),
        r("minted_turnid", "User mentioned their brother", source="digest", user_turn_id=fca.turn_content_id(UID, CMD)),
        r("ordinary", "User is a librarian", source="chat_regex", source_excerpt="hi im a librarian"),
        r("forget_word", "User often forgets their keys at the gym", source="idle_consolidation", source_excerpt=ORDINARY_FORGET),
        r("window", "User likes quiet weekends", source="idle_consolidation", session_id="sess-a", added_at="2026-11-14T00:40:00Z"),
        r("window_late", "User likes tea", source="idle_consolidation", session_id="sess-a", added_at="2026-11-20T00:40:00Z"),
        r("archived", MINTED, source="idle_consolidation", source_excerpt=CMD, status="archived"),
        r("other_member", "User's brother is tall", user=OTHER, source="digest", source_excerpt="my brother is tall"),
    ]


def test_audit_finds_the_minted_rows_and_ignores_ordinary_ones():
    rep = fca.audit(_rows(), _turns())
    assert sorted(x["id"] for x in rep["linked"]) == ["minted_excerpt", "minted_turnid"]
    assert {x["how"] for x in rep["linked"]} == {"excerpt_is_command", "turn_id"}
    assert [x["id"] for x in rep["window"]] == ["window"]                 # unproven, listed for review only
    assert rep["counts"]["command_turns"] == 2                              # the ordinary "forget my keys" turn is not one
    blob = json.dumps(rep["counts"])
    assert UID not in blob and not any(w.lower() in blob.lower() for w in SECRET_WORDS)


def test_control_with_the_detector_blind_nothing_is_found(monkeypatch):
    monkeypatch.setattr(turn_forget, "is_forget_instruction", lambda _t: False)
    rep = fca.audit(_rows(), _turns())
    assert rep["linked"] == [] and rep["window"] == []


def _palace(tmp_path, rows) -> Path:
    db = tmp_path / "chroma.sqlite3"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE collections (id TEXT, name TEXT);
        CREATE TABLE segments (id TEXT, collection TEXT, scope TEXT);
        CREATE TABLE embeddings (id INTEGER, segment_id TEXT, embedding_id TEXT);
        CREATE TABLE embedding_metadata (id INTEGER, key TEXT, string_value TEXT, int_value INTEGER, float_value REAL);
        INSERT INTO collections VALUES ('c1', 'mempalace_drawers');
        INSERT INTO segments VALUES ('s1', 'c1', 'METADATA');
    """)
    for n, r in enumerate(rows, 1):
        con.execute("INSERT INTO embeddings VALUES (?,?,?)", (n, "s1", r["id"]))
        for k, v in {**r["meta"], "chroma:document": r["text"]}.items():
            con.execute("INSERT INTO embedding_metadata VALUES (?,?,?,?,?)", (n, k, str(v), None, None))
    con.commit()
    con.close()
    return db


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_main_reads_only_prints_counts_and_writes_a_private_id_file(tmp_path, monkeypatch, capsys):
    db = _palace(tmp_path, _rows())
    before = _sha(db)
    monkeypatch.setattr(fca, "load_turns", lambda *_a, **_k: _turns())
    out = tmp_path / "ids.json"
    assert fca.main(["--palace", str(tmp_path), "--ids-out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "linked=2" in printed and UID not in printed and not any(w.lower() in printed.lower() for w in SECRET_WORDS)
    assert os.stat(out).st_mode & 0o777 == 0o600
    plan = json.loads(out.read_text())
    assert sorted(x["id"] for x in plan["linked"]) == ["minted_excerpt", "minted_turnid"]
    assert all("text" not in x for x in plan["linked"] + plan["window"])
    assert _sha(db) == before                                                # read-only


def test_cleanup_dry_run_changes_nothing_and_confirm_needs_the_stop(tmp_path, monkeypatch, capsys):
    db = _palace(tmp_path, _rows())
    before = _sha(db)
    plan = tmp_path / "ids.json"
    fca.write_ids(str(plan), fca.audit(_rows(), _turns()))
    called = []
    monkeypatch.setattr(fca, "cleanup", lambda *a, **k: called.append(1))
    monkeypatch.setattr(fca, "load_turns", lambda *_a, **_k: _turns())
    assert fca.main(["--palace", str(tmp_path), "--erase-from", str(plan)]) == 0
    assert '"total": 2' in capsys.readouterr().out and not called and _sha(db) == before
    assert fca.main(["--palace", str(tmp_path), "--erase-from", str(plan), "--confirm"]) == 2      # no stop acknowledgement
    monkeypatch.setattr(fca, "_service_up", lambda: ":8000 is listening")
    assert fca.main(["--palace", str(tmp_path), "--erase-from", str(plan), "--confirm", "--i-stopped-zoe-data"]) == 2
    assert not called and _sha(db) == before


def _store(svc, monkeypatch):  # noqa: F811
    """``svc`` holding the synthetic rows, with call recording; the REAL archive (review), the erase over the stand-in store."""
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "0")
    rows = _rows()
    for r in rows:
        svc._col.rows[r["id"]] = (r["text"], dict(r["meta"]))
    calls = {"review": [], "erase": []}
    real_review = svc.review

    async def review(mem_id, **kw):
        calls["review"].append((mem_id, kw.get("decision")))
        return await real_review(mem_id, **kw)

    async def erase_rows(user_id, ids, **kw):
        # the REAL archive above; the erase over the stand-in store, as test_forget_alias_sweep does (the byte scrub needs chromadb's
        # audit collection and is test_memory_physical_erase's): here, WHICH rows go, owner-matched exactly as erase_rows matches
        calls["erase"].append((user_id, sorted(ids)))
        gone = [i for i in ids if i in svc._col.rows and (svc._col.rows[i][1].get("user_id") or svc._col.rows[i][1].get("wing")) == user_id]
        for i in gone:
            svc._col.rows.pop(i)
        return {"rows_removed": len(gone)}
    monkeypatch.setattr(svc, "review", review)
    monkeypatch.setattr(svc, "erase_rows", erase_rows)
    return rows, calls


def _live_rows(svc):
    return [{"id": i, "user_id": str(md.get("user_id") or ""), "text": d, "meta": dict(md)} for i, (d, md) in svc._col.rows.items()]


def test_cleanup_forgets_exactly_the_audited_ids_through_the_owner_forget_path(svc, monkeypatch):  # noqa: F811
    rows, calls = _store(svc, monkeypatch)
    plan = fca.audit(rows, _turns())
    todo, refused = fca.plan_cleanup(rows, _turns(), plan)
    assert todo == {UID: ["minted_excerpt", "minted_turnid"]} and refused == []
    done = asyncio.run(fca.cleanup(todo, svc=svc))
    assert sorted(calls["review"]) == [("minted_excerpt", "archive"), ("minted_turnid", "archive")]
    assert calls["erase"] == [(UID, ["minted_excerpt", "minted_turnid"])]
    assert list(done.values()) == [{"asked": 2, "archived": 2, "rows_removed": 2}]
    assert "minted_excerpt" not in svc._col.rows and "minted_turnid" not in svc._col.rows
    for keep in ("ordinary", "forget_word", "window", "window_late", "other_member"):
        assert keep in svc._col.rows                                            # nothing else touched


def _confirm(svc, monkeypatch, tmp_path, plan: dict, *extra):  # noqa: F811
    """main --erase-from --confirm over ``svc`` (the service provably down, the palace read from the stand-in store)."""
    import memory_service

    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    monkeypatch.setattr(fca, "_service_up", lambda: None)
    monkeypatch.setattr(fca, "load_turns", lambda *_a, **_k: _turns())
    monkeypatch.setattr(fca, "load_rows", lambda _p: _live_rows(svc))
    monkeypatch.setattr(memory_service, "MemoryService", lambda **_k: svc)
    return fca.main(["--palace", str(tmp_path), "--erase-from", str(path), "--confirm", "--i-stopped-zoe-data", *extra])


def test_an_edited_plan_cannot_erase_an_ordinary_row(svc, monkeypatch, tmp_path, capsys):  # noqa: F811
    rows, calls = _store(svc, monkeypatch)
    plan = fca.audit(rows, _turns())
    forged = {"linked": plan["linked"] + [{"id": "ordinary", "user_id": UID, "source": "chat_regex", "how": "excerpt_is_command"},
                                          {"id": "other_member", "user_id": UID, "source": "digest", "how": "turn_id"}],
              "window": []}
    refused_out = tmp_path / "refused.json"
    assert _confirm(svc, monkeypatch, tmp_path, forged, "--refused-out", str(refused_out)) == 0
    out = capsys.readouterr().out
    assert '"refused_not_linked_now": 2' in out and UID not in out
    assert "ordinary" in svc._col.rows and "other_member" in svc._col.rows             # refused and untouched
    assert "minted_excerpt" not in svc._col.rows and "minted_turnid" not in svc._col.rows   # the true rows still go
    assert all(rid not in ("ordinary", "other_member") for rid, _ in calls["review"])
    assert json.loads(refused_out.read_text()) == {"refused": ["ordinary", "other_member"]}
    assert os.stat(refused_out).st_mode & 0o777 == 0o600


def test_control_the_old_trusting_plan_check_would_have_erased_the_forged_row(svc, monkeypatch):  # noqa: F811
    rows, _ = _store(svc, monkeypatch)
    forged = {"linked": [{"id": "ordinary", "user_id": UID}]}
    trusted = [x["id"] for x in forged["linked"] if any(r["id"] == x["id"] and r["user_id"] == x["user_id"] for r in rows)]
    assert trusted == ["ordinary"]                                       # ownership + status alone pass it (the reviewed defect)
    assert fca.plan_cleanup(rows, _turns(), forged) == ({}, ["ordinary"])  # the recomputed link refuses it


def test_window_rows_are_report_only_even_with_confirm(svc, monkeypatch, tmp_path, capsys):  # noqa: F811
    rows, calls = _store(svc, monkeypatch)
    plan = fca.audit(rows, _turns())
    assert [x["id"] for x in plan["window"]] == ["window"]
    assert _confirm(svc, monkeypatch, tmp_path, plan) == 0
    assert '"window_report_only": 1' in capsys.readouterr().out
    assert "window" in svc._col.rows and all(rid != "window" for rid, _ in calls["review"])
    with pytest.raises(SystemExit):
        fca.main(["--erase-from", str(tmp_path / "plan.json"), "--include-window"])   # the erase path for window rows is gone


def test_turns_are_read_over_the_whole_history_unless_since_is_given(tmp_path, monkeypatch, capsys):
    assert "created_at" not in fca.turns_sql().split("from chat_messages", 1)[1]        # no time bound by default
    assert "'2026-01-02'::date" in fca.turns_sql("2026-01-02")
    with pytest.raises(ValueError):
        fca.turns_sql("2026-01-02'; drop table chat_messages; --")                     # only a date reaches the SQL
    _palace(tmp_path, _rows())
    seen = {}

    def turns(_cmd, since=None):
        seen["since"] = since
        return _turns()
    monkeypatch.setattr(fca, "load_turns", turns)
    assert fca.main(["--palace", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert seen["since"] is None and "turns read: 2026-11-14 .. 2026-11-14 (whole history)" in out
