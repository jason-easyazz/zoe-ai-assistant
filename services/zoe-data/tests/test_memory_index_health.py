"""``memory_index_health`` — the shared tombstone report (stdlib only, no chroma on disk)."""
from __future__ import annotations

import math
import pickle
import sqlite3

import pytest

import memory_index_health as h

pytestmark = pytest.mark.ci_safe


def _palace(tmp_path, *, live, added, fresh=False, bad_pickle=False, vector_segment=True):
    p = tmp_path / "mempalace"
    p.mkdir()
    con = sqlite3.connect(p / "chroma.sqlite3")
    con.executescript(
        "CREATE TABLE collections (id TEXT, name TEXT);"
        "CREATE TABLE segments (id TEXT, scope TEXT, collection TEXT);"
        "CREATE TABLE embeddings (id INTEGER, segment_id TEXT);"
    )
    con.execute("INSERT INTO collections VALUES ('c1', 'mempalace_drawers')")
    con.execute("INSERT INTO segments VALUES ('meta1', 'METADATA', 'c1')")
    if vector_segment:
        con.execute("INSERT INTO segments VALUES ('seg1', 'VECTOR', 'c1')")
    con.executemany("INSERT INTO embeddings VALUES (?, 'meta1')", [(i,) for i in range(live)])
    con.commit()
    con.close()
    if vector_segment and not fresh:
        (p / "seg1").mkdir()
        if bad_pickle:
            (p / "seg1" / "index_metadata.pickle").write_bytes(b"garbage")
        else:
            (p / "seg1" / "index_metadata.pickle").write_bytes(pickle.dumps({"total_elements_added": added}))
    return p


def test_ratio_and_advice_match_the_measured_palace():
    assert h.tombstone_ratio(1591, 258) == pytest.approx(6.17, abs=0.01)
    assert h.compaction_advised(1591, 258) and not h.compaction_advised(300, 258)
    assert h.tombstone_ratio(0, 0) == 0.0 and math.isinf(h.tombstone_ratio(5, 0))
    assert not h.compaction_advised(5, 0)


def test_report_reads_the_persisted_index_metadata(tmp_path):
    row = h.index_health(_palace(tmp_path, live=258, added=1591))
    assert row["live_rows"] == 258 and row["elements_added"] == 1591
    assert row["tombstone_ratio"] == 6.17 and row["compaction_advised"] is True and row["fresh"] is False
    assert row["threshold"] == 3.0


def test_fresh_index_reports_ratio_one_not_unknown(tmp_path):
    """Right after a rebuild chroma has not persisted index_metadata.pickle yet: that is a
    clean index (ratio 1.0, not advised), never an unknown the weekly trigger could misread."""
    row = h.index_health(_palace(tmp_path, live=258, added=0, fresh=True))
    assert row["fresh"] is True and row["tombstone_ratio"] == 1.0 and row["elements_added"] == 258
    assert row["compaction_advised"] is False and "fresh" in row["note"]


def test_unreadable_pickle_is_unknown_and_never_advised(tmp_path):
    row = h.index_health(_palace(tmp_path, live=10, added=0, bad_pickle=True))
    assert row["elements_added"] is None and row["tombstone_ratio"] is None
    assert row["compaction_advised"] is False and "unreadable" in row["note"]


def test_missing_collection_and_metadata_only_collection(tmp_path):
    p = _palace(tmp_path, live=4, added=0, vector_segment=False)
    assert h.index_health(p)["elements_added"] is None
    missing = h.index_health(p, collection="nope")
    assert missing["compaction_advised"] is False and missing["note"] == "collection not found"


def test_script_reexports_the_shared_implementation():
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "compact_drawers_index.py"
    spec = importlib.util.spec_from_file_location("compact_drawers_index", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.report is h.report and mod.tombstone_ratio is h.tombstone_ratio
