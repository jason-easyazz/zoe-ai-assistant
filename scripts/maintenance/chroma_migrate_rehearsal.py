#!/usr/bin/env python3
"""B0.8 rehearsal: rebuild the MemPalace store for Chroma 1.5.x on a COPY, per collection.

WHY NOT `mempalace migrate`: MemPalace 3.10's `extract_drawers_from_sqlite()` selects every
embedding in `chroma.sqlite3` with no collection filter and `add()`s them all into
`mempalace_drawers`. Zoe's palace is mostly audit rows (`mempalace_audit`: ~21k rows vs a few
hundred drawers), plus leftover `mempalace_audit_sec_*` test collections. The tool would push
every audit summary into recall as a drawer and drop the audit collection. Opening the
0.6 palace with a 1.5 client instead migrates the sysdb in place, and that is forward-only.

So this script does the migration one collection at a time, never on the live directory:

  copy     rsync the segment dirs, then snapshot chroma.sqlite3 with SQLite's online backup API
           (a plain file copy of a live SQLite can tear). The source is only ever read.
  export   read each collection straight from the COPY's SQLite (`mode=ro`): ids, documents,
           typed metadata, a per-id hash. The 0.6.3 HNSW index is never loaded here. The
           `mempalace_audit_sec_*` leftovers are skipped and listed. Any other unknown
           collection fails the export, so an operator decides what happens to it.
  rebuild  (inside a throwaway chromadb 1.5.x venv) build a NEW persistent store. Drawers are
           re-embedded with the same all-MiniLM-L6-v2 ONNX model; the archive SHA256 is
           asserted. Audit rows get the constant vector `memory_service` already writes.
           Each collection keeps its effective HNSW settings (space and resize factor).
  prove    each proof runs in its own subprocess, because a chromadb SIGSEGV
           (upstream chroma#1218) must fail one probe, not the whole run.
  run      does all of it end to end. It gates on MemAvailable, runs every heavy step at nice 15
           inside a MemoryMax-capped `systemd-run --user --scope` when available, and records
           peak RSS (wait4 ru_maxrss, what `/usr/bin/time -v` reports) and wall time. It writes
           `manifest.json` and deletes the throwaway venv.

Runbook: docs/knowledge/chroma-1-5-migration.md. Tracker row: B0.8 in
docs/architecture/beat-the-bar-2026-program.md.

Privacy: the copy, the export and the migrated store contain memory text. They stay under a
0700 directory and are never printed. Probes print only counts, ids-free digests and scores.
Recall parity uses a synthetic `demo_b08_*` user only, on scratch copies that are then deleted.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Iterator

HERE = Path(__file__).resolve().parent
LIVE_STORE_DEFAULT = Path("~/.mempalace").expanduser()
REHEARSAL_ROOT_DEFAULT = Path("~/.zoe/chroma-migration-rehearsal").expanduser()
OLD_PYTHON_DEFAULT = Path("~/.zoe/venvs/zoe-data-py312/bin/python").expanduser()
UV_DEFAULT = shutil.which("uv") or str(Path("~/.local/bin/uv").expanduser())

TARGET_CHROMADB = "1.5.9"
# Same archive in chromadb 0.6.3 and 1.5.9 (`ONNXMiniLM_L6_V2._MODEL_SHA256`, both files).
MINILM_ARCHIVE_SHA256 = "913d7300ceae3b2dbc2c50d1de4baacab4be7b9380491c27fab7418616a16ec3"
MINILM_DIR = Path("~/.cache/chroma/onnx_models/all-MiniLM-L6-v2").expanduser()

DOC_KEY = "chroma:document"
DRAWERS = "mempalace_drawers"
AUDIT = "mempalace_audit"
# policy per collection: "reembed" = MiniLM from the stored document; "constant" = the fixed
# audit vector. Anything not listed and not a sec leftover fails the export (fail closed).
COLLECTION_POLICY = {DRAWERS: "reembed", AUDIT: "constant"}
_SEC_LEFTOVER_RE = re.compile(r"mempalace_audit_sec_[0-9a-f]+")

# MUST equal memory_service._AUDIT_NULL_EMBEDDING (pinned by tests/unit/test_chroma_migrate_rehearsal.py).
AUDIT_NULL_EMBEDDING: tuple[float, ...] = (1.0,) + (0.0,) * 383

DEMO_PREFIX = "demo_b08_"
MIN_AVAIL_MB_DEFAULT = 1200
SCOPE_MEMORY_MAX = "700M"
NICE = 15
EMBED_COSINE_FLOOR = 0.9999
RECALL_JACCARD_FLOOR = 0.9
EMBED_SAMPLE = 200
EMBED_CALL_CHUNK = 4
STEP_TIMEOUT_S = 1800

_CHILD_ENV = {
    "ANONYMIZED_TELEMETRY": "False",  # chromadb's posthog telemetry: off in every child
    "PYTHONUNBUFFERED": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
}


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested, stdlib only)
# ---------------------------------------------------------------------------

def is_leftover_sec_collection(name: str) -> bool:
    """True for the `mempalace_audit_sec_<hex>` test collections a security test left behind."""
    return bool(_SEC_LEFTOVER_RE.fullmatch(name or ""))


def collection_policy(name: str) -> str:
    """'reembed' | 'constant' | 'skip' for a known collection; raise for anything else."""
    if name in COLLECTION_POLICY:
        return COLLECTION_POLICY[name]
    if is_leftover_sec_collection(name):
        return "skip"
    raise ValueError(
        f"unknown collection {name!r}: no migration policy; add it to COLLECTION_POLICY "
        "deliberately (re-embed vs constant vector) before migrating"
    )


def _canonical_value(value: Any) -> list:
    # bool before int: bool is an int subclass, and chroma stores them in different columns.
    if isinstance(value, bool):
        return ["b", value]
    if isinstance(value, int):
        return ["i", value]
    if isinstance(value, float):
        return ["f", repr(value)]
    if isinstance(value, str):
        return ["s", value]
    if value is None:
        return ["n", None]
    return ["x", repr(value)]


def metadata_hash(document: str | None, metadata: dict | None) -> str:
    """Type-preserving sha256 of (document, metadata). int 1, float 1.0 and True all differ."""
    canon = {
        "doc": document,
        "meta": [[k, _canonical_value(v)] for k, v in sorted((metadata or {}).items())],
    }
    blob = json.dumps(canon, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def collection_digest(id_hashes: dict[str, str]) -> str:
    """Order-independent digest over `id -> metadata_hash`."""
    h = hashlib.sha256()
    for rid in sorted(id_hashes):
        h.update(f"{rid}\t{id_hashes[rid]}\n".encode("utf-8"))
    return h.hexdigest()


def effective_hnsw(config_json_str: str | None, segment_metadata: dict) -> dict:
    """The HNSW params 0.6.3 actually applies to a collection, as a 1.x `configuration['hnsw']`.

    0.6.3 builds its index from the VECTOR segment's `hnsw:*` metadata, which overrides the
    collection's `config_json_str` (the live drawers show config resize 1.2, segment 2.0).
    """
    cfg: dict = {}
    try:
        hc = (json.loads(config_json_str) if config_json_str else {}).get("hnsw_configuration") or {}
    except (TypeError, ValueError):
        hc = {}
    for src, dst in (("space", "space"), ("ef_construction", "ef_construction"),
                     ("ef_search", "ef_search"), ("M", "max_neighbors"),
                     ("resize_factor", "resize_factor")):
        if src in hc and hc[src] is not None:
            cfg[dst] = hc[src]
    seg_map = {"hnsw:space": "space", "hnsw:construction_ef": "ef_construction",
               "hnsw:search_ef": "ef_search", "hnsw:M": "max_neighbors",
               "hnsw:resize_factor": "resize_factor"}
    for key, dst in seg_map.items():
        if segment_metadata.get(key) is not None:
            cfg[dst] = segment_metadata[key]
    cfg.setdefault("space", "l2")  # chroma's default when nothing was ever set
    return cfg


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    a = list(a)
    b = list(b)
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def sample_ids(ids: Iterable[str], n: int) -> list[str]:
    """Deterministic, content-free sample: the n ids with the smallest sha256."""
    return sorted(ids, key=lambda i: hashlib.sha256(i.encode("utf-8")).hexdigest())[:n]


def build_manifest(export_summary: dict, *, extra: dict | None = None) -> dict:
    """Manifest = per-collection counts/digests/policy + skipped leftovers + run facts."""
    manifest = {
        "schema": "zoe.b08.chroma-migration-rehearsal/1",
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "collections": {
            name: {k: info[k] for k in ("count", "digest", "policy", "dimension", "hnsw")
                   if k in info}
            for name, info in sorted(export_summary.get("collections", {}).items())
        },
        "skipped_leftovers": dict(sorted(export_summary.get("skipped", {}).items())),
        "total_migrated": sum(i["count"] for i in export_summary.get("collections", {}).values()),
    }
    if extra:
        manifest.update(extra)
    return manifest


# ---------------------------------------------------------------------------
# SQLite reading (stdlib) — shared by export and by the 1.x-schema read-back proof
# ---------------------------------------------------------------------------

def _load_export_module():
    path = HERE / "export_memory_store.py"
    spec = importlib.util.spec_from_file_location("export_memory_store", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def connect_ro(db_path: Path) -> sqlite3.Connection:
    if not Path(db_path).exists():
        raise SystemExit(f"no such database: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.isolation_level = None
    return conn


def list_collections(conn: sqlite3.Connection) -> list[dict]:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(collections)")}
    cfg = "config_json_str" if "config_json_str" in cols else "NULL"
    out = []
    for cid, name, dim, cfg_str in conn.execute(
        f"SELECT id, name, dimension, {cfg} FROM collections ORDER BY name"
    ):
        seg_meta: dict = {}
        coll_meta: dict = {}
        for (key, s, i, f, b) in conn.execute(
            "SELECT sm.key, sm.str_value, sm.int_value, sm.float_value, sm.bool_value "
            "FROM segment_metadata sm JOIN segments s ON s.id = sm.segment_id "
            "WHERE s.collection = ? AND s.scope = 'VECTOR'", (cid,)
        ):
            seg_meta[key] = s if s is not None else i if i is not None else f if f is not None else (
                bool(b) if b is not None else None)
        for (key, s, i, f, b) in conn.execute(
            "SELECT key, str_value, int_value, float_value, bool_value "
            "FROM collection_metadata WHERE collection_id = ?", (cid,)
        ):
            coll_meta[key] = s if s is not None else i if i is not None else f if f is not None else (
                bool(b) if b is not None else None)
        out.append({"id": cid, "name": name, "dimension": dim, "config_json_str": cfg_str,
                    "segment_metadata": seg_meta, "collection_metadata": coll_meta})
    return out


def iter_collection_rows(conn: sqlite3.Connection, collection_id: str,
                         row_metadata=None) -> Iterator[tuple[str, str | None, dict]]:
    """(id, document, metadata) from the METADATA segment — the same SQL as export_memory_store."""
    if row_metadata is None:
        row_metadata = _load_export_module()._row_metadata
    rows = conn.execute(
        "SELECT e.id, e.embedding_id FROM embeddings e "
        "JOIN segments s ON s.id = e.segment_id "
        "WHERE s.collection = ? AND s.scope = 'METADATA' ORDER BY e.seq_id",
        (collection_id,),
    ).fetchall()
    for rowid, emb_id in rows:
        doc, meta = row_metadata(conn, rowid)
        yield emb_id, doc, meta


def read_hashes_from_sqlite(db_path: Path, names: Iterable[str] | None = None) -> dict[str, dict[str, str]]:
    conn = connect_ro(db_path)
    conn.execute("BEGIN")
    try:
        out: dict[str, dict[str, str]] = {}
        for c in list_collections(conn):
            if names is not None and c["name"] not in names:
                continue
            out[c["name"]] = {rid: metadata_hash(doc, meta)
                              for rid, doc, meta in iter_collection_rows(conn, c["id"])}
        return out
    finally:
        conn.execute("COMMIT")
        conn.close()


# ---------------------------------------------------------------------------
# Safety guards
# ---------------------------------------------------------------------------

def _real(p: Path | str) -> Path:
    return Path(os.path.realpath(os.path.expanduser(str(p))))


def refuse_live(path: Path | str, live: Path | str = LIVE_STORE_DEFAULT) -> Path:
    """Abort if `path` is (or is inside) the live store. Every mutating step calls this."""
    rp, lv = _real(path), _real(live)
    if rp == lv or lv in rp.parents:
        raise SystemExit(f"REFUSED: {rp} is the live store (or inside it); rehearsal works on copies only")
    return rp


def mem_available_mb() -> int:
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return -1


def gate_memory(min_mb: int, step: str) -> int:
    avail = mem_available_mb()
    if 0 <= avail < min_mb:
        raise SystemExit(f"REFUSED before {step}: MemAvailable {avail} MB < {min_mb} MB; retry in a quiet window")
    return avail


# ---------------------------------------------------------------------------
# copy
# ---------------------------------------------------------------------------

def copy_store(src: Path, dst: Path, live: Path = LIVE_STORE_DEFAULT) -> dict:
    src, dst = _real(src), _real(dst)
    refuse_live(dst, live)
    if dst == src or src in dst.parents:
        raise SystemExit(f"REFUSED: copy target {dst} is inside the source {src}")
    if not (src / "chroma.sqlite3").exists():
        raise SystemExit(f"no chroma.sqlite3 under {src}")
    if dst.exists() and any(dst.iterdir()):
        raise SystemExit(f"REFUSED: copy target {dst} is not empty")
    dst.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(dst, 0o700)
    t0 = time.monotonic()
    # 1) segment dirs + small files first; rsync only READS the source.
    subprocess.run(
        ["rsync", "-a", "--exclude", "chroma.sqlite3", "--exclude", "chroma.sqlite3-*",
         f"{src}/", f"{dst}/"],
        check=True,
    )
    # 2) THEN a consistent SQLite snapshot (online backup API from a read-only handle). Taking
    #    it after the segments means the sqlite log is never older than the HNSW files, so a
    #    0.6.3 open of the copy replays forward instead of meeting vectors it has no rows for.
    s = sqlite3.connect(f"file:{src / 'chroma.sqlite3'}?mode=ro", uri=True)
    d = sqlite3.connect(str(dst / "chroma.sqlite3"))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    os.chmod(dst / "chroma.sqlite3", 0o600)
    chk = sqlite3.connect(f"file:{dst / 'chroma.sqlite3'}?mode=ro", uri=True)
    try:
        integrity = chk.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        chk.close()
    if integrity != "ok":
        raise SystemExit(f"copy failed integrity_check: {integrity}")
    return {"source": str(src), "copy": str(dst), "integrity": integrity,
            "wall_s": round(time.monotonic() - t0, 2)}


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def _write_private(path: Path, text: str) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def export_store(src_copy: Path, out: Path, live: Path = LIVE_STORE_DEFAULT) -> dict:
    src_copy = refuse_live(src_copy, live)  # export reads the COPY, never live
    out = refuse_live(out, live)
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(out, 0o700)
    conn = connect_ro(src_copy / "chroma.sqlite3")
    conn.execute("BEGIN")
    summary: dict = {"source_copy": str(src_copy), "collections": {}, "skipped": {}}
    try:
        cols = list_collections(conn)
        # Decide every policy BEFORE writing anything: an unknown collection aborts the export.
        policies = {c["name"]: collection_policy(c["name"]) for c in cols}
        for c in cols:
            policy = policies[c["name"]]
            if policy == "skip":
                n = sum(1 for _ in iter_collection_rows(conn, c["id"]))
                summary["skipped"][c["name"]] = n
                continue
            hashes: dict[str, str] = {}
            missing_doc = 0
            path = out / f"{c['name']}.jsonl"
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for rid, doc, meta in iter_collection_rows(conn, c["id"]):
                    h = metadata_hash(doc, meta)
                    hashes[rid] = h
                    if doc is None:
                        missing_doc += 1
                    fh.write(json.dumps({"id": rid, "document": doc, "metadata": meta, "hash": h},
                                        ensure_ascii=False) + "\n")
            if policy == "reembed" and missing_doc:
                raise SystemExit(f"{c['name']}: {missing_doc} rows have no document; cannot re-embed")
            _write_private(out / f"{c['name']}.hashes.json", json.dumps(hashes, sort_keys=True))
            summary["collections"][c["name"]] = {
                "count": len(hashes),
                "digest": collection_digest(hashes),
                "policy": policy,
                "dimension": c["dimension"],
                "hnsw": effective_hnsw(c["config_json_str"], c["segment_metadata"]),
                "collection_metadata": c["collection_metadata"],
                "missing_documents": missing_doc,
            }
    finally:
        conn.execute("COMMIT")
        conn.close()
    _write_private(out / "export.json", json.dumps(summary, indent=1, sort_keys=True))
    return summary


# ---------------------------------------------------------------------------
# rebuild (runs under the chromadb 1.5.x interpreter)
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _self_maxrss_mb() -> int:
    import resource  # noqa: PLC0415
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024


def _iter_jsonl(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def _batched(it: Iterable, n: int) -> Iterator[list]:
    buf: list = []
    for x in it:
        buf.append(x)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf


def rebuild_store(export_dir: Path, dst: Path, src_copy: Path | None,
                  live: Path = LIVE_STORE_DEFAULT) -> dict:
    import chromadb  # noqa: PLC0415 — only importable in the lab venv
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction  # noqa: PLC0415
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2  # noqa: PLC0415

    if not chromadb.__version__.startswith("1.5."):
        raise SystemExit(f"rebuild must run under chromadb 1.5.x, got {chromadb.__version__}")
    dst = refuse_live(dst, live)
    if dst.exists() and any(dst.iterdir()):
        raise SystemExit(f"REFUSED: rebuild target {dst} is not empty (always build a NEW store)")
    dst.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(dst, 0o700)

    # Model identity: the package pin, our pin, and the bytes on disk must all agree.
    pkg_sha = ONNXMiniLM_L6_V2._MODEL_SHA256
    archive = MINILM_DIR / "onnx.tar.gz"
    disk_sha = _sha256_file(archive) if archive.exists() else None
    if not (pkg_sha == MINILM_ARCHIVE_SHA256 == disk_sha):
        raise SystemExit(f"MiniLM SHA mismatch: package={pkg_sha} pinned={MINILM_ARCHIVE_SHA256} disk={disk_sha}")
    model_onnx_sha = _sha256_file(MINILM_DIR / "onnx" / "model.onnx")

    summary = json.loads((export_dir / "export.json").read_text())
    client = chromadb.PersistentClient(path=str(dst))
    batch = max(1, min(500, int(client.get_max_batch_size())))
    ef = ONNXMiniLM_L6_V2()  # ONE session for the whole rebuild (DefaultEmbeddingFunction reloads per call)
    timings: dict[str, float] = {}
    for name, info in sorted(summary["collections"].items()):
        t0 = time.monotonic()
        hnsw = {k: v for k, v in info["hnsw"].items()
                if k in ("space", "ef_construction", "ef_search", "max_neighbors", "resize_factor")}
        col = client.create_collection(
            name,
            configuration={"hnsw": hnsw},
            metadata=info.get("collection_metadata") or None,
            # identity "default" == what chromadb and mempalace 3.10's spoofed EF both persist
            embedding_function=DefaultEmbeddingFunction(),
        )
        done = 0
        for chunk in _batched(_iter_jsonl(export_dir / f"{name}.jsonl"), batch):
            ids = [r["id"] for r in chunk]
            docs = [r["document"] for r in chunk]
            metas = [r["metadata"] or None for r in chunk]
            if info["policy"] == "reembed":
                # chroma's tokenizer pads EVERY text to 256 tokens and ORT's arena keeps the
                # peak, so the EF call size sets RSS: measured 706 MB at 32 texts/call, 469 at
                # 16, 264 at 4 (333 drawers, ORT 1.23.2). 4 keeps the rebuild inside the budget.
                embs = []
                for i in range(0, len(docs), EMBED_CALL_CHUNK):
                    embs.extend(list(map(float, v)) for v in ef(docs[i:i + EMBED_CALL_CHUNK]))
            elif info["policy"] == "constant":
                embs = [list(AUDIT_NULL_EMBEDDING) for _ in chunk]
            else:  # pragma: no cover - export never emits skip collections
                raise SystemExit(f"unexpected policy {info['policy']} for {name}")
            col.add(ids=ids, documents=docs, metadatas=metas, embeddings=embs)
            done += len(ids)
            print(f"rebuild {name} {done}/{info['count']} maxrss={_self_maxrss_mb()}MB",
                  file=sys.stderr, flush=True)
        if col.count() != info["count"]:
            raise SystemExit(f"{name}: rebuilt count {col.count()} != exported {info['count']}")
        timings[name] = round(time.monotonic() - t0, 2)

    # Palace config: carry the source's config.json and pin the embedder, so MemPalace 3.10's
    # onboarding default (embeddinggemma) can never silently re-point a MiniLM palace.
    cfg: dict = {}
    if src_copy and (src_copy / "config.json").exists():
        cfg = json.loads((src_copy / "config.json").read_text())
        for extra in (".migration_v1_done",):
            if (src_copy / extra).exists():
                shutil.copy2(src_copy / extra, dst / extra)
    cfg["embedding_model"] = "minilm"
    (dst / "config.json").write_text(json.dumps(cfg, indent=2) + "\n")
    return {
        "chromadb": chromadb.__version__,
        "minilm_archive_sha256": disk_sha,
        "minilm_model_onnx_sha256": model_onnx_sha,
        "embedding_function": "default (ONNXMiniLM_L6_V2)",
        "batch": batch,
        "per_collection_wall_s": timings,
    }


# ---------------------------------------------------------------------------
# Probes — each runs in its own subprocess under the interpreter it names
# ---------------------------------------------------------------------------

def _out(ok: bool, name: str, detail: str, data: dict | None = None) -> int:
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}")
    if data is not None:
        print("DATA " + json.dumps(data, sort_keys=True))
    return 0 if ok else 1


def _client(path: Path):
    import chromadb  # noqa: PLC0415
    return chromadb, chromadb.PersistentClient(path=str(path))


def probe_version(args) -> int:
    import chromadb  # noqa: PLC0415
    return _out(True, "version", chromadb.__version__, {"chromadb": chromadb.__version__})


def probe_counts(args) -> int:
    """(a) row counts: chromadb 1.x API AND the export SQL on the 1.x sqlite both equal the export."""
    summary = json.loads((Path(args.export) / "export.json").read_text())
    want = {n: i["count"] for n, i in summary["collections"].items()}
    _, client = _client(Path(args.store))
    names = sorted(c.name if hasattr(c, "name") else c for c in client.list_collections())
    api = {n: client.get_collection(n).count() for n in names}
    sql = {n: len(h) for n, h in read_hashes_from_sqlite(Path(args.store) / "chroma.sqlite3").items()}
    leftovers = [n for n in names if is_leftover_sec_collection(n)]
    cfg_ok = {}
    for n in want:
        hnsw = (client.get_collection(n).configuration or {}).get("hnsw") or {}
        exp_h = summary["collections"][n]["hnsw"]
        cfg_ok[n] = all(hnsw.get(k) == exp_h[k] for k in ("space", "resize_factor") if k in exp_h)
    ok = api == want and sql == want and not leftovers and all(cfg_ok.values())
    return _out(ok, "counts", f"api={api} sql={sql} want={want} leftovers={len(leftovers)} hnsw_ok={cfg_ok}",
                {"api": api, "sql": sql, "want": want, "hnsw_ok": cfg_ok})


def probe_hashes(args) -> int:
    """(b) per-id metadata+document hash equality through the chromadb 1.x API."""
    export = Path(args.export)
    summary = json.loads((export / "export.json").read_text())
    _, client = _client(Path(args.store))
    result = {}
    ok = True
    for name in sorted(summary["collections"]):
        want = json.loads((export / f"{name}.hashes.json").read_text())
        col = client.get_collection(name)
        got: dict[str, str] = {}
        offset, page = 0, 2000
        while True:
            r = col.get(include=["documents", "metadatas"], limit=page, offset=offset)
            ids = r.get("ids") or []
            for rid, doc, meta in zip(ids, r.get("documents") or [], r.get("metadatas") or []):
                got[rid] = metadata_hash(doc, meta)
            if len(ids) < page:
                break
            offset += page
        mism = sum(1 for k in want if got.get(k) != want[k])
        extra = len(set(got) - set(want))
        result[name] = {"want": len(want), "got": len(got), "mismatch": mism, "extra": extra,
                        "digest_equal": collection_digest(got) == collection_digest(want)}
        ok = ok and mism == 0 and extra == 0 and result[name]["digest_equal"]
    return _out(ok, "hashes", json.dumps(result, sort_keys=True), result)


_SENTINEL_FILE = "roundtrip_sentinels.json"


def probe_roundtrip(args) -> int:
    """(c) write round-trip on EACH collection, verified from a FRESH process each step.

    A 0.6→1.x store can stay readable while writes silently no-op, so the check is not
    "add() returned" but "a new process sees the write, then sees the delete".
    """
    store = refuse_live(args.store)
    _, client = _client(store)
    state_path = store.parent / _SENTINEL_FILE
    stage = args.stage
    if stage == "write":
        sentinels = {}
        for name in args.collections:
            col = client.get_collection(name)
            sid = f"b08-sentinel-{uuid.uuid4().hex[:12]}"
            before = col.count()
            kw: dict = dict(ids=[sid], documents=["b08 sentinel row"],
                            metadatas=[{"user_id": f"{DEMO_PREFIX}sentinel", "b08": True}])
            if name == AUDIT:
                kw["embeddings"] = [list(AUDIT_NULL_EMBEDDING)]
            col.upsert(**kw)
            sentinels[name] = {"id": sid, "before": before}
        state_path.write_text(json.dumps(sentinels))
        return _out(True, "roundtrip.write", f"{len(sentinels)} upserts")
    sentinels = json.loads(state_path.read_text())
    ok = True
    detail = {}
    for name, s in sentinels.items():
        col = client.get_collection(name)
        got = col.get(ids=[s["id"]], include=["metadatas"])
        present = bool(got.get("ids"))
        if stage == "verify-delete":
            meta_ok = present and (got["metadatas"][0] or {}).get("b08") is True
            count_ok = col.count() == s["before"] + 1
            col.delete(ids=[s["id"]])
            detail[name] = {"persisted": present, "meta_ok": meta_ok, "count_plus_one": count_ok}
            ok = ok and present and meta_ok and count_ok
        else:  # verify-gone
            count_ok = col.count() == s["before"]
            detail[name] = {"gone": not present, "count_restored": count_ok}
            ok = ok and (not present) and count_ok
    return _out(ok, f"roundtrip.{stage}", json.dumps(detail, sort_keys=True), detail)


def probe_vectors(args) -> int:
    """Dump the stored vectors for the sample ids (either client version) to a JSON file."""
    store = refuse_live(args.store)
    _, client = _client(store)
    ids = json.loads(Path(args.ids_file).read_text())
    col = client.get_collection(DRAWERS)
    got = col.get(ids=ids, include=["embeddings"])
    vecs = {rid: [float(x) for x in emb] for rid, emb in zip(got["ids"], got["embeddings"])}
    Path(args.out_file).write_text(json.dumps(vecs))
    return _out(len(vecs) == len(ids), "vectors.dump", f"{len(vecs)}/{len(ids)}")


def queue_vectors(db_path: Path, collection_name: str) -> dict[str, list[float]]:
    """Newest ADD/UPSERT/UPDATE vector per id still in the 0.6 embeddings_queue (FLOAT32)."""
    import struct  # noqa: PLC0415
    conn = connect_ro(db_path)
    try:
        cid = conn.execute("SELECT id FROM collections WHERE name=?", (collection_name,)).fetchone()
        if not cid:
            return {}
        out: dict[str, list[float]] = {}
        for rid, vec, enc in conn.execute(
            "SELECT id, vector, encoding FROM embeddings_queue WHERE topic LIKE ? "
            "AND vector IS NOT NULL ORDER BY seq_id", (f"%/{cid[0]}",)
        ):
            if (enc or "").upper() == "FLOAT32" and vec:
                out[rid] = list(struct.unpack(f"<{len(vec) // 4}f", vec))
        return out
    finally:
        conn.close()


def probe_recall(args) -> int:
    """(e) seed a synthetic demo user, run canned queries, dump top-10 ids, tear down."""
    store = refuse_live(args.store)
    _, client = _client(store)
    col = client.get_collection(DRAWERS)
    uid = args.demo_user
    if not uid.startswith(DEMO_PREFIX):
        raise SystemExit("recall probe refuses non-demo users")
    facts = DEMO_FACTS
    ids = [f"{uid}-fact-{i:02d}" for i in range(len(facts))]
    meta = {"user_id": uid, "wing": uid, "visibility": "personal", "memory_type": "fact",
            "status": "approved", "source": "b08_rehearsal", "confidence": 0.8}
    k = EMBED_CALL_CHUNK  # small EF calls: chroma pads every text to 256 tokens (see rebuild)
    for i in range(0, len(facts), k):
        col.add(ids=ids[i:i + k], documents=facts[i:i + k], metadatas=[dict(meta) for _ in facts[i:i + k]])
    top: dict[str, list[str]] = {}
    for i in range(0, len(DEMO_QUERIES), k):
        qs = DEMO_QUERIES[i:i + k]
        res = col.query(query_texts=qs, n_results=10, where={"user_id": uid}, include=["distances"])
        top.update({q: list(r) for q, r in zip(qs, res["ids"])})
    col.delete(ids=ids)
    left = len(col.get(where={"user_id": uid}).get("ids") or [])
    Path(args.out_file).write_text(json.dumps(top))
    return _out(left == 0, "recall.seed-query-teardown", f"queries={len(top)} demo_rows_left={left}")


def probe_mutate_one(args) -> int:
    """(negative-control helper) change ONE drawer's metadata on a scratch copy of the new store."""
    store = refuse_live(args.store)
    _, client = _client(store)
    col = client.get_collection(DRAWERS)
    first = col.get(limit=1, include=["metadatas"])
    rid, meta = first["ids"][0], dict(first["metadatas"][0] or {})
    meta["b08_negative_control"] = True
    col.update(ids=[rid], metadatas=[meta])
    return _out(True, "mutate.one", "1 drawer metadata changed")


