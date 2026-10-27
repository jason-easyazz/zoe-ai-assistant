"""Deep dive 2 S0-8: the host backup sources, the dump manifest and the weekly restore drill.

Hermetic pieces CI can check on every PR, each with a break-the-fix negative control. The end-to-end proof (a real
dump restored into a real throwaway pg17 + pgvector container, the real palace opened with chromadb 1.5.9, the real
host archive) is the lab, ``labs/restore-drill/run.sh``.
"""
from __future__ import annotations

import gzip
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
MAINT = REPO / "scripts" / "maintenance"
LIBEXEC = REPO / "scripts" / "setup" / "p0-deroot" / "libexec"
SYSTEMD = REPO / "scripts" / "setup" / "systemd"


def _load(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, MAINT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ob = _load("zoe_offbox")
man = _load("pg_dump_manifest")
drill = _load("zoe_restore_drill")
hb = _load("zoe_host_snapshot")


# ── the dump manifest ────────────────────────────────────────────────────────────────────────────────────────────

TOC = """;
; Archive created at 2026-10-19 02:31:00 AWST
215; 1259 16390 TABLE public chat_messages zoe
216; 1259 16391 TABLE public Mixed Case zoe
217; 1259 16392 TABLE public learned_voiceprints zoe
218; 1259 16393 TABLE public events zoe
219; 1259 16394 TABLE public events_2026 zoe
220; 0 0 TABLE ATTACH public events_2026 zoe
3456; 0 16390 TABLE DATA public chat_messages zoe
3457; 0 16391 TABLE DATA public Mixed Case zoe
3458; 0 16394 TABLE DATA public events_2026 zoe
"""
COPY = """--
-- PostgreSQL database dump
COPY public.chat_messages (id, body) FROM stdin;
1\tline one\\nline two\\ttab
2\tsecond
3\tthird
\\.
COPY public."Mixed Case" (id) FROM stdin;
1
\\.
COPY public.events_2026 (id, at) FROM stdin;
1\t2026-01-02
2\t2026-01-03
\\.
"""


def test_manifest_counts_rows_from_the_dump_and_lists_excluded_data():
    m = man.build_manifest(TOC.splitlines(), io.StringIO(COPY), None, {"vector": "0.8.6"}, "0057")
    assert m["tables"] == {"public.chat_messages": 3, "public.Mixed Case": 1, "public.learned_voiceprints": 0,
                           "public.events": 0, "public.events_2026": 2}
    assert m["data_excluded"] == ["public.events", "public.learned_voiceprints"]
    assert m["problems"] == [] and m["extensions"] == {"vector": "0.8.6"} and m["alembic_version"] == "0057"


def test_manifest_flags_a_live_table_the_dump_lacks():
    live = {"public.chat_messages", "public.Mixed Case", "public.learned_voiceprints", "public.events",
            "public.events_2026", "public.users"}
    m = man.build_manifest(TOC.splitlines(), io.StringIO(COPY), live, {}, "")
    assert any("public.users" in p for p in m["problems"])
    clean = man.build_manifest(TOC.splitlines(), io.StringIO(COPY), live - {"public.users"}, {}, "")
    assert clean["problems"] == []       # negative control: the same dump against the matching catalog is clean


def test_a_truncated_copy_block_is_an_error_not_a_short_count():
    with pytest.raises(ValueError):
        man.count_copy_rows(io.StringIO("COPY public.t (a) FROM stdin;\n1\n2\n"))


def test_manifest_never_restores_into_a_database():
    src = (MAINT / "pg_dump_manifest.py").read_text()
    calls = re.findall(r'\["docker", "exec".*?\]', src, re.S)
    assert calls, "the manifest reads through docker exec"
    for c in calls:
        if "pg_restore" in c:
            assert '"-l"' in c or '"-f", "-"' in c, c       # list / SQL to stdout: never a -d connection
        assert '"-d", ns.db' not in c or "psql" in c, c
    assert "createdb" not in src and "dropdb" not in src


# ── the drill's comparisons ──────────────────────────────────────────────────────────────────────────────────────

MAN = {"tables": {"public.a": 2, "public.b": 0}, "live_tables": ["public.a", "public.b"], "problems": [],
       "extensions": {"vector": "0.8.6", "plpgsql": "1.0"}}


def test_compare_tables():
    assert drill.compare_tables("zoe", MAN, {"public.a": 2, "public.b": 0}) == []
    assert any("missing after restore: public.b" in f for f in drill.compare_tables("zoe", MAN, {"public.a": 2}))
    assert any("public.a restored 1 row(s), the dump holds 2" in f
               for f in drill.compare_tables("zoe", MAN, {"public.a": 1, "public.b": 0}))
    assert any("does not list" in f for f in drill.compare_tables("zoe", MAN, {"public.a": 2, "public.b": 0, "x": 1}))
    gone = {**MAN, "live_tables": ["public.a", "public.b", "public.c"]}
    assert any("never in the dump: public.c" in f for f in drill.compare_tables("zoe", gone,
                                                                                 {"public.a": 2, "public.b": 0}))


def test_compare_extensions_catches_the_wrong_pgvector():
    assert drill.compare_extensions("multica", MAN, {"vector": "0.8.6", "plpgsql": "1.0"}) == []
    f = drill.compare_extensions("multica", MAN, {"vector": "0.8.2", "plpgsql": "1.0"})
    assert f and "pgvector 0.8.2" in f[0] and "0.8.6" in f[0]
    assert drill.compare_extensions("multica", MAN, {"plpgsql": "1.0"})


def test_parse_counts_keeps_names_with_pipes_and_spaces():
    assert drill.parse_counts(["public.Mixed Case|3", "public.a|b|7"]) == {"public.Mixed Case": 3, "public.a|b": 7}


class FakeSandbox(drill.Sandbox):
    def __init__(self, lines):
        super().__init__(["fake"])
        self._lines = lines

    def sql(self, db, sql):
        return self._lines


def test_synthetic_hnsw_must_use_the_index_and_return_the_probe():
    good = ["PLAN", "Limit", "  ->  Index Scan using drill_vec_hnsw on drill_vec", "RESULT", "1234", "VERSION", "0.8.6"]
    assert drill.check_synthetic_hnsw(FakeSandbox(good)) == ([], "0.8.6")
    seq = ["PLAN", "Limit", "  ->  Sort", "  ->  Seq Scan on drill_vec", "RESULT", "1234", "VERSION", "0.8.6"]
    assert drill.check_synthetic_hnsw(FakeSandbox(seq))[0]
    wrong = ["PLAN", "  ->  Index Scan using drill_vec_hnsw on drill_vec", "RESULT", "77", "VERSION", "0.8.6"]
    assert drill.check_synthetic_hnsw(FakeSandbox(wrong))[0]


# ── the sandbox never touches the live database ──────────────────────────────────────────────────────────────────


def _helper() -> str:
    return (LIBEXEC / "zoe-restore-drill-pg").read_text()


def test_drill_image_is_the_live_database_image():
    compose = (REPO / "docker-compose.yml").read_text()
    live = re.search(r"zoe-database:\s*\n\s*image:\s*(\S+)", compose).group(1)
    assert drill.helper_image() == live and "@sha256:" in live


def test_sandbox_is_isolated_capped_and_never_the_live_container():
    h = _helper()
    assert re.search(r"^NAME=zoe-restore-drill$", h, re.M)
    code = "\n".join(ln for ln in h.splitlines() if not ln.lstrip().startswith("#"))
    assert "zoe-database" not in code                         # no command ever names the live container
    up = h[h.index("docker run"): h.index("# The image's init")]
    assert "--network \"$(recorded network)\"" in up and "--internal" in h and "--memory \"$MEM\"" in up
    assert "--memory-swap" in up and "--cap-drop ALL" in up and "no-new-privileges" in up
    for forbidden in (" -v ", "--volume", "--mount", " -p ", "--publish", "--privileged", "--network host"):
        assert forbidden not in up, forbidden
    # objects are trusted by the id `up` RECORDED, never by name: a planted container is never exec'd or removed
    assert not re.search(r'docker (exec|rm)[^\n]*"\$NAME"', code)
    assert 'docker rm -f -v "$c"' in code                     # -v: the anonymous data volume goes with it
    assert "did not create; refusing" in code
    src = (MAINT / "zoe_restore_drill.py").read_text()
    assert not re.search(r'"exec"[^\n]*LIVE_DB_CONTAINER|LIVE_DB_CONTAINER[^\n]*"exec"', src)
    assert re.findall(r"LIVE_DB_CONTAINER\b", src).__len__() == 2   # the constant + one read-only inspect


def test_root_helpers_honour_overrides_only_when_not_root():
    h = _helper()
    root_branch = h[h.index('if [ "$(id -u)" = 0 ]; then'): h.index("else", h.index('if [ "$(id -u)" = 0 ]; then'))]
    assert "ZOE_DRILL_PG_IMAGE" not in root_branch and "STATE=/run/zoe-restore-drill" in root_branch
    assert h.index("ZOE_DRILL_PG_IMAGE") > h.index('if [ "$(id -u)" = 0 ]; then')  # only in the non-root branch
    assert 'if os.getuid() != 0 and os.environ.get("ZOE_HA_CONFIG_DIR")' in (LIBEXEC / "zoe-ha-config-export").read_text()


@pytest.mark.parametrize("argv", [[], ["up", "x"], ["down", "x"], ["restore"], ["restore", "postgres", "data"],
                                  ["restore", "zoe"], ["restore", "zoe", "x"], ["restore", "zoe", "data", "x"],
                                  ["inspect"], ["inspect", "x"], ["psql", "template1"], ["exec", "zoe"], ["sql", "zoe"]])
def test_drill_helper_refuses_arguments_outside_its_set(argv):
    r = subprocess.run(["sh", str(LIBEXEC / "zoe-restore-drill-pg"), *argv], capture_output=True, text=True)
    assert r.returncode == 2 and "usage" in r.stderr


@pytest.mark.parametrize("argv", [[], ["all"], ["storage", "x"], ["/etc"]])
def test_ha_helper_refuses_arguments_outside_its_set(argv):
    r = subprocess.run([sys.executable, "-I", str(LIBEXEC / "zoe-ha-config-export"), *argv], capture_output=True,
                       text=True)
    assert r.returncode == 2 and "usage" in r.stderr


def _ha_tree(tmp_path: Path) -> Path:
    ha = tmp_path / "homeassistant"
    (ha / ".storage").mkdir(parents=True)
    (ha / ".storage" / "core.config_entries").write_text("{}")
    (ha / ".cloud").mkdir()
    (ha / ".cloud" / "c.json").write_text("{}")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "s.txt").write_text("CANARY")
    return ha


