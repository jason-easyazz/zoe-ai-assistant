"""The scratch Postgres under Hindsight, as the bake-off's physical-erase instrument and the erase itself (capability ``disk``).

Hindsight 0.10.2 keeps everything in Postgres (``HINDSIGHT_API_DATABASE_URL`` -> the scratch container ``zoe-bakeoff-pg``, loopback
:55432, 256 MB cap). "Forgotten means forever" (docs/knowledge/forgotten-text-physical-erase.md) is therefore a question about
Postgres, and Postgres leaves text behind in five places the engine's own delete never revisits. Measured on the run-1 scratch store
(docs/knowledge/zoe-memory-bench.md, "Physical erase on the H arms") and read from the installed engine
(``hindsight_api/engine/memory_engine.py``: ``delete_document`` / ``delete_bank``):

* **log tables with no foreign key to the bank** - ``audit_log`` (``request`` / ``response`` jsonb: the whole retain call, every recall query),
  ``llm_requests`` (``input`` / ``output``: the extraction prompt with the text in it) and, until a worker reaps them, ``async_operations``
  (``task_payload``). ``delete_bank`` and ``delete_document`` delete none of the first two. (Both are diagnostics, OFF by default in
  Hindsight; the bake-off turns them ON to count extraction validity, so the erase must cover them, and under adoption they stay off.)
* **orphan graph rows** - ``entities.canonical_name`` of a person no remaining memory mentions, pruned lazily by a background worker
  (``entity_maintenance_queue``), and ``observation_history.content`` (kept after the observation it describes is deleted).
* **dead tuples** - a DELETE leaves the row's bytes in the heap page, its TOAST chunks, its btree / GIN / trigram index entries and the
  ``search_vector`` until ``VACUUM FULL`` rewrites the relation.
* **planner statistics** - ``pg_statistic`` keeps most-common values and histogram bounds of text columns (a person's name) until re-analysed.
* **the write-ahead log** - every INSERT's full row is in ``pg_wal``; a segment is RECYCLED (renamed, not zeroed) once a checkpoint passes it,
  so the old bytes sit in the file until something overwrites them. ``wal_recycle = off`` makes the checkpoint remove the file instead.

``ScratchPostgres.erase_text`` / ``erase_bank`` delete the first two classes by name / bank; ``compact`` rewrites the rest. ``scan`` is the
verifier: it copies the data directory out of the container (``docker cp``: the bind-mounted directory is owned by the container's postgres
user, mode 0700, and unreadable to the lab) and byte-scans the COPY, then asks the server whether any LIVE row still names the token
(a byte scan is BLIND to a value PostgreSQL compressed into TOAST - measured: 0 of 40 rows found, in the table and in the WAL; ``prepare`` turns
compression off for the text-bearing columns, 40 of 40 found, and the live-row query is the second instrument). It checkpoints first (a file scan
sees only what was written back); the arm refuses to score a token another LIVE bank also holds (``other_banks``). Counts and relation names only: it never returns, logs
or stores a document.

Everything goes through ``docker exec`` / ``docker cp`` against ONE named container. With no docker, or no container, ``PgUnavailable`` (a
``NotImplementedError``) is raised and the cell SKIPs with the reason - never a pass.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

PG_CONTAINER = "zoe-bakeoff-pg"      # == bakeoff.PG_CONTAINER (a test pins the pair)
PG_DATA_DIR = "/var/lib/postgresql/data"

#: tables the engine writes that carry free text and have NO cascade from the bank / document delete: erased by bank (hard delete) or by
#: the forgotten name (forget). ``{table: (text-bearing columns,)}``; every one has a ``bank_id`` column.
LOG_SINKS: "dict[str, tuple[str, ...]]" = {
    "audit_log": ("request", "response", "metadata"),
    "llm_requests": ("input", "output", "error", "metadata"),
    "async_operations": ("task_payload", "result_metadata", "error_message"),
}
#: derived rows the engine prunes lazily or keeps as history: erased by name, and by bank when it is gone (an orphan)
DERIVED_SINKS: "dict[str, tuple[str, ...]]" = {
    "observation_history": ("content",),
    "invalidated_memory_units": ("text", "context", "metadata"),
    "chunks": ("chunk_text",),
}
#: tables the engine deletes through the API (document / bank cascade). They are NOT erased by name (an API delete that missed a row must
#: show up as residue, never be hidden by the scrub); they are rewritten by ``compact`` so the deleted rows' bytes go.
ENGINE_TABLES = ("documents", "memory_units", "unit_entities", "entities", "entity_cooccurrences", "memory_links", "banks",
                 "attachments", "file_storage", "directives", "mental_models", "mental_model_history", "knowledge_pages",
                 "bank_aliases", "bank_stats_cache", "entity_maintenance_queue", "graph_maintenance_queue")
#: columns set to uncompressed storage by ``prepare`` so the byte scan can see a long value (scratch server only)
_RAW_COLUMNS: "dict[str, tuple[str, ...]]" = dict(LOG_SINKS)
_RAW_COLUMNS.update(DERIVED_SINKS)
_RAW_COLUMNS.update(documents=("original_text",), memory_units=("text", "context", "metadata"), entities=("canonical_name", "metadata"))
_ALL_SINKS: "dict[str, tuple[str, ...]]" = dict(LOG_SINKS)
_ALL_SINKS.update(DERIVED_SINKS)

_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_ESC = "ESCAPE '\\'"                           # the SQL text  ESCAPE '\'


class PgUnavailable(NotImplementedError):
    """No docker / no scratch container: the disk cells SKIP with this reason, they never pass."""


def like_pattern(name: str) -> str:
    """``%name%`` for ``ILIKE ... ESCAPE '\\'`` with the LIKE metacharacters of the name escaped."""
    return "%" + re.sub(r"([\\%_])", r"\\\1", name.strip()) + "%"


def token_variants(token: str) -> "list[str]":
    """The spellings a byte scan must look for: as given and lower-case (``tsvector`` lexemes and the GIN / trigram indexes fold case)."""
    return [token] if token.lower() == token else [token, token.lower()]


Runner = Callable[..., "tuple[int, str]"]


def docker_runner(argv: "Sequence[str]", *, stdin: "Optional[str]" = None, timeout: float = 120.0) -> "tuple[int, str]":
    """The real runner: ``docker`` resolved on PATH, text in / text out."""
    exe = shutil.which(argv[0])
    if exe is None:
        raise PgUnavailable("docker is not on PATH: the scratch Postgres cannot be reached")
    try:
        p = subprocess.run([exe, *argv[1:]], input=stdin, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout:.0f}s"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


class ScratchPostgres:
    """The erase and the verifier over the scratch container. ``runner`` is injectable (tests); default = ``docker``."""

    def __init__(self, container: str = PG_CONTAINER, *, db: str = "hindsight", user: str = "hindsight", schema: str = "public",
                 runner: "Optional[Runner]" = None, scratch: "Optional[str]" = None, timeout: float = 120.0):
        if not _SAFE_IDENT.match(schema):
            raise ValueError(f"unsafe schema name {schema!r}")
        self.container, self.db, self.user, self.schema = container, db, user, schema
        self._runner = runner or docker_runner
        self._scratch = scratch
        self.timeout = timeout
        self._tables: "Optional[set[str]]" = None
        self._prepared = False
        self.calls: "list[str]" = []          # the first line of every script run (never a value), for the tests

    # ── plumbing ──
    def psql(self, script: str, **variables: str) -> "list[str]":
        """Run a psql script on stdin (so ``:'var'`` is interpolated and quoted by psql itself); return the non-empty output lines."""
        argv = ["docker", "exec", "-i", self.container, "psql", "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-U", self.user, "-d", self.db]
        for k, v in variables.items():
            argv += ["-v", f"{k}={v}"]
        argv += ["-f", "-"]
        self.calls.append(script.strip().split("\n", 1)[0][:80])
        rc, out = self._runner(argv, stdin=script, timeout=self.timeout)
        if rc != 0:
            if "No such container" in out or "is not running" in out:
                raise PgUnavailable(f"the scratch Postgres container {self.container!r} is not running (bakeoff_window.sh starts it)")
            raise RuntimeError(f"psql failed (rc={rc}): {out.strip()[-300:]}")
        return [ln for ln in out.splitlines() if ln.strip()]

    def tables(self) -> "set[str]":
        if self._tables is None:
            self._tables = {ln.strip() for ln in self.psql(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = :'schema' AND table_type = 'BASE TABLE';",
                schema=self.schema)}
        return self._tables

    def _q(self, table: str) -> str:
        if not _SAFE_IDENT.match(table):
            raise ValueError(f"unsafe table name {table!r}")
        return f"{self.schema}.{table}"

    @staticmethod
    def _counts(lines: "list[str]") -> "dict[str, int]":
        out: "dict[str, int]" = {}
        for ln in lines:
            name, _, n = ln.partition("|")
            if n.strip().isdigit():
                out[name.strip()] = int(n)
        return out

    def prepare(self) -> None:
        """Instrument set-up on the SCRATCH server, idempotent: WAL segments are removed (not recycled) after a checkpoint, and the text-bearing
        columns store uncompressed (``STORAGE EXTERNAL``) so a byte scan can see a long value. Changes no row and no engine behaviour."""
        if self._prepared:
            return
        have = self.tables()
        stmts = ["ALTER SYSTEM SET wal_recycle = off;", "SELECT pg_reload_conf();"]
        for table, cols in _RAW_COLUMNS.items():
            if table in have:
                stmts += [f"ALTER TABLE {self._q(table)} ALTER COLUMN {c} SET STORAGE EXTERNAL;" for c in cols if _SAFE_IDENT.match(c)]
        self.psql("\n".join(stmts))
        self._prepared = True

    # ── the erase ──
    def _delete(self, table: str, where: str) -> str:
        return f"WITH d AS (DELETE FROM {self._q(table)} WHERE {where} RETURNING 1) SELECT '{table}', count(*) FROM d;"

    def erase_text(self, bank: str, name: str) -> "dict[str, int]":
        """Delete the rows of ``bank`` that name ``name`` from the log tables, the derived tables and the orphan entities. Counts only."""
        self.prepare()
        have, stmts = self.tables(), []
        for table, cols in _ALL_SINKS.items():
            if table in have:
                likes = " OR ".join(f"{c}::text ILIKE :'pat' {_ESC}" for c in cols)
                stmts.append(self._delete(table, f"bank_id = :'bank' AND ({likes})"))
        if "entities" in have and "unit_entities" in have:      # what the engine's lazy prune does, now: every entity no memory mentions any more (the place and the people named beside her)
            stmts.append(self._delete(
                "entities", f"bank_id = :'bank' AND NOT EXISTS (SELECT 1 FROM {self._q('unit_entities')} u WHERE u.entity_id = entities.id)"))
        return self._counts(self.psql("\n".join(stmts), bank=bank, pat=like_pattern(name))) if stmts else {}

    def erase_bank(self, bank: str) -> "dict[str, int]":
        """Delete everything the engine's own bank delete leaves behind for ``bank``: its log rows and any derived row of it."""
        self.prepare()
        have = self.tables()
        stmts = [self._delete(t, "bank_id = :'bank'") for t in _ALL_SINKS if t in have]
        return self._counts(self.psql("\n".join(stmts), bank=bank)) if stmts else {}

    def erase_orphans(self) -> "dict[str, int]":
        """Delete the log / derived rows of banks that no longer exist (an earlier cell's leftovers): cell isolation for the byte scan."""
        self.prepare()
        have = self.tables()
        if "banks" not in have:
            return {}
        gone = f"bank_id NOT IN (SELECT bank_id FROM {self._q('banks')})"
        return self._counts(self.psql("\n".join(self._delete(t, gone) for t in _ALL_SINKS if t in have)))

    def compact(self, *, vacuum: bool = True, stats: bool = True, wal: bool = True) -> None:
        """Rewrite every text-bearing relation (VACUUM FULL rebuilds the heap, the TOAST table and every index), recompute and compact the
        planner statistics, then checkpoint and switch the WAL twice so the segments that held the old rows are removed."""
        self.prepare()
        have = self.tables()
        stmts = ["SET lock_timeout = '30s';"]
        if stats:      # ANALYZE leaves the old statistics of a table that is now EMPTY as they were (measured), so they are dropped, not recomputed
            stmts.append("DELETE FROM pg_catalog.pg_statistic WHERE starelid IN (SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                         "WHERE n.nspname = :'schema' AND c.relkind IN ('r', 'i', 'm', 't'));")
        if vacuum:
            stmts += [f"VACUUM (FULL, ANALYZE) {self._q(t)};" for t in (*LOG_SINKS, *DERIVED_SINKS, *ENGINE_TABLES) if t in have]
        if stats:
            stmts.append("VACUUM FULL pg_catalog.pg_statistic;")
        if wal:
            stmts += ["CHECKPOINT;", "SELECT pg_switch_wal();", "CHECKPOINT;", "SELECT pg_switch_wal();", "CHECKPOINT;"]
        for attempt in range(3):       # VACUUM FULL needs an exclusive lock: the server's own background workers may hold one for a moment (the script is idempotent)
            try:
                self.psql("\n".join(stmts), schema=self.schema)
                return
            except RuntimeError as exc:
                if attempt == 2 or "lock timeout" not in str(exc):
                    raise
                time.sleep(1.0)

    # ── the verifier ──
    def _relation_owners(self) -> "dict[str, str]":
        """``{relation file path (no segment suffix): "table" | "table (index)" | "table (toast)"}`` for the relations of the engine's OWN schema
        only. The system catalogs (``pg_proc``, ``pg_description``, ``sql_features`` ...) and every other database of the cluster are left out
        on purpose: they hold ordinary English (``unicode``, ``lines``, ``Tove`` inside a description) and would read as household residue for any
        short name (measured on the run-1 store: ``Ines`` 261 hits, ``Tove`` 20, ``Leo`` 3,556 in an EMPTY store)."""
        rows = self.psql(
            "SELECT pg_relation_filepath(c.oid) || '|' || CASE "
            "WHEN c.relkind = 'i' THEN coalesce(p.relname, c.relname) || ' (index)' "
            "WHEN c.relkind = 't' THEN coalesce(o.relname, c.relname) || ' (toast)' ELSE c.relname END "
            "FROM pg_class c LEFT JOIN pg_index i ON i.indexrelid = c.oid LEFT JOIN pg_class p ON p.oid = i.indrelid "
            "LEFT JOIN pg_class o ON o.reltoastrelid = c.oid "
            "JOIN pg_namespace n ON n.oid = coalesce(p.relnamespace, o.relnamespace, c.relnamespace) "
            "WHERE c.relkind IN ('r', 'i', 't', 'm') AND pg_relation_filepath(c.oid) IS NOT NULL AND n.nspname = :'schema';", schema=self.schema)
        return {a: b for a, _, b in (ln.partition("|") for ln in rows)}

    def _stats_rows(self, tokens: "Sequence[str]") -> "dict[str, int]":
        """``{token: n}`` columns of the engine's own tables whose planner statistics (most-common values, histogram bounds) hold the token. Exact
        and scoped to the schema: the byte scan cannot tell a statistics row of a text column from the catalogs' own."""
        out: "dict[str, int]" = {}
        for t in tokens:
            n = self._counts(self.psql(
                "SELECT 'pg_stats', count(*) FROM pg_stats WHERE schemaname = :'schema' AND (most_common_vals::text ILIKE :'pat' "
                f"{_ESC} OR histogram_bounds::text ILIKE :'pat' {_ESC}) HAVING count(*) > 0;", schema=self.schema, pat=like_pattern(t)))
            if n.get("pg_stats"):
                out[t] = n["pg_stats"]
        return out

    def _live_rows(self, tokens: "Sequence[str]") -> "dict[str, dict[str, int]]":
        """``{token: {table: n}}`` of tables with a LIVE row that names the token (the second instrument: a byte scan cannot see compressed TOAST)."""
        out: "dict[str, dict[str, int]]" = {}
        for t in tokens:
            stmts = [f"SELECT '{tb}', count(*) FROM {self._q(tb)} r WHERE r::text ILIKE :'pat' {_ESC} HAVING count(*) > 0;"
                     for tb in sorted(self.tables())]
            out[t] = self._counts(self.psql("\n".join(stmts), pat=like_pattern(t)))
        stats = self._stats_rows(list(tokens))
        for t, n in stats.items():
            out[t]["pg_stats"] = n
        return out

    def snapshot(self, dest: Path) -> Path:
        """Copy the data directory out of the container into ``dest`` (owned by the caller: ``docker cp`` extracts client-side)."""
        rc, out = self._runner(["docker", "cp", f"{self.container}:{PG_DATA_DIR}/.", str(dest)], timeout=self.timeout)
        if rc != 0:
            if "No such container" in out:
                raise PgUnavailable(f"the scratch Postgres container {self.container!r} does not exist")
            raise RuntimeError(f"docker cp failed (rc={rc}): {out.strip()[-300:]}")
        return dest

    def other_banks(self, tokens: "Sequence[str]", bank: str) -> "dict[str, int]":
        """``{token: n}``: live rows of banks OTHER than ``bank`` that still EXIST and name the token. A byte scan cannot tell whose bytes they are: a token
        another live bank legitimately holds makes the cell's own residue unmeasurable (the caller refuses to score it). (A deleted bank's leftovers are
        residue, not another bank's data.)"""
        cols = self.psql("SELECT table_name FROM information_schema.columns WHERE table_schema = :'schema' AND column_name = 'bank_id';", schema=self.schema)
        out: "dict[str, int]" = {}
        for t in tokens:
            n = sum(self._counts(self.psql("\n".join(
                f"SELECT '{tb}', count(*) FROM {self._q(tb)} r WHERE r.bank_id <> :'bank' AND r.bank_id IN (SELECT bank_id FROM {self._q('banks')}) "
                f"AND r::text ILIKE :'pat' {_ESC};" for tb in sorted(cols)),
                bank=bank, pat=like_pattern(t))).values())
            if n:
                out[t] = n
        return out

    def scan(self, tokens: "Sequence[str]") -> "dict[str, Any]":
        """``scan_palace``-shaped, counts only: ``{"tokens": {tok: {"total", "files": {relpath: n}, "pg_relations": {name: n},
        "live_rows": {table: n}}}, "clean", "seconds"}``. ``files`` are paths inside the data directory (``base/<db>/<filenode>``, ``pg_wal/...``)."""
        from .. import lab_driver
        lab_driver._service_path()
        import memory_residue                     # stdlib only: the same verifier the Chroma disk cells use
        t0 = time.monotonic()
        toks = list(tokens)
        if not toks or any(not t for t in toks):
            raise ValueError("at least one non-empty token is required")
        self.prepare()
        self.psql("CHECKPOINT;")                  # a scan of FILES sees only what was written back: dirty buffers are not "on disk" yet, they will be at the next checkpoint
        owners = self._relation_owners()
        live = self._live_rows(toks)
        base = Path(self._scratch or lab_driver.scratch_root())
        base.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="pg-residue-", dir=str(base)))
        try:
            self.snapshot(work)
            spellings = {t: token_variants(t) for t in toks}
            raw = memory_residue.scan_palace(work, sorted({v for vs in spellings.values() for v in vs}), copy=False)["tokens"]
        finally:
            shutil.rmtree(work, ignore_errors=True)
        out: "dict[str, Any]" = {}
        for t in toks:
            files: "dict[str, int]" = {}
            rel: "dict[str, int]" = {}
            ignored = 0
            for v in spellings[t]:
                for f, n in raw[v]["files"].items():
                    owner = owners.get(re.sub(r"\.\d+$", "", f)) or ("pg_wal" if f.startswith("pg_wal/") else None)
                    if owner is None:             # a system catalog, another database, global/, postgresql.conf: ordinary text, never household residue
                        ignored += n
                        continue
                    files[f] = files.get(f, 0) + n
                    rel[owner] = rel.get(owner, 0) + n
            out[t] = {"total": sum(files.values()) + sum(live[t].values()), "files": files, "pg_relations": rel, "live_rows": live[t],
                      "ignored_system_hits": ignored}
        return {"tokens": out, "clean": all(v["total"] == 0 for v in out.values()), "seconds": round(time.monotonic() - t0, 3)}

