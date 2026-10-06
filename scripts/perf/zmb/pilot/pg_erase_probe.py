"""Does a Hindsight delete leave text on the scratch Postgres' disk, and what removes it? (H-arm capability ``disk``; owner rule "forgotten means forever".)

Run it against the RUNNING scratch container (``docker compose -f ~/.zoe/bakeoff-2026-10/scratch-postgres.compose.yml up -d``; it never starts or stops anything):

    python3 scripts/perf/zmb/pilot/pg_erase_probe.py            # prints one JSON document, counts only

For each variant of the scrub it (1) cleans the cluster and shows a byte scan of a COPY of the data directory is clean (the instrument's own
baseline), (2) writes what the Hindsight engine writes for a retained fact (``documents`` / ``chunks`` / ``memory_units`` + ``search_vector`` /
``entities`` / ``unit_entities`` / ``audit_log`` / ``llm_requests`` / ``async_operations``, the shapes read from the migrated 0.10.2 schema)
and shows the scan SEES the token (the positive control), (3) deletes the documents the way ``MemoryEngine.delete_document`` does (a DELETE on
``documents``, cascading to chunks / memory_units / unit_entities, entities left for the lazy prune) and scans, (4) applies the variant's scrub
steps and scans again. The variants are the ablation: which step removes which bytes, and that the unscrubbed run is red (the negative control).

It writes only synthetic rows under a throwaway bank id and removes them. The engine itself is NOT in the loop (no server, no LLM): the
rows are a model of its writes, so this probe measures the Postgres half; the full path is the bake-off window's F5 / F6 cells.
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from zmb.arms.pg_store import ScratchPostgres  # noqa: E402

NAME, HOME, OTHER = "Quillfeather", "Wexcombe", "Dunmarrow"
#: a long, repetitive prompt: PostgreSQL compresses a value this big into TOAST (unless the column is stored EXTERNAL), which hides the name from a byte scan
PROMPT = "Extract SIGNIFICANT facts from text. Be SELECTIVE - only extract facts worth remembering long-term. " * 40


def retain(pg: ScratchPostgres, bank: str, doc: str, text: str, entity: str) -> None:
    """What one retained fact leaves in the schema (a model of the engine's writes, shapes from ``\\d`` of the migrated store)."""
    req = json.dumps({"items": [{"content": text, "document_id": doc, "tags": ["user:probe"], "context": "the user is speaking"}], "async": False})
    llm = json.dumps([{"role": "system", "content": PROMPT}, {"role": "user", "content": text}])
    pg.psql(
        "INSERT INTO banks (bank_id, name) VALUES (:'bank', :'bank') ON CONFLICT DO NOTHING;\n"
        "INSERT INTO documents (id, bank_id, original_text) VALUES (:'doc', :'bank', :'text');\n"
        "INSERT INTO chunks (chunk_id, document_id, bank_id, chunk_index, chunk_text) VALUES (:'bank' || ':' || :'doc', :'doc', :'bank', 0, :'text');\n"
        "INSERT INTO memory_units (bank_id, document_id, text, chunk_id, fact_type, search_vector, embedding) VALUES (:'bank', :'doc', :'text', "
        ":'bank' || ':' || :'doc', 'world', to_tsvector('english', :'text'), array_fill(0.01::real, ARRAY[384])::vector);\n"
        "INSERT INTO entities (canonical_name, bank_id) VALUES (:'ent', :'bank') ON CONFLICT DO NOTHING;\n"
        "INSERT INTO unit_entities (unit_id, entity_id) SELECT m.id, e.id FROM memory_units m, entities e WHERE m.document_id = :'doc' "
        "AND m.bank_id = :'bank' AND e.canonical_name = :'ent' AND e.bank_id = :'bank';\n"
        "INSERT INTO audit_log (action, transport, bank_id, request, response) VALUES ('retain', 'http', :'bank', :'req'::jsonb, '{}'::jsonb);\n"
        "INSERT INTO llm_requests (bank_id, operation, status, input, output) VALUES (:'bank', 'retain', 'success', :'llm'::jsonb, '[]'::jsonb);\n"
        "INSERT INTO async_operations (bank_id, operation_type, status, task_payload) VALUES (:'bank', 'retain', 'completed', :'req'::jsonb);",
        bank=bank, doc=doc, text=text, ent=entity, req=req, llm=llm)


def api_delete(pg: ScratchPostgres, bank: str, docs: "list[str]") -> None:
    """``MemoryEngine.delete_document``: one DELETE on ``documents`` (cascade to chunks / memory_units / unit_entities); nothing else."""
    for d in docs:
        pg.psql("DELETE FROM documents WHERE id = :'doc' AND bank_id = :'bank';", doc=d, bank=bank)


def snap(pg: ScratchPostgres, tokens: "list[str]") -> "dict":
    out = pg.scan(tokens)
    return {t: {"total": v["total"], "relations": {k: n for k, n in sorted((v["pg_relations"] | v["live_rows"]).items())}, "files": len(v["files"])}
            for t, v in out["tokens"].items()}


def trial(pg: ScratchPostgres, label: str, steps: "list[str]", *, bank_tag: str) -> "dict":
    bank = f"probe-{bank_tag}-{uuid.uuid4().hex[:6]}"
    pg.erase_orphans()
    pg.compact()
    row: "dict" = {"variant": label, "steps": steps, "baseline": snap(pg, [NAME, HOME])}
    docs = [f"d-{bank_tag}-1", f"d-{bank_tag}-2", f"d-{bank_tag}-3"]
    retain(pg, bank, docs[0], f"User's friend {NAME} lives in {HOME}.", NAME)
    retain(pg, bank, docs[1], f"User met {NAME} at the harbour last spring.", NAME)
    retain(pg, bank, docs[2], f"User's sister works at the {OTHER} observatory.", OTHER)
    row["after_retain"] = snap(pg, [NAME, HOME])
    if "autoanalyze" in steps:            # what autovacuum does within a minute of 50 changes: the planner statistics now hold the name
        pg.psql("ANALYZE;")
    api_delete(pg, bank, docs[:2])
    row["after_api_delete"] = snap(pg, [NAME])
    if "erase_text" in steps:
        row["erased"] = pg.erase_text(bank, NAME)
    if "compact" in steps:
        pg.compact()
    if "compact_no_wal" in steps:
        pg.compact(wal=False)
    if "compact_no_stats" in steps:
        pg.compact(stats=False)
    if "compact_wal_only" in steps:
        pg.compact(vacuum=False, stats=False)
    row["after_scrub"] = snap(pg, [NAME])
    pg.erase_bank(bank)
    pg.psql("DELETE FROM documents WHERE bank_id = :'bank';\nDELETE FROM entities WHERE bank_id = :'bank';\nDELETE FROM banks WHERE bank_id = :'bank';", bank=bank)
    return row


def compression_check(pg: ScratchPostgres, rows: int = 40) -> "dict":
    """The instrument's blind spot, MEASURED: a long compressible value stored with the default (compressing) storage is invisible to a byte scan, in
    the table and in the WAL alike (PostgreSQL compresses it before it is written); with ``STORAGE EXTERNAL`` (what ``prepare`` sets on the text-bearing
    columns) the scan finds every row. ``rows`` rows of a ~3 KB repetitive text carry one unique token each, in a throwaway table."""
    import re
    import shutil
    import tempfile
    from zmb import lab_driver
    lab_driver._service_path()
    import memory_residue
    out: "dict" = {}
    token = "Zyxwvutsrq"
    pg.psql("DROP TABLE IF EXISTS zz_comp;")
    for mode in ("EXTENDED", "EXTERNAL"):
        pg.compact()                    # a clean cluster first: the earlier mode's bytes are still in the WAL until it is switched away
        pg.psql("CREATE TABLE zz_comp (id int, t text);\n"
                f"ALTER TABLE zz_comp ALTER COLUMN t SET STORAGE {mode};\n"
                f"INSERT INTO zz_comp SELECT g, repeat('lorem ipsum dolor sit ', 100 + g) || '{token}' || g || repeat('amet consectetur ', 60) "
                f"FROM generate_series(1, {rows}) g;\nCHECKPOINT;")
        work = Path(tempfile.mkdtemp(prefix="pg-comp-"))
        try:
            pg.snapshot(work)
            hits = memory_residue.scan_palace(work, [token], copy=False)["tokens"][token]["files"]
        finally:
            shutil.rmtree(work, ignore_errors=True)
        out[mode] = {"rows_with_the_token": rows, "byte_hits_in_the_files": sum(hits.values()), "files_with_hits": len(hits)}
        pg.psql("DROP TABLE zz_comp;")
    pg.compact()
    return out


VARIANTS = (
    ("no scrub (the engine's own delete only)", []),
    ("erase_text only (log rows + orphan entity, no rewrite)", ["erase_text"]),
    ("compact only (rewrite + WAL, rows still there)", ["compact"]),
    ("erase_text + compact without the WAL switch", ["erase_text", "compact_no_wal"]),
    ("erase_text + compact without pg_statistic", ["erase_text", "compact_no_stats"]),
    ("erase_text + WAL switch only (no VACUUM FULL)", ["erase_text", "compact_wal_only"]),
    ("autoanalyze ran while the rows lived; erase_text + compact without pg_statistic", ["autoanalyze", "erase_text", "compact_no_stats"]),
    ("autoanalyze ran while the rows lived; erase_text + compact (the scrub)", ["autoanalyze", "erase_text", "compact"]),
    ("erase_text + compact (the scrub)", ["erase_text", "compact"]),
)


class LiveEngine:
    """The ``pg=`` sink of ``arms.fake_hindsight.FakeHindsight`` over the REAL scratch Postgres: what the engine writes for a retained item, as SQL (the
    shapes of ``retain`` above). With it a ``HindsightArm`` runs its real disk cells (reset, forget, hard delete, byte scan) against real Postgres files;
    only the engine's own code is modelled."""

    class _NoRows:
        def get(self, _table, default=None):
            return default if default is not None else []

    rows = _NoRows()

    def __init__(self, pg: ScratchPostgres):
        self.pg = pg

    def write(self, table: str, bank: str, text: str, ref: str = "") -> None:
        pg = self.pg
        if table == "banks":
            pg.psql("INSERT INTO banks (bank_id, name) VALUES (:'bank', :'bank') ON CONFLICT DO NOTHING;", bank=bank)
        elif table == "audit_log":
            pg.psql("INSERT INTO audit_log (action, transport, bank_id, request, response) VALUES ('retain', 'http', :'bank', :'text'::jsonb, '{}'::jsonb);",
                    bank=bank, text=text)
        elif table == "llm_requests":
            llm = json.dumps([{"role": "system", "content": PROMPT}, {"role": "user", "content": text}])
            pg.psql("INSERT INTO llm_requests (bank_id, operation, status, input, output) VALUES (:'bank', 'retain', 'success', :'llm'::jsonb, '[]'::jsonb);",
                    bank=bank, llm=llm)
        elif table == "documents":
            pg.psql("INSERT INTO documents (id, bank_id, original_text) VALUES (:'doc', :'bank', :'text') ON CONFLICT DO NOTHING;\n"
                    "INSERT INTO chunks (chunk_id, document_id, bank_id, chunk_index, chunk_text) VALUES (:'bank' || ':' || :'doc', :'doc', :'bank', 0, :'text') "
                    "ON CONFLICT DO NOTHING;", bank=bank, doc=ref, text=text)
        elif table == "memory_units":
            pg.psql("INSERT INTO memory_units (bank_id, document_id, text, chunk_id, fact_type, search_vector, embedding) VALUES (:'bank', :'doc', :'text', "
                    ":'bank' || ':' || :'doc', 'world', to_tsvector('english', :'text'), array_fill(0.01::real, ARRAY[384])::vector);",
                    bank=bank, doc=ref, text=text)
        elif table == "entities":
            pg.psql("INSERT INTO entities (canonical_name, bank_id) VALUES (:'ent', :'bank') ON CONFLICT DO NOTHING;\n"
                    "INSERT INTO unit_entities (unit_id, entity_id) SELECT m.id, e.id FROM memory_units m, entities e WHERE m.bank_id = :'bank' AND "
                    "e.bank_id = :'bank' AND e.canonical_name = :'ent' AND m.text ILIKE '%' || :'ent' || '%' ON CONFLICT DO NOTHING;", bank=bank, ent=text)

    def kill(self, table: str, bank: str, ref: str = "") -> None:
        if table == "documents" and ref:         # the engine's delete_document: one DELETE, the cascade does the rest
            self.pg.psql("DELETE FROM documents WHERE id = :'doc' AND bank_id = :'bank';", doc=ref, bank=bank)

    def drop_bank(self, bank: str) -> None:
        """The engine's delete_bank: documents, units, entities and the bank row; NOT the log tables."""
        self.pg.psql("DELETE FROM documents WHERE bank_id = :'bank';\nDELETE FROM entities WHERE bank_id = :'bank';\nDELETE FROM banks WHERE bank_id = :'bank';",
                     bank=bank)


def cells(seed: str = "zmb-heldout-a-t1") -> "dict":
    """F5 / F6 through the REAL arm code (``HindsightArm`` -> ``ScratchPostgres``) over the real scratch Postgres, with the engine modelled by ``LiveEngine``:
    H1 with the scrub (must PASS), H1 with the ``physical_erase`` control OFF and H0 with no Zoe layer (both must FAIL: residue found)."""
    from zmb import cells as cellmod, spec
    from zmb.arms.fake_hindsight import FakeHindsight
    from zmb.arms.hindsight import HindsightArm
    from zmb.world import make_world
    world = make_world(seed)
    pick = [c for c in spec.load_cells() if c.id in ("F5.forgotten_text_not_on_disk", "F6.hard_delete_not_on_disk")]
    pg = ScratchPostgres()
    out: "dict" = {"seed": seed, "arms": {}}
    for label, variant, kw in (("H1 scrub on", "H1", {}), ("H1 physical_erase OFF", "H1", {"off": frozenset(["physical_erase"])}), ("H0 (no Zoe layer)", "H0", {})):
        arm = HindsightArm(variant, transport=FakeHindsight(pg=LiveEngine(pg)), pg=pg, settle_poll_s=0, **kw)
        row: "dict" = {}
        try:
            for c in pick:
                t0 = time.monotonic()
                o = cellmod.run_cell(c.rendered(world), world, arm)
                ev = (o.evidence.get("probes") or [{}])[-1]
                row[c.id] = {"verdict": o.verdict, "byte_hits": ev.get("byte_hits"), "pg_relations": ev.get("pg_relations"),
                             "live_row_tables": ev.get("live_row_tables"), "reason": o.reason, "seconds": round(time.monotonic() - t0, 1)}
        finally:
            arm.close()
        out["arms"][label] = row
    pg.erase_orphans()
    pg.compact()
    return out


def main() -> int:
    if "--cells" in sys.argv[1:]:
        print(json.dumps(cells(), indent=1))
        return 0
    pg = ScratchPostgres()
    t0 = time.monotonic()
    out: "dict" = {"container": pg.container, "compression_check": compression_check(pg), "variants": []}
    for i, (label, steps) in enumerate(VARIANTS):
        t1 = time.monotonic()
        row = trial(pg, label, steps, bank_tag=f"v{i}")
        row["seconds"] = round(time.monotonic() - t1, 1)
        out["variants"].append(row)
    out["total_seconds"] = round(time.monotonic() - t0, 1)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
