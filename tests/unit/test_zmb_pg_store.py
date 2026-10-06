"""ZMB bake-off: the scratch-Postgres erase and verifier (``scripts/perf/zmb/arms/pg_store.py``) against a FAKE docker.

Slim-lane safe: ``Docker`` below is a runner double (no docker, no Postgres). What it proves is the WIRING: values are bound to psql, never spliced into
the SQL; the scrub is the sequence the measured ablation says it must be; the scan copies the data directory out of the container, scans the copy,
removes it, and reports counts only; a missing container is a SKIP (``PgUnavailable``), never a pass. What Postgres actually leaves on disk is MEASURED
against the real scratch container by ``pilot/pg_erase_probe.py`` (and the opt-in test at the bottom, ``ZMB_PG_LIVE=1``).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "perf"))

from zmb.arms import pg_store  # noqa: E402
from zmb.arms.pg_store import PgUnavailable, ScratchPostgres  # noqa: E402

TABLES = "audit_log\nllm_requests\nasync_operations\nobservation_history\ninvalidated_memory_units\nchunks\nentities\nunit_entities\nbanks\ndocuments\nmemory_units"


class Docker:
    """Records every call; answers the few queries the module makes; ``docker cp`` drops files into the destination."""

    def __init__(self, files: "dict[str, bytes] | None" = None, live: str = "", other: str = ""):
        self.calls: "list[tuple[list[str], str]]" = []
        self.files, self.live, self.other = files or {}, live, other
        self.copied_to: "list[Path]" = []

    def __call__(self, argv, *, stdin=None, timeout=0):
        self.calls.append((list(argv), stdin or ""))
        if argv[1] == "cp":
            dest = Path(argv[3])
            self.copied_to.append(dest)
            for rel, data in self.files.items():
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                (dest / rel).write_bytes(data)
            return 0, ""
        script = stdin or ""
        if "information_schema.tables" in script:
            return 0, TABLES + "\n"
        if "information_schema.columns" in script:
            return 0, "audit_log\nllm_requests\n"
        if "pg_relation_filepath" in script:
            return 0, "base/16384/16400|audit_log\nbase/16384/16401|llm_requests\n"
        if "r.bank_id <>" in script:
            return 0, self.other
        if "r::text ILIKE" in script:
            return 0, self.live
        return 0, ""

    def scripts(self, needle: str) -> "list[str]":
        return [s for _a, s in self.calls if needle in s]


def mk(**kw):
    d = Docker(**kw)
    return ScratchPostgres(runner=d, scratch=None), d


# ── values are bound, never spliced ──────────────────────────────────────────

def test_the_forgotten_name_and_the_bank_reach_psql_as_variables_never_inside_the_sql():
    pg, d = mk()
    evil = "Mari'; DROP TABLE documents; --"
    pg.erase_text("zmb-h1-demo_bar_1", evil)
    (argv, script), = [(a, s) for a, s in d.calls if "DELETE FROM public.audit_log" in s]
    assert "DROP TABLE" not in script and "Mari" not in script and "zmb-h1" not in script
    assert f"pat={pg_store.like_pattern(evil)}" in argv and "bank=zmb-h1-demo_bar_1" in argv
    assert ":'pat'" in script and ":'bank'" in script and argv[:4] == ["docker", "exec", "-i", "zoe-bakeoff-pg"] and "ON_ERROR_STOP=1" in argv


def test_like_metacharacters_in_a_name_are_escaped():
    assert pg_store.like_pattern("50%_off\\") == "%50\\%\\_off\\\\%"
    assert pg_store.like_pattern("  Marisol ") == "%Marisol%"


def test_unsafe_identifiers_are_refused():
    with pytest.raises(ValueError):
        ScratchPostgres(schema="public; DROP SCHEMA x")
    pg, _d = mk()
    with pytest.raises(ValueError):
        pg._q("audit_log; --")


# ── the erase and the compaction are what the measured ablation says ─────────

def test_erase_text_covers_every_log_and_derived_table_and_prunes_orphan_entities_but_never_the_engines_own_rows():
    pg, d = mk()
    out = pg.erase_text("b", "Marisol")
    script = "\n".join(d.scripts("DELETE FROM"))
    for table in ("audit_log", "llm_requests", "async_operations", "observation_history", "invalidated_memory_units", "chunks", "entities"):
        assert f"DELETE FROM public.{table}" in script, table
    assert "NOT EXISTS (SELECT 1 FROM public.unit_entities" in script
    for table in ("documents", "memory_units", "banks"):                          # an API delete that missed a row must show as residue, never be hidden
        assert f"DELETE FROM public.{table}" not in script, table
    assert isinstance(out, dict)


def test_erase_bank_and_orphans_are_scoped():
    pg, d = mk()
    pg.erase_bank("zmb-h1-x")
    assert all("bank_id = :'bank'" in s for s in d.scripts("DELETE FROM") if "NOT IN" not in s)
    pg.erase_orphans()
    assert any("bank_id NOT IN (SELECT bank_id FROM public.banks)" in s for s in d.scripts("DELETE FROM"))


def test_compact_is_vacuum_full_then_statistics_then_two_wal_switches_in_that_order():
    pg, d = mk()
    pg.compact()
    (script,) = d.scripts("VACUUM")
    order = [script.index(x) for x in ("DELETE FROM pg_catalog.pg_statistic", "VACUUM (FULL, ANALYZE) public.audit_log", "VACUUM FULL pg_catalog.pg_statistic",
                                       "CHECKPOINT;", "SELECT pg_switch_wal();")]
    assert order == sorted(order) and script.count("pg_switch_wal") == 2 and script.count("CHECKPOINT") == 3
    for table in ("audit_log", "llm_requests", "documents", "memory_units", "entities", "observation_history"):
        assert f"VACUUM (FULL, ANALYZE) public.{table};" in script
    pg2, d2 = mk()
    pg2.compact(vacuum=False, stats=False, wal=False)                              # the ablation switches of the probe
    assert "VACUUM" not in d2.scripts("lock_timeout")[0] and "pg_switch_wal" not in d2.scripts("lock_timeout")[0]


def test_prepare_turns_wal_recycling_off_and_text_storage_uncompressed_once():
    pg, d = mk()
    pg.prepare()
    pg.prepare()
    (script,) = d.scripts("wal_recycle")
    assert "ALTER SYSTEM SET wal_recycle = off" in script and "ALTER COLUMN request SET STORAGE EXTERNAL" in script
    assert "ALTER COLUMN canonical_name SET STORAGE EXTERNAL" in script


# ── the scan ──────────────────────────────────────────────────────────────────

def test_scan_copies_the_data_directory_out_scans_the_copy_attributes_the_hits_and_removes_the_copy(tmp_path):
    pg, d = mk(files={"base/16384/16400": b"..." + b"Marisol" + b"... marisol ...", "pg_wal/000000010000000000000007": b"xx Marisol xx", "base/16384/9": b"clean"})
    pg._scratch = str(tmp_path)
    out = pg.scan(["Marisol", "Ines"])
    m, i = out["tokens"]["Marisol"], out["tokens"]["Ines"]
    assert m["total"] == 3 and m["files"] == {"base/16384/16400": 2, "pg_wal/000000010000000000000007": 1}       # upper- and lower-case spellings both counted
    assert m["pg_relations"] == {"audit_log": 2, "pg_wal": 1} and i["total"] == 0 and out["clean"] is False
    (cp,) = [a for a, _s in d.calls if a[1] == "cp"]
    assert cp[:2] == ["docker", "cp"] and cp[2] == "zoe-bakeoff-pg:/var/lib/postgresql/data/." and not d.copied_to[0].exists()      # the copy is gone
    assert not list(tmp_path.iterdir())
    assert "Marisol" not in json.dumps({k: v for k, v in out.items() if k != "tokens"})                                  # counts and names of relations only


def test_scan_counts_live_rows_the_byte_scan_cannot_see_and_a_clean_cluster_is_clean(tmp_path):
    pg, _d = mk(files={"base/16384/9": b"nothing here"}, live="llm_requests|1\naudit_log|0\n")
    pg._scratch = str(tmp_path)
    t = pg.scan(["Marisol"])["tokens"]["Marisol"]
    assert t["live_rows"] == {"llm_requests": 1, "audit_log": 0} and t["total"] == 1                       # a compressed TOAST value would be found here
    pg2, _d2 = mk(files={"base/16384/9": b"nothing here"})
    pg2._scratch = str(tmp_path)
    assert pg2.scan(["Marisol"])["clean"] is True
    with pytest.raises(ValueError):
        pg2.scan([""])


def test_other_banks_counts_only_rows_of_banks_that_still_exist():
    pg, d = mk(other="audit_log|2\nllm_requests|0\n")
    assert pg.other_banks(["Marisol"], "mine") == {"Marisol": 2}
    script = d.scripts("r.bank_id <>")[0]
    assert "r.bank_id IN (SELECT bank_id FROM public.banks)" in script
    pg2, _d2 = mk()
    assert pg2.other_banks(["Marisol"], "mine") == {}


# ── no docker / no container is a SKIP with the reason ───────────────────────

def test_a_missing_container_or_docker_is_unavailable_never_a_pass(monkeypatch):
    def gone(argv, *, stdin=None, timeout=0):
        return 1, "Error response from daemon: No such container: zoe-bakeoff-pg"
    pg = ScratchPostgres(runner=gone)
    for call in (lambda: pg.prepare(), lambda: pg.snapshot(Path("/nonexistent"))):
        with pytest.raises(PgUnavailable) as e:
            call()
        assert isinstance(e.value, NotImplementedError)
    monkeypatch.setattr(pg_store.shutil, "which", lambda _n: None)
    with pytest.raises(PgUnavailable, match="docker is not on PATH"):
        pg_store.docker_runner(["docker", "ps"])
    stopped = ScratchPostgres(runner=lambda argv, **kw: (1, "Error: container zoe-bakeoff-pg is not running"))
    with pytest.raises(PgUnavailable, match="not running"):
        stopped.tables()


def test_a_failing_statement_is_loud_not_swallowed():
    pg = ScratchPostgres(runner=lambda argv, **kw: (3, "psql:<stdin>:5: ERROR:  relation does not exist"))
    with pytest.raises(RuntimeError, match="psql failed"):
        pg.tables()


# ── the live check (opt-in: needs the scratch container up; never runs in CI) ────────────────────────────────

@pytest.mark.skipif(not os.environ.get("ZMB_PG_LIVE"), reason="set ZMB_PG_LIVE=1 with the scratch Postgres running (docker compose -f ~/.zoe/bakeoff-2026-10/scratch-postgres.compose.yml up -d)")
def test_live_the_unscrubbed_delete_leaves_text_and_the_scrub_removes_it():
    sys.path.insert(0, str(REPO / "scripts" / "perf" / "zmb" / "pilot"))
    import pg_erase_probe as probe
    pg = ScratchPostgres()
    bare = probe.trial(pg, "no scrub", [], bank_tag="ci0")
    full = probe.trial(pg, "scrub", ["erase_text", "compact"], bank_tag="ci1")
    assert bare["baseline"][probe.NAME]["total"] == 0 and bare["after_retain"][probe.NAME]["total"] > 0          # the scan sees what is there
    assert bare["after_scrub"][probe.NAME]["total"] > 0 and full["after_scrub"][probe.NAME]["total"] == 0        # the negative control, then the scrub


@pytest.mark.skipif(not os.environ.get("ZMB_PG_LIVE"), reason="set ZMB_PG_LIVE=1 with the scratch Postgres running")
def test_live_the_real_arm_runs_f5_and_f6_over_real_postgres_green_with_the_scrub_red_without_it():
    sys.path.insert(0, str(REPO / "scripts" / "perf" / "zmb" / "pilot"))
    import pg_erase_probe as probe
    out = probe.cells()["arms"]
    assert [v["verdict"] for v in out["H1 scrub on"].values()] == ["PASS", "PASS"] and all(v["byte_hits"] == 0 for v in out["H1 scrub on"].values())
    for label in ("H1 physical_erase OFF", "H0 (no Zoe layer)"):                 # the negative controls: the scan finds real residue on the real stack
        assert [v["verdict"] for v in out[label].values()] == ["FAIL", "FAIL"], label
        assert all(v["byte_hits"] > 0 for v in out[label].values()), label


def test_compact_retries_a_lock_timeout_and_only_a_lock_timeout(monkeypatch):
    monkeypatch.setattr(pg_store.time, "sleep", lambda _s: None)
    replies = [(3, "ERROR:  canceling statement due to lock timeout"), (0, "")]
    seen = []

    def runner(argv, *, stdin=None, timeout=0):
        seen.append(stdin or "")
        if "information_schema.tables" in (stdin or ""):
            return 0, TABLES + "\n"
        if "VACUUM" in (stdin or ""):
            return replies.pop(0)
        return 0, ""
    pg = ScratchPostgres(runner=runner)
    pg.compact()
    assert len([s for s in seen if "VACUUM" in s]) == 2 and not replies               # one retry, then it went through
    bad = ScratchPostgres(runner=lambda argv, **kw: (3, "ERROR: out of shared memory") if "VACUUM" in (kw.get("stdin") or "") else (0, TABLES + "\n"))
    with pytest.raises(RuntimeError, match="out of shared memory"):                      # any other failure is loud at once
        bad.compact()
