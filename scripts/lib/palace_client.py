"""Open the MemPalace chroma store only with a client that matches its on-disk format.

B0.8 (2026-09-27) moved the palace to chromadb 1.5.x. The change is ONE-WAY in both
directions that matter:

* a 0.6.3 client on the 1.x palace fails on open (``KeyError: '_type'``). That failure is
  loud, but the process is still dead;
* a 1.x client on a 0.6 palace (for example the rollback snapshot, or a restored backup)
  silently migrates its sysdb IN PLACE on first open, so the snapshot stops being a
  rollback.

The system Python 3.10 site deliberately keeps chromadb 0.6.3 (it hosts the CUDA/Kokoro
lane), while the zoe-data py3.12 venv carries 1.5.x. So every hand-run or timer script
that opens the store goes through ``open_palace_client``. It reads the format from
SQLite (``mode=ro``, no chromadb) and refuses a mismatched client before chromadb can
touch the file.

Import convention (scripts/ is not a package)::

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
    from palace_client import open_palace_client
"""
from __future__ import annotations

import os
import sqlite3

VENV_PYTHON = os.path.expanduser("~/.zoe/venvs/zoe-data-py312/bin/python")
# chromadb 1.x adds sysdb migration 00010 (collection schema); 0.6.x stops at 00009.
_SYSDB_1X = 10


def palace_format(palace_dir: str) -> str:
    """'1.x' | '0.6' | 'missing' | 'unknown' from the palace's own migrations table (read-only)."""
    db = os.path.join(os.path.expanduser(palace_dir), "chroma.sqlite3")
    if not os.path.exists(db):
        return "missing"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT max(version) FROM migrations WHERE dir = 'sysdb'").fetchone()
    except sqlite3.OperationalError as exc:
        # An EXISTING chroma.sqlite3 whose format cannot be read (no migrations table, partial
        # restore, unknown schema) must fail closed: a 1.x client would otherwise initialise or
        # migrate it in place before the guard has identified it. Only a missing DB is "new".
        raise RuntimeError(
            f"palace {palace_dir} has a chroma.sqlite3 whose format cannot be identified ({exc}); "
            "refusing to open it (see docs/knowledge/chroma-1-5-migration.md)"
        ) from exc
    finally:
        con.close()
    return "1.x" if row and row[0] is not None and int(row[0]) >= _SYSDB_1X else "0.6"


def client_major(version: str) -> str:
    return "1.x" if int(str(version).split(".")[0]) >= 1 else "0.6"


def check_client(palace_dir: str, chromadb_version: str) -> None:
    """Raise SystemExit when the client and the on-disk format disagree."""
    fmt = palace_format(palace_dir)
    if fmt in ("missing", "unknown") or not chromadb_version:
        return
    have = client_major(chromadb_version)
    if have != fmt:
        raise SystemExit(
            f"REFUSED: palace {palace_dir} is chromadb {fmt} format but this interpreter has "
            f"chromadb {chromadb_version}. "
            + (f"Run it with {VENV_PYTHON}." if fmt == "1.x" else
               "A 1.x client would migrate this 0.6 palace IN PLACE (one-way); use a 0.6.3 client.")
        )


def open_palace_client(palace_dir: str):
    import chromadb  # noqa: PLC0415 — only after the format check can it be trusted

    check_client(palace_dir, chromadb.__version__)
    return chromadb.PersistentClient(path=os.path.expanduser(palace_dir))
