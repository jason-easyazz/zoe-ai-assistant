"""Can the verbatim tier honour "forgotten means forever"? (HM arm, Part A / hole 2.)

Builds a scratch MemPalace (library path, one wing per household member), plants chunks that name ``Marisol`` in
the spellings a household and a speech-to-text engine really produce, then tries the deletion routes and, after
each, checks THREE things: the API (``get`` / ``query``), the bytes on disk (every file under the palace dir is
scanned for the name, case-insensitive), and what a later maintenance step (SQLite ``VACUUM`` + WAL checkpoint, a
collection rebuild) changes.

Run with the bake-off venv python in the scrubbed env:  bash mp_run.sh forget_probe.py
Prints one JSON document. Uses 120 synthetic turns written ONE AT A TIME (a batch of 32+ takes the process past
700 MB, see batch_rss_probe.py).
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from zmb.pilot import household  # noqa: E402

WORK = Path("/home/zoe/.zoe/bakeoff-2026-10/mp-work/forget-probe")
NAME = "marisol"

#: the planted chunks: (id, wing, text, how a household member / STT would write it)
PLANTED = [
    ("p1", "dana", "Marisol is coming round on Saturday with her sister.", "exact"),
    ("p2", "dana", "marisol is allergic to cats so keep Biscuit out", "lowercase (STT)"),
    ("p3", "tove", "MARISOL called about the lift on Friday", "uppercase"),
    ("p4", "dana", "Marisol's sister is called Ines and she lives in Porto", "possessive + third person"),
    ("p5", "mika", "my friend Mari-sol gave me a drawing", "hyphenated variant"),
    ("p6", "tove", "Marisal is bringing the cake", "STT misspelling"),
    ("p7", "dana", "she is allergic to cats, keep the dog out of the lounge", "pronoun only (names nobody)"),
    ("p8", "leo", "Mari came to the park with us", "nickname / short form"),
]


def scan_disk(root: Path, needle: str = NAME) -> "dict[str, int]":
    """Case-insensitive byte scan of every file under ``root``; returns {relative path: hits} for files with hits."""
    pat = re.compile(re.escape(needle.encode()), re.I)
    out: dict[str, int] = {}
    for f in sorted(root.rglob("*")):
        if f.is_file():
            n = len(pat.findall(f.read_bytes()))
            if n:
                out[str(f.relative_to(root))] = n
    return out


def tables_holding(db: Path, needle: str = NAME) -> "dict[str, int]":
    """Which SQLite tables of the palace still contain the name in a row (read-only; opened via a copy of the file)."""
    out: dict[str, int] = {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for (tname,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            try:
                n = 0
                for row in con.execute(f'SELECT * FROM "{tname}"'):
                    if needle.encode() in repr(row).lower().encode():
                        n += 1
                if n:
                    out[tname] = n
            except sqlite3.DatabaseError:
                out[tname] = -1
    finally:
        con.close()
    return out


def api_hits(col, needle: str = NAME) -> "list[str]":
    got = col.get(include=["documents"])
    return [i for i, d in zip(got["ids"], got["documents"]) if needle in (d or "").lower()]


def main() -> int:
    from mempalace.palace import get_collection
    shutil.rmtree(WORK, ignore_errors=True)
    palace = WORK / "palace"
    palace.mkdir(parents=True)
    col = get_collection(str(palace), create=True)
    turns, _q, _m = household.generate()
    filler = [t for t in turns if t["kind"] == "filler"][:112]
    for t in filler:
        col.upsert(ids=[t["turn_id"]], documents=[t["text"]], metadatas=[{"wing": t["user"], "room": "voice"}])
    for pid, wing, text, _how in PLANTED:
        col.upsert(ids=[pid], documents=[text], metadatas=[{"wing": wing, "room": "voice"}])
    out: dict = {"drawers": col.count(), "planted": {p[0]: p[3] for p in PLANTED}}
    out["disk_before_delete"] = scan_disk(palace)
    out["api_before"] = api_hits(col)

    # MemPalace's collection wrapper has NO delete-by-content: ``delete(*, ids=None, where=None)`` only. Content
    # matching is a ``get(where_document=...)`` (the wrapper accepts it) followed by a delete by id.
    out["wrapper_delete_signature"] = "delete(*, ids=None, where=None): no where_document"
    # route 1: where_document $contains (case-sensitive substring) - the obvious one
    try:
        ids1 = col.get(where_document={"$contains": "Marisol"}, include=[])["ids"]
        out["route1_contains_Marisol_case_sensitive"] = {"matched_ids": sorted(ids1)}
        if ids1:
            col.delete(ids=ids1)
    except Exception as exc:  # noqa: BLE001 - record what the wrapper does
        out["route1_contains_Marisol_case_sensitive"] = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    out["route1_contains_Marisol_case_sensitive"]["still_in_api"] = api_hits(col)
    # route 2: a case-insensitive regex
    try:
        ids2 = col.get(where_document={"$regex": "(?i)marisol"}, include=[])["ids"]
        out["route2_regex_ignorecase"] = {"matched_ids": sorted(ids2)}
        if ids2:
            col.delete(ids=ids2)
    except Exception as exc:  # noqa: BLE001
        out["route2_regex_ignorecase"] = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    out["route2_regex_ignorecase"]["still_in_api"] = api_hits(col)
    remaining_after_lexical = sorted(i for i in col.get()["ids"] if i in {p[0] for p in PLANTED})
    out["planted_ids_surviving_both_lexical_routes"] = remaining_after_lexical
    out["planted_survivors_text"] = {p[0]: p[3] for p in PLANTED if p[0] in remaining_after_lexical}
    # route 3: delete by id from a Zoe-side index (the fix): entity -> ids recorded at write time (aliases included)
    zoe_index = {"marisol": ["p1", "p2", "p3", "p4", "p5", "p6", "p8"]}   # p7 names nobody: no deterministic route
    col.delete(ids=[i for i in zoe_index["marisol"] if i in set(col.get()["ids"])])
    out["after_route3_zoe_index_ids"] = {"planted_ids_left": sorted(i for i in col.get()["ids"]
                                                                   if i in {p[0] for p in PLANTED})}
    r = col.query(query_texts=["Marisol"], n_results=5)
    out["query_marisol_top5_texts_containing_name"] = [d for d in r["documents"][0] if NAME in d.lower()]
    # what is on disk now (deleted through the API, no maintenance)
    out["disk_after_api_delete"] = scan_disk(palace)

    # maintenance 1: close the client, checkpoint the WAL, VACUUM the SQLite file
    try:
        from mempalace.backends.chroma import ChromaBackend  # noqa: F401
    except Exception:
        pass
    import chromadb
    del col
    try:
        chromadb.api.client.SharedSystemClient.clear_system_cache()
    except Exception:
        pass
    db = palace / "chroma.sqlite3"
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    out["disk_after_wal_checkpoint"] = scan_disk(palace)
    con.execute("VACUUM")
    con.close()
    out["disk_after_vacuum"] = scan_disk(palace)
    out["tables_still_holding_after_vacuum"] = tables_holding(db)
    # maintenance 1b: ask SQLite's FTS5 to merge/rebuild its segments (what a stale full-text index needs)
    con = sqlite3.connect(str(db))
    fts_tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE '%fts5%'")]
    out["fts5_tables"] = fts_tables
    for t in fts_tables:
        try:
            con.execute(f"INSERT INTO {t}({t}) VALUES('rebuild')")
        except sqlite3.DatabaseError as exc:
            out.setdefault("fts5_rebuild_errors", {})[t] = str(exc)[:100]
    con.commit()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.execute("VACUUM")
    con.close()
    out["disk_after_fts5_rebuild_and_vacuum"] = scan_disk(palace)
    out["tables_after_fts5_rebuild_and_vacuum"] = tables_holding(db)

    # maintenance 2: rebuild - copy the surviving rows into a fresh palace, drop the old directory
    import time
    col = get_collection(str(palace), create=False)
    got = col.get(include=["documents", "metadatas", "embeddings"])
    new_palace = WORK / "palace_rebuilt"
    new_palace.mkdir()
    ncol = get_collection(str(new_palace), create=True)
    t0 = time.perf_counter()
    for i, d, m in zip(got["ids"], got["documents"], got["metadatas"]):
        ncol.upsert(ids=[i], documents=[d], metadatas=[m])                    # re-embeds every row
    out["rebuild_reembed_seconds"] = round(time.perf_counter() - t0, 2)
    out["rebuilt_drawers"] = ncol.count()
    out["disk_rebuilt_palace"] = scan_disk(new_palace)
    # the same rebuild REUSING the stored vectors (no embedding pass): what a forget-triggered rebuild would do
    reuse_palace = WORK / "palace_rebuilt_reuse"
    reuse_palace.mkdir()
    rcol = get_collection(str(reuse_palace), create=True)
    t0 = time.perf_counter()
    embs = [list(map(float, e)) for e in got["embeddings"]]
    for j in range(0, len(got["ids"]), 25):
        rcol.upsert(ids=got["ids"][j:j + 25], documents=got["documents"][j:j + 25],
                    metadatas=got["metadatas"][j:j + 25], embeddings=embs[j:j + 25])
    out["rebuild_reuse_vectors_seconds"] = round(time.perf_counter() - t0, 3)
    out["rebuild_reuse_drawers"] = rcol.count()
    out["disk_rebuilt_reuse_palace"] = scan_disk(reuse_palace)
    del ncol, rcol, col
    shutil.rmtree(WORK / "palace", ignore_errors=True)
    out["disk_old_palace_dir_removed_scan_of_work_dir"] = scan_disk(WORK)
    print(json.dumps(out, indent=1))
    shutil.rmtree(WORK, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