def probe_old_client_on_new(args) -> int:
    """(f) NEGATIVE CONTROL, run under the 0.6.3 interpreter on a scratch copy of the NEW store.

    PASS = it fails loudly (raises). Returning rows would mean a downgrade is silently possible;
    the runbook's rollback (restore the directory, never re-pin) assumes it is not.
    """
    import chromadb  # noqa: PLC0415
    store = refuse_live(args.store)
    try:
        client = chromadb.PersistentClient(path=str(store))
        n = client.get_collection(DRAWERS).count()
    except BaseException as exc:  # noqa: BLE001 — any exception is the loud failure we want
        return _out(True, "negative.old-client", f"chromadb {chromadb.__version__} raised {type(exc).__name__}",
                    {"raised": type(exc).__name__, "message_head": str(exc)[:160]})
    return _out(False, "negative.old-client", f"chromadb {chromadb.__version__} opened it and counted {n}")


# Synthetic household facts for the demo user (no real memory text anywhere in this file).
DEMO_FACTS = [
    "The demo user's favourite colour is teal.",
    "The demo user drinks green tea every morning.",
    "The demo user has a dog called Biscuit.",
    "The demo user's sister lives in Adelaide.",
    "The demo user works as a marine biologist.",
    "The demo user is allergic to peanuts.",
    "The demo user plays the cello on weekends.",
    "The demo user's birthday is on the fourth of March.",
    "The demo user supports the Fremantle Dockers.",
    "The demo user is learning Japanese.",
    "The demo user drives a blue hatchback.",
    "The demo user's partner is called Sam.",
    "The demo user prefers the thermostat at 21 degrees.",
    "The demo user goes to bed around ten thirty.",
    "The demo user dislikes coriander.",
    "The demo user's favourite film is a space documentary.",
    "The demo user grows tomatoes in the back garden.",
    "The demo user runs five kilometres on Saturdays.",
    "The demo user's mother is a retired nurse.",
    "The demo user wants to visit Iceland next year.",
    "The demo user keeps a sourdough starter named Doughy.",
    "The demo user is vegetarian on weekdays.",
    "The demo user reads mystery novels before sleep.",
    "The demo user has two cats named Pixel and Byte.",
    "The demo user takes vitamin D in winter.",
    "The demo user's car service is due in November.",
    "The demo user plays chess online in the evenings.",
    "The demo user listens to jazz while cooking.",
    "The demo user's best friend is moving to Perth.",
    "The demo user is saving for a new kayak.",
    "The demo user volunteers at the library on Thursdays.",
    "The demo user's favourite dessert is lemon tart.",
    "The demo user wears glasses for reading.",
    "The demo user's grandmother turns ninety in June.",
    "The demo user is afraid of heights.",
    "The demo user collects vintage postcards.",
    "The demo user's work starts at eight thirty.",
    "The demo user is training for a half marathon.",
    "The demo user bakes bread on Sundays.",
    "The demo user uses a standing desk at home.",
]
DEMO_QUERIES = [
    "what colour do I like",
    "what do I drink in the morning",
    "what is my dog's name",
    "where does my sister live",
    "what is my job",
    "do I have any allergies",
    "what instrument do I play",
    "when is my birthday",
    "which football team do I support",
    "what language am I learning",
    "what car do I drive",
    "who is my partner",
    "what temperature do I like the house",
    "what time do I go to bed",
    "what food do I dislike",
    "what are my pets called",
    "what do I do on weekends",
    "where do I want to travel",
    "what exercise do I do",
    "tell me about my family",
]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

