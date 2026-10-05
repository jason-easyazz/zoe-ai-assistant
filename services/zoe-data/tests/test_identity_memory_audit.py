"""scripts/maintenance/identity_memory_audit.py — find (and supersede) memory rows that assert
the user's own name/home against the account.

Pinned: the report finds exactly the conflicting rows (a longer form of the real name, other
people's names and other users' rows are not conflicts), it opens the palace READ-ONLY and
touches nothing on --dry-run, --purge is refused without both acknowledgements and while
something listens on :8000, and the purge goes through ``MemoryService.review(edit)`` (the
normal supersede path — never a delete), for NAME rows only, optionally limited by --only.
Synthetic palace and names (ci_safe)."""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "identity_memory_audit.py"
spec = importlib.util.spec_from_file_location("identity_memory_audit", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

WRONG = "Mika Vale"
ACCOUNTS = {
    "accounts": [
        {"user_id": "member-a", "username": "zed", "settings": "{}", "users_name": "zed",
         "prefs": None, "wp_city": "Hobart", "wp_country": "AU"},
        {"user_id": "member-b", "username": "ola", "settings": "{}", "users_name": "ola",
         "prefs": json.dumps({"preferred_name": "lala"}), "wp_city": "", "wp_country": ""},
    ],
    "sysloc": {"city": "Hobart", "country": "AU", "timezone": "Australia/Hobart"},
}
ROWS = [  # (embedding_id, user, text, status, extra metadata)
    ("m1", "member-a", f"User's name is {WRONG}.", "approved", {"source": "chat_regex", "reviewed_by": "digest",
                                                               "session_id": "tg-1", "added_at": "2026-08-11T19:00:02Z"}),
    ("m2", "member-a", "User's name is Zed.", "approved", {}),
    ("m3", "member-a", "User's full name is Zed Quill.", "approved", {}),
    ("m4", "member-a", f"User's name is {WRONG}.", "superseded", {}),          # not live
    ("m5", "member-a", "Marisol's name is Mika Vale.", "approved", {}),          # someone else
    ("m6", "member-a", "User lives in Perth.", "approved", {}),                  # home conflict
    ("m7", "member-a", "User lives in Hobart.", "approved", {}),
    ("m8", "member-b", "User's name is Lala.", "approved", {}),                  # preferred name
    ("m9", "member-b", f"User's name is {WRONG}.", "approved", {}),
    ("m10", "member-zz", f"User's name is {WRONG}.", "approved", {}),            # not an account
]


@pytest.fixture
def palace(tmp_path):
    p = tmp_path / "palace"
    p.mkdir()
    con = sqlite3.connect(p / "chroma.sqlite3")
    con.executescript("""
        CREATE TABLE collections (id TEXT, name TEXT);
        CREATE TABLE segments (id TEXT, scope TEXT, collection TEXT);
        CREATE TABLE embeddings (id INTEGER PRIMARY KEY, segment_id TEXT, embedding_id TEXT);
        CREATE TABLE embedding_metadata (id INTEGER, key TEXT, string_value TEXT, int_value INTEGER, float_value REAL, bool_value INTEGER);
        INSERT INTO collections VALUES ('c-d', 'mempalace_drawers'), ('c-a', 'mempalace_audit');
        INSERT INTO segments VALUES ('s-d', 'METADATA', 'c-d'), ('s-dv', 'VECTOR', 'c-d'), ('s-a', 'METADATA', 'c-a');
    """)
    for n, (eid, user, text, status, extra) in enumerate(ROWS, 1):
        con.execute("INSERT INTO embeddings VALUES (?, 's-d', ?)", (n, eid))
        meta = {"chroma:document": text, "user_id": user, "status": status, **extra}
        for k, v in meta.items():
            con.execute("INSERT INTO embedding_metadata VALUES (?, ?, ?, NULL, NULL, NULL)", (n, k, v))
    # a row in ANOTHER collection with a conflicting name must be ignored
    con.execute("INSERT INTO embeddings VALUES (99, 's-a', 'audit-1')")
    for k, v in {"chroma:document": f"User's name is {WRONG}.", "user_id": "member-a", "status": "approved"}.items():
        con.execute("INSERT INTO embedding_metadata VALUES (99, ?, ?, NULL, NULL, NULL)", (k, v))
    con.commit()
    con.close()
    return p


@pytest.fixture
def accounts_file(tmp_path):
    f = tmp_path / "accounts.json"
    f.write_text(json.dumps(ACCOUNTS))
    return str(f)


def _conflicts(palace, accounts_file):
    idents = audit.load_identities("unused", accounts_file=accounts_file)
    return audit.find_conflicts(idents, audit.load_memory_rows(str(palace)))


def test_finds_exactly_the_conflicting_live_rows(palace, accounts_file):
    got = {(c["id"], c["kind"]) for c in _conflicts(palace, accounts_file)}
    assert got == {("m1", "name"), ("m6", "home"), ("m9", "name")}


def test_report_carries_provenance_for_the_reviewer(palace, accounts_file):
    c = next(c for c in _conflicts(palace, accounts_file) if c["id"] == "m1")
    assert (c["source"], c["reviewed_by"], c["session_id"], c["added_at"]) == (
        "chat_regex", "digest", "tg-1", "2026-08-11T19:00:02Z")
    assert c["account_name"] == "Zed"


def test_dry_run_is_read_only_and_redaction_hides_names(palace, accounts_file, capsys):
    before = hashlib.sha256((palace / "chroma.sqlite3").read_bytes()).hexdigest()
    assert audit.main(["--dry-run", "--redact", "--palace", str(palace), "--accounts-file", accounts_file]) == 0
    out = capsys.readouterr().out
    assert "m1" in out and "m9" in out and "m6" in out
    assert WRONG not in out and "Zed" not in out
    assert hashlib.sha256((palace / "chroma.sqlite3").read_bytes()).hexdigest() == before


def test_user_filter(palace, accounts_file, capsys):
    audit.main(["--dry-run", "--json", "--user", "member-b", "--palace", str(palace), "--accounts-file", accounts_file])
    assert [c["id"] for c in json.loads(capsys.readouterr().out)] == ["m9"]


def test_clean_palace_says_so(palace, accounts_file, capsys):
    con = sqlite3.connect(palace / "chroma.sqlite3")
    con.execute("UPDATE embedding_metadata SET string_value = 'superseded' WHERE key = 'status'")
    con.commit()
    con.close()
    audit.main(["--dry-run", "--palace", str(palace), "--accounts-file", accounts_file])
    assert "No memory row asserts" in capsys.readouterr().out


@pytest.mark.parametrize("argv,why", [
    ([], "no mode"),
    (["--purge"], "no review ack"),
    (["--purge", "--i-have-reviewed"], "no stop ack"),
    (["--purge", "--i-stopped-zoe-data"], "no review ack"),
])
def test_refusals(argv, why, palace, accounts_file, capsys):
    assert audit.main([*argv, "--palace", str(palace), "--accounts-file", accounts_file]) == 2, why
    assert "refused" in capsys.readouterr().err


def test_purge_refused_while_something_listens_on_8000(palace, accounts_file, monkeypatch, capsys):
    monkeypatch.setattr(audit, "_service_listening", lambda *a, **k: True)
    argv = ["--purge", "--i-have-reviewed", "--i-stopped-zoe-data",
            "--palace", str(palace), "--accounts-file", accounts_file]
    assert audit.main(argv) == 2 and "listening" in capsys.readouterr().err


class FakeSvc:
    def __init__(self):
        self.calls = []

    async def review(self, mem_id, **kw):
        self.calls.append((mem_id, kw))

        class Ref:
            id = f"new-{mem_id}"
        return Ref()


def test_purge_supersedes_name_rows_only_through_review_edit(palace, accounts_file):
    idents = audit.load_identities("unused", accounts_file=accounts_file)
    conflicts = audit.find_conflicts(idents, audit.load_memory_rows(str(palace)))
    svc = FakeSvc()
    res = asyncio.run(audit.purge(conflicts, idents, svc=svc))
    assert sorted(c[0] for c in svc.calls) == ["m1", "m9"]       # m6 (home) is never purged
    for mem_id, kw in svc.calls:
        assert kw["decision"] == "edit" and kw["actor"] == "identity_audit"  # supersede, not delete
    assert dict(svc.calls)["m1"]["edits"] == "User's name is Zed."
    assert dict(svc.calls)["m9"]["edits"] == "User's name is Ola."  # account name, not the nickname override
    assert all(r["ok"] for r in res)


def test_purge_only_limits_the_rows(palace, accounts_file):
    idents = audit.load_identities("unused", accounts_file=accounts_file)
    conflicts = audit.find_conflicts(idents, audit.load_memory_rows(str(palace)))
    svc = FakeSvc()
    asyncio.run(audit.purge(conflicts, idents, only={"m9"}, svc=svc))
    assert [c[0] for c in svc.calls] == ["m9"]


def test_the_purged_edit_passes_the_writer_wall(palace, accounts_file):
    """The audit's own replacement row must not be eaten by the guard it ships with."""
    import identity_facts as idf

    assert "identity_audit" not in idf.AUTOMATIC_SOURCES
    assert idf.is_user_name_assertion("User's name is Zed.")  # it IS an assertion: only the actor lets it through
