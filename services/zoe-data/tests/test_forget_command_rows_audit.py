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
import re
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


RETEACH = "my brother rowan just moved to cairns"          # an ordinary re-teach: none of the forget keywords


def _transcript(uid):
    """The member's FULL on-the-record transcript (what iter_member_turns pages through): the pre-fix command row is on it."""
    return [t for t in _turns() if t["user_id"] == uid] + (
        [{"id": "t9", "session_id": "sess-a", "at": "2026-11-14T09:00:00+08:00", "user_id": UID, "content": "hi im a librarian"}]
        if uid == UID else [])


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
    rep = fca.audit(_rows(), _turns(), member_turns=_transcript)
    assert sorted(x["id"] for x in rep["linked"]) == ["minted_excerpt", "minted_turnid"]
    assert {x["how"] for x in rep["linked"]} == {"excerpt_is_command", "turn_id"}
    assert [x["id"] for x in rep["window"]] == ["window"]                 # unproven, listed for review only
    assert rep["counts"]["command_turns"] == 2                              # the ordinary "forget my keys" turn is not one
    blob = json.dumps(rep["counts"])
    assert UID not in blob and not any(w.lower() in blob.lower() for w in SECRET_WORDS)


def test_control_with_the_detector_blind_nothing_is_found(monkeypatch):
    monkeypatch.setattr(turn_forget, "is_forget_instruction", lambda _t: False)
    rep = fca.audit(_rows(), _turns(), member_turns=_transcript)
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
    monkeypatch.setattr(fca, "iter_member_turns", lambda _cmd, uid, since=None, **_k: iter(_transcript(uid)))
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
    fca.write_ids(str(plan), fca.audit(_rows(), _turns(), member_turns=_transcript))
    called = []
    monkeypatch.setattr(fca, "cleanup", lambda *a, **k: called.append(1))
    monkeypatch.setattr(fca, "load_turns", lambda *_a, **_k: _turns())
    monkeypatch.setattr(fca, "iter_member_turns", lambda _cmd, uid, since=None, **_k: iter(_transcript(uid)))
    assert fca.main(["--palace", str(tmp_path), "--erase-from", str(plan)]) == 0
    assert '"total": 2' in capsys.readouterr().out and not called and _sha(db) == before
    assert fca.main(["--palace", str(tmp_path), "--erase-from", str(plan), "--confirm"]) == 2      # no stop acknowledgement
    monkeypatch.setattr(fca, "_service_up", lambda: ":8000 is listening")
    assert fca.main(["--palace", str(tmp_path), "--erase-from", str(plan), "--confirm", "--i-stopped-zoe-data"]) == 2
    assert not called and _sha(db) == before


PHYSICAL = {"enabled": True, "ok": True}


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
        return {"rows_removed": len(gone), "physical": dict(PHYSICAL)}
    monkeypatch.setattr(svc, "review", review)
    monkeypatch.setattr(svc, "erase_rows", erase_rows)
    return rows, calls


def _live_rows(svc):
    return [{"id": i, "user_id": str(md.get("user_id") or ""), "text": d, "meta": dict(md)} for i, (d, md) in svc._col.rows.items()]


def test_cleanup_forgets_exactly_the_audited_ids_through_the_owner_forget_path(svc, monkeypatch):  # noqa: F811
    rows, calls = _store(svc, monkeypatch)
    plan = fca.audit(rows, _turns(), member_turns=_transcript)
    todo, refused = fca.plan_cleanup(rows, _turns(), plan, member_turns=_transcript)
    assert todo == {UID: ["minted_excerpt", "minted_turnid"]} and refused == []
    done = asyncio.run(fca.cleanup(todo, svc=svc))
    assert sorted(calls["review"]) == [("minted_excerpt", "archive"), ("minted_turnid", "archive")]
    assert calls["erase"] == [(UID, ["minted_excerpt", "minted_turnid"])]
    assert list(done.values()) == [{"asked": 2, "archived": 2, "rows_removed": 2, "physical_scrub_failed": 0, "erase_failed": 0}]
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
    monkeypatch.setattr(fca, "iter_member_turns", lambda _cmd, uid, since=None, **_k: iter(_transcript(uid)))
    monkeypatch.setattr(fca, "load_rows", lambda _p: _live_rows(svc))
    monkeypatch.setattr(memory_service, "MemoryService", lambda **_k: svc)
    return fca.main(["--palace", str(tmp_path), "--erase-from", str(path), "--confirm", "--i-stopped-zoe-data",
                     "--state-dir", str(tmp_path / "state"), *extra])       # never the real state dir