class Step:
    """Run one child process (nice, optional MemoryMax scope) and record wall + peak RSS."""

    def __init__(self, log: list, min_avail_mb: int, use_scope: bool, wait_s: int = 0):
        self.log = log
        self.min_avail_mb = min_avail_mb
        self.use_scope = use_scope
        self.wait_s = wait_s

    def gate(self, name: str) -> int:
        """MemAvailable gate. Waits (polling) up to `wait_s` for a quiet window, then refuses."""
        deadline = time.monotonic() + self.wait_s
        while True:
            avail = mem_available_mb()
            if avail < 0 or avail >= self.min_avail_mb or time.monotonic() >= deadline:
                return gate_memory(self.min_avail_mb, name)
            print(f"  [{name}] waiting: MemAvailable {avail} MB < {self.min_avail_mb} MB", flush=True)
            time.sleep(15)

    def __call__(self, name: str, argv: list[str], *, heavy: bool = True, check: bool = False,
                 expect_fail: bool = False) -> dict:
        avail = self.gate(name) if heavy else mem_available_mb()
        prefix: list[str] = []
        if heavy and self.use_scope:
            prefix = ["systemd-run", "--user", "--scope", "-q", "-p", f"MemoryMax={SCOPE_MEMORY_MAX}",
                      "-p", "MemorySwapMax=0", "--"]
        cmd = prefix + ["timeout", "--kill-after=15", str(STEP_TIMEOUT_S), "nice", "-n", str(NICE)] + argv
        env = dict(os.environ, **_CHILD_ENV)
        t0 = time.monotonic()
        with tempfile.TemporaryFile("w+") as out_fh, tempfile.TemporaryFile("w+") as err_fh:
            proc = subprocess.Popen(cmd, stdout=out_fh, stderr=err_fh, env=env, text=True)
            # Reap with wait4 ourselves: ru_maxrss of THIS child (systemd-run --scope, timeout and
            # nice all exec or wait for the real program) is what `/usr/bin/time -v` reports as
            # "Maximum resident set size". /usr/bin/time is not installed on the box.
            _, status, ru = os.wait4(proc.pid, 0)
            proc.returncode = os.waitstatus_to_exitcode(status)
            wall = time.monotonic() - t0
            out_fh.seek(0)
            err_fh.seek(0)
            stdout, stderr = out_fh.read(), err_fh.read()
        rss_kb = ru.ru_maxrss
        lines = [ln for ln in stdout.splitlines() if ln.startswith(("PASS ", "FAIL "))]
        data = [json.loads(ln[5:]) for ln in stdout.splitlines() if ln.startswith("DATA ")]
        rec = {
            "step": name, "rc": proc.returncode, "wall_s": round(wall, 2),
            "peak_rss_mb": round(rss_kb / 1024, 1) if rss_kb else None,
            "mem_available_before_mb": avail, "verdicts": lines, "data": data,
            "stderr_tail": _scrub_tail(stderr) if proc.returncode else "",
            "expect_fail": expect_fail,
        }
        if proc.returncode < 0:
            rec["verdicts"].append(f"FAIL {name} killed by signal {-proc.returncode}")
        self.log.append(rec)
        for ln in lines:
            print(f"  {ln[:200]}")
        print(f"  [{name}] rc={proc.returncode} wall={rec['wall_s']}s peak_rss={rec['peak_rss_mb']}MB")
        if check and proc.returncode != 0:
            raise SystemExit(f"step {name} failed (rc={proc.returncode}); stderr tail:\n{rec['stderr_tail']}")
        return rec


