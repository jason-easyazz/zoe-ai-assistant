#!/usr/bin/env python3
"""Hermetic lab for the off-box backup (architecture deep dive A5). Hand-run via ``labs/offbox-backup/run.sh``.

A rest-server container (the image + OPTIONS from ``scripts/setup/offbox-backup/the411/compose.yaml``) on a random
loopback port, the pinned restic + age binaries, a synthetic box (fake HOME, fake dumps, fake corpus, fake .env with
CANARY values) and the REAL ``scripts/maintenance/zoe_offbox.py`` and ``the411/prune.sh``. Synthetic data
only. Every cell prints PASS/FAIL; negative controls break one protection and must go red.
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
import urllib.error
import urllib.request
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from pathlib import Path

LAB = Path(__file__).resolve().parent
REPO = LAB.parents[1]
BIN = LAB / ".bin"
SCRIPT = REPO / "scripts" / "maintenance" / "zoe_offbox.py"
THE411 = REPO / "scripts" / "setup" / "offbox-backup" / "the411"
TAG = "zoe-offbox-lab-" + secrets.token_hex(3)
HID = "hlab" + secrets.token_hex(4)

results: list[tuple[str, bool, str]] = []


def cell(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def run(*args, check=True, **kw) -> subprocess.CompletedProcess:
    r = subprocess.run([str(a) for a in args], capture_output=True, text=True, **kw)
    if check and r.returncode != 0:
        raise RuntimeError(f"{args[0]} {args[1] if len(args) > 1 else ''} rc={r.returncode}: {r.stderr[-600:]}")
    return r


def compose_pins() -> tuple[str, str, str]:
    text = (THE411 / "compose.yaml").read_text()
    rest = re.search(r"image:\s*(restic/rest-server[^\s]+)", text).group(1)
    restic_img = re.search(r"image:\s*(restic/restic[^\s]+)", text).group(1)
    opts = re.search(r"OPTIONS=(.+)", text).group(1).strip()
    return rest, restic_img, opts


def build_box(root: Path) -> dict:
    home = root / "home"
    rnd = lambda n: secrets.token_bytes(n)  # noqa: E731
    files = {
        ".zoe/data/pi-intents.jsonl": b'{"t":"canary household words"}\n',
        ".zoe/memory-exports/export-1.json": b'{"m":"canary memory"}',
        ".zoe/music-assistant/db.sqlite": rnd(4096),
        ".zoe/backups/router-shadow-1/raw.jsonl": b"EXCLUDED backup",
        ".zoe/palace-backups/p.tgz": b"EXCLUDED backup",
        ".zoe/engineering_pipeline_runs.jsonl.backup-1": b"EXCLUDED backup",
        ".zoe/kokoro_cache/x.wav": b"EXCLUDED cache",
        ".zoe/venvs/v/bin/python": b"EXCLUDED venv",
        ".zoe/logs/a.log": b"EXCLUDED log",
        ".zoe/zoe-data-poll.log.2": b"EXCLUDED log",
        ".zoe/omi-recordings/u1/r.wav": b"EXCLUDED pendant audio",
        ".zoe/bakeoff-2026-10/pg/x": b"EXCLUDED lab",
        ".zoe/env/extra.env": b"CANARY_ENV_EXTRA=1\n",
        ".zoe/secrets/vapid_keys.json": b'{"private_key":"CANARY_VAPID"}',
        ".zoe-voice-samples/000001_100.wav": rnd(200_000),
        ".zoe-voice-samples/000002_200.wav": rnd(150_000),
        "assistant/.env": b"POSTGRES_PASSWORD=CANARY_PG\n",
        "assistant/.env.example": b"EXCLUDED=example\n",
        "assistant/.env.bak-20261001": b"EXCLUDED=bak\n",
        "assistant/labs/flue-x/.env": b"CANARY_LAB=1\n",
        "assistant/ssl/zoe.key": b"CANARY KEY",
        "assistant/config/tunnel.json": b'{"CANARY":"tunnel"}',
        "assistant/labs/functiongemma-finetune/runs/gen1/merged/model.safetensors": rnd(300_000),
        "models/functiongemma-router/router-Q8_0.gguf": rnd(250_000),
        # host sources (deep dive 2 S0-8)
        "assistant/homeassistant/configuration.yaml": b"homeassistant:\n  name: canary home\n",
        "assistant/homeassistant/secrets.yaml": b"token: CANARY_HA_SECRET\n",
        "assistant/homeassistant/.storage/auth": b"EXCLUDED: arrives as the helper export",
        "assistant/homeassistant/home-assistant.log": b"EXCLUDED log",
        "assistant/homeassistant/home-assistant_v2.db": b"EXCLUDED recorder",
        "assistant/services/zoe-data/data/music_prefs.json": b'{"canary":"prefs"}',
        "assistant/services/zoe-data/data/router_head_shadow.jsonl": b'{"utt_text":"EXCLUDED raw words"}\n',
        "assistant/services/zoe-data/data/zoe.db": b"EXCLUDED dead sqlite",
        "assistant/services/zoe-data/data/vapid_keys.json": b'{"private":"CANARY_VAPID_LEGACY"}',
        ".zoe-backups/host/ha-storage-20261019-023500.tar.gz": b"ha storage export canary",
        ".zoe/backups/panel/hey_zoe.onnx": rnd(4096),
        "models/models.lock": b'{"canary":"lock"}',
        "bin/zoe-tool.sh": b"#!/bin/sh\nPGPASSWORD=CANARY_BIN\n",
        ".config/systemd/user/zoe-data.service.d/10-lab.conf": b"[Service]\nEnvironment=CANARY_DROPIN=1\n",
    }
    for rel, data in files.items():
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    pg = home / ".zoe-backups" / "postgres"
    pg.mkdir(parents=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    for db in ("zoe", "multica"):
        (pg / f"{db}-{ts}.dump.gz").write_bytes(gzip.compress(b"PGDMP canary " + db.encode() + rnd(5000)))
        (pg / f"{db}-{ts}.manifest.json").write_text(json.dumps({"db": db, "tables": {"public.canary": 1}}))
    etc = root / "etc-systemd" / "user@.service.d"
    etc.mkdir(parents=True)
    (etc / "delegate.conf").write_bytes(b"[Service]\nDelegate=yes\n")
    (root / "crontab.txt").write_bytes(b"*/5 * * * * /home/zoe/bin/zoe-tool.sh\n")
    mp = home / ".zoe-backups" / "mempalace"
    mp.mkdir(parents=True)
    pal = root / "palace"
    pal.mkdir()
    (pal / "chroma.sqlite3").write_bytes(b"SQLite format 3\x00" + rnd(20000))
    with tarfile.open(mp / f"mempalace-{ts}.tar.gz", "w:gz") as t:
        t.add(pal, arcname=".mempalace")
    return {"home": home, "files": files}


def htpasswd_line(image: str, user: str, pw: str) -> str:
    return run("docker", "run", "--rm", "--entrypoint", "htpasswd", image, "-nbB", user, pw).stdout.strip()


def http(method: str, url: str, user: str, pw: str) -> int:
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", "Basic " + b64encode(f"{user}:{pw}".encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, ConnectionError):
        return 0          # not listening yet (the readiness poll retries); never a status a cell accepts


def main() -> int:
    rest_img, restic_img, opts = compose_pins()
    print(f"rest-server {rest_img}\nrestic (the411 prune) {restic_img}\nOPTIONS {opts}")
    if "--append-only" not in opts or "--private-repos" not in opts:
        cell("compose OPTIONS carry --append-only and --private-repos", False, opts)
        return 1
    root = Path(tempfile.mkdtemp(prefix="zoe-offbox-lab-"))
    vol = TAG
    box = build_box(root)
    home: Path = box["home"]
    keys = root / "the411-keys"
    keys.mkdir()
    pw = {k: secrets.token_urlsafe(24) for k in ("owner", "box", "prune", "http", "other_http")}
    (keys / "owner.pass").write_text(pw["owner"])
    (keys / "box.pass").write_text(pw["box"])
    prune_keys = root / "the411-prune-keys"         # what the411's prune container mounts: one file per household
    prune_keys.mkdir()
    (prune_keys / f"{HID}.pass").write_text(pw["prune"])
    owner_dir = root / "owner"                    # OFF the box: the owner's vault stand-in
    owner_dir.mkdir()
    ident = owner_dir / "identity.txt"
    run(BIN / "age-keygen", "-o", ident)
    recipient = run(BIN / "age-keygen", "-y", ident).stdout.strip()
    cfgdir = home / ".config" / "zoe-backup"
    cfgdir.mkdir(parents=True)
    (cfgdir / "restic.pass").write_text(pw["box"])
    (cfgdir / "age-recipients.txt").write_text(recipient + "\n")
    container = f"{TAG}-rs"
    try:
        run("docker", "volume", "create", vol)
        lines = htpasswd_line(rest_img, HID, pw["http"]) + "\n" + htpasswd_line(rest_img, "hother", pw["other_http"])
        run("docker", "run", "--rm", "-i", "-v", f"{vol}:/data", "--entrypoint", "/usr/bin/tee", rest_img,
            "/data/.htpasswd", input=lines + "\n")
        # the411 setup: the OWNER inits with the master password, then adds a box key and a prune key
        dr = ["docker", "run", "--rm", "-v", f"{vol}:/data", "-v", f"{keys}:/keys:ro", restic_img, "-r", f"/data/{HID}"]
        run(*dr, "init", "--password-file", "/keys/owner.pass")
        run(*dr, "key", "add", "--password-file", "/keys/owner.pass", "--new-password-file", "/keys/box.pass")
        run("docker", "run", "--rm", "-v", f"{vol}:/data", "-v", f"{keys}:/keys:ro", "-v", f"{prune_keys}:/pk:ro",
            restic_img, "-r", f"/data/{HID}", "key", "add", "--password-file", "/keys/owner.pass",
            "--new-password-file", f"/pk/{HID}.pass")
        run("docker", "run", "-d", "--name", container, "-p", "127.0.0.1::8000", "-v", f"{vol}:/data",
            "-e", f"OPTIONS={opts}", rest_img)
        port = run("docker", "port", container, "8000").stdout.strip().splitlines()[0].rsplit(":", 1)[1]
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            if http("GET", f"{base}/{HID}/config", HID, pw["http"]) in (200, 404):
                break
            time.sleep(0.2)
        (cfgdir / "offbox.env").write_text(
            f"RESTIC_REPOSITORY=rest:{base}/{HID}/\nRESTIC_REST_USERNAME={HID}\nRESTIC_REST_PASSWORD={pw['http']}\n"
            f"RESTIC_PASSWORD_FILE={cfgdir / 'restic.pass'}\nZOE_BACKUP_AGE_RECIPIENTS_FILE={cfgdir / 'age-recipients.txt'}\n"
            f"RESTIC_BIN={BIN / 'restic'}\nAGE_BIN={BIN / 'age'}\nZOE_BACKUP_LIMIT_UPLOAD_KIB=0\n")
        for f in cfgdir.iterdir():
            os.chmod(f, 0o600)
        env = {**os.environ, "ZOE_OFFBOX_HOME": str(home), "ZOE_OFFBOX_SKIP_PG_GLOBALS": "1",
               "ZOE_OFFBOX_NO_ALERT": "1", "ZOE_OFFBOX_ETC_SYSTEMD": str(root / "etc-systemd"),
               "ZOE_OFFBOX_CRONTAB_FILE": str(root / "crontab.txt")}

        def tool(*a, extra_env=None):
            return run(sys.executable, SCRIPT, *a, check=False, env={**env, **(extra_env or {})})

        def box_restic(*a, password_file=None, user=HID, http_pw=None):
            e = {"PATH": "/usr/bin:/bin", "HOME": str(home), "RESTIC_REPOSITORY": f"rest:{base}/{HID}/",
                 "RESTIC_PASSWORD_FILE": str(password_file or cfgdir / "restic.pass"),
                 "RESTIC_REST_USERNAME": user, "RESTIC_REST_PASSWORD": http_pw or pw["http"],
                 "RESTIC_CACHE_DIR": str(root / "cache")}
            return run(BIN / "restic", *a, check=False, env=e)

        def nsnaps() -> int:
            return len(json.loads(box_restic("snapshots", "--json").stdout or "[]"))

        def owner_restore(tier: str) -> dict[str, bytes]:
            tgt = Path(tempfile.mkdtemp(prefix=f"restore-{tier}-", dir=root))
            r = tool("restore", "--identity", ident, "--tier", tier, "--target", tgt)
            if r.returncode != 0:
                raise RuntimeError(f"owner restore {tier}: {r.stdout[-300:]} {r.stderr[-300:]}")
            plain = tgt / "plain"
            out = {p.relative_to(plain).as_posix(): p.read_bytes() for p in plain.rglob("*") if p.is_file()}
            shutil.rmtree(tgt)
            return out

        # ── B1 first backup: three tagged snapshots
        r = tool("backup", "--ignore-window")
        tags = sorted(t for s in json.loads(box_restic("snapshots", "--json").stdout or "[]") for t in s["tags"])
        cell("B1 backup lands one snapshot per tier", r.returncode == 0 and tags ==
             ["zoe-artefacts", "zoe-household", "zoe-secrets"], f"rc={r.returncode} tags={tags} {r.stderr[-200:]}")

        # ── B2 what reached the411, decrypted with the OFF-box identity, is exactly the policy, byte-identical
        hh = owner_restore("household")
        want_hh = {"zoe/data/pi-intents.jsonl", "zoe/memory-exports/export-1.json", "zoe/music-assistant/db.sqlite",
                   "voice-samples/000001_100.wav", "voice-samples/000002_200.wav", "pg/zoe.dump.gz",
                   "pg/multica.dump.gz", "palace/mempalace.tar.gz",
                   # host sources (deep dive 2 S0-8): no .storage walk, log, recorder, shadow log, dead db, vapid
                   "pg/zoe.manifest.json", "pg/multica.manifest.json", "ha/config/configuration.yaml",
                   "ha/storage.tar.gz", "zoe-data/data/music_prefs.json", "models.lock"}   # panel: artefacts tier (#2142)
        ok_bytes = all(hh[k] == box["files"][".zoe/" + k[4:]] for k in hh if k.startswith("zoe/")) and \
            all(hh[k] == box["files"][".zoe-voice-samples/" + k.split("/", 1)[1]] for k in hh
                if k.startswith("voice-samples/"))
        cell("B2 household tier = policy (no backups/caches/logs/pendant audio/secrets), plaintext identical",
             set(hh) == want_hh and ok_bytes, f"extra={sorted(set(hh) - want_hh)} missing={sorted(want_hh - set(hh))}")
        sec = owner_restore("secrets")
        want_sec = {"checkout/.env", "checkout/labs/flue-x/.env", "checkout/ssl/zoe.key", "checkout/config/tunnel.json",
                    "zoe-env/extra.env", "zoe-secrets/vapid_keys.json",
                    "checkout/services/zoe-data/data/vapid_keys.json",
                    # host config (deep dive 2 S0-8)
                    "ha/secrets.yaml", "systemd/user/zoe-data.service.d/10-lab.conf",
                    "systemd/system/user@.service.d/delegate.conf", "bin/zoe-tool.sh", "crontab"}
        cell("B2 secrets tier = .env*/ssl/config/~/.zoe env+secrets, never .example/.bak", set(sec) == want_sec,
             f"got={sorted(sec)}")

        # ── B2b the ciphertext on the server reveals nothing: no canary in any repo file (restic layer) and the
        #     stage dir on the box holds no plaintext either
        stage = home / ".cache" / "zoe-offbox" / "stage"
        leaked = [p for p in stage.rglob("*") if p.is_file() and b"CANARY" in p.read_bytes()]
        cell("B2b the box's stage holds ciphertext only (no canary)", not leaked, f"{len(leaked)} leaking file(s)")

        # ── B3 the drill: restore newest snapshots, checksum against what was sent
        r = tool("drill", "--force", "--ignore-window")
        cell("B3 drill restores and checksum-matches every tier", r.returncode == 0,
             (r.stdout + r.stderr).strip().splitlines()[-1] if (r.stdout + r.stderr).strip() else "")

        # ── B4 an unchanged box re-encrypts nothing (restic de-duplicates the mirror)
        r = tool("backup", "--ignore-window")
        st = json.loads((home / ".local/state/zoe-offbox/last_backup.json").read_text())["snapshots"]
        cell("B4 second run re-encrypts nothing", r.returncode == 0 and st["household"]["encrypted"] == 0
             and st["secrets"]["encrypted"] == 0, f"household={st['household']} secrets={st['secrets']}")

        # ── B5 a forgotten (deleted) file is absent from the next snapshot
        (home / ".zoe/memory-exports/export-1.json").unlink()
        tool("backup", "--ignore-window")
        hh2 = owner_restore("household")
        cell("B5 deleted source file is gone from the next snapshot", "zoe/memory-exports/export-1.json" not in hh2
             and "zoe/data/pi-intents.jsonl" in hh2)

        # ── N1 wrong repo password
        bad = root / "bad.pass"
        bad.write_text("not-the-password")
        r = box_restic("snapshots", password_file=bad)
        cell("N1 wrong repo password is refused", r.returncode != 0 and "password" in r.stderr.lower(),
             r.stderr.strip()[-120:])

        # ── N2 append-only: the box's own (valid) credentials cannot delete history
        before = nsnaps()
        r = box_restic("forget", "--keep-last", "1", "--prune")
        sid = json.loads(box_restic("snapshots", "--json").stdout)[0]["id"]
        code = http("DELETE", f"{base}/{HID}/snapshots/{sid}", HID, pw["http"])
        cell("N2 append-only refuses forget/prune and raw DELETE from the box", r.returncode != 0 and code == 403
             and nsnaps() == before, f"forget rc={r.returncode} DELETE={code} snaps {before}->{nsnaps()}")

        # ── N3 private repos: another household's credentials cannot read this one
        r = box_restic("snapshots", user="hother", http_pw=pw["other_http"])
        code = http("GET", f"{base}/{HID}/config", "hother", pw["other_http"])
        cell("N3 another household's login cannot reach this repo", r.returncode != 0 and code in (401, 403),
             f"rc={r.returncode} GET={code}")

        # ── N4 wrong rest-server password
        code = http("GET", f"{base}/{HID}/config", HID, "wrong")
        cell("N4 wrong rest-server password is 401", code == 401, f"GET={code}")

        # ── N5 the drill catches a restore that differs from what was sent
        latest_hh = [s for s in json.loads(box_restic("snapshots", "--json", "--tag", "zoe-household").stdout)][-1]
        mf = home / ".local/state/zoe-offbox/manifests" / f"{latest_hh['id']}.json"
        orig = mf.read_text()
        doc = json.loads(orig)
        k0 = sorted(doc["files"])[0]
        doc["files"][k0] = "0" * 64
        mf.write_text(json.dumps(doc))
        r = tool("drill", "--force", "--ignore-window")
        mf.write_text(orig)
        cell("N5 drill goes red when a restored file differs from the manifest", r.returncode == 1,
             f"rc={r.returncode}")

        # ── N6 the age IDENTITY on the box is refused (vault O5: key off the box)
        shutil.copy(ident, cfgdir / "oops.txt")
        os.chmod(cfgdir / "oops.txt", 0o600)
        r = tool("backup", "--ignore-window")
        (cfgdir / "oops.txt").unlink()
        cell("N6 an age identity in the config dir refuses the run", r.returncode == 3 and "IDENTITY" in r.stderr,
             r.stderr.strip()[-120:])

        # ── N7 a different identity cannot decrypt
        other = owner_dir / "other.txt"
        run(BIN / "age-keygen", "-o", other)
        tgt = root / "wrong-id"
        r = tool("restore", "--identity", other, "--tier", "household", "--target", tgt)
        cell("N7 a wrong age identity cannot decrypt the household tier", r.returncode != 0, f"rc={r.returncode}")

        # ── R1 retention: a 40-day-old household snapshot fails the drill (prune stopped) ...
        old = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
        tool("backup", "--ignore-window", extra_env={"ZOE_OFFBOX_SNAPSHOT_TIME": old})
        r = tool("drill", "--force", "--ignore-window")
        cell("R1 drill goes red on a household snapshot older than 35 days", r.returncode == 1 and
             "prune is not running" in r.stdout, f"rc={r.returncode}")
        # ... negative control for WHY the household policy is time-bounded: the owner's literal "7 daily + 4 weekly"
        #     (count-based) would KEEP the 40-day snapshot, because restic counts weeks that HAVE snapshots
        cb = run("docker", "run", "--rm", "-v", f"{vol}:/data", "-v", f"{prune_keys}:/keys:ro", "--network", "none",
                 restic_img, "-r", f"/data/{HID}", "--password-file", f"/keys/{HID}.pass", "forget", "--dry-run",
                 "--json", "--tag", "zoe-household", "--group-by", "host,tags", "--keep-daily", "7",
                 "--keep-weekly", "4", check=False)
        kept_ages = [(datetime.now() - datetime.fromisoformat(s["time"][:19])).days
                     for g in json.loads(cb.stdout or "[]") for s in (g.get("keep") or [])]
        cell("R1b control: a count-based 7 daily + 4 weekly policy would keep the 40-day household snapshot",
             any(a >= 39 for a in kept_ages), f"kept ages={sorted(kept_ages)}")
        # ... the411's prune.sh (shipped file, pinned image, prune key) removes it, keeps the secrets monthly ...
        pr = run("docker", "run", "--rm", "-v", f"{vol}:/data", "-v", f"{prune_keys}:/keys:ro", "-v",
                 f"{THE411 / 'prune.sh'}:/prune.sh:ro", "-e", "DATA_DIRECTORY=/data", "-e", "PRUNE_KEYS=/keys",
                 "--network", "none", "--entrypoint", "/bin/sh", restic_img, "/prune.sh", check=False)
        print(pr.stdout[-800:], pr.stderr[-400:])

        def oldest(tag: str) -> float:
            s = json.loads(box_restic("snapshots", "--json", "--tag", tag).stdout or "[]")
            ts = [datetime.fromisoformat(x["time"][:19]) for x in s]
            return (datetime.now() - min(ts)).days if ts else -1
        cell("R2 the411 prune ages household out (<=35 d) and keeps the secrets monthly",
             oldest("zoe-household") <= 35 and oldest("zoe-secrets") >= 39,
             f"household oldest={oldest('zoe-household')}d secrets oldest={oldest('zoe-secrets')}d")
        r = tool("drill", "--force", "--ignore-window")
        cell("R3 drill green again after the prune", r.returncode == 0, f"rc={r.returncode}")
    except Exception as exc:  # noqa: BLE001
        cell("lab aborted", False, f"{type(exc).__name__}: {exc}")
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        subprocess.run(["docker", "volume", "rm", "-f", vol], capture_output=True)
        shutil.rmtree(root, ignore_errors=True)
        left = subprocess.run(["docker", "ps", "-a", "--filter", f"name={TAG}", "-q"], capture_output=True,
                              text=True).stdout.strip()
        vols = subprocess.run(["docker", "volume", "ls", "-q", "--filter", f"name={TAG}"], capture_output=True,
                              text=True).stdout.strip()
        cell("teardown leaves no container, volume or temp dir", not left and not vols and not root.exists())
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} cells passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
