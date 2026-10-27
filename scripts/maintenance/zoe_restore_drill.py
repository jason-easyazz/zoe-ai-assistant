#!/usr/bin/env python3
"""Weekly RESTORE DRILL of the on-box backups into a throwaway sandbox (deep dive 2, S0-8).

Replaces the old ``zoe-backup-verify`` body, which restored the newest dumps INTO THE LIVE Postgres instance,
counted two tables, never loaded the palace's vector index, and ran inside the night window. Now:

1. **Postgres** - the newest daily ``zoe`` and ``multica`` dumps are restored into a THROWAWAY pg17 + pgvector
   container (``zoe-restore-drill``: the same image digest as the live ``zoe-database``, its own ``--internal``
   network, no volume, no published port, memory-capped, removed afterwards) through the fixed-function helper
   ``scripts/setup/p0-deroot/libexec/zoe-restore-drill-pg`` (the owner's root-installed copy via ``sudo -n`` when
   present, else the repo copy while ``zoe`` is still in the docker group). Checked against the manifest
   ``pg_dump_manifest.py`` wrote at backup time:
   * every table's exact row count (``count(*) FROM ONLY``), no table missing, none extra;
   * the dumped table set equals the live catalog's at backup time (a dump that silently lacked a table);
   * the pgvector extension version, and the Alembic revision;
   * HNSW: every HNSW index in the restore is valid and USED by a nearest-neighbour query that returns the probed
     row; and in all cases a synthetic HNSW index is BUILT in the sandbox and queried (today no live table has
     one, so this is what proves the image's pgvector can build and use one).
2. **MemPalace** - the newest tarball is extracted to a temp dir and opened with the zoe-data py3.12 venv's
   chromadb (``palace_drill_check.py``: format guard, counts vs SQLite, a vector query that loads HNSW).
3. **Host archive** - the newest ``~/.zoe-backups/host/host-*.tar.gz`` (``zoe_host_snapshot.py``): every file
   against the sha256 in its ``MANIFEST.json``, every source label present, the HA ``.storage`` export opens.
4. **Freshness** - the newest dump / tarball / host archive is from the last 26 h.

Never touches the live database or the live palace: nothing here connects to ``zoe-database`` (it only reads the
container's image name), and the palace check refuses ``~/.mempalace``.

Guards: refuses inside the night window (01:35-04:55) or while ``zoe-night-window`` runs (quietly: a Persistent=
catch-up after a night boot), waits up to 45 min for a running ``zoe-backup`` / ``zoe-offbox-backup``, and refuses
(LOUDLY) when MemAvailable is under the floor (1100 MB; the voice stack is resident). Findings alert
over Telegram through the crash-loop watcher's sender (``zoe_offbox.alert``).

Exit: 0 all checks pass, 1 findings (alerted), 3 refused QUIETLY (night window: expected, not a failure),
4 refused LOUDLY (low memory, backup stuck, or no pass for STALE_PASS_DAYS: alerted, and the unit shows failed).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
HELPER_REPO = REPO / "scripts" / "setup" / "p0-deroot" / "libexec" / "zoe-restore-drill-pg"
HELPER_INSTALLED = Path("/usr/local/libexec/zoe-priv/zoe-restore-drill-pg")
PALACE_CHECK = HERE / "palace_drill_check.py"
DBS = ("zoe", "multica")
# MEASURED 2026-10-27 on the live Orin (3 runs): the whole drill (pg17 container + palace chromadb + host check)
# drops MemAvailable by ~180 MB, the container itself peaks ~175 MB; its hard cap is 512 MB (zoe-restore-drill-pg
# MEM, swap == cap). Floor = that cap + ~600 MB left for the resident voice stack. The old 2000 MB sat above the
# daytime norm (1.4-1.6 GB), so the drill refused every week and never ran.
MEM_FLOOR_MB = 1100
EXIT_REFUSED_QUIET = 3
EXIT_REFUSED_LOUD = 4
FRESH_HOURS = 26
WAIT_BUSY_S = 45 * 60
STALE_PASS_DAYS = 15
BUSY_UNITS = ("zoe-backup.service", "zoe-offbox-backup.service")
NIGHT_UNIT = "zoe-night-window.service"
LIVE_DB_CONTAINER = "zoe-database"


def _load(name: str):
    if name in sys.modules:                 # one module object per process (tests patch it)
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


ob = _load("zoe_offbox")
restore_plan = _load("pg_restore_plan")

# ── SQL run in the SANDBOX only ──────────────────────────────────────────────────────────────────────────────────
COUNTS_SQL = r"""
SELECT n.nspname || '.' || c.relname || '|' ||
       (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM ONLY %I.%I', n.nspname, c.relname),
                                            false, true, '')))[1]::text
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg\_toast%'
  AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype = 'e')