def _scrub_tail(text: str, n: int = 1200) -> str:
    # stderr may carry a traceback; never let a document slip through: keep only the tail lines
    # that look like exception/type lines or chroma/onnx log lines.
    keep = [ln for ln in (text or "").splitlines()
            if re.match(r"^(Traceback|  File |\w+(\.\w+)*(Error|Exception|Interrupt)\b|REFUSED|MiniLM|step )", ln)]
    return "\n".join(keep)[-n:]


def _self_argv(python: str, *args: str) -> list[str]:
    return [python, str(Path(__file__).resolve()), *args]


# Numeric packages the rehearsal pins to the OLD interpreter's versions, so the 1.5.x vectors are
# produced by the same ORT/numpy/tokenizers the cutover venv will run (only chromadb moves).
PINNED_FROM_OLD = ("numpy", "onnxruntime", "tokenizers")


def old_interpreter_pins(old_py: str) -> dict:
    code = ("import importlib.metadata as m, json; "
            f"print(json.dumps({{p: m.version(p) for p in {list(PINNED_FROM_OLD)!r}}}))")
    out = subprocess.run([old_py, "-c", code], check=True, capture_output=True, text=True).stdout
    return json.loads(out.strip().splitlines()[-1])


def make_venv(venv: Path, uv: str, exclude_newer: str, constraints: dict | None = None) -> dict:
    if venv.exists():
        shutil.rmtree(venv)
    subprocess.run([uv, "venv", "-q", "--python", "3.12", str(venv)], check=True)
    py = venv / "bin" / "python"
    t0 = time.monotonic()
    cmd = [uv, "pip", "install", "-q", "--python", str(py), "--exclude-newer", exclude_newer]
    if constraints:
        cfile = venv / "constraints.txt"
        cfile.write_text("".join(f"{k}=={v}\n" for k, v in sorted(constraints.items())))
        cmd += ["-c", str(cfile)]
    subprocess.run(cmd + [f"chromadb=={TARGET_CHROMADB}"], check=True)
    freeze = subprocess.run([uv, "pip", "freeze", "--python", str(py)], check=True,
                            capture_output=True, text=True).stdout
    pins = {ln.split("==")[0].lower(): ln.split("==")[1] for ln in freeze.splitlines() if "==" in ln}
    pyver = subprocess.run([str(py), "-c", "import platform;print(platform.python_version())"],
                           check=True, capture_output=True, text=True).stdout.strip()
    return {"python": pyver, "install_wall_s": round(time.monotonic() - t0, 1),
            "exclude_newer": exclude_newer,
            "key_pins": {k: pins.get(k) for k in ("chromadb", "onnxruntime", "numpy", "tokenizers",
                                                  "chroma-hnswlib", "pydantic")}}


