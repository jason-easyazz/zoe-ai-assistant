"""Physical erasure of forgotten text from the MemPalace files — stdlib only, no chromadb import.

Why (docs/research/memory-arm-hm-hindsight-mempalace-2026-10-06.md section 3.3; owner rule
"forgotten means forever"): a Chroma 1.5.9 API ``delete`` removes the row from the collection and
NOTHING ELSE. The text survives on disk in places Chroma never revisits:

  * SQLite FREE PAGES  - ``DELETE`` returns pages to the freelist without zeroing them
    (``secure_delete`` is off, ``auto_vacuum`` is NONE), so every deleted document, metadata value,
    FTS5 content row and queue record stays readable in ``chroma.sqlite3`` until the page is reused;
  * the FTS5 INDEX (``embedding_fulltext_search_data``) - trigram segments are only merged lazily;
  * the write-ahead log ``embeddings_queue`` - an ADD/UPSERT/UPDATE record carries the document in its
    ``metadata`` JSON and stays until the segment persists past it (``sync_threshold`` = 1000 records);
  * HNSW segment files - ``data_level0.bin`` is a pre-allocated block whose unused bytes are
    UNINITIALISED HEAP (a long-lived process that held the text leaves it behind), and the directories
    of collections an earlier compaction deleted are never removed by Chroma.

This module is the verifier (:func:`scan_palace`, byte-scans a COPY, never prints text) and the SQLite
half of the fix (:func:`scrub_sqlite`, a FTS5 rebuild, queue purge and ``VACUUM`` on the live file under a
busy timeout) plus :func:`remove_orphan_segments`. The HNSW half is the compaction rebuild in
``memory_service`` (``compact_drawers_index_sync`` runs it with the scrubbed allocator child).

Everything here is read-only except :func:`scrub_sqlite` / :func:`remove_orphan_segments`, and it never
logs or returns a document, a token, or a byte neighbourhood: counts and table names only.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

#: the allocator environment under which an HNSW rebuild child must run so freed heap is overwritten
#: (measured 0/60 residue with it, 13-21/60 without)
SCRUBBED_ALLOCATOR_ENV = {"MALLOC_PERTURB_": "85", "PYTHONMALLOC": "malloc"}

_SQLITE_NAME = "chroma.sqlite3"
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")
_CHUNK = 8 * 1024 * 1024


def _is_segment_dir(p: Path) -> bool:
    n = p.name
    return p.is_dir() and len(n) == 36 and n.count("-") == 4


def copy_palace(palace: str | os.PathLike[str], dest: str | os.PathLike[str]) -> Path:
    """Copy ``chroma.sqlite3`` (+ sidecars) and every HNSW segment directory into ``dest``.

    A plain file copy - it never opens the live DB, so it cannot block or be blocked by the service
    (a copy taken mid-write may be torn; it is a measuring instrument, not a backup)."""
    src = Path(os.path.expanduser(str(palace)))
    dst = Path(dest)
    dst.mkdir(parents=True, exist_ok=True)
    for name in (_SQLITE_NAME, *(_SQLITE_NAME + s for s in _SQLITE_SIDECARS)):
        if (src / name).is_file():
            shutil.copy2(src / name, dst / name)
    for child in sorted(src.iterdir()):
        if _is_segment_dir(child):
            shutil.copytree(child, dst / child.name)
    return dst


def _count_in_file(path: Path, needles: Sequence[bytes]) -> tuple[dict[bytes, int], dict[bytes, list[int]]]:
    """Occurrence count and (capped) byte offsets per needle, streaming with an overlap window."""
    counts = {n: 0 for n in needles}
    offsets: dict[bytes, list[int]] = {n: [] for n in needles}
    keep = max((len(n) for n in needles), default=1) - 1
    base = 0
    tail = b""
    with open(path, "rb") as fh:
        while True:
            block = fh.read(_CHUNK)
            if not block:
                break
            buf = tail + block
            buf_base = base - len(tail)
            for n in needles:
                start = 0
                while True:
                    i = buf.find(n, start)
                    if i < 0:
                        break
                    # a hit wholly inside the carried-over tail was already counted in the last block
                    if i + len(n) > len(tail):
                        counts[n] += 1
                        if len(offsets[n]) < 4096:
                            offsets[n].append(buf_base + i)
                    start = i + 1
            tail = buf[-keep:] if keep else b""
            base += len(block)
    return counts, offsets


def _page_owner_map(db_copy: Path) -> tuple[int, dict[int, str], set[int]]:
    """``(page_size, {pgno: table-or-index name}, freelist page numbers)`` for a COPY of the DB."""
    owners: dict[int, str] = {}
    free: set[int] = set()
    con = sqlite3.connect(f"file:{db_copy}?mode=ro", uri=True)
    try:
        page_size = int(con.execute("PRAGMA page_size").fetchone()[0])
        try:
            for pgno, name in con.execute("SELECT pageno, name FROM dbstat"):
                owners[int(pgno)] = str(name)
        except sqlite3.Error:
            pass
        total = int(con.execute("PRAGMA page_count").fetchone()[0])
        listed = set(owners)
        free = {p for p in range(1, total + 1) if p not in listed}
    finally:
        con.close()
    return page_size, owners, free


def scan_palace(
    palace: str | os.PathLike[str],
    tokens: Sequence[str] | str,
    *,
    scratch: str | os.PathLike[str] | None = None,
    keep_copy: bool = False,
    copy: bool = True,
) -> dict[str, Any]:
    """Byte-scan a COPY of the palace for each token. Counts only - never text.

    Returns ``{"tokens": {tok: {"total": n, "files": {relpath: n}, "sqlite_pages": {name: n}}}, "clean": bool,
    "copy": path|None, "seconds": s}``. ``sqlite_pages`` attributes every hit in ``chroma.sqlite3`` to the
    table / index that owns the page, or ``"<free page>"`` for a page on the freelist (deleted but not
    erased). ``copy=False`` scans ``palace`` in place (tests on a throwaway dir)."""
    t0 = time.monotonic()
    toks = [tokens] if isinstance(tokens, str) else list(tokens)
    if not toks or any(not t for t in toks):
        raise ValueError("at least one non-empty token is required")
    src = Path(os.path.expanduser(str(palace)))
    work: Path
    made: Path | None = None
    if copy:
        base = Path(scratch) if scratch else Path(tempfile.gettempdir())
        base.mkdir(parents=True, exist_ok=True)
        made = Path(tempfile.mkdtemp(prefix="residue-scan-", dir=str(base)))
        work = copy_palace(src, made)
    else:
        work = src
    needles = [t.encode("utf-8") for t in toks]
    out: dict[str, Any] = {t: {"total": 0, "files": {}, "sqlite_pages": {}} for t in toks}
    try:
        files = [p for p in sorted(work.rglob("*")) if p.is_file()]
        for f in files:
            counts, offsets = _count_in_file(f, needles)
            rel = str(f.relative_to(work))
            for tok, n in zip(toks, needles):
                if counts[n]:
                    out[tok]["total"] += counts[n]
                    out[tok]["files"][rel] = counts[n]
                    if f.name == _SQLITE_NAME:
                        psz, owners, free = _page_owner_map(f)
                        for off in offsets[n]:
                            pg = off // psz + 1
                            owner = owners.get(pg) or ("<free page>" if pg in free else "<unknown page>")
                            out[tok]["sqlite_pages"][owner] = out[tok]["sqlite_pages"].get(owner, 0) + 1
    finally:
        if made is not None and not keep_copy:
            shutil.rmtree(made, ignore_errors=True)
    return {
        "tokens": out,
        "clean": all(v["total"] == 0 for v in out.values()),
        "copy": str(made) if (made is not None and keep_copy) else None,
        "seconds": round(time.monotonic() - t0, 3),
    }


def orphan_segment_dirs(palace: str | os.PathLike[str]) -> list[Path]:
    """HNSW segment directories on disk that no ``segments`` row owns (left behind by a collection
    delete / compaction). Read-only. Empty when the DB cannot be read (never guess)."""
    src = Path(os.path.expanduser(str(palace)))
    try:
        con = sqlite3.connect(f"file:{src / _SQLITE_NAME}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return []
    try:
        known = {str(r[0]) for r in con.execute("SELECT id FROM segments")}
    except sqlite3.Error:
        return []
    finally:
        con.close()
    return [c for c in sorted(src.iterdir()) if _is_segment_dir(c) and c.name not in known]


def remove_orphan_segments(palace: str | os.PathLike[str]) -> list[str]:
    """Delete the orphan segment directories (see :func:`orphan_segment_dirs`). Returns their names.

    Safe under the running service: no ``segments`` row points at them, so no client opens them. They are
    the pre-compaction HNSW files - vectors plus whatever heap the writer left in the pre-allocated block."""
    removed: list[str] = []
    for d in orphan_segment_dirs(palace):
        shutil.rmtree(d, ignore_errors=True)
        if not d.exists():
            removed.append(d.name)
    return removed


def _seq(value: Any) -> int:
    """A ``max_seq_id.seq_id`` as an int: chroma 0.6 stored it as an 8-byte big-endian BLOB, 1.x as INTEGER."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return int.from_bytes(bytes(value), "big")
    return int(value)


