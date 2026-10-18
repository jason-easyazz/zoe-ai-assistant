#!/usr/bin/env python3
"""Nightly ON-BOX backup of the host sources (deep dive 2 S0-8): the third step of ``zoe-backup.service``.

The Postgres dumps and the MemPalace tarball were the only on-box backups; lane J (deep dive 2, section 6.3)
listed what a dead disk lost besides them. This writes, under ``~/.zoe-backups/host/`` (0700, files 0600):

* ``ha-storage-<ts>.tar.gz`` - Home Assistant's root-owned ``.storage`` + ``.cloud``, exported by the fixed-function
  root helper ``zoe-ha-config-export`` (``scripts/setup/p0-deroot/libexec/``; the owner installs it). Until it is
  installed this step says so and carries on: it never calls ``sudo`` with anything else.
* ``host-<ts>.tar.gz`` (or ``.tar.gz.age``, below) - one archive of every HOST source ``zoe_offbox.py`` ships off the
  box (``host_household_sources`` + ``host_secret_sources`` + the crontab), with ``MANIFEST.json`` inside: each
  file's sha256 and every source label, including the ones that were missing and why. The weekly restore drill
  (``zoe_restore_drill.py``) opens it and checks the files against that manifest.

One source list: the labels and excludes are ``zoe_offbox``'s, so the on-box and the off-box copies cannot drift.

ENCRYPTION AT REST (optional, recommended once the off-box age recipient exists): with
``ZOE_LOCAL_BACKUP_ENCRYPT=1`` the host archive (which holds credentials: drop-in env lines, ``~/bin``, HA's
secrets) is written as ``host-<ts>.tar.gz.age`` to the SAME public recipient as the off-box backup
(``~/.config/zoe-backup/age-recipients.txt``). The box then cannot read it back - the identity never lives here -
so the drill checks only that it is a well-formed age file. The Postgres dumps and the palace tarball stay
plaintext on purpose: their job is a fast local restore without the owner's vault, and whoever can read them
already holds the live database.

Rotation: 7 of each (inside the 35-day forget bound). Exit 0 ok (warnings printed), 1 a source failed to read or
the helper failed.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
HELPER_INSTALLED = Path("/usr/local/libexec/zoe-priv/zoe-ha-config-export")
KEEP = 7


def _load_offbox():
    if "zoe_offbox" in sys.modules:         # one module object per process (tests patch it)
        return sys.modules["zoe_offbox"]
    spec = importlib.util.spec_from_file_location("zoe_offbox", HERE / "zoe_offbox.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["zoe_offbox"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


ob = _load_offbox()


def ha_helper_cmd() -> Optional[list[str]]:
    """The installed root helper through sudo; ZOE_HA_EXPORT_CMD (lab, tests) names a stand-in. None = not
    installed (an owner step), and nothing else is tried."""
    if os.environ.get("ZOE_HA_EXPORT_CMD"):
        return os.environ["ZOE_HA_EXPORT_CMD"].split() + ["storage"]
    if HELPER_INSTALLED.is_file():
        return ["sudo", "-n", str(HELPER_INSTALLED), "storage"]
    return None


def export_ha_storage(out_dir: Path, ts: str) -> tuple[Optional[Path], list[str], list[str]]:
    """``(file, warnings, errors)``."""
    cmd = ha_helper_cmd()
    if cmd is None:
        return None, [f"HA .storage/.cloud NOT captured: the root helper is not installed ({HELPER_INSTALLED}; "
                      "owner step, docs/knowledge/offbox-backup.md section 7)"], []
    out = out_dir / f"ha-storage-{ts}.tar.gz"
    part = out.with_name(out.name + ".partial")
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        r = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE, timeout=300)
    err = r.stderr.decode(errors="replace").strip()
    if r.returncode != 0:
        part.unlink(missing_ok=True)
        return None, [], [f"HA export failed (exit {r.returncode}): {err[-200:]}"]
    try:
        with tarfile.open(part, "r:gz") as t:
            names = t.getnames()
    except (tarfile.TarError, OSError, EOFError) as exc:
        part.unlink(missing_ok=True)
        return None, [], [f"HA export is not a readable tar: {exc}"]
    os.replace(part, out)
    warn = [] if any(n.startswith(".storage/") for n in names) else ["HA export holds no .storage file"]
    print(f"zoe-host-snapshot: {out.name} ({len(names)} files) {err}")
    return out, warn, []


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def host_sources(p, tmp: Path, ha_export: Optional[Path]) -> list:
    srcs = []
    for s in ob.host_household_sources(p):
        if s.label == "ha/storage.tar.gz":
            s = ob.Source(s.label, ha_export)      # tonight's export, never an older one
        srcs.append(s)
    srcs += ob.host_onbox_only_sources(p)
    srcs += ob.host_secret_sources(p)
    ct = ob.crontab_source(tmp)
    srcs.append(ct if ct else ob.Source("crontab", None))
    return srcs


def build_archive(srcs: list, dest: Path) -> dict:
    """Write a gzip tar of every source under its label + ``MANIFEST.json``; return the manifest."""
    manifest: dict = {"created": datetime.now().astimezone().isoformat(timespec="seconds"),
                      "labels": {}, "files": {}, "unreadable": []}
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
        for s in srcs:
            if s.path is None or not s.path.exists():
                manifest["labels"][s.label] = {"files": 0, "missing": True,
                                               "path": str(s.path) if s.path else None}
                continue
            errors: list[str] = []
            n = 0
            for rel, f in ob.walk_source(s, errors):
                try:
                    data = f.read_bytes()
                except OSError:
                    manifest["unreadable"].append(rel)
                    continue
                ti = tarfile.TarInfo(rel)
                ti.size, ti.mode = len(data), 0o600
                try:
                    ti.mtime = int(f.stat().st_mtime)
                except OSError:
                    pass
                tar.addfile(ti, io.BytesIO(data))
                manifest["files"][rel] = hashlib.sha256(data).hexdigest()
                n += 1
            manifest["unreadable"] += errors
            manifest["labels"][s.label] = {"files": n, "missing": False, "path": str(s.path)}
        blob = json.dumps(manifest, indent=1, sort_keys=True).encode()
        ti = tarfile.TarInfo("MANIFEST.json")
        ti.size, ti.mode = len(blob), 0o600
        tar.addfile(ti, io.BytesIO(blob))
    return manifest


def age_recipients(p) -> Optional[Path]:
    if os.environ.get("ZOE_LOCAL_BACKUP_ENCRYPT", "") not in ("1", "true", "yes"):
        return None
    rec = p.config_dir / "age-recipients.txt"
    if not rec.is_file():
        raise SystemExit(f"ZOE_LOCAL_BACKUP_ENCRYPT=1 but {rec} is missing (offbox-backup.md section 4 step 3)")
    lines = [ln.strip() for ln in rec.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    if not lines or not all(ln.startswith("age1") for ln in lines):
        raise SystemExit(f"{rec} must hold only age PUBLIC recipients (age1...)")
    return rec


def rotate(out_dir: Path, pattern: str, keep: int = KEEP) -> None:
    files = sorted(out_dir.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in files[keep:]:
        f.unlink(missing_ok=True)


def main() -> int:
    p = ob.Paths.from_env()
    out_dir = p.home.joinpath(*ob.HOST_BACKUP_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    rec = age_recipients(p)
    ha, warnings, errors = export_ha_storage(out_dir, ts)
    with tempfile.TemporaryDirectory(prefix="zoe-host-snapshot-") as td:
        os.chmod(td, 0o700)
        srcs = host_sources(p, Path(td), ha)
        part = Path(td) / "host.tar.gz"
        man = build_archive(srcs, part)
        final = out_dir / f"host-{ts}.tar.gz"
        if rec is not None:
            age = shutil.which("age") or str(p.home / ".local" / "bin" / "age")
            enc = Path(td) / "host.tar.gz.age"
            r = subprocess.run([age, "-R", str(rec), "-o", str(enc), str(part)], capture_output=True)
            if r.returncode != 0:
                print(f"zoe-host-snapshot: age failed: {r.stderr.decode(errors='replace')[-200:]}", file=sys.stderr)
                return 1
            part, final = enc, out_dir / f"host-{ts}.tar.gz.age"
        tmp_final = final.with_name(final.name + ".partial")
        shutil.copyfile(part, tmp_final)
        os.chmod(tmp_final, 0o600)
        os.replace(tmp_final, final)
    rotate(out_dir, "ha-storage-*.tar.gz")
    rotate(out_dir, "host-*.tar.gz*")
    missing = sorted(k for k, v in man["labels"].items() if v["missing"])
    print(f"zoe-host-snapshot: {final.name}: {len(man['files'])} files from {len(man['labels'])} sources"
          + (f"; missing: {', '.join(missing)}" if missing else ""))
    for w in warnings:
        print(f"zoe-host-snapshot: WARNING {w}", file=sys.stderr)
    if man["unreadable"]:
        errors.append(f"{len(man['unreadable'])} unreadable: {', '.join(sorted(man['unreadable'])[:5])}")
    for e in errors:
        print(f"zoe-host-snapshot: ERROR {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