_DATE_DIR_RE = re.compile(r"(?:[a-z][a-z0-9]*-)?\d{4}-\d{2}-\d{2}")
REHEARSAL_MARKER = ".b08-rehearsal"


def rehearsal_dir(base: Path | str, date: str, live: Path | str) -> Path:
    """The one directory a run may create or (with --fresh) delete, validated before any use.

    `date` must be `[prefix-]YYYY-MM-DD` (no separators, no `..`), the result must resolve to a
    DIRECT child of the resolved base (no symlink escape), and it must neither be, contain, nor
    sit inside the live palace.
    """
    if not _DATE_DIR_RE.fullmatch(date or ""):
        raise SystemExit(f"REFUSED: --date {date!r} must look like [prefix-]YYYY-MM-DD")
    base_r = _real(base)
    root = _real(base_r / date)
    if root.parent != base_r:
        raise SystemExit(f"REFUSED: {root} is not a direct child of the rehearsal root {base_r}")
    lv = _real(live)
    if root == lv or lv in root.parents or root in lv.parents or base_r == lv or base_r in lv.parents:
        raise SystemExit(f"REFUSED: {root} overlaps the live store {lv}")
    return root


def assert_replaceable(root: Path) -> None:
    """--fresh may only delete a directory this tool created (marker, or our manifest schema)."""
    if (root / REHEARSAL_MARKER).is_file():
        return
    man = root / "manifest.json"
    try:
        if man.is_file() and json.loads(man.read_text()).get("schema", "").startswith("zoe.b08."):
            return
    except (OSError, ValueError):
        pass
    raise SystemExit(f"REFUSED: {root} was not created by this tool (no {REHEARSAL_MARKER}); not deleting it")


