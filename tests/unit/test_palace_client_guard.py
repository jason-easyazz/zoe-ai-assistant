"""B0.8: store openers must match the palace's on-disk chromadb format, and the tombstone
report must read chromadb 1.x's index metadata.

Synthetic SQLite + pickle fixtures in tmp_path only; chromadb is never imported.
"""
from __future__ import annotations

import importlib.util
import pickle
import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guard = _load(ROOT / "scripts" / "lib" / "palace_client.py", "palace_client")
tomb = _load(ROOT / "scripts" / "maintenance" / "check_memory_tombstones.py", "check_memory_tombstones")


def _palace(tmp_path: Path, sysdb: int, segments=()) -> Path:
    d = tmp_path / "palace"
    d.mkdir()
    con = sqlite3.connect(d / "chroma.sqlite3")
    con.executescript("""
        CREATE TABLE migrations (dir TEXT, version INTEGER);
        CREATE TABLE segments (id TEXT, scope TEXT, collection TEXT);
        CREATE TABLE collections (id TEXT, name TEXT);
    """)
    con.executemany("INSERT INTO migrations VALUES ('sysdb', ?)", [(v,) for v in range(1, sysdb + 1)])
    for seg, coll, name in segments:
        con.execute("INSERT INTO segments VALUES (?, 'VECTOR', ?)", (seg, coll))
        con.execute("INSERT INTO collections VALUES (?, ?)", (coll, name))
    con.commit()
    con.close()
    return d


def test_palace_format_detection(tmp_path):
    assert guard.palace_format(str(tmp_path / "nope")) == "missing"
    assert guard.palace_format(str(_palace(tmp_path, 10))) == "1.x"


def test_palace_format_06(tmp_path):
    assert guard.palace_format(str(_palace(tmp_path, 9))) == "0.6"


@pytest.mark.parametrize("sysdb,client,ok", [
    (10, "1.5.9", True), (9, "0.6.3", True), (10, "0.6.3", False), (9, "1.5.9", False),
])
def test_check_client(tmp_path, sysdb, client, ok):
    d = str(_palace(tmp_path, sysdb))
    if ok:
        guard.check_client(d, client)
    else:
        with pytest.raises(SystemExit, match="REFUSED"):
            guard.check_client(d, client)


def test_missing_palace_is_not_refused(tmp_path):
    guard.check_client(str(tmp_path / "fresh"), "1.5.9")


class _PersistentData:  # stand-in for chromadb 0.6's pickled object
    def __init__(self, total, live):
        self.total_elements_added = total
        self.id_to_label = {str(i): i for i in range(live)}


def test_tombstones_reads_both_pickle_formats_and_reports_unflushed_segments(tmp_path, monkeypatch, capsys):
    d = _palace(tmp_path, 10, segments=[("s-dict", "c1", "mempalace_audit"),
                                        ("s-obj", "c2", "legacy"),
                                        ("s-pending", "c3", "mempalace_drawers")])
    (d / "s-dict").mkdir()
    (d / "s-dict" / "index_metadata.pickle").write_bytes(pickle.dumps(
        {"total_elements_added": 100, "id_to_label": {str(i): i for i in range(70)}}))
    (d / "s-obj").mkdir()
    monkeypatch.setattr(sys.modules["__main__"], "_PersistentData", _PersistentData, raising=False)
    (d / "s-obj" / "index_metadata.pickle").write_bytes(pickle.dumps(_PersistentData(10, 10)))
    (d / "s-pending").mkdir()
    (d / "s-pending" / "header.bin").write_bytes(b"\0")
    stats = {s["name"]: s for s in tomb._segment_stats(str(d))}
    assert (stats["mempalace_audit"]["added"], stats["mempalace_audit"]["live"]) == (100, 70)
    assert stats["legacy"]["tombstones"] == 0
    assert stats["mempalace_drawers"].get("pending") is True
    assert tomb.report(str(d), 0.25, 0.40) == 2  # the dict segment is 30% dead: seen, not 0/0
    assert "not persisted yet" in capsys.readouterr().out
