"""No database file may be tracked in this PUBLIC repository.

2026-10-19: a 4.7 MB pre-migration SQLite copy of the household database
(`services/zoe-data/data/zoe.db.pre-migration-20260516`) had been tracked since
2026-05-16. Its name dodged the `*.db` ignore rule. This test looks at file
CONTENT, not names, so a renamed copy cannot slip through again.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
SQLITE_MAGIC = b"SQLite format 3\x00"


def _is_database(head: bytes) -> bool:
    return head.startswith(SQLITE_MAGIC)


def _tracked_files() -> list[Path]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True).stdout
    return [ROOT / p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def test_no_tracked_file_is_a_sqlite_database() -> None:
    files = _tracked_files()
    assert len(files) > 100, "git ls-files returned too little; the scan would be vacuous"
    offenders = []
    for path in files:
        try:
            with open(path, "rb") as fh:
                if _is_database(fh.read(len(SQLITE_MAGIC))):
                    offenders.append(str(path.relative_to(ROOT)))
        except (FileNotFoundError, IsADirectoryError, PermissionError):
            continue
    assert not offenders, (
        f"SQLite database(s) tracked in a PUBLIC repo: {offenders}. Untrack them, keep any needed copy "
        "under ~/.zoe/backups (mode 600), and purge them from history."
    )


def test_detector_negative_control(tmp_path: Path) -> None:
    """The detector must recognise a real SQLite file and ignore ordinary text."""
    import sqlite3

    db = tmp_path / "copy.anything"
    sqlite3.connect(db).execute("CREATE TABLE t (x)").connection.commit()
    assert _is_database(db.read_bytes()[: len(SQLITE_MAGIC)])
    assert not _is_database(b"SQLite format 2 is not this\n")
