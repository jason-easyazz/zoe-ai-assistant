"""Forget-first lineage store (LAB ONLY, sqlite3 + stdlib, scratch data only).

The rule under test: a derived artifact cannot be written without lineage pointers to what it was
derived from, so forget is ONE generic operation over the lineage graph:

  phase 1 (atomic, fast): ledger the salted hash, tombstone the source, erase its payload + FTS row,
                          quarantine the transitive closure of derived artifacts (ids only)
  phase 2 (purge):        drop every quarantined artifact and its edges, queue a rebuild of each from
                          its SURVIVING parents, compact the FTS index so the old terms leave the file

serve() fails closed twice: it returns only `live` artifacts AND re-walks each one's ancestry against
the tombstones, so a missed quarantine still cannot serve forgotten-derived text.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import unicodedata

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS sources_fts USING fts5(id UNINDEXED, payload);
CREATE TABLE IF NOT EXISTS tombstones (id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS forget_ledger (h TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, layer TEXT NOT NULL, depth INTEGER NOT NULL,
                                      payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'live');
CREATE INDEX IF NOT EXISTS artifacts_layer_payload ON artifacts(layer, payload);
CREATE INDEX IF NOT EXISTS artifacts_not_live ON artifacts(state) WHERE state != 'live';
CREATE TABLE IF NOT EXISTS lineage (artifact_id TEXT NOT NULL, parent_id TEXT NOT NULL,
                                    PRIMARY KEY (artifact_id, parent_id)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS lineage_parent ON lineage(parent_id, artifact_id);
CREATE TABLE IF NOT EXISTS rebuild_queue (n INTEGER PRIMARY KEY, old_id TEXT NOT NULL, layer TEXT NOT NULL,
                                          depth INTEGER NOT NULL, parents TEXT NOT NULL);
"""

CLOSURE = """WITH RECURSIVE c(id) AS (SELECT artifact_id FROM lineage WHERE parent_id = ?
             UNION SELECT l.artifact_id FROM lineage l JOIN c ON l.parent_id = c.id) SELECT id FROM c"""
ANCESTORS = """WITH RECURSIVE a(id) AS (SELECT parent_id FROM lineage WHERE artifact_id = ?
               UNION SELECT l.parent_id FROM lineage l JOIN a ON l.artifact_id = a.id) SELECT id FROM a"""


class LineageRequired(ValueError):
    """A derived write with no parents: the choke-point refuses it."""


class Forgotten(ValueError):
    """Content (or a parent) that a member forgot: refused at write time."""


