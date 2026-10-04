"""``memory_index_health`` — the shared tombstone report (stdlib only, no chroma on disk).

The fixture mirrors what chroma 1.5.9 actually leaves behind: ``embeddings`` (live rows),
the vector segment's ``max_seq_id`` row + ``index_metadata.pickle`` (written together, only
every sync_threshold records) and the ``embeddings_queue`` write-ahead log, which keeps
every record above the vector segment's persisted seq (ADD=0 UPDATE=1 UPSERT=2 DELETE=3).
"""
from __future__ import annotations

import pickle
import sqlite3

import pytest

import memory_index_health as h

pytestmark = pytest.mark.ci_safe
TOPIC = "persistent://default/default/c1"


def _palace(tmp_path, *, live, added=0, persisted_seq=None, wal=(), fresh=False, bad_pickle=False,
            vector_segment=True, wal_tables=True, files_missing=False):
    """``wal`` = [(seq_id, operation), …]; ``persisted_seq`` writes the max_seq_id row."""
    p = tmp_path / "mempalace"
    p.mkdir(parents=True)
    con = sqlite3.connect(p / "chroma.sqlite3")
    con.executescript(
        "CREATE TABLE collections (id TEXT, name TEXT);"
        "CREATE TABLE segments (id TEXT, scope TEXT, collection TEXT);"
        "CREATE TABLE embeddings (id INTEGER, segment_id TEXT);"
    )
    if wal_tables:
        con.executescript(
            "CREATE TABLE max_seq_id (segment_id TEXT, seq_id INTEGER);"
            "CREATE TABLE embeddings_queue (seq_id INTEGER, created_at TEXT, operation INTEGER, topic TEXT, id TEXT);"
        )
        if persisted_seq is not None:
            con.execute("INSERT INTO max_seq_id VALUES ('seg1', ?)", (persisted_seq,))
        con.executemany("INSERT INTO embeddings_queue VALUES (?, '', ?, ?, 'x')", [(s, op, TOPIC) for s, op in wal])
        # another collection's records must never be counted
        con.execute("INSERT INTO embeddings_queue VALUES (9999, '', 0, 'persistent://default/default/other', 'y')")
    con.execute("INSERT INTO collections VALUES ('c1', 'mempalace_drawers')")
    con.execute("INSERT INTO segments VALUES ('meta1', 'METADATA', 'c1')")
    if vector_segment:
        con.execute("INSERT INTO segments VALUES ('seg1', 'VECTOR', 'c1')")
    con.executemany("INSERT INTO embeddings VALUES (?, 'meta1')", [(i,) for i in range(live)])
    con.commit()
    con.close()
    if vector_segment and not fresh and not files_missing:
        (p / "seg1").mkdir()
        if bad_pickle:
            (p / "seg1" / "index_metadata.pickle").write_bytes(b"garbage")
        else:
            (p / "seg1" / "index_metadata.pickle").write_bytes(pickle.dumps({"total_elements_added": added}))
    return p


def test_ratio_and_advice_match_the_measured_palace():
    assert h.tombstone_ratio(1591, 258) == pytest.approx(6.17, abs=0.01)
    assert h.compaction_advised(1591, 258) is True and h.compaction_advised(300, 258) is False


def test_ratio_is_never_non_finite_and_unknown_is_none():
    """Codex P2: live=0 with elements added was ``inf`` → Starlette refused to serialise."""
    assert h.tombstone_ratio(5, 0) is None and h.tombstone_ratio(0, 0) is None
    assert h.tombstone_ratio(None, 10) is None
    assert h.compaction_advised(5, 0) is None and h.compaction_advised(None, 10) is None


