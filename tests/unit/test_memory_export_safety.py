"""Pins the safety properties of the memory export + compaction tools.

All four came out of PR review and share a shape: a guard that appears to hold
while protecting nothing. That is worse than an absent guard, because it is
believed.

Runs against tmp_path fixtures and source inspection — never the live palace.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    path = ROOT / "scripts" / "maintenance" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


export_mod = _load("export_memory_store")


def _make_palace(tmp_path: Path) -> Path:
    """Minimal Chroma-shaped sqlite with one collection and two rows."""
    db = tmp_path / "chroma.sqlite3"
    c = sqlite3.connect(db)
    c.executescript(
        """
        CREATE TABLE collections (id TEXT, name TEXT, dimension INT,
                                  database_id TEXT, config_json_str TEXT);
        CREATE TABLE segments (id TEXT, type TEXT, scope TEXT, collection TEXT);
        CREATE TABLE embeddings (id INTEGER PRIMARY KEY, segment_id TEXT,
                                 embedding_id TEXT, seq_id BLOB, created_at TEXT);
        CREATE TABLE embedding_metadata (id INTEGER, key TEXT, string_value TEXT,
                                         int_value INT, float_value REAL, bool_value INT);
        INSERT INTO collections VALUES ('c1','mempalace_drawers',384,'d',NULL);
        INSERT INTO segments VALUES ('s1','t','METADATA','c1');
        INSERT INTO embeddings VALUES (1,'s1','row-1',1,'now'),(2,'s1','row-2',2,'now');
        INSERT INTO embedding_metadata VALUES
            (1,'chroma:document','Jason lives in Geraldton',NULL,NULL,NULL),
            (1,'user_id','jason',NULL,NULL,NULL),
            (2,'chroma:document','the dog is called Rex',NULL,NULL,NULL);
        """
    )
    c.commit()
    c.close()
    return db


def test_export_writes_private_permissions(tmp_path):
    """The export is the household's whole personal memory in plaintext; a
    permissive umask must not publish it to other local accounts."""
    db = _make_palace(tmp_path)
    out = tmp_path / "exports"
    old = os.umask(0o000)  # hostile umask: everything world-readable by default
    try:
        export_mod.export(str(db), str(out), compress=True, keep=5)
    finally:
        os.umask(old)
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    for f in out.iterdir():
        assert stat.S_IMODE(f.stat().st_mode) == 0o600, f"{f.name} is not 0600"


def test_export_roundtrips_documents(tmp_path):
    db = _make_palace(tmp_path)
    out = tmp_path / "exports"
    export_mod.export(str(db), str(out), compress=True, keep=5)
    blob = json.load(gzip.open(next(out.iterdir())))
    rows = blob["collections"]["mempalace_drawers"]
    assert blob["total_records"] == 2
    assert {r["document"] for r in rows} == {
        "Jason lives in Geraldton", "the dog is called Rex"}
    assert rows[0]["metadata"]["user_id"] == "jason"


def test_export_scan_runs_in_one_read_transaction():
    """Without a spanning transaction the per-collection and per-row queries see
    different database states, so the 'recovery' export can describe a row set
    that never existed at any instant."""
    src = (ROOT / "scripts" / "maintenance" / "export_memory_store.py").read_text()
    assert 'conn.execute("BEGIN")' in src
    assert 'conn.execute("COMMIT")' in src


def test_export_opens_database_read_only():
    """It must work when the native index is the broken thing, and must never
    be able to mutate the store it is backing up."""
    src = (ROOT / "scripts" / "maintenance" / "export_memory_store.py").read_text()
    assert "mode=ro" in src
    assert "import chromadb" not in src


def test_compaction_backs_up_the_palace_it_will_destroy():
    """`--palace COPY --execute X` must export COPY, not the default palace —
    otherwise a passing export clears the guard for an unbacked target."""
    src = (ROOT / "scripts" / "maintenance" / "check_memory_tombstones.py").read_text()
    assert '"--db", palace_db' in src, "pre-compaction export must be scoped to --palace"
    assert 'palace_db = os.path.join(palace, "chroma.sqlite3")' in src


def test_compaction_writes_a_salvage_copy_before_deleting():
    """A batch failing after delete_collection leaves recall degraded; the rows
    must exist on disk, not only in the memory of a process that may die."""
    src = (ROOT / "scripts" / "maintenance" / "check_memory_tombstones.py").read_text()
    salvage_at = src.index("compaction-salvage")
    delete_at = src.index("client.delete_collection")
    assert salvage_at < delete_at, "salvage copy must be written BEFORE the delete"
    assert "os.unlink(salvage)" in src, "salvage should be cleared on full success"


def test_compaction_reports_incomplete_rebuild_loudly():
    src = (ROOT / "scripts" / "maintenance" / "check_memory_tombstones.py").read_text()
    assert "IS INCOMPLETE" in src
    assert "Restore from the salvage copy" in src