def _queue_floor(con: sqlite3.Connection, topic_like: str) -> int:
    """Highest seq every segment of the collection has PERSISTED (-1 when any never did) - the same rule
    Chroma's ``purge_log`` uses, so what is below it is already in the HNSW/metadata segments."""
    rows = con.execute(
        "SELECT COALESCE(m.seq_id, -1) FROM segments s LEFT JOIN max_seq_id m ON m.segment_id = s.id "
        "JOIN collections c ON c.id = s.collection WHERE ? LIKE '%/' || c.id", (topic_like,)).fetchall()
    return min((_seq(r[0]) for r in rows), default=-1)


def purge_queue(con: sqlite3.Connection) -> int:
    """Delete ``embeddings_queue`` records every segment of their collection already persisted past.

    Chroma only purges when its in-process writer runs; this is the same rule (``seq_id <= min persisted
    seq over the collection's segments``) applied from outside, so a record cannot be needed for replay."""
    if not _table_exists(con, "embeddings_queue"):
        return 0
    n = 0
    for (topic,) in con.execute("SELECT DISTINCT topic FROM embeddings_queue").fetchall():
        floor = _queue_floor(con, topic)
        if floor >= 0:
            n += con.execute("DELETE FROM embeddings_queue WHERE topic = ? AND seq_id <= ?", (topic, floor)).rowcount
    return n


