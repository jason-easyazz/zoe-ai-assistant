from __future__ import annotations

import sys
import sqlite3
import types
from pathlib import Path

import pytest

from memory_service import _memory_visible_to_user
from scripts.maintenance import remediate_ownerless_memories as remediate


class FakeCollection:
    def __init__(self, rows):
        self.rows = {
            row_id: {"document": document, "metadata": dict(metadata)}
            for row_id, document, metadata in rows
        }
        self.get_calls = 0
        self.before_get = None

    def get(self, ids=None, include=None):
        self.get_calls += 1
        if self.before_get is not None:
            self.before_get(self)
        selected_ids = list(ids) if ids is not None else list(self.rows)
        found_ids = [row_id for row_id in selected_ids if row_id in self.rows]
        return {
            "ids": found_ids,
            "documents": [self.rows[row_id]["document"] for row_id in found_ids],
            "metadatas": [dict(self.rows[row_id]["metadata"]) for row_id in found_ids],
        }

    def update(self, ids, metadatas):
        for row_id, metadata in zip(ids, metadatas):
            self.rows[row_id]["metadata"] = dict(metadata)

    def delete(self, ids):
        for row_id in ids:
            self.rows.pop(row_id, None)


class FakeClient:
    def __init__(self, path, collection):
        self.path = path
        self.collection = collection

    def get_collection(self, name):
        assert name == remediate.DEFAULT_COLLECTION
        return self.collection


@pytest.fixture
def fake_store(monkeypatch):
    collection = FakeCollection(
        [
            ("zoe_01075f3d7a996d38eea9", "legacy one", {"added_by": "pi_agent"}),
            ("zoe_2859092b35dda8771a52", "legacy two", {"added_by": "pi_agent"}),
            (
                "zoe_owned",
                "owned",
                {"user_id": "family-admin", "wing": "family-admin", "visibility": "personal"},
            ),
        ]
    )
    paths = []

    def persistent_client(path):
        paths.append(path)
        return FakeClient(path, collection)

    monkeypatch.setitem(
        sys.modules,
        "chromadb",
        types.SimpleNamespace(PersistentClient=persistent_client),
    )
    return collection, paths


def make_db(tmp_path: Path) -> Path:
    store = tmp_path / "store"
    store.mkdir()
    db = store / "chroma.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("create table fixture(id integer primary key)")
        conn.execute("insert into fixture(id) values (1)")
    (store / "index-segment").mkdir()
    (store / "index-segment" / "header.bin").write_bytes(b"hnsw")
    return db


def backup_dirs(db: Path) -> list[Path]:
    return list(db.parent.parent.glob(f"store.{remediate.BACKUP_MARKER}*.bak"))


def test_audit_counts_ownerless_rows(fake_store, tmp_path, capsys):
    db = make_db(tmp_path)

    assert remediate.main(["--db", str(db), "--audit"]) == 0

    out = capsys.readouterr().out
    assert "Ownerless rows: 2" in out
    assert "zoe_01075f3d7a996d38eea9" in out
    assert "zoe_owned" not in out


def test_delete_dry_run_mutates_nothing(fake_store, tmp_path, capsys):
    collection, _ = fake_store
    db = make_db(tmp_path)

    assert (
        remediate.main(
            [
                "--db",
                str(db),
                "--delete",
                "--ids",
                "zoe_01075f3d7a996d38eea9,zoe_2859092b35dda8771a52",
            ]
        )
        == 0
    )

    assert "zoe_01075f3d7a996d38eea9" in collection.rows
    assert "zoe_2859092b35dda8771a52" in collection.rows
    assert not backup_dirs(db)
    assert "DRY RUN: would delete 2 row(s)" in capsys.readouterr().out


def test_delete_execute_removes_only_ownerless_allowlisted_rows(fake_store, tmp_path):
    collection, paths = fake_store
    db = make_db(tmp_path)

    assert (
        remediate.main(
            [
                "--db",
                str(db),
                "--delete",
                "--execute",
                "--ids",
                "zoe_01075f3d7a996d38eea9,zoe_2859092b35dda8771a52",
            ]
        )
        == 0
    )

    assert "zoe_01075f3d7a996d38eea9" not in collection.rows
    assert "zoe_2859092b35dda8771a52" not in collection.rows
    assert "zoe_owned" in collection.rows
    assert paths == [str(db.parent)]
    backups = backup_dirs(db)
    assert len(backups) == 1
    assert (backups[0] / "chroma.sqlite3").is_file()
    assert (backups[0] / "index-segment" / "header.bin").read_bytes() == b"hnsw"


def test_backfill_execute_sets_exact_metadata_and_creates_backup(fake_store, tmp_path):
    collection, _ = fake_store
    db = make_db(tmp_path)

    assert (
        remediate.main(
            [
                "--db",
                str(db),
                "--backfill",
                "--execute",
                "--ids",
                "zoe_01075f3d7a996d38eea9",
            ]
        )
        == 0
    )

    metadata = collection.rows["zoe_01075f3d7a996d38eea9"]["metadata"]
    assert metadata["user_id"] == "family-admin"
    assert metadata["wing"] == "family-admin"
    assert metadata["visibility"] == "personal"
    assert collection.rows["zoe_2859092b35dda8771a52"]["metadata"] == {"added_by": "pi_agent"}
    assert len(backup_dirs(db)) == 1


def test_refuses_to_remediate_owned_allowlisted_row(fake_store, tmp_path):
    db = make_db(tmp_path)

    with pytest.raises(SystemExit, match="ownership metadata"):
        remediate.main(["--db", str(db), "--delete", "--ids", "zoe_owned"])