def normalise(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


class LineageStore:
    def __init__(self, path: str, *, secure_delete: bool = True, fts_compact: bool = True):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.fts_compact = fts_compact
        self.db.execute(f"PRAGMA secure_delete = {'ON' if secure_delete else 'OFF'}")
        self.db.execute("PRAGMA journal_mode = DELETE")
        self.db.executescript(SCHEMA)
        row = self.db.execute("SELECT v FROM meta WHERE k = 'salt'").fetchone()
        self.salt = row[0] if row else os.urandom(32)
        if not row:  # LAB: the salt lives beside the data; a real store keeps it in a separate key file
            self.db.execute("INSERT INTO meta VALUES ('salt', ?)", (self.salt,))

    # -- writes -------------------------------------------------------------------------------------
    def _h(self, text: str) -> str:
        return hmac.new(self.salt, normalise(text).encode(), hashlib.sha256).hexdigest()

    def ingest(self, sid: str, payload: str) -> None:
        self.db.execute("BEGIN IMMEDIATE")  # ledger + tombstone checks share the insert's txn: a racing forget cannot slip between
        try:
            if self.db.execute("SELECT 1 FROM forget_ledger WHERE h = ?", (self._h(payload),)).fetchone():
                raise Forgotten("re-ingest of forgotten content")
            if self.db.execute("SELECT 1 FROM tombstones WHERE id = ?", (sid,)).fetchone():
                raise Forgotten("source id was forgotten")
            rid = self.db.execute("INSERT INTO sources VALUES (?, ?)", (sid, payload)).lastrowid
            self.db.execute("INSERT INTO sources_fts (rowid, id, payload) VALUES (?, ?, ?)", (rid, sid, payload))
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    def write_derived(self, aid: str, layer: str, payload: str, parents: list[str], *, depth: int = 1) -> None:
        """THE choke-point: no lineage, no write; a forgotten parent or forgotten text, no write."""
        if not parents:
            raise LineageRequired(f"{layer}:{aid} has no lineage")
        self.db.execute("BEGIN IMMEDIATE")  # ledger + parent checks + edges in one txn: a racing forget cannot slip between
        try:
            if self.db.execute("SELECT 1 FROM forget_ledger WHERE h = ?", (self._h(payload),)).fetchone():
                raise Forgotten("derived text equals forgotten content")
            for p in parents:
                live = self.db.execute("SELECT 1 FROM sources WHERE id = ? UNION ALL SELECT 1 FROM artifacts "
                                       "WHERE id = ? AND state = 'live'", (p, p)).fetchone()
                if not live:
                    raise Forgotten(f"parent {p} is forgotten, quarantined or missing")
            self.db.execute("INSERT INTO artifacts (id, layer, depth, payload) VALUES (?, ?, ?, ?)",
                            (aid, layer, depth, payload))
            self.db.executemany("INSERT INTO lineage VALUES (?, ?)", [(aid, p) for p in set(parents)])
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    def write_legacy(self, aid: str, layer: str, payload: str) -> None:
        """NEGATIVE CONTROL ONLY: the lineage-less path today's per-store writers resemble."""
        self.db.execute("INSERT INTO artifacts (id, layer, depth, payload) VALUES (?, ?, 0, ?)", (aid, layer, payload))

    # -- forget -------------------------------------------------------------------------------------
    def forget_phase1(self, sid: str) -> list[str]:
        self.db.execute("BEGIN IMMEDIATE")
        row = self.db.execute("SELECT payload, rowid FROM sources WHERE id = ?", (sid,)).fetchone()
        if row:
            self.db.execute("INSERT OR IGNORE INTO forget_ledger VALUES (?)", (self._h(row[0]),))
            # by rowid: an fts5 UNINDEXED column cannot be searched, so DELETE ... WHERE id = ? is a full scan
            self.db.execute("DELETE FROM sources_fts WHERE rowid = ?", (row[1],))
        self.db.execute("INSERT OR IGNORE INTO tombstones VALUES (?)", (sid,))
        self.db.execute("DELETE FROM sources WHERE id = ?", (sid,))
        closure = [r[0] for r in self.db.execute(CLOSURE, (sid,))]
        self.db.executemany("UPDATE artifacts SET state = 'quarantined' WHERE id = ?", [(a,) for a in closure])
        self.db.execute("COMMIT")
        return closure

    def forget_phase2(self) -> int:
        """Drop every quarantined artifact, queue rebuilds from survivors, compact FTS."""
        self.db.execute("BEGIN IMMEDIATE")
        dead = {r[0]: (r[1], r[2]) for r in self.db.execute(
            "SELECT id, layer, depth FROM artifacts WHERE state = 'quarantined'")}
        for aid, (layer, depth) in dead.items():
            parents = [r[0] for r in self.db.execute("SELECT parent_id FROM lineage WHERE artifact_id = ?", (aid,))]
            keep = [p for p in parents if p in dead or not self._tombstoned(p)]  # dead parents get rebuilt first
            if keep:
                self.db.execute("INSERT INTO rebuild_queue (old_id, layer, depth, parents) VALUES (?, ?, ?, ?)",
                                (aid, layer, depth, json.dumps(keep)))
        self.db.executemany("DELETE FROM lineage WHERE artifact_id = ?", [(a,) for a in dead])
        self.db.executemany("DELETE FROM artifacts WHERE id = ?", [(a,) for a in dead])
        self.db.execute("COMMIT")
        if self.fts_compact:  # FTS5 (sqlite < 3.42 has no 'secure-delete') keeps old terms in segments until merged
            self.db.execute("INSERT INTO sources_fts (sources_fts) VALUES ('optimize')")
        return len(dead)

    def forget(self, sid: str) -> int:
        self.forget_phase1(sid)
        return self.forget_phase2()

    def _tombstoned(self, pid: str) -> bool:
        return bool(self.db.execute("SELECT 1 FROM tombstones WHERE id = ?", (pid,)).fetchone())

    def rebuild(self, derive) -> int:
        """Recompute queued artifacts (shallowest first) from surviving parents via derive(layer, [payloads])."""
        jobs = self.db.execute("SELECT n, old_id, layer, depth, parents FROM rebuild_queue ORDER BY depth, n").fetchall()
        renamed: dict[str, str] = {}
        built = 0
        for n, old, layer, depth, parents in jobs:
            ps = [renamed.get(p, p) for p in json.loads(parents)]
            ps = [p for p in ps if self._payload(p) is not None]
            if ps:
                new = f"{old}~r{n}"
                self.write_derived(new, layer, derive(layer, [self._payload(p) for p in ps]), ps, depth=depth)
                renamed[old] = new
                built += 1
            self.db.execute("DELETE FROM rebuild_queue WHERE n = ?", (n,))
        return built

    # -- reads --------------------------------------------------------------------------------------
    def _payload(self, pid: str):
        row = self.db.execute("SELECT payload FROM sources WHERE id = ? UNION ALL SELECT payload FROM artifacts "
                              "WHERE id = ? AND state = 'live'", (pid, pid)).fetchone()
        return row[0] if row else None

    def serve(self, layer: str | None = None) -> list[tuple[str, str]]:
        q, args = "SELECT id, payload FROM artifacts WHERE state = 'live'", ()
        if layer:
            q, args = q + " AND layer = ?", (layer,)
        rows = self.db.execute(q, args).fetchall()
        out = []
        for aid, payload in rows:  # wall 2: ancestry against tombstones, even if the quarantine missed it
            anc = [r[0] for r in self.db.execute(ANCESTORS, (aid,))]
            if anc and self.db.execute(f"SELECT 1 FROM tombstones WHERE id IN ({','.join('?' * len(anc))}) LIMIT 1",
                                       anc).fetchone():
                continue
            out.append((aid, payload))
        return out

    def residue(self, needle: str) -> list[str]:
        """Every table (FTS shadow tables included) whose text/blob holds needle, plus an FTS MATCH."""
        hits = []
        tables = [r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        for t in tables:
            cols = [r[1] for r in self.db.execute(f"PRAGMA table_info('{t}')")]
            for c in cols:
                n = self.db.execute(f"SELECT COUNT(*) FROM '{t}' WHERE instr(CAST(\"{c}\" AS BLOB), ?) > 0",
                                    (needle.encode(),)).fetchone()[0]
                if n:
                    hits.append(f"{t}.{c}")
        if self.db.execute("SELECT COUNT(*) FROM sources_fts WHERE sources_fts MATCH ?", (f'"{needle}"',)).fetchone()[0]:
            hits.append("sources_fts MATCH")
        return hits

    def close(self) -> None:
        self.db.close()


WORDS = ("blorp", "tavim", "quenzo", "drelk", "mifta", "sorvak", "plimb", "yonder", "kaztu", "vrell")


def concat(_layer: str, parents: list[str]) -> str:
    """Worst-case derivation for the tests: the derived text COPIES every parent verbatim."""
    return " | ".join(parents)


def random_dag(store: LineageStore, rng, n_sources: int, depth: int, per_layer: int, fan_in: int = 3,
               derive=concat) -> dict[str, str]:
    """Synthetic sources (invented words + a unique marker) and `depth` layers of derived artifacts."""
    markers, pool = {}, []
    for i in range(n_sources):
        m = "qx%010x" % rng.getrandbits(40)
        store.ingest(f"s{i}", f"{rng.choice(WORDS)} {rng.choice(WORDS)} {m}")
        markers[f"s{i}"] = m
        pool.append(f"s{i}")
    for d in range(1, depth + 1):
        layer = []
        for j in range(per_layer):
            ps = rng.sample(pool, min(len(pool), rng.randint(1, fan_in)))
            aid = f"a{d}_{j}"
            store.write_derived(aid, f"L{d}", derive(f"L{d}", [store._payload(p) for p in ps]), ps, depth=d)
            layer.append(aid)
        pool += layer
    return markers


def file_residue(path: str, needle: str) -> bool:
    """Raw-byte search of the db file (+ any journal/wal). Uses the needle's TAIL: FTS5 prefix-compresses terms."""
    tail = needle[-8:].encode()
    for p in (path, path + "-journal", path + "-wal"):
        if os.path.exists(p):
            with open(p, "rb") as fh:
                if tail in fh.read():
                    return True
    return False