def _export(ha: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(LIBEXEC / "zoe-ha-config-export"), "storage"],
                          capture_output=True, env={**os.environ, "ZOE_HA_CONFIG_DIR": str(ha)})


def test_ha_helper_exports_storage_and_cloud(tmp_path):
    r = _export(_ha_tree(tmp_path))
    assert r.returncode == 0
    with tarfile.open(fileobj=io.BytesIO(r.stdout), mode="r:gz") as t:
        assert sorted(t.getnames()) == [".cloud/c.json", ".storage/core.config_entries"]


def test_ha_helper_never_follows_a_planted_symlink(tmp_path):
    ha = _ha_tree(tmp_path)
    os.symlink(tmp_path / "outside" / "s.txt", ha / ".storage" / "planted")
    os.link(ha / ".storage" / "core.config_entries", ha / ".storage" / "hardlinked")
    r = _export(ha)
    with tarfile.open(fileobj=io.BytesIO(r.stdout), mode="r:gz") as t:
        names = t.getnames()
    assert r.returncode == 0 and "planted" not in str(names) and "hardlinked" not in str(names)
    assert ".storage/core.config_entries" not in names     # it now has two links: refused too
    os.rename(ha / ".storage", tmp_path / "real")
    os.symlink(tmp_path / "outside", ha / ".storage")
    assert _export(ha).returncode != 0                      # a symlinked .storage
    hl = tmp_path / "hl"
    hl.mkdir()
    os.symlink(ha, hl / "homeassistant")
    assert _export(hl / "homeassistant").returncode != 0    # a symlinked parent


