#!/usr/bin/env python3
"""Size-based rotation for the logs systemd appends to (``StandardOutput=append:``).

WHY THIS EXISTS
    systemd's ``append:`` target never rotates. ``zoe-data.service`` (via the
    host drop-in ``20-capture-output.conf``) appends the service's stdout and
    stderr to ``~/.zoe-logs/zoe-data.{stdout,stderr}.log`` and nothing ever
    trims them: 2026-10-04 they stood at 86 MB and 119 MB, a day after the last
    manual gzip, and the 2026-09-25 review had already found a 445 MB stdout
    log. The in-process app log is rotated by ``logging_setup.py``; these two
    streams are written by uvicorn/systemd and cannot be. logrotate is not
    installed on this host and the user manager cannot run a system timer, so
    this stdlib script runs from a user timer (``zoe-log-rotate.timer``).

HOW IT ROTATES (copytruncate, loss-minimal)
    systemd opens the file ``O_APPEND`` and keeps the descriptor for the life of
    the unit, so renaming the file would leave the service writing into the
    renamed inode. Instead: stream-gzip the first ``size`` bytes to
    ``<name>.1.gz`` (older segments shift to ``.2.gz`` ... and fall off after
    ``--keep``), then truncate the live file in place. Bytes appended while the
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
_NEVER = {"zoe-data.app.log"}


def _segment(path: Path, n: int) -> Path:
    return path.with_name(f"{path.name}.{n}.gz")


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

    # 1. shift older segments up; the oldest falls off the end.
    oldest = _segment(path, keep)
    if oldest.exists():
        oldest.unlink()
    for n in range(keep - 1, 0, -1):
        src = _segment(path, n)
        if src.exists():
            os.replace(src, _segment(path, n + 1))

    # 2. stream-gzip exactly the first size0 bytes into a temp file, then publish.
    target = _segment(path, 1)
    tmp = target.with_name(target.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    try:
        with os.fdopen(fd, "wb") as raw, gzip.GzipFile(
            filename=path.name, mode="wb", fileobj=raw, compresslevel=6
        ) as gz, open(path, "rb") as src_f:
            remaining = size0
            while remaining > 0:
                chunk = src_f.read(min(_CHUNK, remaining))
                if not chunk:
                    break
                gz.write(chunk)
                remaining -= len(chunk)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default=DEFAULT_DIR, help=f"log directory (default {DEFAULT_DIR})")
    ap.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB, help="rotate at/above this size")
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="compressed segments to retain")
    ap.add_argument("--file", action="append", dest="files", help="file name inside --dir (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.keep < 1 or args.max_mb <= 0:
        ap.error("--keep must be >= 1 and --max-mb > 0")
    files = tuple(args.files) if args.files else DEFAULT_FILES
    return run(
        Path(args.dir),
        files,
        max_bytes=int(args.max_mb * 1024 * 1024),
        keep=args.keep,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
