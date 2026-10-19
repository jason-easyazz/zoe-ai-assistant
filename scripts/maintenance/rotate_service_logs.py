#!/usr/bin/env python3
"""Size-based rotation for the logs systemd appends to (``StandardOutput=append:``).

WHY THIS EXISTS
    systemd's ``append:`` target never rotates. ``zoe-data.service`` (via the
    host drop-in ``20-capture-output.conf``) appends the service's stdout and
    stderr to ``~/.zoe-logs/zoe-data.{stdout,stderr}.log``. The only thing that
    trimmed them was a host-local, UNTRACKED pair (``~/bin/zoe-logs-rotate.sh`` +
    ``zoe-logs-rotate.{service,timer}``, daily 02:50) that acts only above 150 MB,
    rewrites the file with ``tail -c 50MB`` (cutting mid-line, non-atomically),
    keeps 3 stdout archives and never prunes the stderr ones. With stderr growing
    ~70 MB/day it ran about every other day and the files sat at 86 MB / 119 MB
    in between (2026-10-04); the 2026-09-25 review had found a 445 MB stdout.
    This script is the tracked replacement: 50 MB threshold, whole-file gzip
    segments, verified before anything is replaced, hourly. logrotate is not
    installed on this host and a user manager cannot run a system timer, hence a
    stdlib script on a user timer (``zoe-log-rotate.timer``). INSTALL STEP:
    retire the old pair first (docs/knowledge/incident-runbook.md section 26).

HOW IT ROTATES (copytruncate, loss-minimal)
    systemd opens the file ``O_APPEND`` and keeps the descriptor for the life of
    the unit, so renaming the file would leave the service writing into the
    renamed inode. Instead: stream-gzip the first ``size`` bytes to a temp file,
    verify it by reading it back, and only THEN publish it as
    ``<stem>.1<ext>.gz`` (``zoe-data.stderr.1.log.gz``; older segments shift to
    ``.2`` ... and fall off after ``--keep``) and truncate the live file in
    place. A failure before the publish (ENOSPC, SIGTERM, OOM) changes nothing. Bytes appended while the
    copy ran are re-appended after the truncate, so the window in which a line
    can be lost is the gap between reading that tail and the ``truncate`` call —
    microseconds, not the seconds the gzip takes. Because the writer is
    ``O_APPEND`` its next write lands at the new end-of-file, so the file does
    not turn sparse.

SAFETY
    * never touches the app log (``zoe-data.app.log``) — it rotates itself;
    * skips symlinks and non-regular files, and files below the threshold;
    * single instance (``flock``) so an overlapping timer tick cannot interleave
      two shifts;
    * rotated segments are written 0640 (the logs hold household conversation);
    * ``--dry-run`` reports what it would do and changes nothing.

TEXT-LOG RETENTION (deep dive A6)
    The same hourly pass also applies the retention window to the local logs that keep utterance TEXT (the router shadow log
    and the intent-miss log): text older than ``ZOE_TEXT_LOG_RETENTION_DAYS`` (default 30) is stripped or dropped. The logic lives
    in ``services/zoe-data/local_text_logs.py`` (stdlib only); this job only schedules it. It also deletes the owner scripts'
    one-off backups past 14 days (``prune_adhoc_backups``). It FAILS CLOSED: if the pass raises or any part reports failure the job
    exits 1 (after the rotation ran), so a retention pass that stopped working shows red.
    ``--no-text-sweep`` skips it.
    The retention knobs (``ZOE_TEXT_LOG_RETENTION_DAYS``, ``ZOE_ROUTER_HEAD_LOG``, ``ZOE_INTENT_MISS_LOG``, ``ZOE_ADHOC_BACKUP_DIR``)
    live in the zoe-data env files, not in this job's environment, so the sweep reads them from ``<repo>/.env`` then
    ``<repo>/services/zoe-data/.env`` (PARSED, never sourced; a variable already in the process environment wins). Without that an
    owner's ``ZOE_TEXT_LOG_RETENTION_DAYS=keep`` would be ignored and text they chose to keep deleted. ``zoe-log-rotate.service``
    also loads the same files via ``EnvironmentFile=-`` (re-installing the unit is an owner step; this reader makes the fix hold
    before that).

Exit status 0 on success (including "nothing to do"), 1 if any file failed.
"""
from __future__ import annotations