def blank_deleted_queue_text(con: sqlite3.Connection) -> int:
    """Blank the text-bearing ``metadata`` of every non-DELETE ``embeddings_queue`` record whose id has a
    LATER DELETE record in the same topic.

    The queue is the write-ahead log: an ADD / UPSERT record carries the document (``chroma:document``) and
    the metadata (``source_excerpt``, ``review_note``, the audit row's before/after text) as JSON and stays
    until its segment persists past it - up to ``sync_threshold`` (1000) records, i.e. days on a quiet box.
    Replay applies the records in order, so ``ADD(x) ... DELETE(x)`` ends with ``x`` absent whatever the ADD
    carried; blanking the carried text changes no replay outcome. A record AFTER the last DELETE of its id
    (an explicit re-add) is never touched, and the vector column is left alone (it is not text)."""
    if not _table_exists(con, "embeddings_queue"):
        return 0
    cur = con.execute(
        "UPDATE embeddings_queue SET metadata = '{}' "
        "WHERE operation != 3 AND metadata IS NOT NULL AND metadata != '{}' AND EXISTS ("
        "  SELECT 1 FROM embeddings_queue d WHERE d.topic = embeddings_queue.topic AND d.id = embeddings_queue.id "
        "  AND d.operation = 3 AND d.seq_id > embeddings_queue.seq_id)")
    return int(cur.rowcount or 0)


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone() is not None


