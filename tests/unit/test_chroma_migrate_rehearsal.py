"""Pure-function pins for the B0.8 Chroma 1.5.x migration rehearsal exporter.

Runs against a tiny synthetic Chroma-shaped SQLite in tmp_path. It never touches the live
palace and never imports chromadb, because the slim CI lane doesn't have it.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import sqlite3
import stat
import struct
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def _load():
    path = ROOT / "scripts" / "maintenance" / "chroma_migrate_rehearsal.py"
    spec = importlib.util.spec_from_file_location("chroma_migrate_rehearsal", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m = _load()

HNSW_CFG = json.dumps({"hnsw_configuration": {"space": "l2", "ef_construction": 100, "ef_search": 100,
                                               "M": 16, "resize_factor": 1.2}})


def _palace(tmp_path: Path, *, extra_collection: str | None = None) -> Path:
    """drawers (2 rows, typed metadata), audit (1 row), one sec leftover (1 row)."""
    store = tmp_path / "palace"
    store.mkdir()
    (store / "config.json").write_text('{"palace_path": "x"}')
    c = sqlite3.connect(store / "chroma.sqlite3")
    c.executescript(
        """
        CREATE TABLE collections (id TEXT, name TEXT, dimension INT, database_id TEXT, config_json_str TEXT);
        CREATE TABLE collection_metadata (collection_id TEXT, key TEXT, str_value TEXT, int_value INT,
                                          float_value REAL, bool_value INT);
        CREATE TABLE segments (id TEXT, type TEXT, scope TEXT, collection TEXT);
        CREATE TABLE segment_metadata (segment_id TEXT, key TEXT, str_value TEXT, int_value INT,
                                       float_value REAL, bool_value INT);
        CREATE TABLE embeddings (id INTEGER PRIMARY KEY, segment_id TEXT, embedding_id TEXT,
                                 seq_id BLOB, created_at TEXT);
        CREATE TABLE embedding_metadata (id INTEGER, key TEXT, string_value TEXT, int_value INT,
                                         float_value REAL, bool_value INT);
        CREATE TABLE embeddings_queue (seq_id INTEGER PRIMARY KEY, created_at TEXT, operation INT,
                                       topic TEXT, id TEXT, vector BLOB, encoding TEXT, metadata TEXT);
        """
    )
    cols = [("cd", "mempalace_drawers"), ("ca", "mempalace_audit"), ("cs", "mempalace_audit_sec_9c3775f8")]
    if extra_collection:
        cols.append(("cx", extra_collection))
    for cid, name in cols:
        c.execute("INSERT INTO collections VALUES (?,?,384,'db',?)", (cid, name, HNSW_CFG))
        c.execute("INSERT INTO segments VALUES (?, 'urn:chroma:segment/metadata/sqlite', 'METADATA', ?)",
                  (f"m-{cid}", cid))
        c.execute("INSERT INTO segments VALUES (?, 'urn:chroma:segment/vector/hnsw-local-persisted', 'VECTOR', ?)",
                  (f"v-{cid}", cid))
    # the live palace's shape: segment metadata overrides the collection config
    c.execute("INSERT INTO segment_metadata VALUES ('v-cd','hnsw:resize_factor',NULL,NULL,2.0,NULL)")
    c.execute("INSERT INTO segment_metadata VALUES ('v-cd','hnsw:space','l2',NULL,NULL,NULL)")
    rows = [
        (1, "m-cd", "d-1", {"chroma:document": ("s", "demo fact one"), "user_id": ("s", "demo_x"),
                             "confidence": ("f", 0.7), "access_count": ("i", 3), "pinned": ("b", 1)}),
        (2, "m-cd", "d-2", {"chroma:document": ("s", "demo fact two"), "user_id": ("s", "demo_x")}),
        (3, "m-ca", "a-1", {"chroma:document": ("s", "update d-1 by x for demo_x"), "action": ("s", "update")}),
        (4, "m-cs", "s-1", {"chroma:document": ("s", "sec leftover"), "action": ("s", "add")}),
    ]
    if extra_collection:
        rows.append((5, "m-cx", "x-1", {"chroma:document": ("s", "x")}))
    for rowid, seg, eid, meta in rows:
        c.execute("INSERT INTO embeddings VALUES (?,?,?,?,'now')", (rowid, seg, eid, rowid))
        for key, (kind, val) in meta.items():
            vals = {"s": (val, None, None, None), "i": (None, val, None, None),
                    "f": (None, None, val, None), "b": (None, None, None, val)}[kind]
            c.execute("INSERT INTO embedding_metadata VALUES (?,?,?,?,?,?)", (rowid, key, *vals))
    vec = struct.pack("<4f", 1.0, 0.0, 0.0, 0.0)
    c.execute("INSERT INTO embeddings_queue VALUES (1,'now',0,'persistent://default/default/cd','d-1',?,'FLOAT32',NULL)",
              (vec,))
    c.commit()
    c.close()
    return store


# --- collection filter -------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("mempalace_audit_sec_9c3775f8", True),
    ("mempalace_audit_sec_01e96ed5", True),
    ("mempalace_audit", False),
    ("mempalace_drawers", False),
    ("mempalace_audit_sec_", False),
    ("mempalace_audit_sec_XYZ", False),
    ("x_mempalace_audit_sec_ab", False),
    ("mempalace_audit_sec_ab_extra", False),
    ("", False),
])
def test_sec_leftover_filter(name, expected):
    assert m.is_leftover_sec_collection(name) is expected


def test_policy_is_fail_closed():
    assert m.collection_policy("mempalace_drawers") == "reembed"
    assert m.collection_policy("mempalace_audit") == "constant"
    assert m.collection_policy("mempalace_audit_sec_ab12") == "skip"
    with pytest.raises(ValueError, match="no migration policy"):
        m.collection_policy("mempalace_closets")


# --- hashing ------------------------------------------------------------------------------

def test_metadata_hash_is_type_sensitive_and_order_free():
    base = m.metadata_hash("doc", {"a": 1, "b": "x"})
    assert base == m.metadata_hash("doc", {"b": "x", "a": 1})  # key order does not matter
    assert base != m.metadata_hash("doc", {"a": 1.0, "b": "x"})  # int vs float
    assert base != m.metadata_hash("doc", {"a": True, "b": "x"})  # int vs bool
    assert base != m.metadata_hash("doc2", {"a": 1, "b": "x"})  # document is covered
    assert base != m.metadata_hash("doc", {"a": 1, "b": "x", "c": None})
    assert m.metadata_hash(None, None) == m.metadata_hash(None, {})


def test_collection_digest_order_independent_and_sensitive():
    h = {"a": "1", "b": "2"}
    assert m.collection_digest(h) == m.collection_digest({"b": "2", "a": "1"})
    assert m.collection_digest(h) != m.collection_digest({"a": "1", "b": "3"})
    assert m.collection_digest(h) != m.collection_digest({"a": "1"})


def test_effective_hnsw_segment_overrides_config():
    seg = {"hnsw:resize_factor": 2.0, "hnsw:space": "l2", "hnsw:M": 64}
    cfg = m.effective_hnsw(HNSW_CFG, seg)
    assert cfg == {"space": "l2", "ef_construction": 100, "ef_search": 100, "max_neighbors": 64,
                   "resize_factor": 2.0}
    assert m.effective_hnsw(None, {}) == {"space": "l2"}
    assert m.effective_hnsw("not json", {"hnsw:space": "cosine"}) == {"space": "cosine"}


def test_audit_constant_matches_memory_service():
    """The rebuilt audit vector must equal what the live code writes."""
    src = (ROOT / "services" / "zoe-data" / "memory_service.py").read_text()
    for node in ast.parse(src).body:
        target = getattr(node, "target", None) or (node.targets[0] if isinstance(node, ast.Assign) else None)
        if getattr(target, "id", None) == "_AUDIT_NULL_EMBEDDING":
            live = eval(compile(ast.Expression(node.value), "<const>", "eval"), {"__builtins__": {}})  # noqa: S307
            break
    else:  # pragma: no cover
        pytest.fail("_AUDIT_NULL_EMBEDDING not found in memory_service.py")
    assert tuple(live) == m.AUDIT_NULL_EMBEDDING
    assert len(m.AUDIT_NULL_EMBEDDING) == 384


def test_small_math_helpers():
    assert m.cosine([1, 0], [2, 0]) == pytest.approx(1.0)
    assert m.cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert m.cosine([0, 0], [1, 0]) == 0.0
    assert m.jaccard([], []) == 1.0
    assert m.jaccard(["a", "b"], ["b", "c"]) == pytest.approx(1 / 3)
    ids = [f"id{i}" for i in range(50)]
    assert m.sample_ids(ids, 10) == m.sample_ids(list(reversed(ids)), 10)
    assert len(m.sample_ids(ids, 10)) == 10


# --- export over the synthetic sqlite -----------------------------------------------------

def test_export_per_collection_skips_leftovers_and_is_private(tmp_path):
    store = _palace(tmp_path)
    out = tmp_path / "export"
    summary = m.export_store(store, out, live=tmp_path / "live-elsewhere")
    assert {n: i["count"] for n, i in summary["collections"].items()} == {
        "mempalace_drawers": 2, "mempalace_audit": 1}
    assert summary["skipped"] == {"mempalace_audit_sec_9c3775f8": 1}
    assert summary["collections"]["mempalace_drawers"]["hnsw"]["resize_factor"] == 2.0
    rows = [json.loads(ln) for ln in (out / "mempalace_drawers.jsonl").read_text().splitlines()]
    first = next(r for r in rows if r["id"] == "d-1")
    # typed decode: float stays float, int stays int, bool column becomes bool
    assert first["metadata"] == {"user_id": "demo_x", "confidence": 0.7, "access_count": 3, "pinned": True}
    assert first["hash"] == m.metadata_hash("demo fact one", first["metadata"])
    hashes = json.loads((out / "mempalace_drawers.hashes.json").read_text())
    assert summary["collections"]["mempalace_drawers"]["digest"] == m.collection_digest(hashes)
    assert not (out / "mempalace_audit_sec_9c3775f8.jsonl").exists()
    for f in out.iterdir():
        assert stat.S_IMODE(f.stat().st_mode) == 0o600, f.name
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    # the reader used by the 1.x-schema read-back proof agrees with the export
    back = m.read_hashes_from_sqlite(store / "chroma.sqlite3", names=["mempalace_drawers"])
    assert back["mempalace_drawers"] == hashes


def test_export_refuses_unknown_collection_before_writing(tmp_path):
    store = _palace(tmp_path, extra_collection="mempalace_closets")
    out = tmp_path / "export"
    with pytest.raises(ValueError, match="mempalace_closets"):
        m.export_store(store, out, live=tmp_path / "live-elsewhere")
    assert not any(out.glob("*.jsonl"))


def test_manifest_counts_and_skips(tmp_path):
    summary = m.export_store(_palace(tmp_path), tmp_path / "export", live=tmp_path / "x")
    man = m.build_manifest(summary, extra={"run": {"k": 1}})
    assert man["total_migrated"] == 3
    assert man["skipped_leftovers"] == {"mempalace_audit_sec_9c3775f8": 1}
    assert set(man["collections"]["mempalace_drawers"]) == {"count", "digest", "policy", "dimension", "hnsw"}
    assert man["run"] == {"k": 1}


def test_queue_vectors_decodes_float32(tmp_path):
    store = _palace(tmp_path)
    q = m.queue_vectors(store / "chroma.sqlite3", "mempalace_drawers")
    assert q == {"d-1": [1.0, 0.0, 0.0, 0.0]}
    assert m.queue_vectors(store / "chroma.sqlite3", "nope") == {}


# --- guards -------------------------------------------------------------------------------

def test_refuse_live_blocks_the_live_dir_and_children(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    with pytest.raises(SystemExit, match="REFUSED"):
        m.refuse_live(live, live)
    with pytest.raises(SystemExit, match="REFUSED"):
        m.refuse_live(live / "sub", live)
    assert m.refuse_live(tmp_path / "copy", live) == (tmp_path / "copy").resolve()
    with pytest.raises(SystemExit, match="REFUSED"):
        m.export_store(live, tmp_path / "out", live=live)


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync not installed")
def test_copy_store_snapshot_and_refusals(tmp_path):
    store = _palace(tmp_path)
    (store / "seg-1").mkdir()
    (store / "seg-1" / "data_level0.bin").write_bytes(b"\0" * 16)
    dst = tmp_path / "copy"
    info = m.copy_store(store, dst, live=tmp_path / "live-elsewhere")
    assert info["integrity"] == "ok"
    assert (dst / "seg-1" / "data_level0.bin").exists()
    assert (dst / "config.json").exists()
    conn = sqlite3.connect(dst / "chroma.sqlite3")
    assert conn.execute("SELECT count(*) FROM embeddings").fetchone()[0] == 4
    conn.close()
    with pytest.raises(SystemExit, match="not empty"):
        m.copy_store(store, dst, live=tmp_path / "live-elsewhere")
    with pytest.raises(SystemExit, match="inside the source"):
        m.copy_store(store, store / "nested", live=tmp_path / "live-elsewhere")


# --- comparison commands (and their negative controls) ------------------------------------

def _vec_files(tmp_path: Path):
    ids = ["a", "b", "c"]
    vecs = {"a": [1.0, 0.0, 0.0], "b": [0.0, 1.0, 0.0], "c": [0.0, 0.0, 1.0]}
    (tmp_path / "ids.json").write_text(json.dumps(ids))
    (tmp_path / "old.json").write_text(json.dumps(vecs))
    (tmp_path / "new.json").write_text(json.dumps(vecs))
    store = _palace(tmp_path)
    return ["--old", str(tmp_path / "old.json"), "--new", str(tmp_path / "new.json"),
            "--queue-db", str(store / "chroma.sqlite3"), "--ids-file", str(tmp_path / "ids.json")]


def test_compare_vectors_passes_and_mispair_control_fails(tmp_path, capsys):
    argv = _vec_files(tmp_path)
    assert m.main(["compare-vectors", *argv]) == 0
    assert m.main(["compare-vectors", *argv, "--mispair"]) == 1
    out = capsys.readouterr().out
    assert "PASS embedding_cosine" in out and "FAIL embedding_cosine" in out


def test_compare_recall_threshold(tmp_path):
    top = {q: [f"id{i}" for i in range(10)] for q in m.DEMO_QUERIES}
    (tmp_path / "old.json").write_text(json.dumps(top))
    (tmp_path / "same.json").write_text(json.dumps(top))
    worse = dict(top)
    worse[m.DEMO_QUERIES[0]] = [f"id{i}" for i in range(5)] + [f"zz{i}" for i in range(5)]
    (tmp_path / "worse.json").write_text(json.dumps(worse))
    assert m.main(["compare-recall", "--old", str(tmp_path / "old.json"), "--new", str(tmp_path / "same.json")]) == 0
    assert m.main(["compare-recall", "--old", str(tmp_path / "old.json"), "--new", str(tmp_path / "worse.json")]) == 1


def test_proof_table_negative_control_semantics():
    rec = {"step": "proof.x_neg", "rc": 1, "verdicts": ["FAIL x"], "wall_s": 0, "peak_rss_mb": 1,
           "expect_fail": True}
    assert m._proof_table([rec])[0]["verdict"] == "PASS"
    killed = dict(rec, rc=-9, verdicts=["FAIL x killed by signal 9"])
    assert m._proof_table([killed])[0]["verdict"] == "FAIL"  # a crash is not a detection
    silent = dict(rec, rc=0, verdicts=["PASS x"])
    assert m._proof_table([silent])[0]["verdict"] == "FAIL"
    normal = {"step": "proof.y", "rc": 0, "verdicts": ["PASS y"], "wall_s": 0, "peak_rss_mb": 1}
    assert m._proof_table([normal])[0]["verdict"] == "PASS"


def test_recall_parity_requires_identical_order_by_default(tmp_path):
    top = {q: [f"id{i}" for i in range(10)] for q in m.DEMO_QUERIES}
    swapped = dict(top)
    swapped[m.DEMO_QUERIES[3]] = ["id0", "id2", "id1"] + [f"id{i}" for i in range(3, 10)]
    (tmp_path / "old.json").write_text(json.dumps(top))
    (tmp_path / "swapped.json").write_text(json.dumps(swapped))
    argv = ["compare-recall", "--old", str(tmp_path / "old.json"), "--new", str(tmp_path / "swapped.json")]
    assert m.main(argv) == 1  # same ids, different order -> FAIL by default
    assert m.main([*argv, "--parity-tolerance"]) == 0  # top-1 kept, set identical -> opt-in PASS
    top1_moved = dict(top)
    top1_moved[m.DEMO_QUERIES[0]] = ["id1", "id0"] + [f"id{i}" for i in range(2, 10)]
    (tmp_path / "top1.json").write_text(json.dumps(top1_moved))
    assert m.main(["compare-recall", "--old", str(tmp_path / "old.json"), "--new", str(tmp_path / "top1.json"),
                   "--parity-tolerance"]) == 1  # tolerance never forgives a changed top-1
    missing = {q: v for q, v in top.items() if q != m.DEMO_QUERIES[5]}
    (tmp_path / "missing.json").write_text(json.dumps(missing))
    assert m.main(["compare-recall", "--old", str(tmp_path / "old.json"), "--new", str(tmp_path / "missing.json")]) == 1


def test_rehearsal_dir_validation(tmp_path):
    base = tmp_path / "rehearsals"
    base.mkdir()
    live = tmp_path / "live-palace"
    live.mkdir()
    assert m.rehearsal_dir(base, "2026-09-27", live) == (base / "2026-09-27").resolve()
    assert m.rehearsal_dir(base, "cutover-2026-09-27", live).name == "cutover-2026-09-27"
    for bad in ("..", "../2026-09-27", "2026-09-27/..", "x/2026-09-27", "", "2026-9-27", "/tmp"):
        with pytest.raises(SystemExit, match="REFUSED"):
            m.rehearsal_dir(base, bad, live)
    # the run root must never be, contain, or sit inside the live palace
    inner_live = base / "2026-09-27" / "palace"
    inner_live.mkdir(parents=True)
    with pytest.raises(SystemExit, match="overlaps the live store"):
        m.rehearsal_dir(base, "2026-09-27", inner_live)
    with pytest.raises(SystemExit, match="overlaps the live store"):
        m.rehearsal_dir(live, "2026-09-27", live)
    # a symlinked date dir that escapes the base is refused
    (base / "2026-01-01").symlink_to(tmp_path)
    with pytest.raises(SystemExit, match="REFUSED"):
        m.rehearsal_dir(base, "2026-01-01", live)


def test_fresh_only_deletes_directories_this_tool_made(tmp_path):
    foreign = tmp_path / "2026-09-27"
    foreign.mkdir()
    (foreign / "precious.txt").write_text("x")
    with pytest.raises(SystemExit, match="not created by this tool"):
        m.assert_replaceable(foreign)
    (foreign / m.REHEARSAL_MARKER).touch()
    m.assert_replaceable(foreign)
    legacy = tmp_path / "2026-09-26"
    legacy.mkdir()
    (legacy / "manifest.json").write_text(json.dumps({"schema": "zoe.b08.chroma-migration-rehearsal/1"}))
    m.assert_replaceable(legacy)
    # end to end: `run --fresh --date ..` refuses before touching anything
    with pytest.raises(SystemExit, match="REFUSED"):
        m.main(["run", "--fresh", "--date", "..", "--rehearsal-root", str(tmp_path),
                "--copy-from", str(tmp_path / "live")])
    assert (foreign / "precious.txt").exists()


def _rec(ok: bool) -> dict:
    return {"rc": 0 if ok else 1, "verdicts": ["PASS x"] if ok else ["FAIL x"]}


def test_parity_baseline_is_retained_only_after_every_step_passes(tmp_path):
    keep = tmp_path / "recall-parity"
    keep.mkdir()
    (keep / "old_top.json").write_text("PREVIOUS-OLD")
    (keep / "new_top.json").write_text("PREVIOUS-NEW")
    old, new = tmp_path / "old_top.json", tmp_path / "new_top.json"
    old.write_text("NEXT-OLD")
    new.write_text("NEXT-NEW")
    # failing parity proof (or either probe, or a killed step) -> previous pair untouched
    for records in ([_rec(True), _rec(True), _rec(False)], [_rec(False), _rec(True), _rec(True)],
                    [_rec(True), {"rc": -9, "verdicts": []}, _rec(True)]):
        assert m.retain_parity_baseline(records, old, new, keep) is False
        assert (keep / "old_top.json").read_text() == "PREVIOUS-OLD"
        assert (keep / "new_top.json").read_text() == "PREVIOUS-NEW"
    # a missing half is never published
    new.unlink()
    assert m.retain_parity_baseline([_rec(True)] * 3, old, new, keep) is False
    assert (keep / "new_top.json").read_text() == "PREVIOUS-NEW"
    # all passed -> the complete new pair replaces the old one, no staging leftovers
    new.write_text("NEXT-NEW")
    assert m.retain_parity_baseline([_rec(True)] * 3, old, new, keep) is True
    assert (keep / "old_top.json").read_text() == "NEXT-OLD"
    assert (keep / "new_top.json").read_text() == "NEXT-NEW"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["new_top.json", "old_top.json", "recall-parity"]
