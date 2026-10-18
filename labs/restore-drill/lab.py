#!/usr/bin/env python3
"""Hermetic lab for the weekly restore drill and the host sources (deep dive 2 S0-8). Hand-run via ``run.sh``.

A synthetic SOURCE database in its own throwaway container (the pinned live image, ``--network none``, capped),
a synthetic palace built with the real chromadb 1.5.9 venv, a synthetic box (fake HOME, fake checkout, fake HA
config, fake /etc/systemd/system, fake crontab) and the REAL scripts: ``postgres-nightly-backup.sh`` (+
``pg_dump_manifest.py``), ``mempalace-nightly-backup.sh``, ``zoe_host_snapshot.py`` (+ the ``zoe-ha-config-export``
helper, run unprivileged against the fake HA dir), ``zoe_restore_drill.py`` (+ the ``zoe-restore-drill-pg``
helper, the repo copy, exactly what the owner installs). Nothing touches the live database, the live palace or
the live backups. Every cell prints PASS/FAIL; negative controls break one thing and must go red.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

LAB = Path(__file__).resolve().parent
REPO = LAB.parents[1]
MAINT = REPO / "scripts" / "maintenance"
LIBEXEC = REPO / "scripts" / "setup" / "p0-deroot" / "libexec"
VENV_PY = Path.home() / ".zoe" / "venvs" / "zoe-data-py312" / "bin" / "python"
SRC = "zoe-restore-drill-lab-src-" + secrets.token_hex(3)
OLD_PGVECTOR_IMAGE = "pgvector/pgvector:pg17"       # the box's pre-pin image (pgvector 0.8.2): the wrong-version control

results: list[tuple[str, bool, str]] = []


def cell(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def run(*args, check=True, **kw) -> subprocess.CompletedProcess:
    r = subprocess.run([str(a) for a in args], capture_output=True, text=True, **kw)
    if check and r.returncode != 0:
        raise RuntimeError(f"{args[0]} {args[1] if len(args) > 1 else ''} rc={r.returncode}: {r.stderr[-600:]}")
    return r


def pinned_image() -> str:
    m = re.search(r'^PINNED_IMAGE="([^"]+)"', (LIBEXEC / "zoe-restore-drill-pg").read_text(), re.M)
    return m.group(1)


def src_psql(db: str, sql: str) -> str:
    return run("docker", "exec", "-i", SRC, "psql", "-U", "zoe", "-d", db, "-XAt", "-v", "ON_ERROR_STOP=1",
               input=sql).stdout


ZOE_SQL = r"""
CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY);
INSERT INTO alembic_version VALUES ('0057');
CREATE TABLE users (id serial PRIMARY KEY, name text);
INSERT INTO users (name) SELECT 'synthetic user ' || i FROM generate_series(1, 7) i;
CREATE TABLE chat_messages (id serial PRIMARY KEY, user_id int, body text);
INSERT INTO chat_messages (user_id, body) SELECT i % 7 + 1, 'line one' || E'\n' || 'line two' || E'\t' || 'tab \\ backslash ' || i
  FROM generate_series(1, 1234) i;
CREATE TABLE "Mixed Case" (id int);
INSERT INTO "Mixed Case" SELECT generate_series(1, 3);
CREATE TABLE learned_voiceprints (id serial PRIMARY KEY, emb bytea);
INSERT INTO learned_voiceprints (emb) SELECT '\x0102'::bytea FROM generate_series(1, 5);
CREATE TABLE events (id int, at date) PARTITION BY RANGE (at);
CREATE TABLE events_2026 PARTITION OF events FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
INSERT INTO events SELECT i, DATE '2026-01-01' + i FROM generate_series(1, 40) i;
CREATE TABLE empty_table (id int);
"""
MULTICA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE issue (id serial PRIMARY KEY, title text);
INSERT INTO issue (title) SELECT 'issue ' || i FROM generate_series(1, 105) i;
CREATE TABLE embeddings (id int PRIMARY KEY, v vector(8));
INSERT INTO embeddings SELECT i, ARRAY[cos(i), sin(i), cos(2*i), sin(3*i), cos(5*i), sin(7*i), cos(11*i), sin(13*i)]::vector
  FROM generate_series(1, 500) i;
CREATE INDEX embeddings_v_hnsw ON embeddings USING hnsw (v vector_cosine_ops);
"""