def scrub_sqlite(palace: str | os.PathLike[str], *, vacuum: bool = True, busy_timeout_s: float = 30.0) -> dict[str, Any]:
    """The SQLite half of "forgotten means forever": FTS5 rebuild, queue purge, ``VACUUM``.

    Intended to run while the service is up but its collection ops are drained (the caller closes the
    maintenance gate). Returns counts and timings only."""
    t0 = time.monotonic()
    report: dict[str, Any] = {"queue_purged": 0, "queue_blanked": 0, "fts_rebuilt": False, "vacuumed": False}
    path = Path(os.path.expanduser(str(palace))) / _SQLITE_NAME
    if not path.is_file():   # never create a database by "scrubbing" a directory that has none
        return {**report, "skipped": f"no {_SQLITE_NAME}", "seconds": 0.0}
    con = sqlite3.connect(str(path), timeout=busy_timeout_s, isolation_level=None)
    try:
        con.execute(f"PRAGMA busy_timeout = {int(busy_timeout_s * 1000)}")
        con.execute("PRAGMA secure_delete = ON")
        con.execute("BEGIN IMMEDIATE")
        try:
            report["queue_purged"] = purge_queue(con)
            report["queue_blanked"] = blank_deleted_queue_text(con)
            if _table_exists(con, "embedding_fulltext_search"):
                con.execute("INSERT INTO embedding_fulltext_search(embedding_fulltext_search) VALUES('rebuild')")
                report["fts_rebuilt"] = True
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        if vacuum:
            con.execute("VACUUM")
            report["vacuumed"] = True
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")   # no-op in rollback-journal mode; clears a WAL
    finally:
        con.close()
    report["seconds"] = round(time.monotonic() - t0, 3)
    return report


_M_PERTURB = -6          # glibc mallopt(3) parameter
_HEAP_SCRUB_ON = False


def enable_heap_scrub(byte: int = 85) -> bool:
    """Turn on glibc's allocator scrubbing for THIS process from now on (``mallopt(M_PERTURB, byte)``): freed
    heap is overwritten and fresh allocations are filled, so the unused bytes hnswlib writes into its index
    files are a constant, never the remains of a forgotten text. The runtime twin of the
    ``MALLOC_PERTURB_=85`` environment variable (which needs a restart). Measured on chromadb 1.5.9:
    +2% wall on a write/query/update workload, no RSS change (``PYTHONMALLOC=malloc`` as well: +4%).
    glibc only; returns False (nothing changed) elsewhere. Idempotent."""
    global _HEAP_SCRUB_ON
    if _HEAP_SCRUB_ON:
        return True
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6")
        if libc.mallopt(_M_PERTURB, int(byte)) == 1:
            _HEAP_SCRUB_ON = True
    except Exception:  # noqa: BLE001 - musl / macOS / no libc: report, never fail the service
        pass
    return _HEAP_SCRUB_ON


def disable_heap_scrub() -> None:
    """Undo :func:`enable_heap_scrub` (``mallopt(M_PERTURB, 0)``): for a lab / test process that turned it on
    for one measurement and must leave the interpreter as it found it. A scrub requested through the
    ``MALLOC_PERTURB_`` environment variable is not ours to undo."""
    global _HEAP_SCRUB_ON
    if not _HEAP_SCRUB_ON:
        return
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").mallopt(_M_PERTURB, 0)
    except Exception:  # noqa: BLE001
        pass
    _HEAP_SCRUB_ON = False


def heap_scrub_on() -> bool:
    """True when this process scrubs its heap: ``enable_heap_scrub`` ran, or the allocator env is set
    (``MALLOC_PERTURB_`` non-zero; ``PYTHONMALLOC=malloc`` additionally covers Python's own small objects)."""
    env = os.environ.get("MALLOC_PERTURB_", "").strip() not in ("", "0")
    return _HEAP_SCRUB_ON or env


def scrubbed_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """``base`` (default ``os.environ``) plus the scrubbing allocator settings, for an HNSW rebuild child."""
    env = dict(os.environ if base is None else base)
    env.update(SCRUBBED_ALLOCATOR_ENV)
    return env
