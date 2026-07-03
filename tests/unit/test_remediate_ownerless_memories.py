from __future__ import annotations

import sys
import sqlite3
import types
from pathlib import Path

import pytest

from scripts.maintenance import remediate_ownerless_memories as remediate


class FakeCollection:
    def __init__(self, rows):
        self.rows = {
            row_id: {"document": document, "metadata": dict(metadata)}
            for row_id, document, metadata in rows
        }

    def get(self, ids=None, include=None):
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
    db = tmp_path / "chroma.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("create table fixture(id integer primary key)")
        conn.execute("insert into fixture(id) values (1)")
    return db


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
    assert not list(tmp_path.glob("*.bak"))
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
    assert paths == [str(tmp_path)]
    assert len(list(tmp_path.glob("chroma.sqlite3.ownerless-remediation.*.bak"))) == 1


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
    assert len(list(tmp_path.glob("chroma.sqlite3.ownerless-remediation.*.bak"))) == 1


def test_refuses_to_remediate_owned_allowlisted_row(fake_store, tmp_path):
    db = make_db(tmp_path)

    with pytest.raises(SystemExit, match="ownership metadata"):
        remediate.main(["--db", str(db), "--delete", "--ids", "zoe_owned"])


def test_refuses_missing_allowlisted_id(fake_store, tmp_path):
    db = make_db(tmp_path)

    with pytest.raises(SystemExit, match="target ids not found"):
        remediate.main(["--db", str(db), "--delete", "--ids", "zoe_missing"])


def test_all_ownerless_execute_requires_explicit_confirmation(fake_store, tmp_path):
    db = make_db(tmp_path)

    with pytest.raises(SystemExit):
        remediate.main(["--db", str(db), "--delete", "--all-ownerless", "--execute"])


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