def build_box(root: Path) -> dict:
    home, co = root / "home", root / "home" / "assistant"
    files = {
        co / "homeassistant/configuration.yaml": b"homeassistant:\n  name: Lab\n",
        co / "homeassistant/automations.yaml": b"[]\n",
        co / "homeassistant/custom_components/x/__init__.py": b"# lab\n",
        co / "homeassistant/secrets.yaml": b"lab_token: CANARY_HA_SECRET\n",
        co / "homeassistant/.storage/core.config_entries": b'{"data":{"entries":[]}}',
        co / "homeassistant/.storage/auth": b'{"data":"CANARY_HA_AUTH"}',
        co / "homeassistant/.cloud/x.json": b"{}",
        co / "homeassistant/home-assistant.log": b"EXCLUDED log",
        co / "homeassistant/home-assistant_v2.db": b"EXCLUDED recorder",
        co / "homeassistant/tts/a.mp3": b"EXCLUDED tts",
        co / "services/zoe-data/data/music_prefs.json": b'{"a":1}',
        co / "services/zoe-data/data/music_discovery/u/p.json": b'{"p":1}',
        co / "services/zoe-data/data/router_head_shadow.jsonl": b'{"utt_text":"EXCLUDED raw words"}\n',
        co / "services/zoe-data/data/vapid_keys.json": b'{"k":"CANARY_VAPID"}',
        co / "services/zoe-data/data/zoe.db": b"EXCLUDED dead sqlite",
        home / ".zoe/backups/panel/hey_zoe.onnx": os.urandom(4096),
        home / "bin/zoe-tool.sh": b"#!/bin/sh\necho lab\n",
        home / ".config/systemd/user/zoe-data.service.d/10-lab.conf": b"[Service]\nMemorySwapMax=0\n",
        home / ".config/systemd/user/zoe-lab.service": b"[Service]\nExecStart=/bin/true\n",
        home / "models/models.lock": b'{"models":[{"path":"x.gguf","sha256":"00","url":"https://example.invalid"}]}',
        root / "etc-systemd/user@.service.d/delegate.conf": b"[Service]\nDelegate=yes\n",
        root / "crontab.txt": b"*/5 * * * * /home/lab/bin/zoe-tool.sh\n",
        root / "outside/secret.txt": b"CANARY_OUTSIDE_HA",
    }
    for p, data in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (home / ".zoe-backups").mkdir(parents=True, exist_ok=True)
    (root / "state").mkdir()
    env_file = root / "pg.env"
    env_file.write_text(f"POSTGRES_PASSWORD={secrets.token_urlsafe(16)}\n")
    os.chmod(env_file, 0o600)
    return {"home": home, "co": co, "env_file": env_file}