def cmd_run(args) -> int:
    live = _real(args.copy_from)
    date = args.date or _dt.date.today().isoformat()
    root = rehearsal_dir(args.rehearsal_root, date, live)
    src, exp, dst, scratch = root / "src", root / "export", root / "dst", root / "scratch"
    venv = root / ".lab-venv"
    prior: dict = {}
    if args.prove_only:
        # Re-run only the proofs against an existing rehearsal (e.g. the first run stopped at the
        # RAM gate). The copy/export/rebuild facts of that run are carried into the new manifest.
        for need in (src, exp / "export.json", dst):
            if not need.exists():
                raise SystemExit(f"--prove-only: {need} missing; run the full rehearsal first")
        if (root / "manifest.json").exists():
            prior = json.loads((root / "manifest.json").read_text()).get("run", {})
    elif root.exists() and any(root.iterdir()):
        if not args.fresh:
            raise SystemExit(f"REFUSED: {root} exists; pass --fresh to replace this date's rehearsal")
        assert_replaceable(root)
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    (root / REHEARSAL_MARKER).touch()
    os.chmod(root, 0o700)
    os.chmod(root.parent, 0o700)
    log: list = [r for r in prior.get("steps", []) if not r["step"].startswith(("proof.", "vectors.", "recall."))]
    step = Step(log, args.min_avail_mb, use_scope=shutil.which("systemd-run") is not None and not args.no_scope,
                wait_s=args.wait_mem_s)
    facts: dict = {k: v for k, v in prior.items() if k not in ("steps", "total_wall_s")}
    facts.update({"live_store": str(live), "rehearsal_dir": str(root)})
    facts["mem_available_start_mb"] = step.gate("start")
    t_all = time.monotonic()
    try:
        # NOT realpath: a venv python is a symlink, and resolving it escapes the venv (no chromadb)
        old_py = os.path.expanduser(str(args.old_python))
        v_old = step("version.old", _self_argv(old_py, "probe", "version"), heavy=False, check=True)
        facts["old_chromadb"] = v_old["data"][0]["chromadb"] if v_old["data"] else None
        if not str(facts["old_chromadb"]).startswith("0.6."):
            raise SystemExit(f"--old-python must carry chromadb 0.6.x, has {facts['old_chromadb']}")
        facts["old_numeric_pins"] = old_interpreter_pins(old_py)

        if not args.prove_only:
            print(f"[copy] {live} -> {src}")
            facts["copy"] = copy_store(live, src, live)
            print(f"[export] {src} -> {exp}")
            t0 = time.monotonic()
            summary = export_store(src, exp, live)
            facts["export_wall_s"] = round(time.monotonic() - t0, 2)
        summary = json.loads((exp / "export.json").read_text())
        for n, i in sorted(summary["collections"].items()):
            print(f"  {n}: {i['count']} rows ({i['policy']}, hnsw {i['hnsw']})")
        print(f"  skipped leftovers: {len(summary['skipped'])} ({sum(summary['skipped'].values())} rows)")

        print(f"[venv] chromadb=={TARGET_CHROMADB} in {venv} (pinned {facts['old_numeric_pins']})")
        exclude_newer = (_dt.date.today() - _dt.timedelta(days=14)).isoformat()
        facts["lab_venv"] = make_venv(venv, args.uv, exclude_newer, facts["old_numeric_pins"])
        new_py = str(venv / "bin" / "python")

        if not args.prove_only:
            print(f"[rebuild] -> {dst}")
            rb = step("rebuild", _self_argv(new_py, "rebuild", "--export", str(exp), "--dst", str(dst),
                                            "--src-copy", str(src), "--live-store", str(live)), check=True)
            facts["rebuild"] = rb["data"][0] if rb["data"] else {}

        print("[prove]")
        if scratch.exists():
            shutil.rmtree(scratch)
        scratch.mkdir(mode=0o700)
        run_proofs(step, facts, summary, src=src, exp=exp, dst=dst, scratch=scratch,
                   old_py=old_py, new_py=new_py, live=live)
    finally:
        facts["total_wall_s"] = round(time.monotonic() - t_all, 1)
        if scratch.exists():
            shutil.rmtree(scratch, ignore_errors=True)
        if venv.exists() and not args.keep_venv:
            shutil.rmtree(venv, ignore_errors=True)
        facts["lab_venv_deleted"] = not venv.exists()
        facts["peak_rss_mb_max_step"] = max((r["peak_rss_mb"] or 0) for r in log) if log else None
        facts["steps"] = log
        if (exp / "export.json").exists():
            summary = json.loads((exp / "export.json").read_text())
            manifest = build_manifest(summary, extra={"run": facts, "proofs": _proof_table(log)})
            _write_private(root / "manifest.json", json.dumps(manifest, indent=1, sort_keys=True))
            print(f"[manifest] {root / 'manifest.json'}")
    table = _proof_table(log)
    print("\nPROOF TABLE")
    for row in table:
        print(f"  {row['verdict']:4s} {row['proof']}  wall={row['wall_s']}s peak_rss={row['peak_rss_mb']}MB")
    print(f"peak RSS (largest step): {facts.get('peak_rss_mb_max_step')} MB; total wall {facts['total_wall_s']} s")
    return 0 if table and all(r["verdict"] == "PASS" for r in table) else 1