import argparse
import fcntl
import gzip
import os
import stat
import sys
from pathlib import Path

DEFAULT_DIR = "~/.zoe-logs"
DEFAULT_MAX_MB = 50
DEFAULT_KEEP = 4
#: Logs systemd appends to and nothing else rotates. ``zoe-data.app.log`` is
#: deliberately absent: it is a RotatingFileHandler.
DEFAULT_FILES = (
    "zoe-data.stderr.log",
    "zoe-data.stdout.log",
    "crash-loop-watch.log",
    "memory-export.log",
    "voice-regression.log",
)
_CHUNK = 1024 * 1024
_REPO = Path(__file__).resolve().parents[2]
#: env files that configure zoe-data, in the order its unit loads them (later wins)
ENV_FILES = (_REPO / ".env", _REPO / "services" / "zoe-data" / ".env")
#: the only names the sweep takes from them - nothing else in those files (they hold secrets) is read into this process
SWEEP_ENV_KEYS = ("ZOE_TEXT_LOG_RETENTION_DAYS", "ZOE_ROUTER_HEAD_LOG", "ZOE_INTENT_MISS_LOG", "ZOE_ADHOC_BACKUP_DIR")
_NEVER = {"zoe-data.app.log"}


def _segment(path: Path, n: int) -> Path:
    # ``zoe-data.stdout.log`` -> ``zoe-data.stdout.1.log.gz``. Deliberately NOT
    # ``zoe-data.stdout.log.1.gz``: the host's older ~/bin/zoe-logs-rotate.sh
    # prunes ``zoe-data.stdout.log.*.gz`` down to 3 files, which would silently eat
    # these segments if both rotators ever ran.
    return path.with_name(f"{path.stem}.{n}{path.suffix}.gz")


def rotate_file(path: Path, *, max_bytes: int, keep: int, dry_run: bool = False) -> str:
    """Rotate ``path`` if it is at least ``max_bytes``. Returns a status string:
    ``rotated`` | ``dry-run`` | ``small`` | ``missing`` | ``skipped``.
    """
    if path.name in _NEVER:
        return "skipped"
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "missing"
    if not stat.S_ISREG(st.st_mode):
        return "skipped"  # symlink / fifo / dir: never follow or truncate
    if st.st_size < max_bytes:
        return "small"
    if dry_run:
        return "dry-run"

    size0 = st.st_size

    # 1. Build the new segment FIRST, in a temp file, and verify it. Nothing
    #    that already exists is touched until a complete, readable archive is
    #    on disk: a gzip failure (ENOSPC, SIGTERM, OOM) must leave the archive
    #    and the live file exactly as they were, not erode one segment per tick.
    target = _segment(path, 1)
    tmp = target.with_name(target.name + ".tmp")
    written = 0
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    try:
        with os.fdopen(fd, "wb") as raw, gzip.GzipFile(
            filename=path.name, mode="wb", fileobj=raw, compresslevel=6
        ) as gz, open(path, "rb") as src_f:
            while written < size0:
                chunk = src_f.read(min(_CHUNK, size0 - written))
                if not chunk:
                    break
                gz.write(chunk)
                written += len(chunk)
        if written != size0:
            raise OSError(f"{path.name} shrank during rotation ({written} of {size0} bytes read)")
        verified = 0
        with gzip.open(tmp, "rb") as check:
            while True:
                chunk = check.read(_CHUNK)
                if not chunk:
                    break
                verified += len(chunk)
        if verified != size0:
            raise OSError(f"verification of {tmp.name} failed ({verified} of {size0} bytes)")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    # 2. Only now make room: drop the oldest, shift the rest up, publish.
    oldest = _segment(path, keep)
    if oldest.exists():
        oldest.unlink()
    for n in range(keep - 1, 0, -1):
        src = _segment(path, n)
        if src.exists():
            os.replace(src, _segment(path, n + 1))
    os.replace(tmp, target)

    # 3. truncate in place, carrying over whatever was appended during the copy.
    with open(path, "r+b") as live:
        live.seek(size0)
        tail = live.read()
        live.truncate(0)
    if tail:
        # O_APPEND, like the service's own descriptor: lands after anything
        # written since the truncate instead of overwriting it.
        with open(path, "ab") as live:
            live.write(tail)
    return "rotated"