def main() -> int:
    image = pinned_image()
    root = Path(tempfile.mkdtemp(prefix="zoe-restore-drill-lab-"))
    os.chmod(root, 0o700)
    box = build_box(root)
    home, co = box["home"], box["co"]
    backups = home / ".zoe-backups"
    base_env = {**os.environ, "ZOE_OFFBOX_HOME": str(home), "ZOE_OFFBOX_CHECKOUT": str(co),
                "ZOE_OFFBOX_STATE_DIR": str(root / "state"), "ZOE_OFFBOX_ETC_SYSTEMD": str(root / "etc-systemd"),
                "ZOE_OFFBOX_CRONTAB_FILE": str(root / "crontab.txt"), "ZOE_OFFBOX_NO_ALERT": "1",
                "ZOE_BACKUP_DIR": str(backups), "ZOE_DRILL_PYTHON": str(VENV_PY),
                # run.sh already required 2000 MB before anything started; the drill's own floor is relaxed so
                # the lab's chromadb process cannot flake a cell (N7 proves the floor with an impossible value)
                "ZOE_DRILL_MEM_FLOOR_MB": "1500"}
    base_env.pop("ZOE_DRILL_HELPER", None)
    base_env.pop("ZOE_DRILL_PG_IMAGE", None)

    def drill(**extra) -> subprocess.CompletedProcess:
        return run(sys.executable, MAINT / "zoe_restore_drill.py", "--ignore-window", check=False,
                   env={**base_env, **extra}, timeout=1800)

    def tail(r) -> str:
        lines = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.startswith("FINDING") or "refused" in ln]
        return " | ".join(lines)[:400]

    live_dbs_before = run("docker", "exec", "zoe-database", "psql", "-U", "zoe", "-XAtc",
                          "SELECT string_agg(datname, ',' ORDER BY datname) FROM pg_database", check=False).stdout
    snapshot = root / "pristine-backups"
    try:
        # ── the source database (synthetic), dumped by the REAL nightly script
        run("docker", "run", "-d", "--name", SRC, "--network", "none", "--memory", "384m", "--memory-swap", "384m",
            "-e", "POSTGRES_USER=zoe", "-e", "POSTGRES_DB=zoe", "-e", "POSTGRES_HOST_AUTH_METHOD=trust", image)
        for _ in range(120):
            if run("docker", "exec", SRC, "pg_isready", "-q", "-h", "127.0.0.1", "-U", "zoe", check=False).returncode == 0:
                break
            time.sleep(1)
        src_psql("zoe", "CREATE DATABASE multica;")
        src_psql("zoe", ZOE_SQL)
        src_psql("multica", MULTICA_SQL)
        r = run("bash", MAINT / "postgres-nightly-backup.sh", check=False,
                env={**base_env, "ZOE_POSTGRES_CONTAINER": SRC, "ZOE_ENV_FILE": str(box["env_file"])})
        print("      " + (r.stdout + r.stderr).strip().replace("\n", "\n      "))
        man = {db: json.loads(next((backups / "postgres").glob(f"{db}-*.manifest.json")).read_text())
               for db in ("zoe", "multica")}
        z, m = man["zoe"], man["multica"]
        ok = (r.returncode == 0 and z["tables"].get("public.chat_messages") == 1234
              and z["tables"].get("public.Mixed Case") == 3 and z["tables"].get("public.events_2026") == 40
              and z["tables"].get("public.events") == 0 and "public.learned_voiceprints" in z["data_excluded"]
              and z["tables"].get("public.learned_voiceprints") == 0 and z["alembic_version"] == "0057"
              and not z["problems"] and m["extensions"].get("vector") and m["tables"].get("public.embeddings") == 500
              and set(z["live_tables"]) == set(z["tables"]))
        cell("S1 the nightly script writes a manifest counted FROM the dump (multi-line rows, quoted names, "
             "partitions, biometric data excluded, pgvector, Alembic)", ok,
             f"rc={r.returncode} zoe={z['tables']} vector={m['extensions'].get('vector')} problems={z['problems']}")
        # a dump that LACKS a table (for N2), made the same way the nightly script makes one
        missing_dump = root / "zoe-missing-table.dump.gz"
        p = subprocess.run(["docker", "exec", SRC, "pg_dump", "-U", "zoe", "-Fc",
                            "--exclude-table=public.chat_messages", "--exclude-table-data=learned_voiceprints", "zoe"], capture_output=True)
        missing_dump.write_bytes(gzip.compress(p.stdout))
        run("docker", "rm", "-f", SRC)

        # ── the palace (real chromadb 1.5.9) and its REAL nightly tarball
        palace = home / ".mempalace"
        run(VENV_PY, "-I", LAB / "make_palace.py", palace)
        r = run("bash", MAINT / "mempalace-nightly-backup.sh", check=False,
                env={**base_env, "MEMPALACE_DATA_DIR": str(palace)})
        cell("S2 the real MemPalace backup tars the synthetic palace", r.returncode == 0, r.stderr[-200:])

        # ── the host archive, with the REAL HA helper (unprivileged, against the fake HA dir)
        ha_env = {"ZOE_HA_EXPORT_CMD": f"{sys.executable} {LIBEXEC / 'zoe-ha-config-export'}",
                  "ZOE_HA_CONFIG_DIR": str(co / "homeassistant")}
        r = run(sys.executable, MAINT / "zoe_host_snapshot.py", check=False, env={**base_env, **ha_env})
        host = sorted((backups / "host").glob("host-*.tar.gz"))[-1]
        with tarfile.open(host) as t:
            names = set(t.getnames())
            hm = json.loads(t.extractfile("MANIFEST.json").read())
        want = {"ha/config/configuration.yaml", "ha/config/automations.yaml", "ha/config/custom_components/x/__init__.py",
                "ha/storage.tar.gz", "ha/secrets.yaml", "zoe-data/data/music_prefs.json",
                "zoe-data/data/music_discovery/u/p.json", "panel/hey_zoe.onnx", "models.lock", "bin/zoe-tool.sh",
                "systemd/user/zoe-data.service.d/10-lab.conf", "systemd/user/zoe-lab.service",
                "systemd/system/user@.service.d/delegate.conf", "crontab", "MANIFEST.json"}
        excluded_leak = [n for n in names if any(x in n for x in ("router_head_shadow", "vapid", "zoe.db", ".log",
                                                                 "v2.db", "tts/"))]
        cell("S3 host archive = every new source, none of the exclusions", r.returncode == 0 and names == want
             and not excluded_leak and not any(v["missing"] for v in hm["labels"].values()),
             f"rc={r.returncode} extra={sorted(names - want)} missing={sorted(want - names)} leak={excluded_leak} "
             f"{r.stderr[-200:]}")
        ha_tar = next((backups / "host").glob("ha-storage-*.tar.gz"))
        with tarfile.open(ha_tar) as t:
            ha_names = set(t.getnames())
        cell("S4 the HA helper exports .storage + .cloud (0600 file)",
             ha_names == {".storage/core.config_entries", ".storage/auth", ".cloud/x.json"}
             and (ha_tar.stat().st_mode & 0o077) == 0, f"{sorted(ha_names)} mode={oct(ha_tar.stat().st_mode)}")

        shutil.copytree(backups, snapshot)

        def reset() -> None:
            shutil.rmtree(backups)
            shutil.copytree(snapshot, backups)

        # ── D1 the drill on good backups
        r = drill()
        out = r.stdout + r.stderr
        ok = (r.returncode == 0 and "HNSW index embeddings_v_hnsw valid, used" in out and "an HNSW index was built"
              in out and "vector query loaded HNSW" in out and "checksum-verified" in out)
        cell("D1 drill green: every table count, pgvector, Alembic, existing + synthetic HNSW, palace vector "
             "query, host archive", ok, tail(r) or out.strip().splitlines()[-1])
        print("\n".join("      " + ln for ln in out.splitlines() if ln.startswith("ok:")))

        # ── N1 a corrupted dump (bytes flipped on disk)
        reset()
        dump = next((backups / "postgres").glob("zoe-*.dump.gz"))
        b = bytearray(dump.read_bytes())
        for i in range(len(b) // 2, len(b) // 2 + 64):
            b[i] ^= 0xFF
        dump.write_bytes(bytes(b))
        r = drill()
        cell("N1 a corrupted dump goes red", r.returncode == 1 and "zoe:" in tail(r), tail(r))

        # ── N1b corruption INSIDE the archive (gzip and sha256 both consistent): the restore itself must catch it
        reset()
        dump = next((backups / "postgres").glob("zoe-*.dump.gz"))
        raw = bytearray(gzip.decompress(dump.read_bytes()))
        k = int(len(raw) * 0.7)                    # past the TOC, inside the (zlib-compressed) table data blocks
        raw[k: k + 512] = os.urandom(512)
        dump.write_bytes(gzip.compress(bytes(raw)))
        mp = dump.with_name(dump.name.replace(".dump.gz", ".manifest.json"))
        mj = json.loads(mp.read_text())
        mj["dump_sha256"] = __import__("hashlib").sha256(dump.read_bytes()).hexdigest()
        mp.write_text(json.dumps(mj))
        r = drill()
        cell("N1b a dump corrupted before its manifest was written still goes red (restore or counts)",
             r.returncode == 1 and "zoe:" in tail(r), tail(r))

        # ── N2 a dump missing a table the manifest (and the live catalog) list
        reset()
        dump = next((backups / "postgres").glob("zoe-*.dump.gz"))
        shutil.copyfile(missing_dump, dump)
        mp = dump.with_name(dump.name.replace(".dump.gz", ".manifest.json"))
        mj = json.loads(mp.read_text())
        mj["dump_sha256"] = __import__("hashlib").sha256(dump.read_bytes()).hexdigest()
        mp.write_text(json.dumps(mj))
        r = drill()
        cell("N2 a dump missing a table goes red", r.returncode == 1 and "chat_messages" in tail(r), tail(r))

        # ── N3 the wrong pgvector in the restore image
        reset()
        have_old = run("docker", "image", "inspect", OLD_PGVECTOR_IMAGE, check=False).returncode == 0
        if have_old:
            r = drill(ZOE_DRILL_PG_IMAGE=OLD_PGVECTOR_IMAGE)
            cell("N3 a restore image with another pgvector goes red", r.returncode == 1 and "pgvector" in tail(r),
                 tail(r))
        else:
            cell("N3 a restore image with another pgvector goes red", False, f"{OLD_PGVECTOR_IMAGE} not present")

        # ── N4 a palace tarball whose HNSW segment files are gone
        reset()
        ptar = next((backups / "mempalace").glob("mempalace-*.tar.gz"))
        ex = root / "pal-x"
        ex.mkdir()
        with tarfile.open(ptar) as t:
            t.extractall(ex, filter="data")
        segs = [d for d in (ex / ".mempalace").iterdir() if d.is_dir()]
        for d in segs:
            shutil.rmtree(d)
        with tarfile.open(ptar, "w:gz") as t:
            t.add(ex / ".mempalace", arcname=".mempalace")
        r = drill()
        cell(f"N4 a palace without its HNSW segment files ({len(segs)} removed) goes red",
             r.returncode == 1 and "palace" in tail(r), tail(r))

        # ── N5 a host-archive file that differs from its manifest
        reset()
        host = sorted((backups / "host").glob("host-*.tar.gz"))[-1]
        hx = root / "host-x"
        hx.mkdir()
        with tarfile.open(host) as t:
            t.extractall(hx, filter="data")
        (hx / "bin" / "zoe-tool.sh").write_bytes(b"#!/bin/sh\necho tampered\n")
        with tarfile.open(host, "w:gz") as t:
            for f in sorted(hx.rglob("*")):
                if f.is_file():
                    t.add(f, arcname=f.relative_to(hx).as_posix())
        r = drill()
        cell("N5 a host-archive file that differs from its manifest goes red",
             r.returncode == 1 and "differ from its manifest" in tail(r), tail(r))

        # ── N6 the HA helper never follows a symlink zoe planted
        reset()
        ha = co / "homeassistant"
        shutil.move(str(ha / ".storage"), str(root / "real-storage"))
        os.symlink(root / "outside", ha / ".storage")
        r6 = subprocess.run([sys.executable, LIBEXEC / "zoe-ha-config-export", "storage"], capture_output=True,
                            env={**base_env, "ZOE_HA_CONFIG_DIR": str(ha)})
        os.unlink(ha / ".storage")
        shutil.move(str(root / "real-storage"), str(ha / ".storage"))
        os.symlink(root / "outside" / "secret.txt", ha / ".storage" / "planted")
        r6b = subprocess.run([sys.executable, LIBEXEC / "zoe-ha-config-export", "storage"], capture_output=True,
                             env={**base_env, "ZOE_HA_CONFIG_DIR": str(ha)})
        os.unlink(ha / ".storage" / "planted")
        hl = root / "hl-ha"
        hl.mkdir()
        os.symlink(ha, hl / "homeassistant")
        r6c = subprocess.run([sys.executable, LIBEXEC / "zoe-ha-config-export", "storage"], capture_output=True,
                             env={**base_env, "ZOE_HA_CONFIG_DIR": str(hl / "homeassistant")})
        leaked = b"CANARY_OUTSIDE_HA" in gzip.decompress(r6b.stdout) if r6b.returncode == 0 else True
        cell("N6 HA helper: a symlinked .storage, a symlinked file in it, a symlinked parent are never followed",
             r6.returncode != 0 and b"CANARY" not in r6.stdout and r6b.returncode == 0 and not leaked
             and r6c.returncode != 0, f"dir rc={r6.returncode} file leaked={leaked} parent rc={r6c.returncode}")

        # ── N7 the memory floor refuses before any container exists
        r = drill(ZOE_DRILL_MEM_FLOOR_MB="999999")
        made = run("docker", "ps", "-a", "--filter", "name=zoe-restore-drill", "--format", "{{.Names}}").stdout.strip()
        cell("N7 MemAvailable under the floor refuses (exit 3), no container created",
             r.returncode == 3 and not [n for n in made.split() if n == "zoe-restore-drill"], f"rc={r.returncode}")

        # ── N8 the helper refuses arguments outside its fixed set
        bad = [["restore", "postgres"], ["psql", "template1"], ["up", "x"], ["restore"], ["exec", "zoe"]]
        rcs = [subprocess.run(["sh", LIBEXEC / "zoe-restore-drill-pg", *a], capture_output=True).returncode
               for a in bad]
        cell("N8 the drill helper refuses every argument outside its set", rcs == [2] * len(bad), str(rcs))
    finally:
        run("docker", "rm", "-f", SRC, check=False)
        run("sh", LIBEXEC / "zoe-restore-drill-pg", "down", check=False)
        live_dbs_after = run("docker", "exec", "zoe-database", "psql", "-U", "zoe", "-XAtc",
                             "SELECT string_agg(datname, ',' ORDER BY datname) FROM pg_database", check=False).stdout
        left = run("docker", "ps", "-a", "--filter", "name=zoe-restore-drill", "--format", "{{.Names}}",
                   check=False).stdout.strip()
        nets = run("docker", "network", "ls", "--filter", "name=zoe-restore-drill", "--format", "{{.Name}}",
                   check=False).stdout.strip()
        scratch = list((home / ".cache" / "zoe-restore-drill").glob("drill-*")) if home.exists() else []
        shutil.rmtree(root, ignore_errors=True)
        cell("T1 teardown: no drill/lab container or network left, no drill scratch dir, the live database's "
             "database list unchanged", not left and not nets and not scratch and live_dbs_before == live_dbs_after
             and not root.exists(), f"containers={left!r} networks={nets!r} scratch={len(scratch)} "
             f"live dbs unchanged={live_dbs_before == live_dbs_after}")
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} cells PASS" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