def _proof_table(log: list) -> list[dict]:
    rows = []
    for rec in log:
        if not rec["step"].startswith("proof."):
            continue
        verdicts = rec["verdicts"] or [f"FAIL {rec['step']} no verdict (rc={rec['rc']})"]
        if rec.get("expect_fail"):
            # A negative control passes only when the probe ran to completion and said FAIL
            # (rc 1). A crash, a kill or a timeout is not a detection.
            ok = rec["rc"] == 1 and bool(rec["verdicts"]) and all(v.startswith("FAIL") for v in rec["verdicts"])
        else:
            ok = rec["rc"] == 0 and all(v.startswith("PASS") for v in verdicts)
        rows.append({"proof": rec["step"][6:], "verdict": "PASS" if ok else "FAIL",
                     "wall_s": rec["wall_s"], "peak_rss_mb": rec["peak_rss_mb"]})
    return rows


def _step_passed(rec: dict | None) -> bool:
    return bool(rec) and rec.get("rc") == 0 and bool(rec.get("verdicts")) and all(
        v.startswith("PASS") for v in rec["verdicts"])


def retain_parity_baseline(records: list, old_top: Path, new_top: Path, keep: Path) -> bool:
    """Publish the (old_top, new_top) pair as the post-cutover parity baseline, ALL OR NOTHING.

    Only when both recall probes AND the parity proof passed, and both files exist. The pair is
    staged in a sibling directory and swapped in by rename, so `keep` is always either the
    previous complete pair or the new complete pair, never a half pair. On any failure the
    previous pair is left untouched and False is returned.
    """
    if not all(_step_passed(r) for r in records) or not (old_top.is_file() and new_top.is_file()):
        return False
    stage = keep.with_name(keep.name + f".staging-{os.getpid()}")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(mode=0o700, parents=True)
    for f in (old_top, new_top):
        shutil.copy2(f, stage / f.name)
    retired = keep.with_name(keep.name + f".previous-{os.getpid()}")
    if keep.exists():
        os.rename(keep, retired)
    os.rename(stage, keep)
    if retired.exists():
        shutil.rmtree(retired, ignore_errors=True)
    return True


def _scratch_copy(src: Path, dst: Path) -> Path:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True)
    return dst


def run_proofs(step: Step, facts: dict, summary: dict, *, src: Path, exp: Path, dst: Path,
               scratch: Path, old_py: str, new_py: str, live: Path) -> None:
    common = ["--live-store", str(live)]
    # (a) counts and (b) hashes read the kept migrated store itself (1.x client, read paths).
    step("proof.a_counts", _self_argv(new_py, "probe", "counts", "--store", str(dst), "--export", str(exp), *common))
    step("proof.b_hashes", _self_argv(new_py, "probe", "hashes", "--store", str(dst), "--export", str(exp), *common))

    # (b-neg) NEGATIVE CONTROL for (b): one changed metadata value on a scratch copy must be caught.
    mut = _scratch_copy(dst, scratch / "mutated" / "store")
    step("mutate.one", _self_argv(new_py, "probe", "mutate-one", "--store", str(mut), *common), check=True)
    step("proof.b_neg_hashes_catch_one_mutation", _self_argv(new_py, "probe", "hashes", "--store", str(mut),
                                                           "--export", str(exp), *common), expect_fail=True)
    shutil.rmtree(mut.parent, ignore_errors=True)

    # (c) write round-trip on each collection, three fresh processes, on a SCRATCH copy.
    rt = _scratch_copy(dst, scratch / "roundtrip" / "store")
    colls = sorted(summary["collections"])
    for stage in ("write", "verify-delete", "verify-gone"):
        step(f"proof.c_roundtrip_{stage}", _self_argv(new_py, "probe", "roundtrip", "--store", str(rt),
                                                       "--stage", stage, "--collections", *colls, *common))

    # (d) embedding parity: stored 0.6.3 vectors (old client on a scratch copy of the source)
    #     and the vectors still in the sqlite embeddings_queue vs the rebuilt 1.x vectors.
    drawer_hashes = json.loads((exp / f"{DRAWERS}.hashes.json").read_text())
    ids = sample_ids(drawer_hashes.keys(), EMBED_SAMPLE)
    ids_file = scratch / "sample_ids.json"
    ids_file.write_text(json.dumps(ids))
    old_src = _scratch_copy(src, scratch / "old-src")
    old_vec, new_vec = scratch / "old_vectors.json", scratch / "new_vectors.json"
    step("vectors.old", _self_argv(old_py, "probe", "vectors", "--store", str(old_src), "--ids-file",
                                   str(ids_file), "--out-file", str(old_vec), *common))
    step("vectors.new", _self_argv(new_py, "probe", "vectors", "--store", str(dst), "--ids-file",
                                   str(ids_file), "--out-file", str(new_vec), *common))
    step("proof.d_embedding_cosine", _self_argv(sys.executable, "compare-vectors", "--old", str(old_vec),
                                                "--new", str(new_vec), "--queue-db", str(src / "chroma.sqlite3"),
                                                "--ids-file", str(ids_file)), heavy=False)

    # (d-neg) NEGATIVE CONTROL for (d): the same vectors paired with the WRONG ids must fail.
    step("proof.d_neg_cosine_catches_mispairing", _self_argv(sys.executable, "compare-vectors", "--old", str(old_vec),
                                                             "--new", str(new_vec), "--queue-db", str(src / "chroma.sqlite3"),
                                                             "--ids-file", str(ids_file), "--mispair"),
         heavy=False, expect_fail=True)

    # (e) recall parity for a synthetic demo user, seeded into scratch copies of BOTH stores.
    demo = f"{DEMO_PREFIX}{uuid.uuid4().hex[:8]}"
    facts["recall_demo_user"] = demo
    old_r = _scratch_copy(src, scratch / "recall-old")
    new_r = _scratch_copy(dst, scratch / "recall-new")
    old_top, new_top = scratch / "old_top.json", scratch / "new_top.json"
    r_old = step("recall.old", _self_argv(old_py, "probe", "recall", "--store", str(old_r), "--demo-user", demo,
                                          "--out-file", str(old_top), *common))
    r_new = step("recall.new", _self_argv(new_py, "probe", "recall", "--store", str(new_r), "--demo-user", demo,
                                          "--out-file", str(new_top), *common))
    r_par = step("proof.e_recall_parity", _self_argv(sys.executable, "compare-recall", "--old", str(old_top),
                                                     "--new", str(new_top)), heavy=False)
    # Keep the two top-10 files (synthetic demo ids only): after a cutover the 0.6.3 client
    # is gone, so this is the only "old" side a post-install live parity check can compare to.
    facts["recall_parity_baseline_retained"] = retain_parity_baseline(
        [r_old, r_new, r_par], old_top, new_top, scratch.parent / "recall-parity")

    # (f) negative control: the 0.6.3 client must fail loudly on (a scratch copy of) the new store.
    neg = _scratch_copy(dst, scratch / "negative" / "store")
    step("proof.f_old_client_fails_loudly", _self_argv(old_py, "probe", "old-client-on-new", "--store",
                                                       str(neg), *common))