def test_refuses_missing_allowlisted_id(fake_store, tmp_path):
    db = make_db(tmp_path)

    with pytest.raises(SystemExit, match="target ids not found"):
        remediate.main(["--db", str(db), "--delete", "--ids", "zoe_missing"])


def test_all_ownerless_execute_requires_explicit_confirmation(fake_store, tmp_path):
    collection, paths = fake_store
    db = make_db(tmp_path)

    with pytest.raises(SystemExit):
        remediate.main(["--db", str(db), "--delete", "--all-ownerless", "--execute"])
    assert "zoe_01075f3d7a996d38eea9" in collection.rows
    assert paths == []
    assert not backup_dirs(db)


def test_all_ownerless_execute_with_confirmation_removes_ownerless_only(fake_store, tmp_path):
    collection, _ = fake_store
    db = make_db(tmp_path)

    assert (
        remediate.main(
            [
                "--db",
                str(db),
                "--delete",
                "--all-ownerless",
                "--confirm-all-ownerless",
                "--execute",
            ]
        )
        == 0
    )

    assert "zoe_01075f3d7a996d38eea9" not in collection.rows
    assert "zoe_2859092b35dda8771a52" not in collection.rows
    assert "zoe_owned" in collection.rows


def test_refuses_missing_db_before_chroma_connect(fake_store, tmp_path):
    _, paths = fake_store
    missing = tmp_path / "missing" / "chroma.sqlite3"

    with pytest.raises(SystemExit, match="database file does not exist"):
        remediate.main(["--db", str(missing), "--audit"])

    assert paths == []


def test_backup_failure_refuses_before_chroma_connect(fake_store, tmp_path, monkeypatch):
    _, paths = fake_store
    db = make_db(tmp_path)

    def fail_copytree(*args, **kwargs):
        raise OSError("copy failed")

    monkeypatch.setattr(remediate.shutil, "copytree", fail_copytree)

    with pytest.raises(SystemExit, match="backup failed"):
        remediate.main(["--db", str(db), "--delete", "--execute"])

    assert paths == []
    assert not backup_dirs(db)


def test_execute_backup_happens_before_chroma_connect(fake_store, tmp_path, monkeypatch):
    collection, paths = fake_store
    db = make_db(tmp_path)
    events = []
    real_copytree = remediate.shutil.copytree

    def record_copytree(*args, **kwargs):
        events.append("backup")
        return real_copytree(*args, **kwargs)

    def persistent_client(path):
        events.append("connect")
        paths.append(path)
        return FakeClient(path, collection)

    monkeypatch.setattr(remediate.shutil, "copytree", record_copytree)
    monkeypatch.setitem(
        sys.modules,
        "chromadb",
        types.SimpleNamespace(PersistentClient=persistent_client),
    )

    assert (
        remediate.main(
            [
                "--db",
                str(db),
                "--delete",
                "--execute",
                "--ids",
                "zoe_01075f3d7a996d38eea9",
            ]
        )
        == 0
    )

    assert events[0] == "backup"
    assert "connect" in events
    assert events.index("backup") < events.index("connect")
    assert paths == [str(db.parent)]


def test_chroma_lock_error_prints_maintenance_window_guidance(monkeypatch, tmp_path):
    db = make_db(tmp_path)

    def locked_client(path):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setitem(
        sys.modules,
        "chromadb",
        types.SimpleNamespace(PersistentClient=locked_client),
    )

    with pytest.raises(SystemExit, match="maintenance window"):
        remediate.main(["--db", str(db), "--audit"])


def test_delete_revalidates_ownerless_immediately_before_mutation(fake_store, tmp_path):
    collection, _ = fake_store
    db = make_db(tmp_path)

    def gain_owner_after_selection(col):
        if col.get_calls == 2:
            col.rows["zoe_01075f3d7a996d38eea9"]["metadata"]["user_id"] = "family-admin"

    collection.before_get = gain_owner_after_selection

    with pytest.raises(SystemExit, match="gained ownership metadata"):
        remediate.main(
            [
                "--db",
                str(db),
                "--delete",
                "--execute",
                "--ids",
                "zoe_01075f3d7a996d38eea9",
            ]
        )

    assert "zoe_01075f3d7a996d38eea9" in collection.rows


def test_ids_cannot_be_combined_with_all_ownerless(fake_store, tmp_path):
    collection, paths = fake_store
    db = make_db(tmp_path)

    with pytest.raises(SystemExit):
        remediate.main(
            [
                "--db",
                str(db),
                "--delete",
                "--all-ownerless",
                "--ids",
                "zoe_01075f3d7a996d38eea9",
            ]
        )

    assert "zoe_01075f3d7a996d38eea9" in collection.rows
    assert paths == []


def test_empty_ids_allowlist_is_an_error(fake_store, tmp_path):
    collection, _ = fake_store
    db = make_db(tmp_path)

    with pytest.raises(SystemExit, match="empty allowlist"):
        remediate.main(["--db", str(db), "--delete", "--ids", " , "])

    assert "zoe_01075f3d7a996d38eea9" in collection.rows


@pytest.mark.parametrize(
    ("metadata", "ownerless"),
    [
        (None, True),
        ({}, True),
        ({"user_id": ""}, True),
        ({"user_id": "   ", "wing": "", "visibility": None}, True),
        ({"user_id": 123}, False),
        ({"wing": "family-admin"}, False),
        ({"visibility": "family"}, False),
        ({"visibility": "personal"}, False),
    ],
)
def test_is_ownerless_metadata_matches_visibility_boundary(metadata, ownerless):
    assert remediate.is_ownerless_metadata(metadata) is ownerless
    visible = _memory_visible_to_user(metadata or {}, "family-admin")
    if ownerless:
        assert visible is False