ORDER BY 1;
"""
EXT_SQL = "SELECT extname || '=' || extversion FROM pg_extension ORDER BY 1;"
# a CASE would still plan the missing relation: psql's \if skips it instead
ALEMBIC_SQL = """SELECT to_regclass('public.alembic_version') IS NOT NULL AS has_alembic \\gset
\\if :has_alembic
SELECT string_agg(version_num, ',' ORDER BY version_num) FROM public.alembic_version;
\\endif
"""
HNSW_LIST_SQL = r"""
SELECT ci.relname || '|' || n.nspname || '|' || ct.relname || '|' || a.attname || '|' || oc.opcname || '|' || ix.indisvalid
FROM pg_index ix
JOIN pg_class ci ON ci.oid = ix.indexrelid JOIN pg_am am ON am.oid = ci.relam AND am.amname = 'hnsw'
JOIN pg_class ct ON ct.oid = ix.indrelid JOIN pg_namespace n ON n.oid = ct.relnamespace
JOIN pg_attribute a ON a.attrelid = ct.oid AND a.attnum = ix.indkey[0]
JOIN pg_opclass oc ON oc.oid = ix.indclass[0]
ORDER BY 1;
"""
OPS = {"l2": "<->", "cosine": "<=>", "ip": "<#>", "l1": "<+>"}
SYNTHETIC_HNSW_SQL = r"""
CREATE EXTENSION IF NOT EXISTS vector;
DROP TABLE IF EXISTS drill_vec;
CREATE TABLE drill_vec (id int PRIMARY KEY, v vector(8));
INSERT INTO drill_vec SELECT i, ARRAY[sin(i), cos(i), sin(2*i), cos(3*i), sin(5*i), cos(7*i), sin(11*i), cos(13*i)]::vector
  FROM generate_series(1, 2000) AS i;