def cmd_compare_vectors(args) -> int:
    old = json.loads(Path(args.old).read_text())
    new = json.loads(Path(args.new).read_text())
    ids = json.loads(Path(args.ids_file).read_text())
    both = [i for i in ids if i in old and i in new]
    if args.mispair:  # negative control: rotate the pairing by one id
        rot = both[1:] + both[:1]
        new = {i: new[j] for i, j in zip(both, rot)}
    cos = [cosine(old[i], new[i]) for i in both]
    q = queue_vectors(Path(args.queue_db), DRAWERS)
    qcos = [cosine(q[i], new[i]) for i in both if i in q]
    data = {"sample": len(ids), "compared": len(both), "min": min(cos) if cos else None,
            "mean": sum(cos) / len(cos) if cos else None,
            "below_floor": sum(1 for c in cos if c < EMBED_COSINE_FLOOR),
            "sqlite_queue_compared": len(qcos), "sqlite_queue_min": min(qcos) if qcos else None}
    ok = len(both) == len(ids) and data["below_floor"] == 0 and all(c >= EMBED_COSINE_FLOOR for c in qcos)
    return _out(ok, "embedding_cosine",
                f"n={len(both)}/{len(ids)} min={data['min']} floor={EMBED_COSINE_FLOOR} "
                f"queue_n={len(qcos)} queue_min={data['sqlite_queue_min']}", data)


def cmd_compare_recall(args) -> int:
    old = json.loads(Path(args.old).read_text())
    new = json.loads(Path(args.new).read_text())
    per = {q: jaccard(old[q], new.get(q, [])) for q in old}
    identical = sum(1 for q in old if old[q] == new.get(q))
    top1 = sum(1 for q in old if old[q][:1] == new.get(q, [])[:1])
    worst = min(per.values()) if per else 0.0
    complete = len(per) == len(DEMO_QUERIES) and set(new) >= set(old)
    if args.parity_tolerance:
        # Opt-in tolerance: every query keeps its top-1 AND its top-10 set within the Jaccard floor.
        ok = complete and top1 == len(per) and all(v >= RECALL_JACCARD_FLOOR for v in per.values())
    else:
        # Default: ranking ORDER must be identical for every query (the measured bar, 20/20).
        ok = complete and identical == len(per)
    data = {"queries": len(per), "identical_order": identical, "top1_equal": top1, "min_jaccard": worst,
            "mode": "tolerance" if args.parity_tolerance else "identical-order"}
    return _out(ok, "recall_parity", json.dumps(data, sort_keys=True), data)


def cmd_rebuild(args) -> int:
    info = rebuild_store(Path(args.export), Path(args.dst), Path(args.src_copy) if args.src_copy else None,
                         _real(args.live_store))
    return _out(True, "rebuild", f"chromadb {info['chromadb']} batch={info['batch']}", info)


def cmd_probe(args) -> int:
    if getattr(args, "store", None):
        refuse_live(args.store, args.live_store)
    fn = {"version": probe_version, "counts": probe_counts, "hashes": probe_hashes,
          "roundtrip": probe_roundtrip, "vectors": probe_vectors, "recall": probe_recall,
          "old-client-on-new": probe_old_client_on_new, "mutate-one": probe_mutate_one}[args.probe]
    return fn(args)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="copy -> export -> lab venv -> rebuild -> prove -> manifest")
    r.add_argument("--copy-from", default=str(LIVE_STORE_DEFAULT), help="store to copy (read only)")
    r.add_argument("--rehearsal-root", default=str(REHEARSAL_ROOT_DEFAULT))
    r.add_argument("--date", help="subdirectory name (default: today)")
    r.add_argument("--old-python", default=str(OLD_PYTHON_DEFAULT), help="interpreter with chromadb 0.6.x")
    r.add_argument("--uv", default=UV_DEFAULT)
    r.add_argument("--min-avail-mb", type=int, default=MIN_AVAIL_MB_DEFAULT)
    r.add_argument("--no-scope", action="store_true", help="skip the systemd-run MemoryMax scope")
    r.add_argument("--keep-venv", action="store_true")
    r.add_argument("--fresh", action="store_true", help="replace an existing rehearsal for this date")
    r.add_argument("--prove-only", action="store_true",
                   help="re-run only the proofs on this date's existing copy/export/rebuild")
    r.add_argument("--wait-mem-s", type=int, default=900,
                   help="seconds to wait for MemAvailable >= --min-avail-mb before a heavy step")

    c = sub.add_parser("copy", help="rsync segments + SQLite online-backup snapshot")
    c.add_argument("--copy-from", required=True)
    c.add_argument("--copy-to", required=True)
    c.add_argument("--live-store", default=str(LIVE_STORE_DEFAULT))

    e = sub.add_parser("export", help="per-collection export from a COPY's SQLite")
    e.add_argument("--src", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--live-store", default=str(LIVE_STORE_DEFAULT))

    b = sub.add_parser("rebuild", help="(chromadb 1.5.x interpreter) build a NEW store from an export")
    b.add_argument("--export", required=True)
    b.add_argument("--dst", required=True)
    b.add_argument("--src-copy")
    b.add_argument("--live-store", default=str(LIVE_STORE_DEFAULT))

    p = sub.add_parser("probe", help="(internal) one proof, run in a subprocess")
    p.add_argument("probe", choices=["version", "counts", "hashes", "roundtrip", "vectors", "recall",
                                     "old-client-on-new", "mutate-one"])
    p.add_argument("--store")
    p.add_argument("--export")
    p.add_argument("--stage", choices=["write", "verify-delete", "verify-gone"])
    p.add_argument("--collections", nargs="*", default=[])
    p.add_argument("--ids-file")
    p.add_argument("--out-file")
    p.add_argument("--demo-user")
    p.add_argument("--live-store", default=str(LIVE_STORE_DEFAULT))

    cv = sub.add_parser("compare-vectors")
    cv.add_argument("--old", required=True)
    cv.add_argument("--new", required=True)
    cv.add_argument("--queue-db", required=True)
    cv.add_argument("--ids-file", required=True)
    cv.add_argument("--mispair", action="store_true", help="negative control: pair vectors with the wrong ids")
    cr = sub.add_parser("compare-recall")
    cr.add_argument("--old", required=True)
    cr.add_argument("--new", required=True)
    cr.add_argument("--parity-tolerance", action="store_true",
                    help="accept top-1 equal + top-10 Jaccard >= 0.9 instead of identical order")

    args = ap.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "copy":
        print(json.dumps(copy_store(Path(args.copy_from), Path(args.copy_to), _real(args.live_store)), indent=1))
        return 0
    if args.cmd == "export":
        s = export_store(Path(args.src), Path(args.out), _real(args.live_store))
        print(json.dumps({n: i["count"] for n, i in s["collections"].items()} | {"skipped": s["skipped"]}))
        return 0
    if args.cmd == "rebuild":
        return cmd_rebuild(args)
    if args.cmd == "probe":
        return cmd_probe(args)
    if args.cmd == "compare-vectors":
        return cmd_compare_vectors(args)
    if args.cmd == "compare-recall":
        return cmd_compare_recall(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