def test_sudoers_grants_the_backup_helpers_exact_argv_only():
    text = (REPO / "scripts/setup/p0-deroot/sudoers.d/60-zoe-deroot").read_text()
    for line in ("zoe-ha-config-export storage", "zoe-restore-drill-pg up", "zoe-restore-drill-pg down",
                 *(f"zoe-restore-drill-pg restore {d} {sec}" for d in ("zoe", "multica")
                   for sec in ("pre-data", "data", "post-data")),
                 "zoe-restore-drill-pg inspect list", "zoe-restore-drill-pg inspect schema",
                 "zoe-restore-drill-pg psql drill"):
        assert f"/usr/local/libexec/zoe-priv/{line}" in text, line
    grant = next(ln for ln in text.splitlines() if ln.startswith("zoe ALL=(root)"))
    assert "ZOE_BACKUP" in grant.split(":", 1)[1]                # the zoe rule carries the backup alias


# ── host sources ─────────────────────────────────────────────────────────────────────────────────────────────────


def _box(tmp_path: Path) -> "ob.Paths":
    home, co = tmp_path / "home", tmp_path / "home" / "assistant"
    files = {
        co / "homeassistant/configuration.yaml": "x", co / "homeassistant/secrets.yaml": "s",
        co / "homeassistant/.storage/auth": "root-only", co / "homeassistant/home-assistant.log": "log",
        co / "homeassistant/home-assistant_v2.db": "rec", co / "homeassistant/tts/a.mp3": "t",
        co / "services/zoe-data/data/music_prefs.json": "{}", co / "services/zoe-data/data/vapid_keys.json": "k",
        co / "services/zoe-data/data/router_head_shadow.jsonl": "words", co / "services/zoe-data/data/zoe.db": "d",
        home / ".zoe/backups/panel/hey_zoe.onnx": "w", home / "bin/t.sh": "b",
        home / ".config/systemd/user/zoe-data.service.d/10.conf": "c", home / "models/models.lock": "{}",
        tmp_path / "etc/user@.service.d/delegate.conf": "d", tmp_path / "etc/serena-bridge.service": "u",
        tmp_path / "etc/snap-core22-1.mount": "snapd's own",
    }
    for p, data in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data)
    (tmp_path / "etc/multi-user.target.wants").mkdir()
    os.symlink(tmp_path / "etc/serena-bridge.service", tmp_path / "etc/multi-user.target.wants/serena-bridge.service")
    return ob.Paths(home=home, checkout=co, config_dir=tmp_path / "c", state_dir=tmp_path / "s",
                    stage_dir=tmp_path / "st", etc_systemd=tmp_path / "etc")


