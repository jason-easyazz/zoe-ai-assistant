"""scripts/maintenance/memory_ledger_audit.py — read-only ledger reconciliation.

Builds a miniature chroma-shaped sqlite (the real table layout) and checks every bucket with a
negative control, that the palace file is never written, and that no memory text is ever printed.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from scripts.maintenance import memory_ledger_audit as mla

pytestmark = pytest.mark.ci_safe

OWNER = "jason"
SECRET = "Zorblax the household secret sentence"      # must never appear in any output


def _make_db(path, drawers, audit):
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE collections (id TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE segments (id TEXT PRIMARY KEY, scope TEXT, collection TEXT);
        CREATE TABLE embeddings (id INTEGER PRIMARY KEY, segment_id TEXT, embedding_id TEXT);
        CREATE TABLE embedding_metadata (id INTEGER, key TEXT, string_value TEXT, int_value INTEGER,
                                         float_value REAL, bool_value INTEGER);
    """)
    n = [0]

    def add(coll, seg, rows):
        c.execute("INSERT INTO collections VALUES (?,?)", (coll, coll))
        c.execute("INSERT INTO segments VALUES (?,?,?)", (seg, "METADATA", coll))
        for eid, md in rows:
            n[0] += 1
            c.execute("INSERT INTO embeddings VALUES (?,?,?)", (n[0], seg, eid))
            for k, v in md.items():
                c.execute("INSERT INTO embedding_metadata VALUES (?,?,?,?,?,?)", (n[0], k, str(v), None, None, None))
    add("mempalace_drawers", "s1", drawers)
    add("mempalace_audit", "s2", audit)
    c.commit()
    c.close()


def _ing(mid, ts, text=SECRET, status="approved", session="", user=OWNER):
    after = {"text": text, "status": status, "user_id": user}
    if session:
        after["session_id"] = session
    return (f"a-{mid}-{ts}", {"mempalace_id": mid, "user_id": user, "action": "ingest", "actor": "chat_regex",
                              "timestamp": ts, "before": "{}", "after": json.dumps(after)})


