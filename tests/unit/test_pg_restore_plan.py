"""A backed-up table with a FOREIGN KEY into a data-excluded (biometric) table must not break a restore.

2026-10-25: ``omi_segments.voice_cluster_id`` -> ``omi_voice_clusters`` (rows excluded from the dump by owner privacy
rule) made every restore end with a failed ``ADD CONSTRAINT ... FOREIGN KEY`` and the weekly verify red. The fix is a
sectioned restore with the dangling links nulled (``scripts/maintenance/pg_restore_plan.py``). Hermetic: no Docker.
The end-to-end proof (a real dump into a scratch DB: 0 errors) is recorded in the PR; the lab is labs/restore-drill.
"""
from __future__ import annotations

import gzip
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

MAINT = Path(__file__).resolve().parents[2] / "scripts" / "maintenance"


def _load(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, MAINT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


plan = _load("pg_restore_plan")
drill = _load("zoe_restore_drill")

SCHEMA = '''
ALTER TABLE ONLY public.omi_segments
    ADD CONSTRAINT omi_segments_voice_cluster_id_fkey FOREIGN KEY (voice_cluster_id) REFERENCES public.omi_voice_clusters(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.learned_voiceprint_segments
    ADD CONSTRAINT learned_voiceprint_segments_voiceprint_id_fkey FOREIGN KEY (voiceprint_id) REFERENCES public.learned_voiceprints(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.chat_messages
    ADD CONSTRAINT chat_messages_user_fk FOREIGN KEY (user_id) REFERENCES public.auth_users(id);

ALTER TABLE ONLY public."Odd Child"
    ADD CONSTRAINT "odd fk" FOREIGN KEY (a, "b c") REFERENCES public.omi_voice_clusters(a, b);
'''
TOC = """;
1; 1259 1 TABLE public omi_segments zoe
2; 1259 2 TABLE public omi_voice_clusters zoe
3; 1259 3 TABLE public learned_voiceprints zoe
4; 1259 4 TABLE public learned_voiceprint_segments zoe
5; 1259 5 TABLE public chat_messages zoe
6; 1259 6 TABLE public auth_users zoe
7; 1259 7 TABLE public Odd Child zoe
10; 0 1 TABLE DATA public omi_segments zoe
15; 0 5 TABLE DATA public chat_messages zoe
16; 0 6 TABLE DATA public auth_users zoe
17; 0 7 TABLE DATA public Odd Child zoe
"""
EXCLUDED = ["public.learned_voiceprint_segments", "public.learned_voiceprints", "public.omi_voice_clusters"]


def test_parse_fks_reads_columns_quoting_and_composites():
    fks = {f["name"]: f for f in plan.parse_fks(SCHEMA)}
    assert fks["omi_segments_voice_cluster_id_fkey"] == {
        "child": "public.omi_segments", "name": "omi_segments_voice_cluster_id_fkey",
        "columns": ["voice_cluster_id"], "parent": "public.omi_voice_clusters"}
    assert fks["odd fk"]["child"] == "public.Odd Child" and fks["odd fk"]["columns"] == ["a", "b c"]
    assert len(fks) == 4


def test_only_fks_from_a_backed_up_table_into_an_excluded_one_are_planned():
    got = plan.excluded_fks(plan.parse_fks(SCHEMA), EXCLUDED)
    assert {f["name"] for f in got} == {"omi_segments_voice_cluster_id_fkey", "odd fk"}
    # both ends excluded (learned_voiceprint_segments -> learned_voiceprints): no data either side, nothing to null
    # an FK into a table whose data IS in the dump (chat_messages -> auth_users): untouched
    assert plan.excluded_fks(plan.parse_fks(SCHEMA), []) == []          # negative control: nothing excluded, no plan


def test_nulling_sql_quotes_identifiers_and_keeps_rows():
    sql = plan.nulling_sql(plan.excluded_fks(plan.parse_fks(SCHEMA), EXCLUDED))
    assert 'UPDATE "public"."omi_segments" SET "voice_cluster_id" = NULL WHERE "voice_cluster_id" IS NOT NULL;' in sql
    assert ('UPDATE "public"."Odd Child" SET "a" = NULL, "b c" = NULL '
            'WHERE "a" IS NOT NULL OR "b c" IS NOT NULL;') in sql
    assert "DELETE" not in sql and "DROP" not in sql               # the row and the constraint survive
    assert plan.nulling_sql([]) == ""


def test_excluded_set_prefers_the_manifest_but_a_missing_manifest_cannot_hide_the_problem():
    toc = TOC.splitlines()
    from_toc = plan.excluded_tables(None, toc)
    assert "public.omi_voice_clusters" in from_toc and "public.omi_segments" not in from_toc
    man = {"tables": {}, "data_excluded": ["public.omi_voice_clusters"]}
    assert plan.excluded_tables(man, toc) == ["public.omi_voice_clusters"]
    # an old manifest without the field falls back to the TOC
    assert plan.excluded_tables({"tables": {}}, toc) == from_toc
    assert {f["name"] for f in plan.plan_for(toc, SCHEMA, None)} >= {"omi_segments_voice_cluster_id_fkey"}


def test_manifest_records_the_excluded_fks_field():
    man = _load("pg_dump_manifest")
    import io
    m = man.build_manifest(TOC.splitlines(), io.StringIO(""), None, {}, "")
    assert m["excluded_fks"] == []              # filled from the dump's schema by _manifest at backup time
    src = (MAINT / "pg_dump_manifest.py").read_text()
    assert 'man["excluded_fks"]' in src and '"-s", "-f", "-"' in src


# ── the sandbox restore sequence ─────────────────────────────────────────────────────────────────────────────────

class Rec(drill.Sandbox):
    """A sandbox whose helper is a recorder: returns canned structure for ``inspect`` and fails what it is told to."""

    def __init__(self, fail=None):
        super().__init__(["fake"])
        self.calls: list[tuple] = []
        self.fail = fail
        self.nulling = None
        self.runner = self._run

    def _run(self, argv, **kw):
        args = tuple(argv[1:])
        self.calls.append(args)
        out, rc, err = b"", 0, b""
        if args == ("inspect", "list"):
            out = TOC.encode()
        elif args == ("inspect", "schema"):
            out = SCHEMA.encode()
        elif args[0] == "psql":
            self.nulling = kw["input"].decode()
        if args == self.fail:
            rc, err = 1, b"ERROR: boom"
        return subprocess.CompletedProcess(argv, rc, out, err)


def _dump(tmp_path: Path) -> Path:
    p = tmp_path / "zoe-1.dump.gz"
    with gzip.open(p, "wb") as fh:
        fh.write(b"PGDMP-not-read-by-the-fake")
    return p


def test_restore_runs_sections_with_the_nulling_between_data_and_post_data(tmp_path):
    sb = Rec()
    ok, err = sb.restore("zoe", _dump(tmp_path))
    assert (ok, err) == (True, "")
    assert sb.calls == [("inspect", "list"), ("inspect", "schema"), ("restore", "zoe", "pre-data"),
                        ("restore", "zoe", "data"), ("psql", "zoe"), ("restore", "zoe", "post-data")]
    assert 'UPDATE "public"."omi_segments"' in sb.nulling


def test_restore_with_nothing_to_null_skips_the_update(tmp_path):
    sb = Rec()
    sb.restore("multica", _dump(tmp_path), {"tables": {}, "data_excluded": []})
    assert ("psql", "multica") not in sb.calls and sb.calls[-1] == ("restore", "multica", "post-data")


@pytest.mark.parametrize("failing", [("restore", "zoe", "pre-data"), ("restore", "zoe", "data"),
                                     ("restore", "zoe", "post-data"), ("psql", "zoe")])
def test_any_other_error_still_fails_the_restore_and_stops_there(tmp_path, failing):
    # negative control: errors are NOT blanket-ignored - the first failing step fails the restore with its stderr
    sb = Rec(fail=failing)
    ok, err = sb.restore("zoe", _dump(tmp_path))
    assert ok is False and "boom" in err
    assert sb.calls[-1] == failing


def test_unreadable_dump_structure_fails_the_restore(tmp_path):
    sb = Rec(fail=("inspect", "schema"))
    ok, err = sb.restore("zoe", _dump(tmp_path))
    assert ok is False and "structure" in err and not any(c[0] == "restore" for c in sb.calls)


# ── the root helper ──────────────────────────────────────────────────────────────────────────────────────────────

def test_helper_restores_one_section_with_exit_on_error_and_inspects_without_a_connection():
    h = (Path(__file__).resolve().parents[2] / "scripts/setup/p0-deroot/libexec/zoe-restore-drill-pg").read_text()
    assert '--section="$3" --exit-on-error' in h
    assert "pg_restore -l" in h and "pg_restore -s -f -" in h
    inspect = h[h.index("  inspect)\n"):h.index("  psql)\n")]
    assert " -d " not in inspect                 # inspect never opens a database connection