def _rels(srcs) -> set[str]:
    return {r for s in srcs for r, _ in ob.walk_source(s)}


def test_host_sources_ship_the_new_stores_and_keep_the_exclusions(tmp_path):
    p = _box(tmp_path)
    hh, sec = _rels(ob.host_household_sources(p)), _rels(ob.host_secret_sources(p))
    assert hh == {"ha/config/configuration.yaml", "zoe-data/data/music_prefs.json", "models.lock"}
    # the panel backup is on-box only here: off the box it rides in the artefacts tier (#2142), never twice
    assert _rels(ob.host_onbox_only_sources(p)) == {"panel/hey_zoe.onnx"}
    assert "panel" not in {s.label for s in ob.household_sources(p)}
    assert sec == {"ha/secrets.yaml", "bin/t.sh", "systemd/user/zoe-data.service.d/10.conf",
                   "systemd/system/user@.service.d/delegate.conf", "systemd/system/serena-bridge.service"}
    # both host lists reach the off-box tiers
    assert {s.label for s in ob.host_household_sources(p)} <= {s.label for s in ob.household_sources(p)}
    assert {s.label for s in ob.host_secret_sources(p)} <= {s.label for s in ob.secret_sources(p)}


def test_dropping_an_exclusion_is_caught(tmp_path):
    p = _box(tmp_path)
    loose = ob.Source("zoe-data/data", p.checkout / "services/zoe-data/data", ())
    assert "zoe-data/data/router_head_shadow.jsonl" in _rels([loose])     # negative control for the filter above