def test_report_adds_the_unpersisted_wal_tail_to_the_persisted_total(tmp_path):
    """The live palace on 2026-10-04: pickle says 1591 at seq 100; 7 records above it in the
    WAL (3 ADD/UPSERT, 2 UPDATE, 2 DELETE) → 1594 elements, 7 unpersisted ops."""
    wal = [(50, 0), (101, 0), (102, 2), (103, 1), (104, 3), (105, 2), (106, 1), (107, 3)]
    row = h.index_health(_palace(tmp_path, live=258, added=1591, persisted_seq=100, wal=wal))
    assert row["persisted_elements_added"] == 1591 and row["elements_added"] == 1594
    assert row["unpersisted_ops"] == 7 and row["fresh"] is False
    assert row["tombstone_ratio"] == 6.18 and row["ratio_known"] is True and row["compaction_advised"] is True
    assert row["threshold"] == 3.0


def test_fresh_index_is_counted_from_the_wal_not_assumed_clean(tmp_path):
    """Codex P2: a never-persisted index (no pickle, no max_seq_id row — chroma persists only
    every sync_threshold) used to be forced to ratio 1.0, so a small collection churning
    below the threshold could never be advised. The WAL is complete for such a segment:
    300 adds and 290 deletes for 10 live rows → ratio 30, advised."""
    wal = [(i, 0) for i in range(1, 301)] + [(300 + i, 3) for i in range(1, 291)]
    row = h.index_health(_palace(tmp_path, live=10, fresh=True, wal=wal))
    assert row["fresh"] is True and row["persisted_elements_added"] is None
    assert row["elements_added"] == 300 and row["unpersisted_ops"] == 590
    assert row["tombstone_ratio"] == 30.0 and row["compaction_advised"] is True and row["ratio_known"] is True
    assert "write-ahead log" in row["note"]


def test_just_rebuilt_index_with_only_its_adds_is_not_advised(tmp_path):
    wal = [(i, 0) for i in range(1, 259)]
    row = h.index_health(_palace(tmp_path, live=258, fresh=True, wal=wal))
    assert row["fresh"] is True and row["elements_added"] == 258
    assert row["tombstone_ratio"] == 1.0 and row["compaction_advised"] is False


def test_unknown_when_nothing_persisted_and_no_wal(tmp_path):
    """Never claim fresh from a missing file alone."""
    row = h.index_health(_palace(tmp_path, live=12, fresh=True, wal_tables=False))
    assert row["fresh"] is True and row["elements_added"] is None
    assert row["tombstone_ratio"] is None and row["ratio_known"] is False and row["compaction_advised"] is None
    assert "unknown" in row["note"]


def test_unreadable_pickle_and_missing_files_are_unknown(tmp_path):
    row = h.index_health(_palace(tmp_path, live=10, bad_pickle=True, persisted_seq=5))
    assert row["elements_added"] is None and row["compaction_advised"] is None and "unreadable" in row["note"]
    gone = h.index_health(_palace(tmp_path / "b", live=10, persisted_seq=5, files_missing=True))
    assert gone["elements_added"] is None and gone["fresh"] is False and "missing" in gone["note"]


def test_legacy_palace_without_wal_tables_still_reads_the_pickle(tmp_path):
    row = h.index_health(_palace(tmp_path, live=258, added=1591, wal_tables=False))
    assert row["elements_added"] == 1591 and row["unpersisted_ops"] is None and row["compaction_advised"] is True


def test_empty_collection_payload_is_json_compliant(tmp_path):
    import json

    row = h.index_health(_palace(tmp_path, live=0, added=40, persisted_seq=100))
    assert row["elements_added"] == 40 and row["tombstone_ratio"] is None and row["compaction_advised"] is None
    json.dumps(row, allow_nan=False)   # what Starlette does


def test_missing_collection_and_metadata_only_collection(tmp_path):
    p = _palace(tmp_path, live=4, vector_segment=False)
    assert h.index_health(p)["elements_added"] is None
    missing = h.index_health(p, collection="nope")
    assert missing["compaction_advised"] is None and missing["ratio_known"] is False
    assert missing["note"] == "collection not found"


def test_script_reexports_the_shared_implementation():
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "compact_drawers_index.py"
    spec = importlib.util.spec_from_file_location("compact_drawers_index", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.report is h.report and mod.tombstone_ratio is h.tombstone_ratio
