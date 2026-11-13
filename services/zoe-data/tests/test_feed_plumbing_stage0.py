"""Feed plumbing Stage 0 (gap audit 2026-11-13, lane D): migration 0078, the account-secret seam, the sync log + generation fence,
member identities, the ``external_source`` authority class and the member erase reaching accounts and calendars.

Synthetic data only, SQLite through the compat-DB shape (``ci_safe``). Negative controls are noted per test.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations

import account_secrets
import calendar_forget
import connected_accounts as ca
import db_compat
import identity_facts
import member_identities as mi
import memory_authority as ma
import user_erase_gate as erase_gate
from db_pool import _Cursor, _ExecResult

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
_DUMMY_URL = "postgresql+psycopg2://u:p@localhost/db"


def run(coro):
    return asyncio.run(coro)


def _migrate(engine, fname, down=False):
    spec = importlib.util.spec_from_file_location(f"mig_{fname[:4]}", SVC / "alembic/versions" / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        (mod.downgrade if down else mod.upgrade)()


class _Db:
    """The compat connection's shape over sqlite3 (execute -> cursor, commit, transaction)."""

    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self._txn = 0

    def execute(self, sql, params=()):
        async def go():
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            if not self._txn:
                self.conn.commit()
            return _Cursor(rows, rowcount=cur.rowcount)
        return _ExecResult(go())

    async def commit(self):
        if not self._txn:
            self.conn.commit()

    @contextlib.asynccontextmanager
    async def transaction(self):
        self._txn += 1
        try:
            yield self
        except BaseException:
            self._txn -= 1
            self.conn.rollback()
            raise
        self._txn -= 1
        if not self._txn:
            self.conn.commit()

    def rows(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "zoe.db")
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, role TEXT)")
        c.exec_driver_sql("CREATE TABLE events (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL, "
                          "visibility TEXT NOT NULL DEFAULT 'family')")
    _migrate(engine, "0052_calendar_data_model.py")
    _migrate(engine, "0078_connected_accounts.py")
    d = _Db(path)
    d.engine = engine

    @contextlib.asynccontextmanager
    async def fake():
        yield d
    monkeypatch.setattr(db_compat, "get_compat_db", fake)
    erase_gate.reset()
    yield d
    erase_gate.reset()


# -- 1. the migration -----------------------------------------------------------------------------------------------------------------

def _cols(d, table):
    return {r[1] for r in d.rows(f"PRAGMA table_info({table})")}