def test_crontab_comes_from_crontab_l_or_the_lab_file(tmp_path, monkeypatch):
    f = tmp_path / "ct"
    f.write_text("*/5 * * * * x\n")
    monkeypatch.setenv("ZOE_OFFBOX_CRONTAB_FILE", str(f))
    s = ob.crontab_source(tmp_path)
    assert s.label == "crontab" and s.path.read_text() == "*/5 * * * * x\n" and (s.path.stat().st_mode & 0o077) == 0
    ino = s.path.stat().st_ino, s.path.stat().st_ctime_ns
    assert (ob.crontab_source(tmp_path).path.stat().st_ino, s.path.stat().st_ctime_ns) == ino   # unchanged: kept
    f.write_text("*/10 * * * * y\n")
    assert ob.crontab_source(tmp_path).path.read_text() == "*/10 * * * * y\n"              # changed: rewritten
    monkeypatch.setenv("ZOE_OFFBOX_CRONTAB_FILE", str(tmp_path / "absent"))
    assert ob.crontab_source(tmp_path) is None


def test_od3_pendant_transcripts_are_kept_audio_is_not():
    """Owner decision OD3 (2026-10-19): pendant TRANSCRIPTS stay in backups (Postgres omi_* rows, in every dump for
    up to 35 days); pendant AUDIO stays out (~/.zoe/omi-recordings)."""
    bio = re.search(r'^BIOMETRIC_TABLES="([^"]+)"', (MAINT / "postgres-nightly-backup.sh").read_text(), re.M)
    excluded = set(bio.group(1).split())
    migrations = "\n".join(f.read_text() for f in (REPO / "services/zoe-data/alembic/versions").glob("*.py"))
    for kept in ("omi_recordings", "omi_segments", "omi_day_reviews", "omi_day_candidates"):
        assert f"CREATE TABLE IF NOT EXISTS {kept}" in migrations, kept   # a real table, so "not excluded" means kept
        assert kept not in excluded, kept
    assert "omi-recordings" in ob.ZOE_TREE_EXCLUDES


# ── the host archive ─────────────────────────────────────────────────────────────────────────────────────────────


def _archive(tmp_path: Path) -> Path:
    p = _box(tmp_path)
    dest = tmp_path / "host-1.tar.gz"
    hb.build_archive(ob.host_household_sources(p) + ob.host_secret_sources(p), dest)
    return dest


def test_host_archive_verifies_and_reports_missing_sources(tmp_path):
    a = _archive(tmp_path)
    (tmp_path / "sc").mkdir()
    findings, notes = drill.check_host_archive(a, tmp_path / "sc")
    assert any("ha/storage.tar.gz was MISSING" in f for f in findings)   # no helper export in this tree
    assert not [f for f in findings if "MISSING" not in f] and any("checksum-verified" in n for n in notes)


def test_a_tampered_host_archive_goes_red(tmp_path):
    a = _archive(tmp_path)
    x = tmp_path / "x"
    with tarfile.open(a) as t:
        t.extractall(x, filter="data")
    (x / "bin" / "t.sh").write_text("tampered")
    with tarfile.open(a, "w:gz") as t:
        for f in sorted(x.rglob("*")):
            if f.is_file():
                t.add(f, arcname=f.relative_to(x).as_posix())
    (tmp_path / "sc").mkdir()
    findings, _ = drill.check_host_archive(a, tmp_path / "sc")
    assert any("differ from its manifest: bin/t.sh" in f for f in findings)