@pytest.fixture
def world(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_x.py").write_text('LITERAL = "the repo literal sentence appears here"\n')
    audit = [
        _ing("zoe_present", "2026-09-01T00:00:01Z"),
        _ing("zoe_removed", "2026-09-01T00:00:02Z"),
        ("a-arch", {"mempalace_id": "zoe_removed", "user_id": OWNER, "action": "archive", "actor": "x",
                    "timestamp": "2026-09-02T00:00:00Z", "before": "{}", "after": "{}"}),
        _ing("zoe_lost", "2026-09-01T00:00:03Z"),
        _ing("zoe_once", "2026-09-01T00:00:04Z"),
        _ing("zoe_testsess", "2026-09-01T00:00:05Z", session="sess-hermes"),
        _ing("zoe_old", "2026-01-01T00:00:00Z"),                       # before --since: out of the ledger
    ]
    for i in range(6):                                                  # approved + re-ingested: impossible if it persisted
        audit.append(_ing("zoe_phantom", f"2026-09-0{i + 1}T10:00:00Z", text="the repo literal sentence appears here"))
    audit.append(("a-noop", {"mempalace_id": "zoe_present", "user_id": "family-admin", "action": "edit",
                             "actor": "consolidation", "timestamp": "2026-09-03T00:00:00Z",
                             "before": json.dumps({"id": "zoe_present"}), "after": json.dumps({"id": "zoe_present"})}))
    drawers = [
        ("zoe_present", {"user_id": OWNER, "status": "approved", "chroma:document": SECRET, "source": "voice"}),
        ("zoe_real_copy", {"user_id": OWNER, "status": "approved", "source": "conversation", "session_id": "telegram-1",
                           "chroma:document": "the repo literal sentence appears here"}),
        ("zoe_test_row", {"user_id": OWNER, "status": "approved", "source": "chat_regex",
                          "chroma:document": "the repo literal sentence appears here"}),
        ("zoe_demo", {"user_id": "demo_bar_ab0e0001", "status": "approved",
                      "chroma:document": "the repo literal sentence appears here"}),
        ("zoe_guest", {"user_id": "guest", "status": "approved", "chroma:document": SECRET}),
    ]
    db = tmp_path / "chroma.sqlite3"
    _make_db(str(db), drawers, audit)
    ref = tmp_path / "backup.json"
    ref.write_text(json.dumps([{"id": "zoe_lost", "text": SECRET}]))
    return {"db": str(db), "repo": str(repo), "ref": str(ref), "tmp": tmp_path}


def _report(world, **kw):
    drawers = mla.load_collection(mla.connect_ro(world["db"]), mla.DRAWERS)
    audit = mla.load_collection(mla.connect_ro(world["db"]), mla.AUDIT)
    refs = {"backup": mla.reference_ids(world["ref"])}
    lits = mla.repo_literals(world["repo"])
    return mla.reconcile(drawers, audit, owner=OWNER, since="2026-08-15", references=refs, literals=lits), drawers, audit, lits


def test_every_ledger_row_lands_in_exactly_one_bucket(world):
    rep, *_ = _report(world)
    by_id = {t["id"]: t["bucket"] for t in rep["table"]}
    assert by_id == {
        "zoe_present": "present",
        "zoe_removed": "recorded_removal",        # an archive audit row accounts for it
        "zoe_lost": "lost_recoverable",           # gone, unrecorded, but a backup still has it
        "zoe_once": "unexplained",                # gone, unrecorded, no copy, one event
        "zoe_testsess": "test_session_gone",
        "zoe_phantom": "phantom_audit_only",      # 6 approved ingests of one id cannot all be real
    }
    assert "zoe_old" not in by_id                 # outside --since
    assert rep["ledger_ids"] == 6 and sum(v["ids"] for v in rep["buckets"].values()) == 6


def test_control_one_ingest_is_not_a_phantom(world):
    rep, *_ = _report(world)
    assert next(t for t in rep["table"] if t["id"] == "zoe_once")["bucket"] != "phantom_audit_only"


def test_counts_events_and_literal_matches(world):
    rep, *_ = _report(world)
    assert rep["ledger_events"] == 11 and rep["buckets"]["phantom_audit_only"] == {"ids": 1, "events": 6}
    assert rep["missing_events_literal"] == 6      # the phantom's text equals a repo literal


def test_a_pending_batch_phantom_needs_the_suite_batch_evidence(world):
    """Pending rows CAN be legitimately re-ingested; they are phantoms only when the repeats sit
    inside many-ids-at-once suite batches (the real shape: 14 ids in one minute, 430 times)."""
    audit = [_ing(f"zoe_b{i}", f"2026-09-0{d}T10:00:00Z", status="pending")
             for d in range(1, 4) for i in range(9)]            # 9 ids x 3 minutes
    audit += [_ing("zoe_slow", f"2026-09-0{d}T{d}1:00:00Z", status="pending") for d in range(1, 4)]
    db = world["tmp"] / "b.sqlite3"
    _make_db(str(db), [], [a for a in audit])
    drawers, aud = [], mla.load_collection(mla.connect_ro(str(db)), mla.AUDIT)
    rep = mla.reconcile(drawers, aud, owner=OWNER, since="2026-08-15", references={}, literals={})
    by_id = {t["id"]: t["bucket"] for t in rep["table"]}
    assert all(by_id[f"zoe_b{i}"] == "phantom_audit_only" for i in range(9))
    assert by_id["zoe_slow"] == "unexplained"                    # control: same repeats, but not in a batch


def test_literal_rows_under_real_ids_only(world):
    _, drawers, _, lits = _report(world)
    rows = {r["id"]: r for r in mla.literal_rows(drawers, lits)}
    assert set(rows) == {"zoe_real_copy", "zoe_test_row"}          # demo_* and guest are not 'real ids'
    assert rows["zoe_real_copy"]["verdict"] == "likely_real_utterance_copied_into_a_test"
    assert rows["zoe_test_row"]["verdict"] == "likely_test_row"


def test_population_counts_guest_and_noop_rewrites(world):
    _, drawers, audit, _ = _report(world)
    pop = mla.population(drawers, audit)
    assert pop["guest_approved"] == 1 and pop["audit_edit_rows_noop_same_id"] == 1 and pop["audit_edit_rows"] == 1


def test_reference_gap_separates_recorded_from_unrecorded(world):
    _, drawers, audit, _ = _report(world)
    ref = world["tmp"] / "r.json"
    ref.write_text(json.dumps({"ids": ["zoe_present", "zoe_removed", "zoe_lost"]}))
    gap = mla.reference_gaps(drawers, audit, {"r": mla.reference_ids(str(ref))})["r"]
    assert gap["absent_now"] == 2 and gap["recorded_removal"] == 1 and gap["unrecorded"] == 1


def test_the_palace_is_opened_read_only_and_never_modified(world):
    before = hashlib.sha256(open(world["db"], "rb").read()).hexdigest()
    with pytest.raises(sqlite3.OperationalError):                  # the handle itself cannot write
        mla.connect_ro(world["db"]).execute("DELETE FROM embeddings")
    mla.main(["--db", world["db"], "--repo", world["repo"], "--json"])
    assert hashlib.sha256(open(world["db"], "rb").read()).hexdigest() == before


def test_no_memory_text_is_ever_printed(world, capsys):
    plan = world["tmp"] / "plan.json"
    mla.main(["--db", world["db"], "--repo", world["repo"], "--reference", world["ref"],
              "--plan-archive", str(plan)])
    human = capsys.readouterr().out
    mla.main(["--db", world["db"], "--repo", world["repo"], "--reference", world["ref"], "--json"])
    machine = capsys.readouterr().out
    for blob in (human, machine, plan.read_text()):
        assert SECRET not in blob and "the repo literal sentence appears here" not in blob


def test_plan_archive_lists_only_test_shaped_rows_and_names_the_normal_path(world):
    plan = world["tmp"] / "plan.json"
    mla.main(["--db", world["db"], "--repo", world["repo"], "--plan-archive", str(plan)])
    body = json.loads(plan.read_text())
    assert [r["id"] for r in body["rows"]] == ["zoe_test_row"]     # the real-utterance copy is NOT planned
    assert body["rows"][0]["decision"] == "archive" and "never raw delete" in body["how_to_apply"]


def test_the_script_has_no_write_or_delete_path():
    import ast

    tree = ast.parse(open(mla.__file__, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
            assert not any("chromadb" in n for n in names), "no chromadb: the palace is read via sqlite mode=ro"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in ("delete", "delete_collection", "executescript", "executemany", "commit"), node.func.attr
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and not node.value.startswith(("#!", "Reconcile")):
            head = node.value.lstrip().upper()
            assert not head.startswith(("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "CREATE ", "VACUUM", "REPLACE ", "PRAGMA ")), head[:30]