def test_migration_creates_the_spine_reruns_and_downgrades(db):
    _migrate(db.engine, "0078_connected_accounts.py")                       # rerun: a no-op
    assert {"id", "user_id", "provider", "kind", "subject_hash", "label", "scopes", "status", "account_generation", "last_sync_at",
            "last_error_class", "created_at", "updated_at"} == _cols(db, "connected_accounts")
    assert {"n_seen", "n_created", "n_updated", "n_deleted", "cursor_before", "cursor_after", "backoff_until", "outcome",
            "error_class"} <= _cols(db, "sync_runs")
    assert {"user_id", "kind", "value_norm", "verified", "source"} <= _cols(db, "member_identities")
    every = " ".join(_cols(db, t).__repr__() for t in ("connected_accounts", "sync_runs", "member_identities"))
    assert not any(w in every for w in ("token", "secret", "password", "ciphertext"))    # no credential column, anywhere
    with pytest.raises(sqlite3.IntegrityError):                                           # an error is a CLASS, not a message
        db.conn.execute("INSERT INTO connected_accounts (id, user_id, provider, kind, subject_hash, last_error_class, created_at, "
                        "updated_at) VALUES ('a', 'u', 'google', 'calendar', 'h', 'Invalid grant for someone@example.org', 'x', 'x')")
    db.conn.rollback()
    _migrate(db.engine, "0078_connected_accounts.py", down=True)
    left = {r[0] for r in db.rows("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not left & {"connected_accounts", "sync_runs", "member_identities"} and "calendars" in left
    _migrate(db.engine, "0078_connected_accounts.py")                       # and back up again
    assert "account_generation" in _cols(db, "connected_accounts")


def test_postgres_render_and_single_head(monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", _DUMMY_URL)
    buf = io.StringIO()
    cfg = Config(output_buffer=buf, stdout=buf)
    cfg.set_main_option("script_location", str(SVC / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _DUMMY_URL)
    command.upgrade(cfg, "0077:0078", sql=True)
    command.downgrade(cfg, "0078:0077", sql=True)
    sql = buf.getvalue()
    assert "CREATE TABLE IF NOT EXISTS connected_accounts" in sql and "DROP TABLE IF EXISTS connected_accounts" in sql
    from alembic.script import ScriptDirectory
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, heads


# -- 2. the secret seam ---------------------------------------------------------------------------------------------------------------

def test_the_secret_seam_fails_closed_without_a_backend():
    assert not account_secrets.configured()
    with pytest.raises(account_secrets.SecretsUnavailable):
        run(account_secrets.put("acct_1", user_id="ann", purpose="oauth_refresh", secret=b"not-a-real-token"))
    with pytest.raises(account_secrets.SecretsUnavailable):
        run(account_secrets.get("acct_1", user_id="ann", purpose="oauth_refresh"))
    assert run(account_secrets.remove("acct_1")) == 0 and run(account_secrets.forget_user("ann")) == 0
    for bad in ({"purpose": "session_cookie"}, {"user_id": "ann; drop"}):
        with pytest.raises(ValueError):
            run(account_secrets.put("acct_1", **{"user_id": "ann", "purpose": "oauth_refresh", **bad}, secret=b"x"))
    with pytest.raises(ValueError):
        run(account_secrets.put("acct_1", user_id="ann", purpose="oauth_refresh", secret=b""))


class _Spy:
    """Records the seam calls the accounts module makes (patched over the seam's functions; the seam itself takes no backend)."""
    name = "spy"

    def __init__(self, monkeypatch=None):
        self.calls = []
        if monkeypatch is not None:
            monkeypatch.setattr(account_secrets, "remove", self.remove)
            monkeypatch.setattr(account_secrets, "forget_user", self.forget_user)

    async def put(self, *a):
        self.calls.append(("put", a[0]))

    async def get(self, *a):
        raise account_secrets.SecretNotFound(a[0])

    async def remove(self, account_id):
        self.calls.append(("remove", account_id))
        return 1

    async def forget_user(self, user_id):
        self.calls.append(("forget_user", user_id))
        return 2


def test_no_backend_can_be_installed_and_put_forwards_nothing(monkeypatch):
    """Stage 0 refuses unconditionally: there is no setter, and a planted backend is never consulted."""
    spy = _Spy()
    with pytest.raises(account_secrets.SecretsUnavailable):
        account_secrets.configure(spy)
    monkeypatch.setattr(account_secrets, "_backend", spy, raising=False)          # the old module-global: must have no effect
    assert not account_secrets.configured()
    with pytest.raises(account_secrets.SecretsUnavailable):
        run(account_secrets.put("acct_1", user_id="ann", purpose="oauth_refresh", secret=b"not-a-real-token"))
    with pytest.raises(account_secrets.SecretsUnavailable):
        run(account_secrets.get("acct_1", user_id="ann", purpose="oauth_refresh"))
    assert spy.calls == []                                                        # nothing was forwarded anywhere


# -- 3. accounts, the sync log and the generation fence ---------------------------------------------------------------------------

def test_create_is_idempotent_and_stores_no_subject(db):
    a = run(ca.create("ann", "google", "calendar", "Ann.Example@example.org", label="Work", scopes=["calendar.readonly"]))
    assert a and run(ca.create("ann", "google", "calendar", "ann.example@example.org ")) == a
    (row,) = db.rows("SELECT subject_hash, status, account_generation, scopes FROM connected_accounts")
    assert "example" not in row[0] and row[1:] == ("pending", 1, '["calendar.readonly"]')
    with pytest.raises(ValueError):
        run(ca.create("ann", "Google Inc", "calendar", "x"))


def test_a_run_records_counts_and_a_disconnect_fences_the_one_in_flight(db, monkeypatch):
    spy = _Spy(monkeypatch)
    a = run(ca.create("ann", "google", "calendar", "s1"))
    r = run(ca.start_run(a, "ann", scope="calendar:primary", cursor_before="c0"))
    assert run(ca.finish_run(r, outcome="ok", counts={"seen": 3, "created": 2}, cursor_after="c1"))
    assert db.rows("SELECT outcome, n_seen, n_created, cursor_after FROM sync_runs") == [("ok", 3, 2, "c1")]
    assert db.rows("SELECT status, last_error_class FROM connected_accounts")[0] == ("active", None)
    late = run(ca.start_run(a, "ann"))
    assert run(ca.disconnect(a)) == 2 and ("remove", a) in spy.calls          # generation bumped, secrets deleted
    assert run(ca.finish_run(late, outcome="ok", counts={"created": 9}, cursor_after="stale")) is False
    assert db.rows("SELECT outcome, n_created, cursor_after FROM sync_runs WHERE id = ?", (late.run_id,)) == [("fenced", 0, None)]
    assert db.rows("SELECT status FROM connected_accounts") == [("revoked",)]
    assert run(ca.start_run(a, "ann")) is None                                 # a revoked account starts nothing
    assert run(ca.start_run(a, "ben")) is None                                 # nor another member's
    with pytest.raises(ValueError):
        run(ca.finish_run(r, outcome="ok", error_class="Token expired for ann@example.org"))


def test_upsert_by_external_id_is_idempotent_and_honours_the_version(db):
    async def up(etag):
        async with db_compat.get_compat_db() as d:
            return await ca.upsert_by_external_id(
                d, "events", match={"calendar_id": "cal1", "external_uid": "uid-1", "recurrence_id": None},
                values={"id": "ev1", "user_id": "ann", "title": "Practice", "etag": etag}, version_col="etag")
    assert [run(up("e1")), run(up("e1")), run(up("e2"))] == ["created", "unchanged", "updated"]
    assert db.rows("SELECT id, etag FROM events") == [("ev1", "e2")]
    with pytest.raises(ValueError):
        run(ca.upsert_by_external_id(db, "users", match={"id": "x"}, values={"name": "y"}))


# -- 4. member identities -------------------------------------------------------------------------------------------------------------

def test_identities_normalise_and_never_move_between_members(db):
    assert mi.normalise("email", "  Ann@Example.ORG ") == "ann@example.org" and mi.normalise("email", "nope") is None
    assert mi.normalise("phone", "+61 (8) 5550 0123") == "+61855500123" and mi.normalise("phone", "12") is None
    i = run(mi.add("ann", "email", "Ann@example.org"))
    assert i and run(mi.add("ann", "email", "ann@example.org", verified=True)) == i
    assert run(mi.add("ben", "email", "ANN@example.org")) is None              # already Ann's: refused, not moved
    assert run(mi.member_for("email", "ann@EXAMPLE.org")) == "ann" and run(mi.member_for("email", "x@example.org")) is None
    assert db.rows("SELECT user_id, verified FROM member_identities") == [("ann", 1)]


# -- 5. the external_source authority class -------------------------------------------------------------------------------------------

def test_external_source_is_below_the_person_and_carries_a_pointer():
    res = ma.resolve_write("external_source", "User's dentist is Dr Example.", claimed=ma.USER_CONFIRMED)
    assert (res.cls, res.basis, res.authority) == (ma.EXTERNAL_SOURCE, "provider_said", ma.INFERRED)   # cannot claim its way up
    assert ma.writer_class("external_source") == ma.EXTERNAL_SOURCE and ma.is_known_writer("external_source")
    assert identity_facts.is_automatic_source("external_source")              # never writes the member's own name
    for person in (ma.USER_STATED, ma.USER_CONFIRMED):
        assert not ma.may_override(res.power, person)                          # never supersedes what the person said ...
        assert ma.may_override(ma.RANK[person], ma.EXTERNAL_SOURCE)            # ... and is superseded by it
    ref = ma.external_source_ref("contact_card", "acct_1", "people/c123")
    md = ma.provenance("external_source", res, source_ref=ref)
    assert md["authority_class"] == "external_source" and md["source_ref_external_id"] == "people/c123"
    assert {"source_ref_kind", "source_ref_account", "source_ref_external_id"} <= ma.PROVENANCE_KEYS   # an edit never inherits it
    with pytest.raises(ValueError):
        ma.external_source_ref("contact_card", "acct_1", "")
    with pytest.raises(ValueError):                                             # a pointer only on a provider's row
        ma.provenance("voice_fact", ma.resolve_write("voice_fact", "my dentist is Dr Example"), source_ref=ref)


# -- 6. forget reach + the erase-gate race --------------------------------------------------------------------------------------------

def _seed_calendar(d, uid, tag):
    d.conn.execute("INSERT INTO calendars (id, user_id, name, source, source_account_id, sync_cursor, created_at, updated_at) "
                   "VALUES (?, ?, 'Work', 'google', ?, 'cursor', 'x', 'x')", (f"cal-{tag}", uid, f"acct-{tag}"))
    d.conn.execute("INSERT INTO events (id, user_id, title, calendar_id, source, external_uid) VALUES (?, ?, 'Synced', ?, 'google', ?)",
                   (f"ev-sync-{tag}", uid, f"cal-{tag}", f"uid-{tag}"))
    d.conn.execute("INSERT INTO events (id, user_id, title) VALUES (?, ?, 'Local household event')", (f"ev-local-{tag}", uid))
    d.conn.execute("INSERT INTO event_attendees (id, event_id, user_id, display_name, email, created_at, updated_at) "
                   "VALUES (?, ?, ?, 'Guest Example', 'guest@example.org', 'x', 'x')", (f"att-{tag}", f"ev-sync-{tag}", uid))
    d.conn.execute("INSERT INTO event_alarms (id, event_id, user_id, created_at) VALUES (?, ?, ?, 'x')",
                   (f"al-{tag}", f"ev-sync-{tag}", uid))
    d.conn.commit()


def test_forget_me_reaches_accounts_secrets_runs_identities_and_calendars(db, monkeypatch):
    spy = _Spy(monkeypatch)
    for uid in ("ann", "ben"):
        a = run(ca.create(uid, "google", "calendar", f"{uid}-subject"))
        run(ca.finish_run(run(ca.start_run(a, uid)), outcome="ok"))
        run(mi.add(uid, "phone", {"ann": "+61855500112", "ben": "+61855500122"}[uid]))
        _seed_calendar(db, uid, uid)
    run(ca.forget_user("ann"))
    run(mi.forget_user("ann"))
    removed = run(calendar_forget.forget_user("ann"))
    assert ("forget_user", "ann") in spy.calls
    for table in ("connected_accounts", "sync_runs", "member_identities", "calendars", "event_attendees", "event_alarms"):
        assert {r[0] for r in db.rows(f"SELECT user_id FROM {table}")} == {"ben"}, table
    # the synced event and everything in Ann's calendar are gone; her calendar-less local event stays (ledgered owner decision)
    assert {r[0] for r in db.rows("SELECT id FROM events")} == {"ev-local-ann", "ev-sync-ben", "ev-local-ben"}
    assert removed == 4
    # negative control: an unmigrated store is "nothing to erase", not an error
    db.conn.execute("DROP TABLE member_identities")
    assert run(mi.forget_user("ben")) == 0


def test_a_write_scheduled_before_the_erase_is_dropped(db):
    """The race: an account / identity / run write snapshotted before 'forget me' must not land after it. Negative control: the same
    write scheduled AFTER the erase lands."""
    a = run(ca.create("ann", "google", "calendar", "s"))
    before = erase_gate.generation("ann")
    run(ca.forget_user("ann"))
    assert run(ca.create("ann", "google", "mail", "s2", scheduled_at=before)) is None
    assert run(mi.add("ann", "email", "ann@example.org", scheduled_at=before)) is None
    assert run(ca.start_run(a, "ann", scheduled_at=before)) is None
    assert db.rows("SELECT COUNT(*) FROM connected_accounts")[0][0] == 0
    assert db.rows("SELECT COUNT(*) FROM member_identities")[0][0] == 0
    assert run(mi.add("ann", "email", "ann@example.org")) is not None       # scheduled after: allowed

    async def in_flight():
        """A run already open when the member is erased cannot record its result afterwards."""
        acct = await ca.create("ann", "google", "contacts", "s3")
        r = await ca.start_run(acct, "ann")
        await ca.forget_user("ann")
        return await ca.finish_run(r, outcome="ok", counts={"created": 5})
    assert run(in_flight()) is False and db.rows("SELECT COUNT(*) FROM sync_runs")[0][0] == 0


def test_forget_never_deletes_another_members_event_in_my_calendar(db):
    """Ben put an event (with an attendee and an alarm) in a calendar Ann owns. Forgetting Ann removes her calendar and her own
    events; Ben's event is detached and kept, with its attendee and alarm."""
    _seed_calendar(db, "ann", "ann")
    db.conn.execute("INSERT INTO events (id, user_id, title, calendar_id, source) VALUES ('ev-ben-in-ann', 'ben', 'Shared', 'cal-ann', 'local')")
    db.conn.execute("INSERT INTO event_attendees (id, event_id, user_id, display_name, email, created_at, updated_at) "
                    "VALUES ('att-ben', 'ev-ben-in-ann', 'ben', 'Guest Example', 'guest@example.org', 'x', 'x')")
    db.conn.execute("INSERT INTO event_alarms (id, event_id, user_id, created_at) VALUES ('al-ben', 'ev-ben-in-ann', 'ben', 'x')")
    db.conn.commit()
    run(calendar_forget.forget_user("ann"))
    assert db.rows("SELECT user_id, calendar_id FROM events WHERE id = 'ev-ben-in-ann'") == [("ben", None)]
    assert db.rows("SELECT id FROM event_attendees") == [("att-ben",)] and db.rows("SELECT id FROM event_alarms") == [("al-ben",)]
    assert db.rows("SELECT id FROM calendars") == [] and {r[0] for r in db.rows("SELECT id FROM events")} == {"ev-local-ann", "ev-ben-in-ann"}