def test_an_encrypted_host_archive_needs_an_intact_ciphertext_and_a_verified_plaintext(tmp_path):
    a = tmp_path / "host-1.tar.gz.age"
    a.write_bytes(b"age-encryption.org/v1\n-> X25519 x\npayload")
    side = tmp_path / "host-1.tar.gz.age.sha256"
    assert drill.check_host_archive(a, tmp_path)[0]                      # no sidecar: integrity unknown = red
    side.write_text(json.dumps({"sha256": hb._sha256(a), "files": 3, "verified_plain": True}))
    assert drill.check_host_archive(a, tmp_path)[0] == []
    a.write_bytes(b"age-encryption.org/v1\n-> X25519 x\npay")           # truncated after it was written
    assert any("truncated" in f for f in drill.check_host_archive(a, tmp_path)[0])
    a.write_bytes(b"not age")
    assert drill.check_host_archive(a, tmp_path)[0]


def test_snapshot_verifies_the_plaintext_before_encrypting(tmp_path):
    a = _archive(tmp_path)
    with tarfile.open(a) as t:
        man = json.loads(t.extractfile("MANIFEST.json").read())
    assert hb.verify_archive(a, man) == []
    man["files"]["bin/t.sh"] = "0" * 64
    assert hb.verify_archive(a, man) == ["bin/t.sh"]


def test_local_encryption_takes_only_public_recipients(tmp_path, monkeypatch):
    p = ob.Paths(home=tmp_path, checkout=tmp_path, config_dir=tmp_path / "c", state_dir=tmp_path / "s",
                 stage_dir=tmp_path / "st")
    monkeypatch.delenv("ZOE_LOCAL_BACKUP_ENCRYPT", raising=False)
    assert hb.age_recipients(p) is None                         # off by default
    monkeypatch.setenv("ZOE_LOCAL_BACKUP_ENCRYPT", "1")
    with pytest.raises(SystemExit):
        hb.age_recipients(p)                                    # on, but no recipients file
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "age-recipients.txt").write_text("AGE-SECRET-KEY-1XYZ\n")
    with pytest.raises(SystemExit):
        hb.age_recipients(p)
    (tmp_path / "c" / "age-recipients.txt").write_text("age1abc\n")
    assert hb.age_recipients(p) == tmp_path / "c" / "age-recipients.txt"


# ── guards and schedule ──────────────────────────────────────────────────────────────────────────────────────────


def test_preflight_night_window_quiet_busy_backup_alerts_low_memory_alerts(monkeypatch):
    day = datetime(2026, 10, 18, 5, 30)
    with pytest.raises(ob.Refused) as e:
        drill.preflight(day, 2000, active=lambda u: u == drill.NIGHT_UNIT)
    assert e.value.alert is False
    with pytest.raises(ob.Refused) as e:
        drill.preflight(datetime(2026, 10, 18, 3, 0), 2000, active=lambda u: False)
    assert e.value.alert is False
    with pytest.raises(ob.Refused) as e:
        drill.preflight(day, 2000, wait_s=0, active=lambda u: u == "zoe-offbox-backup.service", sleep=lambda s: None)
    assert e.value.alert is True
    monkeypatch.setattr(ob, "mem_available_mb", lambda: 1200)
    with pytest.raises(ob.Refused) as e:
        drill.preflight(day, 2000, active=lambda u: False)
    assert e.value.alert is True and "1200" in str(e.value)
    monkeypatch.setattr(ob, "mem_available_mb", lambda: 4000)
    drill.preflight(day, 2000, active=lambda u: False)           # control: a clear Sunday morning runs


def test_verify_timer_is_sunday_morning_outside_the_night_window():
    t = (SYSTEMD / "zoe-backup-verify.timer").read_text()
    m = re.search(r"^OnCalendar=Sun \*-\*-\* (\d\d):(\d\d):00$", t, re.M)
    assert m and (int(m.group(1)), int(m.group(2))) == (5, 30)
    delay = int(re.search(r"RandomizedDelaySec=(\d+)", t).group(1))
    assert delay <= 600 and not ob.in_quiet_hours(datetime(2026, 10, 18, 5, 30))