CREATE INDEX drill_vec_hnsw ON drill_vec USING hnsw (v vector_l2_ops);
SET enable_seqscan = off;
SELECT v AS probe FROM drill_vec WHERE id = 1234 \gset
\echo PLAN
EXPLAIN (COSTS OFF) SELECT id FROM drill_vec ORDER BY v <-> :'probe' LIMIT 1;
\echo RESULT
SELECT id FROM drill_vec ORDER BY v <-> :'probe' LIMIT 1;
\echo VERSION
SELECT extversion FROM pg_extension WHERE extname = 'vector';
"""


def _ident(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


# ── the sandbox (through the helper; never the live container) ───────────────────────────────────────────────────


@dataclass
class Sandbox:
    """Runs the fixed-function helper. ``base`` is its argv prefix."""
    base: list[str]
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run

    @classmethod
    def default(cls) -> "Sandbox":
        if os.environ.get("ZOE_DRILL_HELPER"):              # lab / tests
            return cls(os.environ["ZOE_DRILL_HELPER"].split())
        if HELPER_INSTALLED.is_file():                       # after P0: the owner's root-owned copy
            return cls(["sudo", "-n", str(HELPER_INSTALLED)])
        return cls(["sh", str(HELPER_REPO)])                 # before P0: zoe is still in the docker group

    def call(self, *args: str, stdin=None, text_input: Optional[str] = None, timeout: int = 1800):
        kw: dict = {"capture_output": True, "timeout": timeout}
        if text_input is not None:
            kw.update(input=text_input.encode())
        elif stdin is not None:
            kw.update(stdin=stdin)
        return self.runner([*self.base, *args], **kw)

    def up(self) -> None:
        r = self.call("up", timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"sandbox up failed: {r.stderr.decode(errors='replace').strip()[-300:]}")

    def down(self) -> bool:
        return self.call("down", timeout=120).returncode == 0

    def restore(self, db: str, dump_gz: Path, manifest: Optional[dict] = None) -> tuple[bool, str]:
        """pre-data -> data -> NULL the links into data-excluded (biometric) tables -> post-data. One pass would end
        with a failed FK on every dump whose parent rows were deliberately left out (pg_restore_plan.py); sectioned,
        any OTHER pg_restore error still fails the restore (the helper runs ``--exit-on-error``)."""
        # decompressed to a private temp file first: a GzipFile passed as stdin= would hand the child the
        # COMPRESSED descriptor (the lab caught exactly that)
        def err(r) -> str:
            return r.stderr.decode(errors="replace").strip()[-400:]

        with tempfile.TemporaryDirectory(prefix="drill-dump-") as td:
            os.chmod(td, 0o700)
            plain = Path(td) / "dump"
            with gzip.open(dump_gz, "rb") as src, open(plain, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            with open(plain, "rb") as fh:
                toc = self.call("inspect", "list", stdin=fh)
            with open(plain, "rb") as fh:
                schema = self.call("inspect", "schema", stdin=fh)
            for r in (toc, schema):
                if r.returncode != 0:
                    return False, "could not read the dump's structure: " + err(r)
            nulling = restore_plan.nulling_sql(restore_plan.plan_for(toc.stdout.decode(errors="replace").splitlines(),
                                                     schema.stdout.decode(errors="replace"), manifest))
            for section in ("pre-data", "data", "post-data"):
                if section == "post-data" and nulling:
                    r = self.call("psql", db, text_input=nulling, timeout=600)
                    if r.returncode != 0:
                        return False, "nulling links into data-excluded tables failed: " + err(r)
                with open(plain, "rb") as fh:
                    r = self.call("restore", db, section, stdin=fh)
                if r.returncode != 0:
                    return False, f"{section}: " + err(r)
        return True, ""

    def sql(self, db: str, sql: str) -> list[str]:
        r = self.call("psql", db, text_input=sql, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"sandbox psql {db}: {r.stderr.decode(errors='replace').strip()[-300:]}")
        return [ln for ln in r.stdout.decode(errors="replace").splitlines() if ln.strip()]


# ── comparisons (pure; unit-tested) ──────────────────────────────────────────────────────────────────────────────


def compare_tables(db: str, manifest: dict, restored: dict[str, int]) -> list[str]:
    out = []
    want: dict[str, int] = manifest.get("tables") or {}
    if not want:
        return [f"{db}: the manifest lists no tables"]
    missing = sorted(set(want) - set(restored))
    extra = sorted(set(restored) - set(want))
    wrong = sorted(t for t in set(want) & set(restored) if want[t] != restored[t])
    if missing:
        out.append(f"{db}: {len(missing)} table(s) in the manifest missing after restore: {', '.join(missing[:5])}")
    if extra:
        out.append(f"{db}: {len(extra)} table(s) restored that the manifest does not list: {', '.join(extra[:5])}")
    for t in wrong[:5]:
        out.append(f"{db}: {t} restored {restored[t]} row(s), the dump holds {want[t]}")
    if len(wrong) > 5:
        out.append(f"{db}: ... {len(wrong) - 5} more table(s) with a wrong row count")
    live = manifest.get("live_tables")
    if live is not None:
        gone = sorted(set(live) - set(want))
        if gone:
            out.append(f"{db}: {len(gone)} live table(s) were never in the dump: {', '.join(gone[:5])}")
    for prob in manifest.get("problems") or []:
        out.append(f"{db}: at backup time: {prob}")
    return out


def compare_extensions(db: str, manifest: dict, restored: dict[str, str]) -> list[str]:
    out = []
    for ext, ver in sorted((manifest.get("extensions") or {}).items()):
        got = restored.get(ext)
        if got is None:
            out.append(f"{db}: extension {ext} {ver} is not in the restore")
        elif got != ver:
            label = "pgvector" if ext == "vector" else ext
            out.append(f"{db}: {label} {got} in the restore image, the dump was taken at {ver} - "
                       "the drill image is not the live one")
    return out


def parse_counts(lines: list[str]) -> dict[str, int]:
    out = {}
    for ln in lines:
        name, _, n = ln.rpartition("|")
        out[name] = int(n)
    return out


def _op_for(opclass: str) -> Optional[str]:
    for key, op in OPS.items():
        if opclass.endswith(f"_{key}_ops"):
            return op
    return None


def check_existing_hnsw(sb: Sandbox, db: str) -> tuple[list[str], list[str]]:
    """``(findings, notes)`` for every HNSW index in a restored database."""
    findings, notes = [], []
    for ln in sb.sql(db, HNSW_LIST_SQL):
        idx, schema, table, col, opclass, valid = ln.split("|")
        if valid not in ("t", "true"):
            findings.append(f"{db}: HNSW index {idx} is INVALID after restore")
            continue
        op = _op_for(opclass)
        if op is None:
            notes.append(f"{db}: HNSW index {idx} uses {opclass}; no probe for that operator class")
            continue
        q = f"{_ident(schema)}.{_ident(table)}"
        c = _ident(col)
        sql = (f"SELECT ctid AS pctid, {c} AS probe FROM {q} WHERE {c} IS NOT NULL LIMIT 1 \\gset\n"
               f"SET enable_seqscan = off;\n\\echo PLAN\n"
               f"EXPLAIN (COSTS OFF) SELECT ctid FROM {q} ORDER BY {c} {op} :'probe' LIMIT 1;\n"
               f"\\echo RESULT\nSELECT ctid = :'pctid'::tid OR ({c} {op} :'probe') <= 1e-6 "
               f"FROM {q} ORDER BY {c} {op} :'probe' LIMIT 1;\n")
        try:
            lines = sb.sql(db, sql)
        except RuntimeError as exc:
            findings.append(f"{db}: HNSW probe on {idx} failed: {exc}")
            continue
        plan = lines[lines.index("PLAN") + 1: lines.index("RESULT")] if "RESULT" in lines else []
        result = lines[lines.index("RESULT") + 1:] if "RESULT" in lines else []
        if not any(idx in p for p in plan):
            findings.append(f"{db}: the nearest-neighbour query on {q} did not use HNSW index {idx}")
        elif result[:1] != ["t"]:
            findings.append(f"{db}: HNSW index {idx} did not return the probed row as its nearest neighbour")
        else:
            notes.append(f"{db}: HNSW index {idx} valid, used and returns the probed row")
    return findings, notes


def check_synthetic_hnsw(sb: Sandbox) -> tuple[list[str], str]:
    try:
        lines = sb.sql("drill", SYNTHETIC_HNSW_SQL)
    except RuntimeError as exc:
        return [f"pgvector: building an HNSW index in the sandbox failed: {exc}"], ""
    try:
        plan = lines[lines.index("PLAN") + 1: lines.index("RESULT")]
        result = lines[lines.index("RESULT") + 1: lines.index("VERSION")]
        version = lines[lines.index("VERSION") + 1]
    except (ValueError, IndexError):
        return [f"pgvector: unexpected output from the synthetic HNSW check: {lines[-5:]}"], ""
    out = []
    if not any("drill_vec_hnsw" in p for p in plan):
        out.append("pgvector: the synthetic nearest-neighbour query did not use its HNSW index")
    if result[:1] != ["1234"]:
        out.append(f"pgvector: the synthetic HNSW query returned {result[:1]}, not the probed row 1234")
    return out, version


# ── files ────────────────────────────────────────────────────────────────────────────────────────────────────────


def newest(d: Path, glob: str, skip: str = "-weekly-") -> Optional[Path]:
    c = [p for p in d.glob(glob) if p.is_file() and skip not in p.name and not p.name.endswith(".partial")]
    return max(c, key=lambda p: p.stat().st_mtime) if c else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_extract(tar_path: Path, dest: Path) -> None:
    with tarfile.open(tar_path, "r:*") as t:
        t.extractall(dest, filter="data")


def check_host_archive(path: Path, scratch: Path) -> tuple[list[str], list[str]]:
    findings, notes = [], []
    if path.name.endswith(".age"):
        # The box cannot decrypt it (the identity is off the box, by design). What it CAN prove: the plaintext was
        # checked against its manifest just before encryption (the sidecar says so), and the ciphertext on disk is
        # still byte-identical to what was written (no truncation, no bit rot).
        with open(path, "rb") as fh:
            head = fh.read(64)
        side = path.with_name(path.name + ".sha256")
        try:
            meta = json.loads(side.read_text())
        except (OSError, ValueError):
            meta = None
        if not head.startswith(b"age-encryption.org/v1"):
            findings.append(f"host archive {path.name} is not an age file")
        elif not isinstance(meta, dict):
            findings.append(f"host archive {path.name} has no readable .sha256 sidecar - its integrity is unknown")
        elif meta.get("sha256") != _sha256(path):
            findings.append(f"host archive {path.name} differs from the ciphertext that was written (truncated or "
                            "corrupted)")
        elif not meta.get("verified_plain"):
            findings.append(f"host archive {path.name}: the plaintext was not verified before encryption")
        else:
            notes.append(f"host archive {path.name}: age-encrypted, ciphertext intact, {meta.get('files')} file(s) "
                         "verified before encryption (contents readable only with the owner's identity)")
        return findings, notes
    dest = scratch / "host"
    dest.mkdir()
    try:
        safe_extract(path, dest)
    except (tarfile.TarError, OSError, EOFError) as exc:
        return [f"host archive {path.name} does not open: {exc}"], notes
    try:
        man = json.loads((dest / "MANIFEST.json").read_text())
    except (OSError, ValueError):
        return [f"host archive {path.name} has no readable MANIFEST.json"], notes
    bad = sorted(rel for rel, h in man.get("files", {}).items()
                 if not (dest / rel).is_file() or _sha256(dest / rel) != h)
    if bad:
        findings.append(f"host archive: {len(bad)} file(s) differ from its manifest: {', '.join(bad[:5])}")
    missing = sorted(k for k, v in man.get("labels", {}).items() if v.get("missing"))
    for label in missing:
        findings.append(f"host archive: source {label} was MISSING at backup time"
                        + (" (owner step: install zoe-ha-config-export)" if label == "ha/storage.tar.gz" else ""))
    if man.get("unreadable"):
        findings.append(f"host archive: {len(man['unreadable'])} unreadable file(s) at backup time: "
                        + ", ".join(sorted(man["unreadable"])[:5]))
    ha = dest / "ha" / "storage.tar.gz"
    if ha.is_file():
        try:
            with tarfile.open(ha, "r:gz") as t:
                if not any(n.startswith(".storage/core.config_entries") for n in t.getnames()):
                    findings.append("host archive: the HA export has no .storage/core.config_entries")
        except (tarfile.TarError, OSError, EOFError) as exc:
            findings.append(f"host archive: the HA export does not open: {exc}")
    per_label = {k: v.get("files", 0) for k, v in man.get("labels", {}).items() if not v.get("missing")}
    notes.append(f"host archive {path.name}: {len(man.get('files', {}))} file(s) checksum-verified across "
                 f"{len(per_label)} source(s)")
    return findings, notes


def check_palace(tar_path: Path, scratch: Path, python: str) -> tuple[list[str], list[str]]:
    dest = scratch / "palace"
    dest.mkdir()
    try:
        safe_extract(tar_path, dest)
    except (tarfile.TarError, OSError, EOFError) as exc:
        return [f"palace tarball {tar_path.name} does not open: {exc}"], []
    dbs = list(dest.rglob("chroma.sqlite3"))
    if len(dbs) != 1:
        return [f"palace tarball {tar_path.name} holds {len(dbs)} chroma.sqlite3 file(s), not 1"], []
    if not Path(python).is_file():
        return [f"palace: the chromadb interpreter {python} is missing"], []
    r = subprocess.run([python, "-I", str(PALACE_CHECK), str(dbs[0].parent)], capture_output=True, text=True,
                       timeout=900, env={**os.environ, "ANONYMIZED_TELEMETRY": "False"})
    try:
        res = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return [f"palace check crashed: {r.stderr.strip()[-300:]}"], []
    if not res.get("ok"):
        return [f"palace: {p}" for p in res.get("problems", [])] or ["palace: check failed"], []
    cols = ", ".join(f"{k} {v['count']}" for k, v in sorted(res["collections"].items()))
    return [], [f"palace {tar_path.name}: chromadb {res.get('chromadb')} opened it ({cols}); vector query loaded "
                "HNSW and returned the probed record"]


# ── guards ───────────────────────────────────────────────────────────────────────────────────────────────────────


def unit_active(unit: str) -> bool:
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.stdout.strip() in ("active", "activating", "deactivating")


def preflight(now: datetime, floor_mb: int, wait_s: int = WAIT_BUSY_S,
              active: Callable[[str], bool] = unit_active, sleep: Callable[[float], None] = time.sleep) -> None:
    if ob.in_quiet_hours(now) or active(NIGHT_UNIT):
        raise ob.Refused("the night window owns the box", alert=False)
    deadline = time.monotonic() + wait_s
    while any(active(u) for u in BUSY_UNITS):
        if time.monotonic() >= deadline:
            raise ob.Refused(f"a backup is still running after {wait_s // 60} min")
        sleep(30)
    avail = ob.mem_available_mb()
    if avail < floor_mb:
        raise ob.Refused(f"MemAvailable {avail} MB < floor {floor_mb} MB")


# ── the drill ────────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass
class Result:
    findings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def live_image() -> Optional[str]:
    """The live database container's image NAME (read-only inspect; never an exec)."""
    try:
        r = subprocess.run(["docker", "inspect", "--format", "{{.Config.Image}}", LIVE_DB_CONTAINER],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def helper_image() -> str:
    m = re.search(r'^PINNED_IMAGE="([^"]+)"', HELPER_REPO.read_text(), re.M)
    return m.group(1) if m else ""


def drill_postgres(sb: Sandbox, backup_root: Path, now: datetime, res: Result) -> None:
    pg = backup_root / "postgres"
    plan = []
    for db in DBS:
        dump = newest(pg, f"{db}-*.dump.gz")
        if dump is None:
            res.findings.append(f"{db}: no dump in {pg}")
            continue
        age_h = (now.timestamp() - dump.stat().st_mtime) / 3600
        if age_h > FRESH_HOURS:
            res.findings.append(f"{db}: the newest dump is {age_h:.0f} h old (zoe-backup.service not running?)")
        man_path = ob.manifest_for(dump)
        if man_path is None:
            res.findings.append(f"{db}: {dump.name} has no manifest - row counts cannot be checked")
            manifest = None
        else:
            manifest = json.loads(man_path.read_text())
            if manifest.get("dump_sha256") and manifest["dump_sha256"] != _sha256(dump):
                res.findings.append(f"{db}: {dump.name} does not match the sha256 its manifest recorded")
        try:
            with gzip.open(dump, "rb") as fh:
                while fh.read(1 << 20):
                    pass
        except (OSError, EOFError) as exc:
            res.findings.append(f"{db}: {dump.name} is corrupt ({type(exc).__name__}: {exc})")
            continue
        plan.append((db, dump, manifest))
    sb.down()                               # a sandbox left by a killed run
    try:
        sb.up()
        for db, dump, manifest in plan:
            ok, err = sb.restore(db, dump, manifest)
            if not ok:
                res.findings.append(f"{db}: pg_restore of {dump.name} into the sandbox FAILED: {err}")
                continue
            restored = parse_counts(sb.sql(db, COUNTS_SQL))
            exts = dict(x.split("=", 1) for x in sb.sql(db, EXT_SQL))
            alembic = "".join(sb.sql(db, ALEMBIC_SQL))
            if manifest is not None:
                res.findings += compare_tables(db, manifest, restored)
                res.findings += compare_extensions(db, manifest, exts)
                if manifest.get("alembic_version", "") != alembic:
                    res.findings.append(f"{db}: Alembic {alembic or 'none'} restored, the dump recorded "
                                        f"{manifest.get('alembic_version') or 'none'}")
            res.notes.append(f"{db}: {dump.name} restored, {len(restored)} tables, {sum(restored.values())} rows"
                             + (f", Alembic {alembic}" if alembic else "")
                             + (f", pgvector {exts['vector']}" if "vector" in exts else ""))
            if "vector" in exts:
                f, n = check_existing_hnsw(sb, db)
                res.findings += f
                res.notes += n
        f, version = check_synthetic_hnsw(sb)
        res.findings += f
        if not f:
            res.notes.append(f"pgvector {version}: an HNSW index was built in the sandbox and a nearest-neighbour "
                             "query used it and returned the probed row")
    finally:
        if not sb.down():
            res.findings.append("the sandbox container did not tear down (zoe-restore-drill-pg down)")


def run_drill(p, backup_root: Path, now: datetime, sb: Sandbox, python: str) -> Result:
    res = Result()
    scratch_root = p.home / ".cache" / "zoe-restore-drill"
    scratch_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="drill-", dir=scratch_root))
    os.chmod(scratch, 0o700)
    try:
        try:
            drill_postgres(sb, backup_root, now, res)
        except Exception as exc:  # noqa: BLE001 - every failure must reach the alert
            res.findings.append(f"postgres drill aborted: {type(exc).__name__}: {str(exc)[:300]}")
        pal = newest(backup_root / "mempalace", "mempalace-*.tar.gz")
        if pal is None:
            res.findings.append("palace: no tarball")
        else:
            if (now.timestamp() - pal.stat().st_mtime) / 3600 > FRESH_HOURS:
                res.findings.append(f"palace: the newest tarball {pal.name} is older than {FRESH_HOURS} h")
            f, n = check_palace(pal, scratch, python)
            res.findings += f
            res.notes += n
        hosts = [f for f in (backup_root / "host").glob("host-*.tar.gz*")
                 if f.is_file() and f.name.endswith((".tar.gz", ".tar.gz.age"))]   # not sidecars / .partial
        host = max(hosts, key=lambda f: f.stat().st_mtime) if hosts else None
        if host is None:
            res.findings.append("host archive: none in ~/.zoe-backups/host (zoe_host_snapshot.py not running?)")
        else:
            if (now.timestamp() - host.stat().st_mtime) / 3600 > FRESH_HOURS:
                res.findings.append(f"host archive: the newest, {host.name}, is older than {FRESH_HOURS} h")
            f, n = check_host_archive(host, scratch)
            res.findings += f
            res.notes += n
        pinned, live = helper_image(), live_image()
        if live and pinned and live != pinned:
            res.findings.append(f"the drill's pinned image differs from the live database's ({live}): bump "
                                "PINNED_IMAGE in zoe-restore-drill-pg with docker-compose.yml")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return res