def run(directory: Path, files: tuple[str, ...], *, max_bytes: int, keep: int, dry_run: bool) -> int:
    directory = directory.expanduser()
    if not directory.is_dir():
        print(f"rotate_service_logs: no such directory: {directory}", file=sys.stderr)
        return 1
    # A dry run must leave the directory exactly as it found it, so it takes no lock.
    lock = None if dry_run else open(directory / ".rotate_service_logs.lock", "a")
    try:
        if lock is not None:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("rotate_service_logs: another instance is running; exiting", file=sys.stderr)
                return 0
        failed = 0
        for name in files:
            path = directory / name
            try:
                status = rotate_file(path, max_bytes=max_bytes, keep=keep, dry_run=dry_run)
            except Exception as exc:  # noqa: BLE001 — one bad file must not block the rest
                failed += 1
                print(f"rotate_service_logs: {name}: FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            if status in ("rotated", "dry-run"):
                print(f"rotate_service_logs: {name}: {status}")
        return 1 if failed else 0
    finally:
        if lock is not None:
            lock.close()


def read_env_settings(files: tuple[Path, ...], keys: tuple[str, ...]) -> dict[str, str]:
    """``{key: value}`` for the wanted ``keys`` found in the env ``files`` (later file wins). Parses ``NAME=value`` / ``export NAME=value``
    lines, strips one pair of matching quotes, ignores comments and everything else; unreadable files are skipped. Never executes
    or expands anything."""
    found: dict[str, str] = {}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export") and line[6:7].isspace():
                line = line[6:].lstrip()
            name, sep, value = line.partition("=")
            name = name.strip()
            if not sep or name not in keys:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            found[name] = value
    return found


def apply_env_files() -> None:
    """Fill the retention settings from the zoe-data env files for any name the process environment does not already set."""
    for key, value in read_env_settings(ENV_FILES, SWEEP_ENV_KEYS).items():
        os.environ.setdefault(key, value)


def sweep_text_logs(dry_run: bool) -> int:
    """Apply ``local_text_logs`` retention and expire the owner scripts' one-off backups. Returns 0, or 1 when any part FAILED
    (fail closed: a retention pass that did not run leaves words on disk, so the job must go red, not stay quietly green)."""
    try:
        import importlib.util

        apply_env_files()
        mod_path = Path(__file__).resolve().parents[2] / "services" / "zoe-data" / "local_text_logs.py"
        spec = importlib.util.spec_from_file_location("local_text_logs", mod_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        if dry_run:
            for log in module.LOGS:
                total, with_text = module.count_text(log)
                print(f"rotate_service_logs: text log {log.name}: {with_text}/{total} records carry text, "
                      f"{module.count_malformed(log)} malformed rows would be dropped (dry-run, retention "
                      f"{module.retention_days()} days)")
            return 0
        result = module.sweep()
        swept = {k: v for k, v in result.items() if v}
        if swept:
            print("rotate_service_logs: text-log retention: " + " ".join(f"{k}={v}" for k, v in swept.items()))
        failed = sorted(k for k, v in result.items() if v < 0)
        if failed:
            print(f"rotate_service_logs: text-log retention FAILED for {', '.join(failed)}", file=sys.stderr)
            return 1
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"rotate_service_logs: text-log retention FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default=DEFAULT_DIR, help=f"log directory (default {DEFAULT_DIR})")
    ap.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB, help="rotate at/above this size")
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="compressed segments to retain")
    ap.add_argument("--file", action="append", dest="files", help="file name inside --dir (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-text-sweep", action="store_true", help="skip the utterance-text log retention pass")
    args = ap.parse_args(argv)
    if args.keep < 1 or args.max_mb <= 0:
        ap.error("--keep must be >= 1 and --max-mb > 0")
    files = tuple(args.files) if args.files else DEFAULT_FILES
    status = run(
        Path(args.dir),
        files,
        max_bytes=int(args.max_mb * 1024 * 1024),
        keep=args.keep,
        dry_run=args.dry_run,
    )
    if not args.no_text_sweep and sweep_text_logs(args.dry_run):
        status = 1                      # fail closed, AFTER the rotation above ran
    return status


if __name__ == "__main__":
    sys.exit(main())