def test_an_edited_plan_cannot_erase_an_ordinary_row(svc, monkeypatch, tmp_path, capsys):  # noqa: F811
    rows, calls = _store(svc, monkeypatch)
    plan = fca.audit(rows, _turns(), member_turns=_transcript)
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
    assert fca.plan_cleanup(rows, _turns(), forged, member_turns=_transcript) == ({}, ["ordinary"])  # the recomputed link refuses it


def test_window_rows_are_report_only_even_with_confirm(svc, monkeypatch, tmp_path, capsys):  # noqa: F811
    rows, calls = _store(svc, monkeypatch)
    plan = fca.audit(rows, _turns(), member_turns=_transcript)
    assert [x["id"] for x in plan["window"]] == ["window"]
    assert _confirm(svc, monkeypatch, tmp_path, plan) == 0
    assert '"window_report_only": 1' in capsys.readouterr().out
    assert "window" in svc._col.rows and all(rid != "window" for rid, _ in calls["review"])
    with pytest.raises(SystemExit):
        fca.main(["--erase-from", str(tmp_path / "plan.json"), "--include-window"])   # the erase path for window rows is gone


def test_turns_are_read_over_the_whole_history_unless_since_is_given(tmp_path, monkeypatch, capsys):
    assert "::date" not in fca.turns_sql() and ">= " not in fca.turns_sql()           # no time bound by default
    assert "'2026-01-02'::date" in fca.turns_sql("2026-01-02")
    with pytest.raises(ValueError):
        fca.turns_sql("2026-01-02'; drop table chat_messages; --")                     # only a date reaches the SQL
    _palace(tmp_path, _rows())
    seen = {}

    def turns(_cmd, since=None):
        seen["since"] = since
        return _turns()
    monkeypatch.setattr(fca, "load_turns", turns)
    monkeypatch.setattr(fca, "iter_member_turns", lambda _cmd, uid, since=None, **_k: iter(_transcript(uid)))
    assert fca.main(["--palace", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert seen["since"] is None and "forget-command turns read: 2026-11-14 .. 2026-11-14 (whole history)" in out



# ── review #2482 PRRT ...aUVK: a re-teach after the forget is never "command-only" ─────────────────────────────────────────────
def _reteach_rows():
    rows = _rows()
    rows.append({"id": "reteach", "user_id": UID, "text": "User's brother Rowan moved to Cairns",
                 "meta": {"user_id": UID, "status": "approved", "source": "digest", "source_excerpt": "my brother"}})
    return rows


def _with_reteach(uid):
    extra = [{"id": "t10", "session_id": "sess-c", "at": "2026-11-15T08:00:00+08:00", "user_id": UID, "content": RETEACH}]
    return _transcript(uid) + (extra if uid == UID else [])


def test_a_row_also_resting_on_an_ordinary_reteach_turn_is_never_erased():
    rep = fca.audit(_reteach_rows(), _turns(), member_turns=_with_reteach)
    assert "reteach" not in {x["id"] for x in rep["linked"]} and "reteach" in {x["id"] for x in rep["unclear"]}
    assert sorted(x["id"] for x in rep["linked"]) == ["minted_excerpt", "minted_turnid"]        # the leak is still found
    plan = {"linked": [{"id": "reteach", "user_id": UID}]}
    assert fca.plan_cleanup(_reteach_rows(), _turns(), plan, member_turns=_with_reteach) == ({}, ["reteach"])   # same rule


def test_an_ordinary_turn_holding_a_proven_excerpt_keeps_the_row():
    keep = "please dont forget that i told you about my brother he matters to me"     # a teach, not a command
    assert not turn_forget.is_forget_instruction(keep)
    extra = {"id": "t11", "session_id": "sess-a", "at": "2026-11-14T10:00:00+08:00", "user_id": UID, "content": keep}
    rep = fca.audit(_rows(), _turns(), member_turns=lambda uid: _transcript(uid) + ([extra] if uid == UID else []))
    assert "minted_excerpt" not in {x["id"] for x in rep["linked"]} and rep["counts"]["not_command_only"] == 1


def _unproven_rows():
    return _rows() + [
        {"id": "shaped_absent", "user_id": UID, "text": "User asked Zoe to forget something",
         "meta": {"user_id": UID, "status": "approved", "source": "digest", "source_excerpt": "forget everything about tamsin"}},
        {"id": "other_members_cmd", "user_id": UID, "text": "User mentioned Rowan",
         "meta": {"user_id": UID, "status": "approved", "source": "digest", "source_excerpt": "forget everything about rowan"}},
    ]


def test_a_command_shaped_excerpt_with_no_command_turn_of_that_member_is_unclear():
    rep = fca.audit(_unproven_rows(), _turns(), member_turns=_transcript)
    assert {"shaped_absent", "other_members_cmd"} <= {x["id"] for x in rep["unclear"]}    # absent / another member's command
    assert sorted(x["id"] for x in rep["linked"]) == ["minted_excerpt", "minted_turnid"]  # the matching cases still link
    plan = {"linked": [{"id": "shaped_absent", "user_id": UID}, {"id": "other_members_cmd", "user_id": UID}]}
    assert fca.plan_cleanup(_unproven_rows(), _turns(), plan, member_turns=_transcript) == ({}, ["shaped_absent", "other_members_cmd"])


def test_control_the_old_any_command_shaped_excerpt_rule_links_them(monkeypatch):
    monkeypatch.setattr(fca, "proven_by_command", lambda *_a: True)           # the withdrawn rule: never proven against a turn
    rep = fca.audit(_unproven_rows(), _turns(), member_turns=_transcript)
    assert {"shaped_absent", "other_members_cmd"} <= {x["id"] for x in rep["linked"]}


def test_an_unreadable_transcript_makes_every_candidate_unclear_and_nothing_erasable():
    def down(_uid):
        raise OSError("db down")
        yield  # pragma: no cover
    rep = fca.audit(_rows(), _turns(), member_turns=down)
    assert rep["linked"] == [] and rep["counts"]["unclear"] == 2
    assert fca.audit(_rows(), _turns())["linked"] == []                     # no transcript reader at all: unclear too
    plan = fca.audit(_rows(), _turns(), member_turns=_transcript)
    assert fca.plan_cleanup(_rows(), _turns(), plan, member_turns=down)[0] == {}


def test_the_member_turn_read_is_every_on_record_turn_paged_with_no_keyword_filter():
    import memory_provenance

    q = fca.member_turns_sql(UID, None, fca.MEMBER_TURN_PAGE)
    assert memory_provenance.OFF_RECORD_JSON_MARK in q and "~*" not in q and f"limit {fca.MEMBER_TURN_PAGE}" in q
    assert "> ('2026-11-14 08:00:00+00', 't1')" in fca.member_turns_sql(UID, ("2026-11-14 08:00:00+00", "t1"), 10)
    assert "o''brien" in fca.member_turns_sql("o'brien", None, 10)            # quoted, never raw
    pages = []

    def fake_run(argv, **_k):
        n = len(pages)
        pages.append(argv[-1])
        batch = [{"id": f"m{n}-{i}", "session_id": "s", "at": f"2026-11-1{n}T00:00:0{i}", "content": "x"} for i in range(2 if n < 2 else 1)]
        return type("R", (), {"stdout": json.dumps(batch)})()
    import subprocess
    orig = subprocess.run
    subprocess.run = fake_run
    try:
        got = list(fca.iter_member_turns("psql", UID, page=2))
    finally:
        subprocess.run = orig
    assert len(got) == 5 and len(pages) == 3 and "('2026-11-10T00:00:01', 'm0-1')" in pages[1]


# ── review #2482 PRRT ...aUVS: an owner- or operator-authority row is the owner's to forget ─────────────────────────────────────
@pytest.mark.parametrize("cls", ["user_stated", "user_confirmed", "operator"])
def test_an_owner_authority_row_is_listed_as_owner_decision_and_never_erased(cls, svc, monkeypatch, tmp_path, capsys):  # noqa: F811
    rows, calls = _store(svc, monkeypatch)
    svc._col.rows["minted_turnid"][1]["authority_class"] = cls
    rows = _live_rows(svc)
    rep = fca.audit(rows, _turns(), member_turns=_transcript)
    assert [x["id"] for x in rep["owner_decision"]] == ["minted_turnid"] and [x["id"] for x in rep["linked"]] == ["minted_excerpt"]
    forged = {"linked": rep["linked"] + rep["owner_decision"], "owner_decision": rep["owner_decision"], "window": []}
    assert _confirm(svc, monkeypatch, tmp_path, forged) == 0
    assert '"owner_decision_report_only": 1' in capsys.readouterr().out
    assert "minted_turnid" in svc._col.rows and "minted_excerpt" not in svc._col.rows


# ── review #2482 PRRT ...cWrH: a failed residue scrub is said and fails the run ──────────────────────────────────────────────────
@pytest.mark.parametrize("physical, code", [({"enabled": True, "ok": True}, 0), ({"enabled": True, "ok": False, "error": "x"}, 6),
                                            ({"enabled": False}, 6)])
def test_a_failed_or_skipped_residue_scrub_exits_non_zero_and_says_so(physical, code, svc, monkeypatch, tmp_path, capsys):  # noqa: F811
    monkeypatch.setitem(PHYSICAL, "ok", physical.get("ok"))
    monkeypatch.setitem(PHYSICAL, "enabled", physical.get("enabled"))
    rows, _ = _store(svc, monkeypatch)
    assert _confirm(svc, monkeypatch, tmp_path, fca.audit(rows, _turns(), member_turns=_transcript)) == code
    err = capsys.readouterr().err
    assert ("physical_scrub_failed rows=2 members=1" in err) is (code == 6)
    assert UID not in err and "brother" not in err
    assert "minted_excerpt" not in svc._col.rows                               # logically erased either way


# ── review #2482 PRRT ...dCE8: an erase that RAISES after the archive is finished by a retry ──────────────────────────────────
def _raising(*_a, **_k):
    async def boom(*_x, **_y):
        raise RuntimeError("intent audit write failed")
    return boom


def test_an_erase_that_raises_exits_7_and_a_retry_finishes_it(svc, monkeypatch, tmp_path, capsys):  # noqa: F811
    rows, _ = _store(svc, monkeypatch)
    good = svc.erase_rows
    plan = fca.audit(rows, _turns(), member_turns=_transcript)
    monkeypatch.setattr(svc, "erase_rows", _raising())
    assert _confirm(svc, monkeypatch, tmp_path, plan) == 7
    err = capsys.readouterr().err
    assert "erase_failed rows=2 members=1" in err and UID not in err
    for rid in ("minted_excerpt", "minted_turnid"):                       # stranded: archived WITH the marker, still stored
        md = svc._col.rows[rid][1]
        assert md["status"] == "archived" and md["reviewed_by"] == fca.ACTOR and md["review_note"] == fca.ARCHIVE_NOTE
    monkeypatch.setattr(svc, "erase_rows", good)
    assert _confirm(svc, monkeypatch, tmp_path, plan) == 0                # the retry re-proves and erases them
    assert "minted_excerpt" not in svc._col.rows and "minted_turnid" not in svc._col.rows


def test_the_retry_exits_8_and_keeps_the_ids_pending_when_its_scrub_fails(svc, monkeypatch, tmp_path):  # noqa: F811
    rows, _ = _store(svc, monkeypatch)
    good = svc.erase_rows
    plan = fca.audit(rows, _turns(), member_turns=_transcript)
    monkeypatch.setattr(svc, "erase_rows", _raising())
    assert _confirm(svc, monkeypatch, tmp_path, plan) == 7
    monkeypatch.setattr(svc, "erase_rows", good)
    monkeypatch.setitem(PHYSICAL, "ok", False)
    # the rows are erased now, but their ids stay PENDING (the scrub is owed) and the next run retries it: 8, not 6
    assert _confirm(svc, monkeypatch, tmp_path, plan) == 8 and "minted_excerpt" not in svc._col.rows
    assert (tmp_path / "state" / fca.PENDING_FILE).exists()


def test_an_archive_someone_else_made_is_never_touched(svc, monkeypatch, tmp_path):  # noqa: F811
    rows, calls = _store(svc, monkeypatch)
    svc._col.rows["minted_excerpt"][1].update(status="archived", reviewed_by="demo_bar_00000001", review_note="owner archived it")
    live = _live_rows(svc)
    rep = fca.audit(live, _turns(), member_turns=_transcript)
    assert "minted_excerpt" not in {x["id"] for x in rep["linked"] + rep["unclear"] + rep["owner_decision"]}
    forged = {"linked": [{"id": "minted_excerpt", "user_id": UID}], "window": []}
    assert _confirm(svc, monkeypatch, tmp_path, forged) == 0
    assert "minted_excerpt" in svc._col.rows and all(rid != "minted_excerpt" for rid, _ in calls["review"])


def test_control_the_old_filter_strands_the_row_and_exits_0(svc, monkeypatch, tmp_path):  # noqa: F811
    rows, _ = _store(svc, monkeypatch)
    good = svc.erase_rows
    plan = fca.audit(rows, _turns(), member_turns=_transcript)
    monkeypatch.setattr(svc, "erase_rows", _raising())
    assert _confirm(svc, monkeypatch, tmp_path, plan) == 7
    monkeypatch.setattr(svc, "erase_rows", good)
    monkeypatch.setattr(fca, "_own_archive", lambda _m: False)             # the withdrawn rule: every archived row is out
    (tmp_path / "state" / fca.PENDING_FILE).unlink()                       # ... and the code of that time kept no pending file
    assert _confirm(svc, monkeypatch, tmp_path, plan) == 0                  # "clean" exit ...
    assert "minted_excerpt" in svc._col.rows                                # ... with the forgotten row still stored



# ── review #2482 PRRT ...dTd8: erase_rows raised AFTER deleting the drawer rows ─────────────────────────────────────────────────
def _residue_model(svc, monkeypatch, *, audit_fails=False, scrub_ok=True):
    """The audit trail of the minted rows (still holding their words) and the scrub, as the recovery path calls them."""
    trail = {"minted_excerpt", "minted_turnid"}
    state = {"audit_fails": audit_fails, "scrub_ok": scrub_ok}

    def delete_audit(ids):
        if state["audit_fails"]:
            raise RuntimeError("audit delete failed")
        trail.difference_update(ids)
        return len(ids)

    async def physical(_needles):
        return {"enabled": True, "ok": state["scrub_ok"]}
    monkeypatch.setattr(svc, "_delete_audit_for_rows_sync", delete_audit)
    monkeypatch.setattr(svc, "_physical_erase", physical)
    return trail, state


def _rows_gone_then_raise(svc):
    async def erase(user_id, ids, **_k):
        for i in ids:                                  # the drawer rows ARE deleted ...
            svc._col.rows.pop(i, None)
        raise RuntimeError("audit trail delete failed")   # ... then the audit-trail delete raises
    return erase


def test_rows_deleted_then_audit_delete_fails_leaves_a_private_ids_only_pending_file(svc, monkeypatch, tmp_path, capsys):  # noqa: F811
    rows, _ = _store(svc, monkeypatch)
    _residue_model(svc, monkeypatch)
    plan = fca.audit(rows, _turns(), member_turns=_transcript)
    monkeypatch.setattr(svc, "erase_rows", _rows_gone_then_raise(svc))
    assert _confirm(svc, monkeypatch, tmp_path, plan) == 7
    f = tmp_path / "state" / fca.PENDING_FILE
    assert f.exists() and os.stat(f).st_mode & 0o777 == 0o600
    assert json.loads(f.read_text()) == {"pending": {UID: ["minted_excerpt", "minted_turnid"]}}      # ids only, no text
    assert "brother" not in f.read_text()


@pytest.mark.parametrize("scrub_ok, code", [(True, 0), (False, 8)])
def test_a_retry_finishes_the_audit_delete_and_the_scrub(scrub_ok, code, svc, monkeypatch, tmp_path, capsys):  # noqa: F811
    rows, _ = _store(svc, monkeypatch)
    good = svc.erase_rows
    trail, state = _residue_model(svc, monkeypatch)
    plan = fca.audit(rows, _turns(), member_turns=_transcript)
    monkeypatch.setattr(svc, "erase_rows", _rows_gone_then_raise(svc))
    assert _confirm(svc, monkeypatch, tmp_path, plan) == 7
    monkeypatch.setattr(svc, "erase_rows", good)
    state["scrub_ok"] = scrub_ok
    assert _confirm(svc, monkeypatch, tmp_path, plan) == code
    assert trail == set()                                                    # the audit trail is deleted either way
    f = tmp_path / "state" / fca.PENDING_FILE
    assert f.exists() is (not scrub_ok)                                      # removed only after BOTH succeed
    if not scrub_ok:
        assert "recovery_pending rows=2" in capsys.readouterr().err
        state["scrub_ok"] = True
        assert _confirm(svc, monkeypatch, tmp_path, plan) == 0 and not f.exists()


def test_a_retry_whose_audit_delete_fails_again_keeps_the_file(svc, monkeypatch, tmp_path):  # noqa: F811
    rows, _ = _store(svc, monkeypatch)
    good = svc.erase_rows
    trail, state = _residue_model(svc, monkeypatch, audit_fails=True)
    plan = fca.audit(rows, _turns(), member_turns=_transcript)
    monkeypatch.setattr(svc, "erase_rows", _rows_gone_then_raise(svc))
    assert _confirm(svc, monkeypatch, tmp_path, plan) == 7
    monkeypatch.setattr(svc, "erase_rows", good)
    assert _confirm(svc, monkeypatch, tmp_path, plan) == 8 and (tmp_path / "state" / fca.PENDING_FILE).exists()
    assert trail == {"minted_excerpt", "minted_turnid"}


def test_control_without_the_pending_file_the_retry_exits_0_with_residue(svc, monkeypatch, tmp_path):  # noqa: F811
    rows, _ = _store(svc, monkeypatch)
    good = svc.erase_rows
    trail, _state = _residue_model(svc, monkeypatch)
    plan = fca.audit(rows, _turns(), member_turns=_transcript)
    monkeypatch.setattr(fca, "add_pending", lambda *_a, **_k: None)          # the withdrawn handler: nothing recorded
    monkeypatch.setattr(svc, "erase_rows", _rows_gone_then_raise(svc))
    assert _confirm(svc, monkeypatch, tmp_path, plan) == 7
    monkeypatch.setattr(svc, "erase_rows", good)
    assert _confirm(svc, monkeypatch, tmp_path, plan) == 0                  # "clean" ...
    assert trail == {"minted_excerpt", "minted_turnid"}                      # ... with the forgotten words in the audit trail



# ── review #2482 PRRT ...dv8g: the prefilter is DERIVED from the detector, a provable superset ────────────────────────────────
#: every shape the detectors accept that the review named, plus one per detector family (placeholder names only)
COMMANDS = ["ignore that i said my brother lives in darwin", "disregard that i told you my sister is a nurse",
            "forget that i told you about my brother", "forget everything about rowan", "forget that", "never mind what i said",
            "scrap that", "delete that memory", "no, don't remember that, forget that", "forget what i said about being lonely",
            "remove everything about tamsin", "erase what i told you about my mums heart", "please forget that i said i live in perth",
            "stop remembering that", "dont save that"]
OLD_PREFILTER = r"(forget|delete|remove|erase|scrap|never ?mind|remember|keep|sav|stor)"     # the withdrawn hand-kept list


def _prefilter_re():
    terms = fca.prefilter_terms()
    assert terms, "every detector pattern must yield a provable literal set"
    return re.compile("(" + "|".join(terms) + ")", re.IGNORECASE)            # what ``content ~* '(...)'`` does


@pytest.mark.parametrize("text", COMMANDS)
def test_every_accepted_command_passes_the_derived_prefilter(text):
    if turn_forget.is_forget_instruction(text):
        assert _prefilter_re().search(text)


def test_the_two_review_examples_are_commands_and_the_old_list_missed_them():
    for text in COMMANDS[:2]:
        assert turn_forget.is_forget_instruction(text) and _prefilter_re().search(text)
        assert not re.search(OLD_PREFILTER, text, re.IGNORECASE)                # the negative control


def test_the_patterns_list_is_the_detectors_only_source(monkeypatch):
    import forget_topic
    import intent_router
    import memory_quality

    never = re.compile(r"(?!)")
    for mod, name in ((memory_quality, "_FORGET_WHOLE_RE"), (memory_quality, "_FORGET_I_SAID_RE"),
                      (memory_quality, "_FORGET_CLAUSE_RE"), (intent_router, "_FORGET_LAST_RE"), (intent_router, "_FORGET_ENTITY_RE"),
                      (forget_topic, "_TOPIC_RE"), (forget_topic, "_CLAUSE_RE")):
        assert getattr(mod, name) in turn_forget.instruction_patterns()
        monkeypatch.setattr(mod, name, never)
    assert not any(turn_forget.is_forget_instruction(t) for t in COMMANDS)   # nothing outside the list decides


def test_a_verb_added_to_a_detector_reaches_the_prefilter_automatically(monkeypatch):
    added = re.compile(r"^(?:please\s+)?(?:forget|zapify)\s+that\s+i\s+said", re.IGNORECASE)
    monkeypatch.setattr(turn_forget, "instruction_patterns", lambda: [added])
    assert fca.prefilter_terms() == ["forget", "zapify"]


@pytest.mark.parametrize("pattern, sample, word", [
    (r"(?:um\s+)?(?:ignore|disregard)\s+that", "um disregard that", "disregard"),
    (r"(?:x|y)?forget(?:ting)?\s+it", "forgetting it", "forget"),
    (r"(?:hey\s+)*(?:scrap|(?:do\s+not|stop)\s+(?:keeping|saving))\b", "stop saving", "saving"),
])
def test_necessary_literals_is_sound_on_small_patterns(pattern, sample, word):
    try:
        from re import _parser as sp
    except ImportError:  # py 3.10
        import sre_parse as sp

    got = fca._necessary_literals(sp.parse(pattern, re.IGNORECASE))
    assert got and word in got and any(w in sample for w in got)


def _fake_psql(table):
    """psql over an in-memory chat table: applies the SQL's own ``content ~* '(...)'`` prefilter, keyset paging and LIMIT."""
    def run(argv, **_k):
        sql = argv[-1]
        m = re.search(r"content ~\* '\(([^)]*)\)'", sql)
        rx = re.compile("(" + m.group(1) + ")", re.IGNORECASE) if m else None
        cur = re.search(r"\(created_at::text, id\) > \('([^']*)', '([^']*)'\)", sql)
        limit = int(re.search(r"limit (\d+)", sql).group(1))
        rows = [r for r in table if (rx is None or rx.search(r["content"])) and (not cur or (r["at"], r["id"]) > (cur.group(1), cur.group(2)))]
        rows = sorted(rows, key=lambda r: (r["at"], r["id"]))[:limit]
        return type("R", (), {"stdout": json.dumps([{**r, "meta": json.dumps({"user_id": r["user_id"]})} for r in rows])})()
    return run


@pytest.mark.parametrize("prefilter, found", [(None, True), (OLD_PREFILTER, False)])
def test_end_to_end_the_review_examples_are_read_and_their_leaked_rows_found(prefilter, found, monkeypatch):
    import subprocess

    table = [{"id": f"t{i}", "session_id": "sess-z", "at": f"2026-11-1{i}T08:00:00", "user_id": UID, "content": c}
             for i, c in enumerate(COMMANDS[:2] + ["my brother rowan is a chef", "we went to the beach"])]
    if prefilter:                                                           # the control: the old hand-kept list in the SQL
        monkeypatch.setattr(fca, "prefilter_sql", lambda: f"and content ~* '{prefilter}'")
    monkeypatch.setattr(subprocess, "run", _fake_psql(table))
    turns = fca.load_turns("psql", page=2)
    rows = [{"id": f"leak{i}", "user_id": UID, "text": "User mentioned a relative",
             "meta": {"user_id": UID, "status": "approved", "source": "idle_consolidation", "source_excerpt": c}}
            for i, c in enumerate(COMMANDS[:2])]
    rep = fca.audit(rows, turns, member_turns=lambda uid: iter(table if uid == UID else []))
    assert (sorted(x["id"] for x in rep["linked"]) == ["leak0", "leak1"]) is found
