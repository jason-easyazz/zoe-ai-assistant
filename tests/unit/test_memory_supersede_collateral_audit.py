"""scripts/maintenance/memory_supersede_collateral_audit.py: the read-only count of rows the conflict pass retired
for a different person's / attribute's fact (bake-off verification X1 / X2).

A miniature chroma-shaped sqlite (the real table layout); the matcher is the repo's own ``memory_supersede``.
Negative control: with the matcher blind to names the same rows are not counted; the palace file is never
written; no memory text is ever printed.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from scripts.maintenance import memory_supersede_collateral_audit as audit_mod

pytestmark = pytest.mark.ci_safe

OWNER = "demo-member"
SECRET_NAME = "Zorblax"      # must never appear in any output


def _make_db(path, drawers, audit_rows):
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
    add("mempalace_audit", "s2", audit_rows)
    c.commit()
    c.close()


def _row(eid, text, status="approved", by=None, user=OWNER):
    md = {"user_id": user, "status": status, "chroma:document": text}
    if by:
        md["superseded_by_id"] = by
    return (eid, md)


def _sup(eid, actor="implicit_supersede"):
    return (f"a-{eid}", {"mempalace_id": eid, "user_id": OWNER, "action": "supersede", "actor": actor})


@pytest.fixture
def db(tmp_path):
    drawers = [
        # X1: Leo's home retired by Dana's (a different person) - restorable
        _row("old-leo", f"User's friend {SECRET_NAME} lives in Perth.", "superseded", by="new-dana"),
        _row("new-dana", "User's friend Dana lives in Hobart."),
        # X2: a friend's job retired by the same friend's move (a different attribute) - restorable
        _row("old-job", "User's friend Ines works at a bookbinder.", "superseded", by="new-move"),
        _row("new-move", "User's friend Ines moved to Perth."),
        # a REAL change of the same thing: not collateral
        _row("old-home", "User's friend Tove lives in Cork.", "superseded", by="new-home"),
        _row("new-home", "User's friend Tove lives in Ghent."),
        # the owner's own home move: not collateral
        _row("old-own", "User lives in Hobart.", "superseded", by="new-own"),
        _row("new-own", "User moved to Cork."),
        # a retirement by another writer (a review edit), also a different person: counted apart
        _row("old-edit", "User's friend Ravi lives in Bergen.", "superseded", by="new-dana"),
        # a RICHER restatement that still names everyone the old row was about: an enrichment, not collateral
        _row("old-rich", "Emily lives in Hobart.", "superseded", by="new-rich"),
        _row("new-rich", "Emily, the user's wife and a friend of Dana, lives in Hobart."),
        _row("orphan", "User's friend Oskar lives in Lisbon.", "superseded", by="gone-id"),
    ]
    audit_rows = [_sup("old-leo"), _sup("old-job"), _sup("old-home"), _sup("old-own"), _sup("old-edit", "review")]
    path = tmp_path / "chroma.sqlite3"
    _make_db(path, drawers, audit_rows)
    return path


def _run(db, tmp_path, capsys, *extra):
    rc = audit_mod.main(["--db", str(db), "--json", *extra])
    out = capsys.readouterr().out
    return rc, out, json.loads(out)


def test_counts_the_two_collateral_classes_and_nothing_else(db, tmp_path, capsys):
    rc, out, rep = _run(db, tmp_path, capsys)
    t = rep["totals"]
    assert rc == 0
    assert t["other_person.implicit_pass"] == 1 and t["other_person.other_writer"] == 1
    assert t["other_attribute.implicit_pass"] == 1
    assert t["same_subject.implicit_pass"] == 2          # the friend's real move and the owner's own move
    assert t["same_subject.other_writer"] == 1             # the enrichment
    assert t["successor_missing"] == 1 and t["superseded_total"] == 7
    assert rep["restorable_rows"] == 3 and rep["restorable_via_implicit_pass"] == 2


def test_prints_counts_never_text(db, tmp_path, capsys):
    _rc, out, _rep = _run(db, tmp_path, capsys)
    assert SECRET_NAME not in out and "lives in" not in out and "bookbinder" not in out
    rc = audit_mod.main(["--db", str(db)])                # the human summary too
    assert rc == 0 and SECRET_NAME not in capsys.readouterr().out


def test_plan_restore_lists_ids_not_text_and_the_palace_is_untouched(db, tmp_path, capsys):
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    plan = tmp_path / "plan.json"
    _run(db, tmp_path, capsys, "--plan-restore", str(plan))
    body = plan.read_text()
    data = json.loads(body)
    assert sorted(r["id"] for r in data["rows"]) == ["old-edit", "old-job", "old-leo"]
    assert SECRET_NAME not in body and "text" not in {k for r in data["rows"] for k in r}
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_owner_filter(db, tmp_path, capsys):
    _rc, _out, rep = _run(db, tmp_path, capsys, "--owner", "someone-else")
    assert rep["restorable_rows"] == 0 and rep["totals"] == {}


def test_negative_control_a_matcher_blind_to_names_counts_nothing_for_x1(db, tmp_path, capsys, monkeypatch):
    """Put the pre-fix key back (no names): the two friends read as one subject, so the Leo / Ravi rows are NOT
    counted as other-person retirements. The audit is only as good as the shared matcher, which is the point."""

    monkeypatch.setattr(audit_mod._matcher(audit_mod.REPO), "subject_names", lambda text: frozenset())
    _rc, _out, rep = _run(db, tmp_path, capsys)
    assert rep["totals"].get("other_person.implicit_pass", 0) == 0