def note_refusal(state: Path, now: datetime) -> bool:
    """Record a refusal; True when the drill has gone STALE_PASS_DAYS without a pass (so even a quiet refusal must
    now be loud: a drill that refuses week after week has to show up)."""
    try:
        prev = json.loads(state.read_text())
        if not isinstance(prev, dict):
            prev = {}
    except (OSError, ValueError):
        prev = {}
    since = prev.get("last_ok") or prev.get("first_refusal") or now.isoformat(timespec="seconds")
    prev["first_refusal"] = prev.get("first_refusal") or now.isoformat(timespec="seconds")
    prev["last_refusal"] = now.isoformat(timespec="seconds")
    try:
        ob._atomic_write(state, json.dumps(prev, indent=1))
        return now - ob._parse_time(since) > timedelta(days=STALE_PASS_DAYS)
    except (OSError, ValueError):
        return False


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ignore-window", action="store_true", help="lab / manual: skip the night-window guard")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--teardown", action="store_true", help="only remove the sandbox (the unit's ExecStopPost)")
    ns = ap.parse_args(argv)
    if ns.teardown:
        return 0 if Sandbox.default().down() else 1
    p = ob.Paths.from_env()
    backup_root = Path(os.environ.get("ZOE_BACKUP_DIR", str(p.home / ".zoe-backups")))
    python = os.environ.get("ZOE_DRILL_PYTHON", str(p.home / ".zoe" / "venvs" / "zoe-data-py312" / "bin" / "python"))
    floor = int(os.environ.get("ZOE_DRILL_MEM_FLOOR_MB", MEM_FLOOR_MB))
    now = datetime.now().astimezone()
    state = p.state_dir / "last_restore_drill.json"
    try:
        if ns.ignore_window:
            avail = ob.mem_available_mb()
            if avail < floor:
                raise ob.Refused(f"MemAvailable {avail} MB < floor {floor} MB")
        else:
            preflight(now, floor)
    except ob.Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        stale = note_refusal(state, now)          # always record, even when this refusal already alerts
        loud = exc.alert or stale
        if loud:
            ob.alert(f"weekly restore drill refused: {exc}" if exc.alert else
                     f"weekly restore drill has not passed in {STALE_PASS_DAYS}+ days (latest refusal: {exc})")
            return EXIT_REFUSED_LOUD
        return EXIT_REFUSED_QUIET
    res = run_drill(p, backup_root, now, Sandbox.default(), python)
    try:
        prev_state = json.loads(state.read_text())
        prev_ok = prev_state.get("last_ok")
        prev_first = prev_state.get("first_refusal")
    except (OSError, ValueError, AttributeError):
        prev_ok = prev_first = None
    if res.findings and prev_ok:
        try:
            if now - ob._parse_time(prev_ok) > timedelta(days=STALE_PASS_DAYS):
                res.findings.append(f"the restore drill has not passed since {prev_ok}")
        except ValueError:
            pass
    stamp = now.isoformat(timespec="seconds")
    ob._atomic_write(state, json.dumps({"time": stamp, "ok": not res.findings,
                                        "last_ok": stamp if not res.findings else prev_ok,
                                        "findings": res.findings, "notes": res.notes,
                                        "first_refusal": None if not res.findings else prev_first}, indent=1))
    for n in res.notes:
        print(f"ok: {n}")
    for f in res.findings:
        print(f"FINDING: {f}")
    if ns.json:
        print(json.dumps({"findings": res.findings, "notes": res.notes}))
    if res.findings:
        ob.alert(f"weekly restore drill FAILED ({len(res.findings)}): " + "; ".join(res.findings)[:900])
        return 1
    print("restore drill: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