def test_units_run_the_tracked_drill_and_host_backup():
    svc = (SYSTEMD / "zoe-backup.service").read_text()
    lines = [ln for ln in svc.splitlines() if ln.startswith("ExecStart=")]
    assert lines[-1].endswith("scripts/maintenance/zoe_host_snapshot.py")            # last: skips nothing before it
    assert "postgres-nightly-backup.sh" in lines[0]
    verify = (SYSTEMD / "zoe-backup-verify.service").read_text()
    assert "zoe_restore_drill.py --teardown" in verify
    body = (MAINT / "zoe-backup-verify.sh").read_text()
    assert "zoe_restore_drill.py" in body and "createdb" not in body and "pg_restore" not in body


# ── RAM floor, container cap, loud refusal (2026-10-27: the 2000 MB floor meant the drill never ran) ─────────────


def _helper_mem_mb() -> int:
    m = re.search(r'^MEM=(\d+)m$', (LIBEXEC / "zoe-restore-drill-pg").read_text(), re.M)
    assert m
    return int(m.group(1))


def test_floor_fits_a_normal_daytime_box_and_leaves_the_voice_stack_headroom():
    cap = _helper_mem_mb()
    # measured peak ~180 MB (container ~175 MB); the floor must be cap + voice headroom, and reachable by day
    assert drill.MEM_FLOOR_MB >= cap + 500                 # never starve the resident voice stack
    assert drill.MEM_FLOOR_MB <= 1300                      # below the 1.4-1.6 GB daytime norm, or it never runs
    assert drill.MEM_FLOOR_MB == 1100                      # the measured value; the old 2000 refused at a normal 1500


def test_floor_refuses_below_and_runs_at_or_above(monkeypatch):
    day = datetime(2026, 10, 25, 5, 30)
    monkeypatch.setattr(ob, "mem_available_mb", lambda: drill.MEM_FLOOR_MB - 1)
    with pytest.raises(ob.Refused):
        drill.preflight(day, drill.MEM_FLOOR_MB, active=lambda u: False)
    monkeypatch.setattr(ob, "mem_available_mb", lambda: drill.MEM_FLOOR_MB)
    drill.preflight(day, drill.MEM_FLOOR_MB, active=lambda u: False)      # control: exactly the floor runs


def test_container_is_hard_capped_with_swap_off_and_small_postgres():
    h = (LIBEXEC / "zoe-restore-drill-pg").read_text()
    assert '--memory "$MEM" --memory-swap "$MEM"' in h      # swap == cap: the drill can never push the box to swap
    sb = int(re.search(r"shared_buffers=(\d+)MB", h).group(1))
    assert sb * 2 <= _helper_mem_mb()                       # shared_buffers well inside the cap
    assert "--pids-limit" in h


def _run_main(monkeypatch, tmp_path, *, avail, now, alerts):
    monkeypatch.setattr(ob, "mem_available_mb", lambda: avail)
    monkeypatch.setattr(ob, "alert", lambda t: alerts.append(t))
    monkeypatch.setattr(drill, "unit_active", lambda u: False)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(drill, "datetime", _DT)
    monkeypatch.setattr(ob.Paths, "from_env", classmethod(lambda cls: ob.Paths(
        home=tmp_path, checkout=tmp_path, config_dir=tmp_path / "c", state_dir=tmp_path / "s",
        stage_dir=tmp_path / "st")))
    (tmp_path / "s").mkdir(exist_ok=True)
    return drill.main([])


def test_low_memory_refusal_is_loud_exit_4_and_alerts(monkeypatch, tmp_path):
    alerts: list[str] = []
    rc = _run_main(monkeypatch, tmp_path, avail=drill.MEM_FLOOR_MB - 100,
                   now=datetime(2026, 10, 25, 5, 30).astimezone(), alerts=alerts)
    assert rc == drill.EXIT_REFUSED_LOUD == 4 and alerts and "refused" in alerts[0]


def test_night_window_refusal_stays_quiet_exit_3_no_alert(monkeypatch, tmp_path):     # the kept brain-window guard
    alerts: list[str] = []
    rc = _run_main(monkeypatch, tmp_path, avail=4000,
                   now=datetime(2026, 10, 25, 3, 0).astimezone(), alerts=alerts)
    assert rc == drill.EXIT_REFUSED_QUIET == 3 and not alerts


