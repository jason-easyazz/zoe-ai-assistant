#!/usr/bin/env python3
"""Off-box encrypted backup of the box to a household repo on the411 (architecture deep dive A5, 2026-10-18).

restic writes to a rest-server in APPEND-ONLY mode (the box can add snapshots, never delete them); retention is
applied on the411 by its own prune job (``scripts/setup/offbox-backup/the411/prune.sh``). Runbook, owner steps and
the threat model: ``docs/knowledge/offbox-backup.md``.

TWO KEYS, AND WHY. restic is symmetric: the box must hold a repo key to WRITE. So the box holds a revocable device
key (``restic key add``), never the owner's master password, and everything that holds household words or secrets is
ALSO encrypted with ``age`` to a public recipient whose identity (private key) lives only in the owner's vault
(vault decision O5). A stolen box key or a compromised the411 therefore yields ciphertext for those tiers. The box
refuses to run if an age identity is found in its config directory.

Tiers (one restic snapshot each, tagged ``zoe-<tier>``):

* ``household`` - age-encrypted: the newest on-box Postgres dumps (biometric table data already excluded by
  ``postgres-nightly-backup.sh``), ``pg_dumpall --globals-only``, the newest MemPalace tarball, ``~/.zoe`` minus
  caches, logs, backups, pendant audio and secrets, and the voice regression corpus. Retention one per day for 7
  days and one per week for 35 days, NO monthly: gone within 35 days, the same bound as the on-box backups
  (``docs/knowledge/forget-reach.md``: forgotten means forever). See RETENTION for why it is time-bounded.
* ``secrets`` - age-encrypted: the live checkout's ``.env*`` files (never ``.example`` / ``.bak*``), ``ssl/``,
  ``config/``, ``~/.zoe/env`` and ``~/.zoe/secrets`` (the VAPID key's new home). Retention 7 / 4 / 6 monthly.
* ``artefacts`` - restic only: the router's lineage checkpoint and stage-2 GGUF (trained on the committed corpus;
  no household words). Retention 7 / 4 / 6 monthly.

age ciphertext is randomised, so a naive nightly re-encryption would defeat restic's de-duplication. The age
MIRROR (``~/.cache/zoe-offbox/stage/<tier>``) keeps one ciphertext file per source file, re-encrypting only what
changed (size + mtime) and deleting what disappeared, so an unchanged corpus costs nothing and a deleted (forgotten)
file is absent from the next snapshot. Mirror names are keyed HMACs of the path; the path map is itself
age-encrypted (``index.json.age``). The stage holds ciphertext only.

Commands (all print no secret; read-only towards the box's data):

    zoe_offbox.py check            # config + tools + guards, no network
    zoe_offbox.py backup           # nightly (zoe-offbox-backup.timer)
    zoe_offbox.py drill [--force]  # monthly restore + checksum + retention check (zoe-backup-verify)
    zoe_offbox.py restore --identity FILE --tier T --target DIR [--snapshot ID]   # OWNER, disaster recovery

Exit: 0 ok, 1 failure (alerted), 2 bad invocation / not configured, 3 refused (quiet hours, low memory).
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import hmac
import importlib.util
import json
import os
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

HOME = Path(os.environ.get("HOME", str(Path.home())))
REPO_ROOT = Path(__file__).resolve().parents[2]

# ── Retention: the ONE source of truth. the411's prune.sh carries the same flags; a ci_safe test pins them equal. ──
# household is TIME-bounded, not count-bounded: restic's `--keep-weekly 4` keeps the last 4 weeks THAT HAVE
# snapshots, so after a gap a 7d+4w policy keeps a snapshot of any age (measured in the lab, cell R1b). The
# `--keep-within-*` windows are relative to the NEWEST snapshot, which is right for disaster recovery: a dead box's
# last weeks are kept as its restore point instead of ageing out under it. The window is 34 days, not 35: the411
# prunes once a day, so a snapshot can sit up to one day past the window before the next prune - 34 + 1 keeps the
# HARD bound at 35 days (forgotten means forever), and the drill alerts on anything past 35, with no slack.
RETENTION: dict[str, dict[str, object]] = {
    "household": {"within-daily": "7d", "within-weekly": "34d"},
    "secrets": {"daily": 7, "weekly": 4, "monthly": 6},
    "artefacts": {"daily": 7, "weekly": 4, "monthly": 6},
}
HOUSEHOLD_MAX_AGE_DAYS = 35          # the forget-reach bound for anything holding household words: never exceeded
PRUNE_CADENCE_DAYS = 1               # the411 prunes daily; window + cadence must stay <= HOUSEHOLD_MAX_AGE_DAYS


def forget_flags(tier: str) -> list[str]:
    """The `restic forget` policy flags for a tier (the411's prune.sh must carry exactly these)."""
    out: list[str] = []
    for k, v in RETENTION[tier].items():
        out += [f"--keep-{k}", str(v)]
    return out
NEWEST_MAX_AGE_HOURS = 50            # a nightly backup that has not landed in two nights is an alert
SOURCE_DUMP_MAX_AGE_HOURS = 26       # the on-box dump we ship must be from last night
DRILL_INTERVAL_DAYS = 28
QUIET_START, QUIET_END = (1, 35), (4, 55)   # the night window (12B) owns the box; never run inside it
MEM_FLOOR_MB = 1500
HOST_TAG = "zoe"                     # stable restic --host, so snapshot groups survive a hostname change

# ~/.zoe: components matched against EVERY path component below the root (fnmatch). Each line says why.
ZOE_TREE_EXCLUDES = (
    "*backup*",                # backups, palace-backups, deploy-backups, env-backups, *.backup-*: backups of backups
    "*cache*", "venvs",        # rebuildable
    "bakeoff-*", "chroma-migration-rehearsal",   # lab artefacts (the bake-off pgdata is root-owned)
    "logs", "*.log", "*.log.[0-9]*",              # text logs age out on the box (forget-reach: app_logs)
    "omi-recordings",          # pendant audio: claim-time retention + third-party voices; never off-box
    "compaction-salvage-*",    # transient emergency spill files
    "env", "secrets",          # the secrets tier owns these
)
# ssl/ and config/ (SECRET_TREES, below) are walked RECURSIVELY as Sources: the relay's household CA lives in ssl/relay/
SECRET_GLOBS = (".env", ".env.*", "services/*/.env*", "labs/*/.env*", "modules/*/.env*",
                "services/zoe-data/data/vapid_keys.json")
SECRET_EXCLUDES = ("*.example", "*.bak*", "*.orig", "*.sample")
SECRET_TREES = ("ssl", "config")
AGE_IDENTITY_MARKER = b"AGE-SECRET-KEY-"


# ── paths ────────────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass
class Paths:
    home: Path
    checkout: Path
    config_dir: Path
    state_dir: Path
    stage_dir: Path

    @classmethod
    def from_env(cls) -> "Paths":
        home = Path(os.environ.get("ZOE_OFFBOX_HOME", str(HOME)))
        return cls(
            home=home,
            checkout=Path(os.environ.get("ZOE_OFFBOX_CHECKOUT", str(home / "assistant"))),
            config_dir=Path(os.environ.get("ZOE_OFFBOX_CONFIG_DIR", str(home / ".config" / "zoe-backup"))),
            state_dir=Path(os.environ.get("ZOE_OFFBOX_STATE_DIR", str(home / ".local" / "state" / "zoe-offbox"))),
            stage_dir=Path(os.environ.get("ZOE_OFFBOX_STAGE_DIR", str(home / ".cache" / "zoe-offbox" / "stage"))),
        )


@dataclass
class Source:
    """One mirrored input: a directory tree, or a single file under a fixed mirror label."""
    label: str
    path: Optional[Path]
    excludes: tuple[str, ...] = ()
    required: bool = False
    max_age_hours: Optional[float] = None


def newest(pattern_dir: Path, glob: str, skip: str = "-weekly-") -> Optional[Path]:
    cands = [p for p in pattern_dir.glob(glob) if p.is_file() and skip not in p.name]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def household_sources(p: Paths) -> list[Source]:
    bk = p.home / ".zoe-backups"
    return [
        Source("pg/zoe.dump.gz", newest(bk / "postgres", "zoe-*.dump.gz"), required=True,
               max_age_hours=SOURCE_DUMP_MAX_AGE_HOURS),
        Source("pg/multica.dump.gz", newest(bk / "postgres", "multica-*.dump.gz"), required=False,
               max_age_hours=SOURCE_DUMP_MAX_AGE_HOURS),
        Source("palace/mempalace.tar.gz", newest(bk / "mempalace", "mempalace-*.tar.gz"), required=True,
               max_age_hours=SOURCE_DUMP_MAX_AGE_HOURS),
        Source("zoe", p.home / ".zoe", ZOE_TREE_EXCLUDES, required=True),
        Source("voice-samples", p.home / ".zoe-voice-samples"),
    ]


def _secret_glob_files(p: Paths) -> list[tuple[str, Path]]:
    out: dict[str, Path] = {}
    for g in SECRET_GLOBS:
        for f in sorted(p.checkout.glob(g)):
            if f.is_file() and not f.is_symlink() and not any(fnmatch.fnmatch(f.name, x) for x in SECRET_EXCLUDES):
                out["checkout/" + str(f.relative_to(p.checkout))] = f
    return sorted(out.items())


def secret_sources(p: Paths) -> list[Source]:
    """The directory trees are walked as Sources (not pre-enumerated with glob), so a subtree that cannot be
    listed is REPORTED by walk_source and its last good ciphertext kept, never read as deleted."""
    srcs = [Source(label, f) for label, f in _secret_glob_files(p)]
    srcs += [Source(f"checkout/{t}", p.checkout / t, SECRET_EXCLUDES) for t in SECRET_TREES]
    srcs += [Source("zoe-env", p.home / ".zoe" / "env"), Source("zoe-secrets", p.home / ".zoe" / "secrets")]
    return srcs


def secret_files(p: Paths) -> list[tuple[str, Path]]:
    """Every checkout secret file the secrets tier ships (for ``check`` and the tests)."""
    out = dict(_secret_glob_files(p))
    for t in SECRET_TREES:
        out.update(walk_source(Source(f"checkout/{t}", p.checkout / t, SECRET_EXCLUDES)))
    return sorted(out.items())


def artefact_paths(p: Paths) -> list[Path]:
    return [x for x in (p.checkout / "labs" / "functiongemma-finetune" / "runs" / "gen1" / "merged",
                        p.home / "models" / "functiongemma-router") if x.exists()]


# ── the age mirror ───────────────────────────────────────────────────────────────────────────────────────────────


def excluded(rel_parts: Iterable[str], patterns: Iterable[str]) -> bool:
    pats = tuple(patterns)
    return any(fnmatch.fnmatch(part, pat) for part in rel_parts for pat in pats)


def walk_source(src: Source, errors: Optional[list[str]] = None) -> list[tuple[str, Path]]:
    """``(mirror-relative label path, file)`` for every regular file the source contributes. A directory that
    cannot be listed is appended to ``errors`` as its label-relative prefix - never silently skipped, because the
    mirror would otherwise read its absence as "deleted" and drop the files it already holds."""
    if src.path is None or not src.path.exists():
        return []
    if src.path.is_file():
        return [(src.label, src.path)]
    out = []

    def on_error(e: OSError) -> None:
        if errors is None:
            return
        try:
            rel = Path(e.filename).relative_to(src.path).as_posix()
        except (TypeError, ValueError):
            rel = "."
        errors.append(src.label if rel in ("", ".") else f"{src.label}/{rel}")

    for dirpath, dirnames, filenames in os.walk(src.path, onerror=on_error):
        rel_dir = Path(dirpath).relative_to(src.path)
        dirnames[:] = sorted(d for d in dirnames
                             if not excluded(rel_dir.parts + (d,), src.excludes)
                             and not os.path.islink(os.path.join(dirpath, d)))
        for fn in sorted(filenames):
            f = Path(dirpath) / fn
            if excluded(rel_dir.parts + (fn,), src.excludes) or f.is_symlink() or not f.is_file():
                continue
            out.append((f"{src.label}/{(rel_dir / fn).as_posix()}", f))
    return out


Encryptor = Callable[[Path, Path], str]   # (source file, ciphertext path) -> sha256 of the plaintext


def age_encryptor(age_bin: str, recipients_file: Path) -> Encryptor:
    def enc(src: Path, dst: Path) -> str:
        h = hashlib.sha256()
        with open(src, "rb") as fh:      # open FIRST: an unreadable file must not leave an age process waiting
            proc = subprocess.Popen([age_bin, "-R", str(recipients_file), "-o", str(dst)], stdin=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            assert proc.stdin is not None
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
                proc.stdin.write(chunk)
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        if proc.wait() != 0:
            raise RuntimeError(f"age failed on {src.name}: {err.strip()[:200]}")
        return h.hexdigest()
    return enc


@dataclass
class MirrorResult:
    encrypted: int = 0
    kept: int = 0
    removed: int = 0
    unreadable: list[str] = field(default_factory=list)


def _fingerprint(st: os.stat_result) -> list[int]:
    """What says "this file is unchanged": size, mtime AND ctime + inode. mtime alone is settable (``touch -d``,
    ``cp -p``, ``tar`` restore), so a same-size rewrite with the old mtime put back would keep stale ciphertext
    forever; ctime is set by the kernel on every write or utime and cannot be put back from user space, and a
    replace-by-rename changes the inode."""
    return [st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino]


def _name_for(salt: bytes, rel: str) -> str:
    d = hmac.new(salt, rel.encode(), hashlib.sha256).hexdigest()[:40]
    return f"{d[:2]}/{d[2:]}.age"


def mirror(sources: list[Source], stage: Path, state_file: Path, salt: bytes, encrypt: Encryptor,
           encrypt_bytes: Callable[[bytes, Path], None]) -> MirrorResult:
    """Bring ``stage`` in line with ``sources``: encrypt new/changed files, keep unchanged ciphertext byte-identical
    (so restic de-duplicates it), delete ciphertext whose source is gone, and write an age-encrypted index."""
    stage.mkdir(parents=True, exist_ok=True)
    os.chmod(stage, 0o700)
    try:
        prev = json.loads(state_file.read_text())
    except (OSError, ValueError):
        prev = {}
    res = MirrorResult()
    cur: dict[str, dict] = {}
    dir_errors: list[str] = []
    for src in sources:
        for rel, f in walk_source(src, dir_errors):
            try:
                st = f.stat()
            except OSError:
                res.unreadable.append(rel)
                continue
            name = _name_for(salt, rel)
            dst = stage / name
            old = prev.get(name)
            if old and old.get("fp") == _fingerprint(st) and dst.exists():
                cur[name] = old
                res.kept += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_suffix(".tmp")
            try:
                digest = encrypt(f, tmp)
            except (OSError, PermissionError):
                tmp.unlink(missing_ok=True)
                res.unreadable.append(rel)
                continue
            os.replace(tmp, dst)
            cur[name] = {"rel": rel, "size": st.st_size, "fp": _fingerprint(st), "sha256": digest}
            res.encrypted += 1
    res.unreadable += dir_errors
    # A file or directory we could not READ this run is not a deleted one: keep its last good ciphertext (and
    # report it) instead of dropping it from the backup.
    unread = set(res.unreadable)
    for name in set(prev) - set(cur):
        rel = prev[name].get("rel", "")
        if (rel in unread or any(rel.startswith(u + "/") for u in unread)) and (stage / name).exists():
            cur[name] = prev[name]
            res.kept += 1
            continue
        (stage / name).unlink(missing_ok=True)
        res.removed += 1
    for p in sorted(stage.rglob("*")):
        rel = p.relative_to(stage).as_posix()
        if p.is_file() and rel not in cur and rel not in ("index.json.age", "MANIFEST.sha256"):
            p.unlink()          # stray (crash mid-run): not in the index, never ship it
    for d in sorted((x for x in stage.rglob("*") if x.is_dir()), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    index = {n: {"rel": v["rel"], "size": v["size"], "sha256": v["sha256"]} for n, v in sorted(cur.items())}
    encrypt_bytes(json.dumps(index, indent=1).encode(), stage / "index.json.age")
    _atomic_write(state_file, json.dumps(cur))
    return res


def manifest_of(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name != "MANIFEST.sha256":
            out[p.relative_to(root).as_posix()] = sha256_file(p)
    return out


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


# ── config + guards ──────────────────────────────────────────────────────────────────────────────────────────────


class NotConfigured(Exception):
    pass


class Refused(Exception):
    def __init__(self, msg: str, alert: bool = True):
        super().__init__(msg)
        self.alert = alert


@dataclass
class Config:
    env: dict[str, str]
    restic: str
    age: str
    recipients: Path
    limit_upload_kib: int


def _read_env_file(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _private(path: Path) -> bool:
    return not (path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO))


def load_config(p: Paths) -> Config:
    env_file = p.config_dir / "offbox.env"
    if not env_file.is_file():
        raise NotConfigured(f"{env_file} missing - off-box backup is not configured (runbook: offbox-backup.md)")
    # The age IDENTITY must never be on this box (vault O5). Refuse loudly if any file in the config dir holds one.
    for f in p.config_dir.rglob("*"):
        if f.is_file():
            try:
                if AGE_IDENTITY_MARKER in f.read_bytes():
                    raise Refused(f"{f} holds an age IDENTITY (private key). It must live off the box; remove it")
            except OSError:
                pass
    for f in [env_file] + [x for x in p.config_dir.iterdir() if x.is_file()]:
        if not _private(f):
            raise Refused(f"{f} is readable by group/other; chmod 600 it")
    vals = _read_env_file(env_file)
    need = ("RESTIC_REPOSITORY", "RESTIC_PASSWORD_FILE", "RESTIC_REST_USERNAME", "RESTIC_REST_PASSWORD",
            "ZOE_BACKUP_AGE_RECIPIENTS_FILE")
    missing = [k for k in need if not vals.get(k)]
    if missing:
        raise NotConfigured(f"{env_file} lacks {', '.join(missing)}")
    if "@" in vals["RESTIC_REPOSITORY"].split("://", 1)[-1].split("/", 1)[0]:
        raise Refused("RESTIC_REPOSITORY carries credentials in the URL (process listings show it); "
                      "use RESTIC_REST_USERNAME / RESTIC_REST_PASSWORD")
    pw = Path(os.path.expanduser(vals["RESTIC_PASSWORD_FILE"]))
    rec = Path(os.path.expanduser(vals["ZOE_BACKUP_AGE_RECIPIENTS_FILE"]))
    for f in (pw, rec):
        if not f.is_file():
            raise NotConfigured(f"{f} missing")
    if not _private(pw):
        raise Refused(f"{pw} is readable by group/other; chmod 600 it")
    recips = [ln.strip() for ln in rec.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    if not recips or not all(r.startswith("age1") for r in recips):
        raise Refused(f"{rec} must hold only age PUBLIC recipients (age1...)")
    restic = vals.get("RESTIC_BIN") or shutil.which("restic") or str(p.home / ".local" / "bin" / "restic")
    age = vals.get("AGE_BIN") or shutil.which("age") or str(p.home / ".local" / "bin" / "age")
    for b in (restic, age):
        if not (Path(b).is_file() and os.access(b, os.X_OK)):
            raise NotConfigured(f"{b} not installed (scripts/setup/offbox-backup/install_tools.sh)")
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(p.home),
        "RESTIC_REPOSITORY": vals["RESTIC_REPOSITORY"], "RESTIC_PASSWORD_FILE": str(pw),
        "RESTIC_REST_USERNAME": vals["RESTIC_REST_USERNAME"], "RESTIC_REST_PASSWORD": vals["RESTIC_REST_PASSWORD"],
        "RESTIC_CACHE_DIR": str(p.home / ".cache" / "restic"), "GOGC": "50",
    }
    if vals.get("RESTIC_CACERT"):
        env["RESTIC_CACERT"] = os.path.expanduser(vals["RESTIC_CACERT"])
    return Config(env=env, restic=restic, age=age, recipients=rec,
                  limit_upload_kib=int(vals.get("ZOE_BACKUP_LIMIT_UPLOAD_KIB", "2048") or 0))


def in_quiet_hours(now: datetime) -> bool:
    t = (now.hour, now.minute)
    return QUIET_START <= t < QUIET_END


def mem_available_mb() -> int:
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


def preflight(now: datetime, floor_mb: int = MEM_FLOOR_MB) -> None:
    if in_quiet_hours(now):
        # not alerted: a Persistent= catch-up after a night-time boot lands here; the drill's
        # "newest snapshot older than 50 h" check catches a backup that really stopped landing
        raise Refused(f"inside the night window {QUIET_START[0]:02d}:{QUIET_START[1]:02d}-"
                      f"{QUIET_END[0]:02d}:{QUIET_END[1]:02d}; the 12B owns the box", alert=False)
    avail = mem_available_mb()
    if avail < floor_mb:
        raise Refused(f"MemAvailable {avail} MB < floor {floor_mb} MB")


# ── restic ───────────────────────────────────────────────────────────────────────────────────────────────────────


def restic(cfg: Config, *args: str, check: bool = True, timeout: int = 6 * 3600) -> subprocess.CompletedProcess:
    r = subprocess.run([cfg.restic, *args], env=cfg.env, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"restic {args[0]} exited {r.returncode}: {r.stderr.strip()[-400:]}")
    return r


def snapshots(cfg: Config, tier: str) -> list[dict]:
    r = restic(cfg, "snapshots", "--json", "--tag", f"zoe-{tier}", "--host", HOST_TAG)
    snaps = json.loads(r.stdout or "[]")
    return sorted(snaps, key=lambda s: s["time"])


def _parse_time(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    if "." in s:                       # restic prints nanoseconds; fromisoformat takes at most microseconds
        head, frac = s.split(".", 1)
        tz = frac[frac.find("+"):] if "+" in frac else (frac[frac.find("-"):] if "-" in frac else "")
        digits = frac[: len(frac) - len(tz)] if tz else frac
        s = f"{head}.{digits[:6]}{tz}"
    return datetime.fromisoformat(s)


def backup_tier(cfg: Config, tier: str, paths: list[Path]) -> str:
    args = ["backup", "--json", "--tag", f"zoe-{tier}", "--host", HOST_TAG, "--no-scan"]
    if cfg.limit_upload_kib > 0:
        args += ["--limit-upload", str(cfg.limit_upload_kib)]
    if os.environ.get("ZOE_OFFBOX_SNAPSHOT_TIME"):       # lab only: back-date a snapshot to test retention
        args += ["--time", os.environ["ZOE_OFFBOX_SNAPSHOT_TIME"]]
    r = restic(cfg, *args, *[str(x) for x in paths])
    for line in reversed(r.stdout.splitlines()):
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("message_type") == "summary":
            return msg["snapshot_id"]
    raise RuntimeError(f"restic backup ({tier}) printed no summary")


# ── alerting ─────────────────────────────────────────────────────────────────────────────────────────────────────


def alert(text: str) -> None:
    """Telegram via the crash-loop watcher's sender (a separate process from zoe-data). Never carries a secret."""
    print(f"ALERT: {text}", file=sys.stderr)
    if os.environ.get("ZOE_OFFBOX_NO_ALERT"):
        return
    try:
        spec = importlib.util.spec_from_file_location("_clw", REPO_ROOT / "scripts" / "maintenance" /
                                                      "zoe_crash_loop_watch.py")
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        mod._telegram(f"Zoe backup: {text}")
    except Exception as exc:  # noqa: BLE001 - the alert path must never mask the failure it reports
        print(f"alert not delivered: {type(exc).__name__}", file=sys.stderr)


# ── commands ─────────────────────────────────────────────────────────────────────────────────────────────────────


def _salt(p: Paths) -> bytes:
    f = p.state_dir / "name.salt"
    if not f.exists():
        _atomic_write(f, secrets.token_hex(32))
    return bytes.fromhex(f.read_text().strip())


def _enc_bytes(cfg: Config) -> Callable[[bytes, Path], None]:
    def enc(data: bytes, dst: Path) -> None:
        tmp = dst.with_suffix(".tmp")
        r = subprocess.run([cfg.age, "-R", str(cfg.recipients), "-o", str(tmp)], input=data, capture_output=True)
        if r.returncode != 0:
            raise RuntimeError("age failed on the index")
        os.replace(tmp, dst)
    return enc


def _globals_dump(cfg: Config, tmpdir: Path) -> Optional[Source]:
    """``pg_dumpall --globals-only`` (roles, tablespaces): the per-database dumps carry no roles."""
    container = os.environ.get("ZOE_POSTGRES_CONTAINER", "zoe-database")
    if os.environ.get("ZOE_OFFBOX_SKIP_PG_GLOBALS"):
        return None
    out = tmpdir / "globals.sql.gz"
    with open(out, "wb") as fh:
        p1 = subprocess.Popen(["docker", "exec", container, "pg_dumpall", "-U", "zoe", "--globals-only"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        p2 = subprocess.run(["gzip", "-c"], stdin=p1.stdout, stdout=fh)
        if p1.wait() != 0 or p2.returncode != 0:
            raise RuntimeError("pg_dumpall --globals-only failed")
    return Source("pg/globals.sql.gz", out)


def cmd_backup(p: Paths, cfg: Config, now: datetime) -> int:
    salt = _salt(p)
    enc = age_encryptor(cfg.age, cfg.recipients)
    encb = _enc_bytes(cfg)
    problems: list[str] = []
    result: dict = {"time": now.isoformat(timespec="seconds"), "snapshots": {}}
    with tempfile.TemporaryDirectory(prefix="zoe-offbox-") as td:
        os.chmod(td, 0o700)
        hh = household_sources(p)
        for s in hh:
            if s.path is None or not s.path.exists():
                if s.required:
                    problems.append(f"{s.label}: source missing")
                continue
            if s.max_age_hours is not None and (now.timestamp() - s.path.stat().st_mtime) > s.max_age_hours * 3600:
                problems.append(f"{s.label}: newest on-box copy is older than {s.max_age_hours:.0f} h "
                                f"(did zoe-backup.service fail?) - shipping it anyway")
        g = _globals_dump(cfg, Path(td))
        if g:
            hh.append(g)
        tiers = {"household": hh, "secrets": secret_sources(p)}
        for tier, srcs in tiers.items():
            stage = p.stage_dir / tier
            res = mirror(srcs, stage, p.state_dir / f"mirror-{tier}.json", salt, enc, encb)
            if res.unreadable:
                problems.append(f"{tier}: {len(res.unreadable)} unreadable file(s)/dir(s) - last good copy kept: "
                                + ", ".join(sorted(res.unreadable)[:5]))
            man = manifest_of(stage)
            (stage / "MANIFEST.sha256").write_text("".join(f"{v}  {k}\n" for k, v in man.items()))
            sid = backup_tier(cfg, tier, [stage])
            _atomic_write(p.state_dir / "manifests" / f"{sid}.json", json.dumps({"tier": tier, "files": man}))
            result["snapshots"][tier] = {"id": sid, "encrypted": res.encrypted, "kept": res.kept,
                                         "removed": res.removed, "files": len(man)}
            print(f"{tier}: snapshot {sid[:8]} - {len(man)} files ({res.encrypted} re-encrypted, "
                  f"{res.kept} unchanged, {res.removed} removed)")
    arts = artefact_paths(p)
    if not arts:      # never a silent skip: the drill would then compare an ever-older artefacts snapshot
        problems.append("artefacts: neither the router checkpoint nor the GGUF directory exists - no snapshot")
    if arts:
        sid = backup_tier(cfg, "artefacts", arts)
        man = {}
        for a in arts:
            for f in (sorted(a.rglob("*")) if a.is_dir() else [a]):
                if f.is_file():
                    man[str(f)] = sha256_file(f)
        _atomic_write(p.state_dir / "manifests" / f"{sid}.json", json.dumps({"tier": "artefacts", "files": man}))
        result["snapshots"]["artefacts"] = {"id": sid, "files": len(man)}
        print(f"artefacts: snapshot {sid[:8]} - {len(man)} files")
    _prune_local_manifests(p, keep=200)
    result["problems"] = problems
    _atomic_write(p.state_dir / "last_backup.json", json.dumps(result, indent=1))
    if problems:
        alert("off-box backup landed with problems: " + "; ".join(problems))
        return 1
    return 0


def _prune_local_manifests(p: Paths, keep: int) -> None:
    files = sorted((p.state_dir / "manifests").glob("*.json"), key=lambda f: f.stat().st_mtime)
    for f in files[:-keep]:
        f.unlink(missing_ok=True)


def drill_due(p: Paths, now: datetime) -> bool:
    """Due 28 days after the last PASSING drill. A failed drill does not advance the clock, so the next weekly
    verify retries it instead of leaving a broken backup unchecked for a month."""
    try:
        state = json.loads((p.state_dir / "last_drill.json").read_text())
        last = _parse_time(state["last_ok"])
    except (OSError, ValueError, KeyError, TypeError):
        return True
    return now - last >= timedelta(days=DRILL_INTERVAL_DAYS)


def retention_findings(tier: str, snaps: list[dict], now: datetime) -> list[str]:
    out = []
    if not snaps:
        return [f"{tier}: no snapshot in the repo"]
    newest_t = _parse_time(snaps[-1]["time"])
    if now - newest_t > timedelta(hours=NEWEST_MAX_AGE_HOURS):
        out.append(f"{tier}: newest snapshot is {(now - newest_t).days} day(s) old - backups are not landing")
    if tier == "household":
        oldest_t = _parse_time(snaps[0]["time"])
        if newest_t - oldest_t > timedelta(days=HOUSEHOLD_MAX_AGE_DAYS):     # hard bound, no slack
            out.append(f"household: oldest snapshot is {(newest_t - oldest_t).days} days older than the newest "
                       f"(> {HOUSEHOLD_MAX_AGE_DAYS}) - the411's prune is not running; "
                       "forgotten words outlive the forget")
    return out


def cmd_drill(p: Paths, cfg: Config, now: datetime, force: bool) -> int:
    if not force and not drill_due(p, now):
        print("drill not due (monthly); --force to run now")
        return 0
    findings: list[str] = []
    scratch_root = p.home / ".cache" / "zoe-offbox"
    scratch_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="drill-", dir=scratch_root))
    os.chmod(scratch, 0o700)
    try:
        for tier in ("household", "secrets", "artefacts"):
            snaps = snapshots(cfg, tier)
            findings += retention_findings(tier, snaps, now)
            if not snaps:
                continue
            snap = snaps[-1]
            target = scratch / tier
            args = ["restore", snap["id"], "--target", str(target)]
            if tier == "artefacts":
                args += ["--include", "*.gguf"]     # a 1 GB checkpoint restore every month is not worth the WAN
            restic(cfg, *args)
            local = p.state_dir / "manifests" / f"{snap['id']}.json"
            if tier == "artefacts":
                want = {k: v for k, v in json.loads(local.read_text())["files"].items() if k.endswith(".gguf")} \
                    if local.exists() else {}
                # restic restores an absolute source path P under <target>/P
                got = {"/" + f.relative_to(target).as_posix(): sha256_file(f) for f in target.rglob("*")
                       if f.is_file()}
            else:
                root = target / str(snap["paths"][0]).lstrip("/")
                got = manifest_of(root)
                if local.exists():
                    want = json.loads(local.read_text())["files"]
                else:          # a new box: compare against the manifest that travelled inside the snapshot
                    want = {ln[66:].strip(): ln[:64] for ln in (root / "MANIFEST.sha256").read_text().splitlines()}
                    findings.append(f"{tier}: no local manifest for {snap['short_id']} (new box?) - compared "
                                    "against the in-snapshot manifest only")
            if not want:
                findings.append(f"{tier}: nothing to compare for snapshot {snap['short_id']}")
            elif got != want:
                bad = sorted(k for k in set(want) | set(got) if want.get(k) != got.get(k))
                findings.append(f"{tier}: restore of {snap['short_id']} differs from what was sent in "
                                f"{len(bad)} file(s)")
            else:
                print(f"{tier}: snapshot {snap['short_id']} restored, {len(want)} file(s) checksum-identical")
        month = now.month
        r = restic(cfg, "check", f"--read-data-subset={month}/12", check=False)
        if r.returncode != 0:
            findings.append(f"restic check (data subset {month}/12) failed: exit {r.returncode}")
    except Exception as exc:  # noqa: BLE001 - every failure must reach the alert
        findings.append(f"drill aborted: {type(exc).__name__}: {str(exc)[:300]}")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    hard = [f for f in findings if "in-snapshot manifest only" not in f]
    try:
        prev_ok = json.loads((p.state_dir / "last_drill.json").read_text()).get("last_ok")
    except (OSError, ValueError, AttributeError):
        prev_ok = None
    stamp = now.isoformat(timespec="seconds")
    _atomic_write(p.state_dir / "last_drill.json",
                  json.dumps({"time": stamp, "ok": not hard, "last_ok": prev_ok if hard else stamp,
                              "findings": findings}))
    for f in findings:
        print(f"FINDING: {f}")
    if hard:
        alert("monthly off-box restore drill FAILED: " + "; ".join(hard))
        return 1
    print("drill: all checks passed")
    return 0


def safe_join(root: Path, rel: object) -> Optional[Path]:
    """``root / rel`` only when ``rel`` is a non-empty RELATIVE path with no ``..`` and the result stays under
    ``root`` (symlinks resolved); otherwise None."""
    if not isinstance(rel, str) or not rel or "\x00" in rel:
        return None
    pp = Path(rel)
    if pp.is_absolute() or ".." in pp.parts:
        return None
    out = (root / pp)
    try:
        out.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return out


def is_zoe_box(p: Paths) -> bool:
    """This host RUNS the off-box backup: the unit is installed. The identity must never be used here."""
    units = p.home / ".config" / "systemd" / "user"
    return any((units / f"zoe-offbox-backup.{s}").exists() for s in ("service", "timer"))


def cmd_restore(p: Paths, cfg: Config, identity: Path, tier: str, target: Path, snapshot: str,
                allow_on_box: bool = False) -> int:
    """OWNER / disaster recovery: restic restore, then age-decrypt back to the original paths and verify each
    plaintext sha256 against the encrypted index. Run where the identity is - never on a box that runs the backup."""
    if tier != "artefacts":
        if is_zoe_box(p) and not allow_on_box:
            raise Refused("this host runs zoe-offbox-backup: the age identity must never be used on the box (vault "
                          "O5). Restore on the owner's machine; on a REPLACEMENT box before its units are installed, "
                          "pass --allow-on-box and delete the identity afterwards", alert=False)
        try:
            identity.resolve().relative_to(p.config_dir.resolve())
            raise Refused(f"{identity} is inside the backup config dir; the identity never lives there", alert=False)
        except ValueError:
            pass
    target.mkdir(parents=True, exist_ok=True)
    raw = Path(tempfile.mkdtemp(prefix="raw-", dir=target))
    restic(cfg, "restore", snapshot, "--target", str(raw), "--tag", f"zoe-{tier}", "--host", HOST_TAG)
    if tier == "artefacts":
        print(f"artefacts restored (plain) under {raw}")
        return 0
    idx_files = list(raw.rglob("index.json.age"))
    if len(idx_files) != 1:
        raise RuntimeError("restored snapshot has no single index.json.age")
    stage = idx_files[0].parent
    dec = subprocess.run([cfg.age, "-d", "-i", str(identity), str(stage / "index.json.age")], capture_output=True)
    if dec.returncode != 0:
        raise RuntimeError("cannot decrypt the index with that identity")
    index = json.loads(dec.stdout)
    bad = 0
    out_root = target / "plain"
    for name, meta in index.items():
        # The index is attacker-writable (anyone with the box has the PUBLIC recipient), so neither path is trusted:
        # both must stay under their roots, or a forged index writes outside the restore dir on the owner's machine.
        src = safe_join(stage, name)
        dst = safe_join(out_root, meta["rel"])
        if src is None or dst is None:
            bad += 1
            print(f"REFUSED unsafe path in the index: {name!r} -> {str(meta.get('rel'))!r}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([cfg.age, "-d", "-i", str(identity), "-o", str(dst), str(src)],
                           capture_output=True)
        if r.returncode != 0 or sha256_file(dst) != meta["sha256"]:
            bad += 1
            print(f"MISMATCH {meta['rel']}")
    shutil.rmtree(raw, ignore_errors=True)
    print(f"{tier}: {len(index) - bad}/{len(index)} file(s) decrypted and verified under {out_root}")
    return 1 if bad else 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    nt = sub.add_parser("notify", help="send one alert line (used by zoe-backup-verify.sh)")
    nt.add_argument("text")
    b = sub.add_parser("backup")
    b.add_argument("--ignore-window", action="store_true", help="lab/manual: run inside the night window")
    d = sub.add_parser("drill")
    d.add_argument("--force", action="store_true")
    d.add_argument("--ignore-window", action="store_true")
    r = sub.add_parser("restore")
    r.add_argument("--identity", type=Path, required=True)
    r.add_argument("--tier", choices=tuple(RETENTION), required=True)
    r.add_argument("--target", type=Path, required=True)
    r.add_argument("--snapshot", default="latest")
    r.add_argument("--allow-on-box", action="store_true",
                   help="disaster recovery on a REPLACEMENT box before its backup units are installed")
    ns = ap.parse_args(argv)
    if ns.cmd == "notify":
        alert(ns.text)
        return 0
    p = Paths.from_env()
    now = datetime.now().astimezone()
    try:
        cfg = load_config(p)
        if ns.cmd == "check":
            print(f"configured: repo set, recipients {cfg.recipients}, restic {cfg.restic}, age {cfg.age}")
            print(f"household sources: {[s.label for s in household_sources(p) if s.path and s.path.exists()]}")
            print(f"secret files: {len(secret_files(p))}; artefacts: {[str(a) for a in artefact_paths(p)]}")
            return 0
        if ns.cmd == "restore":
            return cmd_restore(p, cfg, ns.identity, ns.tier, ns.target, ns.snapshot, ns.allow_on_box)
        if not getattr(ns, "ignore_window", False):
            preflight(now, int(os.environ.get("ZOE_OFFBOX_MEM_FLOOR_MB", MEM_FLOOR_MB)))
        if ns.cmd == "backup":
            return cmd_backup(p, cfg, now)
        return cmd_drill(p, cfg, now, ns.force)
    except NotConfigured as exc:
        print(f"not configured: {exc}", file=sys.stderr)
        return 2
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        if ns.cmd in ("backup", "drill") and exc.alert:
            alert(f"{ns.cmd} refused on {socket.gethostname()}: {exc}")
        return 3
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        alert(f"{ns.cmd} FAILED: {type(exc).__name__}: {str(exc)[:300]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
