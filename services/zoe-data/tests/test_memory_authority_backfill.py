"""scripts/maintenance/memory_authority_backfill.py - the dry-run report (and the stamp apply)
for memory rows written before provenance existed.

Pinned: the report classifies with the SAME rule the runtime derives (``legacy_authority_basis``),
opens the palace READ-ONLY and writes nothing on --dry-run, prints counts and writer labels
only (never row text), finds the incident-shaped rows (model-reviewed, user-path label carried
forward), --apply is refused without both acknowledgements and while zoe-data listens, and the
apply merges metadata only, only into unstamped rows, idempotently. Synthetic palace (ci_safe).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

import memory_authority as ma

pytestmark = pytest.mark.ci_safe

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "memory_authority_backfill.py"
spec = importlib.util.spec_from_file_location("memory_authority_backfill", SCRIPT)
bf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bf)

SECRET = "Quillfeather"  # must never appear in any output
ROWS = [  # (embedding_id, user, text, status, extra metadata)
    ("r1", "member-a", f"User's dog is named {SECRET}.", "approved", {"source": "voice_fact"}),
    ("r2", "member-a", "User lives in Hobart.", "approved", {"source": "chat_regex"}),
    # the incident shape: a user-path label carried forward onto a row a MODEL wrote
    ("r3", "member-a", "User's cat is named Pip.", "approved",
     {"source": "chat_regex", "reviewed_by": "digest", "session_id": "tg-1"}),
    ("r4", "member-a", "User likes quiet mornings.", "approved", {"source": "digest"}),
    ("r5", "member-a", "User lives in Perth.", "approved",
     {"source": "turn_digest", "source_excerpt": "I live in Perth"}),
    ("r6", "member-a", "User lives in Perth.", "approved",
     {"source": "turn_digest", "source_excerpt": "Casey lives in Perth"}),
    ("r7", "member-a", "User likes tea.", "pending", {"source": "synthesis"}),
    ("r8", "member-b", "User likes tea.", "approved", {"source": "digest", "reviewed_by": "review_ui"}),
    ("r9", "member-b", "User's dog is named Rex.", "approved", {"source": "voice_fact", "authority": "inferred"}),
]


@pytest.fixture
def palace(tmp_path):
    p = tmp_path / "palace"
    p.mkdir()
    con = sqlite3.connect(p / "chroma.sqlite3")
    con.executescript("""
        CREATE TABLE collections (id TEXT, name TEXT);
        CREATE TABLE segments (id TEXT, scope TEXT, collection TEXT);
        CREATE TABLE embeddings (id INTEGER PRIMARY KEY, segment_id TEXT, embedding_id TEXT);
        CREATE TABLE embedding_metadata (id INTEGER, key TEXT, string_value TEXT, int_value INTEGER, float_value REAL, bool_value INTEGER);
        INSERT INTO collections VALUES ('c-d', 'mempalace_drawers'), ('c-a', 'mempalace_audit');
        INSERT INTO segments VALUES ('s-d', 'METADATA', 'c-d'), ('s-a', 'METADATA', 'c-a');
    """)
    for n, (eid, user, text, status, extra) in enumerate(ROWS, 1):
        con.execute("INSERT INTO embeddings VALUES (?, 's-d', ?)", (n, eid))
        meta = {"chroma:document": text, "user_id": user, "status": status, **extra}
        for k, v in meta.items():
            con.execute("INSERT INTO embedding_metadata VALUES (?, ?, ?, NULL, NULL, NULL)", (n, k, v))
    con.execute("INSERT INTO embeddings VALUES (99, 's-a', 'audit-1')")  # another collection: ignored
    con.execute("INSERT INTO embedding_metadata VALUES (99, 'chroma:document', 'x', NULL, NULL, NULL)")
    con.commit()
    con.close()
    return p


def _plan(palace, user=None):
    return {p["id"]: p for p in bf.classify(bf.load_rows(str(palace)), user)["plan"]}


def test_classification_is_the_runtime_rule(palace):
    got = {i: (p["authority"], p["basis"]) for i, p in _plan(palace).items()}
    assert got == {
        "r1": (ma.USER_STATED, "legacy_user_path"),
        "r2": (ma.USER_STATED, "legacy_user_path"),
        "r3": (ma.INFERRED, "legacy_reviewed_by_model"),
        "r4": (ma.INFERRED, "legacy_unanchored"),
        "r5": (ma.USER_STATED, "legacy_anchored_excerpt"),
        "r6": (ma.INFERRED, "legacy_unanchored"),
        "r7": (ma.INFERRED, "legacy_automatic_source"),
        "r8": (ma.USER_CONFIRMED, "legacy_reviewed_by_person"),
    }  # r9 already carries a stamp: left alone
    for row in bf.load_rows(str(palace)):
        if row["id"] in got:
            assert ma.row_authority(row["meta"], row["text"]) == got[row["id"]][0]


def test_counts(palace):
    rep = bf.classify(bf.load_rows(str(palace)))
    assert (rep["rows"], rep["already_stamped"], rep["to_stamp"]) == (9, 1, 8)
    assert rep["incident_shaped_rows"] == 1                      # r3
    assert rep["protected_approved"] == 4                        # r1 r2 r5 r8
    assert rep["by_authority_and_status"] == {
        "inferred/approved": 3, "inferred/pending": 1, "user_confirmed/approved": 1,
        "user_stated/approved": 3}
    assert bf.classify(bf.load_rows(str(palace)), "member-b")["to_stamp"] == 1


def test_dry_run_is_read_only_and_prints_no_text(palace, capsys):
    before = hashlib.sha256((palace / "chroma.sqlite3").read_bytes()).hexdigest()
    assert bf.main(["--dry-run", "--palace", str(palace)]) == 0
    out = capsys.readouterr().out
    assert "incident-shaped rows" in out and "voice_fact/user_stated" in out
    assert SECRET not in out and "Hobart" not in out
    assert bf.main(["--dry-run", "--json", "--palace", str(palace)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["to_stamp"] == 8 and "plan" not in doc
    assert hashlib.sha256((palace / "chroma.sqlite3").read_bytes()).hexdigest() == before


def test_refusals(palace, capsys, monkeypatch):
    assert bf.main(["--palace", str(palace)]) == 2                              # no mode
    assert bf.main(["--apply", "--palace", str(palace)]) == 2                   # not reviewed
    assert bf.main(["--apply", "--i-have-reviewed", "--palace", str(palace)]) == 2   # zoe-data not stopped
    monkeypatch.setattr(bf, "_service_listening", lambda *a, **k: True)
    assert bf.main(["--apply", "--i-have-reviewed", "--i-stopped-zoe-data", "--palace", str(palace)]) == 2
    assert "listening" in capsys.readouterr().err


class _Col:
    def __init__(self, metas):
        self.metas = metas
        self.updates = 0

    def get(self, *, ids, include=None):
        keep = [i for i in ids if i in self.metas]
        return {"ids": keep, "metadatas": [dict(self.metas[i]) for i in keep]}

    def update(self, *, ids, metadatas):
        self.updates += 1
        for i, m in zip(ids, metadatas):
            self.metas[i] = dict(m)


def test_apply_merges_metadata_only_into_unstamped_rows_idempotently(palace):
    rows = {r["id"]: dict(r["meta"]) for r in bf.load_rows(str(palace))}
    col = _Col(rows)
    plan = bf.classify(bf.load_rows(str(palace)))["plan"]
    assert bf.apply_stamps(col, plan, batch=3) == 8
    assert col.metas["r3"]["authority"] == ma.INFERRED and col.metas["r3"]["origin"] == "chat_regex"
    assert col.metas["r3"]["session_id"] == "tg-1"                    # existing metadata preserved
    assert col.metas["r9"]["authority"] == "inferred" and "authority_basis" not in col.metas["r9"]
    again = col.updates
    assert bf.apply_stamps(col, plan, batch=3) == 0 and col.updates == again   # idempotent