def test_quiet_refusals_turn_loud_once_the_drill_has_not_passed_for_15_days(monkeypatch, tmp_path):
    alerts: list[str] = []
    s = tmp_path / "s"
    s.mkdir()
    (s / "last_restore_drill.json").write_text(json.dumps({"last_ok": "2026-09-20T05:30:00+08:00"}))
    rc = _run_main(monkeypatch, tmp_path, avail=4000,
                   now=datetime(2026, 10, 25, 3, 0).astimezone(), alerts=alerts)
    assert rc == drill.EXIT_REFUSED_LOUD and alerts and "has not passed" in alerts[0]
    # control: a recent pass keeps the same quiet refusal quiet
    alerts.clear()
    (s / "last_restore_drill.json").write_text(json.dumps({"last_ok": "2026-10-20T05:30:00+08:00"}))
    rc = _run_main(monkeypatch, tmp_path, avail=4000,
                   now=datetime(2026, 10, 25, 3, 0).astimezone(), alerts=alerts)
    assert rc == drill.EXIT_REFUSED_QUIET and not alerts


def test_verify_script_fails_the_unit_on_loud_refusal_not_on_quiet(tmp_path):
    def run(code: int) -> subprocess.CompletedProcess:
        d = tmp_path / f"m{code}"
        d.mkdir()
        (d / "zoe-backup-verify.sh").write_text((MAINT / "zoe-backup-verify.sh").read_text())
        (d / "zoe_restore_drill.py").write_text(f"import sys; sys.exit({code})\n")
        return subprocess.run(["bash", str(d / "zoe-backup-verify.sh")], capture_output=True, text=True,
                              env={**os.environ, "HOME": str(tmp_path)})
    r4 = run(4)
    assert r4.returncode == 1 and "REFUSED to run" in r4.stderr   # loud refusal -> unit failed, named as a refusal
    assert run(3).returncode == 0                    # quiet (night window) -> not a failure
    assert run(0).returncode == 0                    # control: pass


def test_loud_refusals_are_recorded_so_a_later_quiet_one_still_escalates(monkeypatch, tmp_path):
    alerts: list[str] = []
    _run_main(monkeypatch, tmp_path, avail=10, now=datetime(2026, 9, 20, 5, 30).astimezone(), alerts=alerts)
    st = json.loads((tmp_path / "s" / "last_restore_drill.json").read_text())
    assert st["first_refusal"].startswith("2026-09-20")            # recorded although this refusal alerted
    alerts.clear()
    rc = _run_main(monkeypatch, tmp_path, avail=4000, now=datetime(2026, 10, 25, 3, 0).astimezone(), alerts=alerts)
    assert rc == drill.EXIT_REFUSED_LOUD and alerts                # 35 days after the first refusal: loud


def test_failed_drill_keeps_the_refusal_anchor_and_a_pass_clears_it(monkeypatch, tmp_path):
    s = tmp_path / "s"
    s.mkdir()
    (s / "last_restore_drill.json").write_text(json.dumps({"first_refusal": "2026-09-20T05:30:00+08:00"}))
    monkeypatch.setattr(ob, "alert", lambda t: None)
    monkeypatch.setattr(ob, "mem_available_mb", lambda: 5000)
    monkeypatch.setattr(drill, "unit_active", lambda u: False)
    monkeypatch.setattr(ob.Paths, "from_env", classmethod(lambda cls: ob.Paths(
        home=tmp_path, checkout=tmp_path, config_dir=tmp_path / "c", state_dir=s, stage_dir=tmp_path / "st")))
    monkeypatch.setattr(drill.Sandbox, "default", classmethod(lambda cls: None))
    for findings, kept in ((["x"], True), ([], False)):
        monkeypatch.setattr(drill, "run_drill", lambda *a, _f=findings, **k: drill.Result(findings=list(_f)))
        drill.main(["--ignore-window"])
        st = json.loads((s / "last_restore_drill.json").read_text())
        assert bool(st["first_refusal"]) is kept
        (s / "last_restore_drill.json").write_text(json.dumps({"first_refusal": "2026-09-20T05:30:00+08:00"}))
