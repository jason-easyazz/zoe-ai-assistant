"""first_boot_probe: reading first_boot.Facts from the real tables, on a scratch SQLite store (columns as the migrations name them)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

import first_boot as fb
import first_boot_probe as probe
import speaker_models

pytestmark = pytest.mark.ci_safe

NOW = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
OWNER = "owner-a"


SCHEMA = (
    "CREATE TABLE auth_users (user_id TEXT PRIMARY KEY, username TEXT, role TEXT NOT NULL, password_hash TEXT, created_at TEXT, "
    "is_active INTEGER DEFAULT 1)",
    "CREATE TABLE passcodes (user_id TEXT, is_active INTEGER DEFAULT 1, expires_at TEXT)",
    "CREATE TABLE panels (panel_id TEXT PRIMARY KEY, name TEXT NOT NULL, room_id TEXT, is_active INTEGER DEFAULT 1)",
    "CREATE TABLE device_tokens (panel_id TEXT, revoked INTEGER DEFAULT 0, expires_at TEXT)",
    "CREATE TABLE rooms (id TEXT PRIMARY KEY, name TEXT, slug TEXT)",
    "CREATE TABLE speaker_profiles (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, sample_count INTEGER DEFAULT 0, enrolled_at TEXT, "
    "consent_at TEXT, model_id TEXT)",
    "CREATE TABLE face_profiles (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, active INTEGER DEFAULT 1)",
    "CREATE TABLE enrollment_offers (user_id TEXT NOT NULL, modality TEXT NOT NULL, last_offered_at TEXT, offer_count INTEGER "
    "NOT NULL DEFAULT 0, pending_since TEXT, ignored_count INTEGER NOT NULL DEFAULT 0, accepted_at TEXT, declined_at TEXT, "
    "declined_until TEXT, updated_at TEXT NOT NULL, PRIMARY KEY (user_id, modality))",
    "CREATE TABLE push_subscriptions (user_id TEXT, endpoint TEXT)",
    "CREATE TABLE user_preferences (user_id TEXT PRIMARY KEY, prefs TEXT)",
    "CREATE TABLE phone_devices (id TEXT PRIMARY KEY, user_id TEXT, revoked INTEGER DEFAULT 0)",
    "CREATE TABLE reminders (id TEXT PRIMARY KEY, user_id TEXT, is_active INTEGER DEFAULT 1, deleted INTEGER DEFAULT 0)",
    "CREATE TABLE lists (id TEXT PRIMARY KEY, user_id TEXT, deleted INTEGER DEFAULT 0)",
    "CREATE TABLE member_modes (user_id TEXT PRIMARY KEY, mode TEXT, minor INTEGER DEFAULT 0)",
    "CREATE TABLE connected_accounts (id TEXT PRIMARY KEY, user_id TEXT, kind TEXT, status TEXT)",
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("ZOE_TIMEZONE", raising=False)
    monkeypatch.delenv("ZOE_HOUSEHOLD_COUNTRY", raising=False)
    monkeypatch.delenv("ZOE_LOCATION_COUNTRY", raising=False)
    monkeypatch.delenv("ZOE_FORGET_LEDGER_SALT", raising=False)
    path = str(tmp_path / "scratch.db")

    async def make():
        async with aiosqlite.connect(path) as db:
            for ddl in SCHEMA:
                await db.execute(ddl)
            await db.commit()

    asyncio.run(make())
    return path


def gather(path, sql=(), **kw) -> fb.Facts:
    async def go():
        async with aiosqlite.connect(path) as db:
            db.row_factory = aiosqlite.Row
            for stmt in sql:
                await db.execute(*stmt) if isinstance(stmt, tuple) else await db.execute(stmt)
            await db.commit()
            return await probe.gather(db=db, now=NOW, **kw)

    return asyncio.run(go())


def test_probe_blank_unit(store, tmp_path):
    f = gather(store, backup_dir=str(tmp_path / "none"))
    assert f.owner_account is False and f.panel_paired is False and f.forget_secret is False
    assert f.time_and_place is False and f.backup_target is False and f.child_marked == fb.NA
    assert fb.next_step(f).id == "owner_account"


def test_probe_pending_owner_is_not_an_owner(store, tmp_path):
    f = gather(store, [f"INSERT INTO auth_users VALUES ('{OWNER}', 'o', 'admin', 'SETUP_REQUIRED', '1', 1)"],
               backup_dir=str(tmp_path))
    assert f.owner_account is False


def test_probe_a_set_up_household(store, tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_TIMEZONE", "Europe/Dublin")
    monkeypatch.setenv("ZOE_FORGET_LEDGER_SALT", "x" * 32)
    (tmp_path / "offbox.env").write_text("# placeholder\n")
    rows = [
        f"INSERT INTO auth_users VALUES ('{OWNER}', 'o', 'admin', 'hash', '1', 1)",
        "INSERT INTO auth_users VALUES ('kid-b', 'k', 'child', 'hash', '2', 1)",
        "INSERT INTO member_modes VALUES ('kid-b', 'kid', 1)",
        f"INSERT INTO passcodes VALUES ('{OWNER}', 1, NULL)",
        "INSERT INTO rooms VALUES ('r1', 'Kitchen', 'kitchen')",
        "INSERT INTO panels VALUES ('panel-x1', 'Kitchen panel', 'r1', 1)",
        "INSERT INTO device_tokens VALUES ('panel-x1', 0, NULL)",
        ("INSERT INTO speaker_profiles VALUES ('s1', ?, 3, '2026-11-14 09:00:00', '2026-11-14 09:00:00', ?)",
         (OWNER, speaker_models.RESNET34_MODEL_ID)),
        f"INSERT INTO user_preferences VALUES ('{OWNER}', '{{\"telegram_id\": \"1\"}}')",
        f"INSERT INTO reminders VALUES ('m1', '{OWNER}', 1, 0)",
        f"INSERT INTO connected_accounts VALUES ('c1', '{OWNER}', 'calendar', 'active')",
    ]
    f = gather(store, rows, backup_dir=str(tmp_path))
    assert f.owner_account and f.forget_secret and f.panel_paired and f.panel_named and f.owner_pin and f.owner_voice
    assert f.time_and_place, "an explicit zone that household_locale maps to a country"
    assert f.phone_linked and f.backup_target and f.first_win and f.calendar and f.more_members
    assert f.child_marked is True and f.music is False
    assert fb.ready(f)


def test_probe_default_panel_name_and_missing_room(store, tmp_path):
    base = [f"INSERT INTO auth_users VALUES ('{OWNER}', 'o', 'admin', 'hash', '1', 1)",
            "INSERT INTO panels VALUES ('panel-ab12', 'Panel-AB12', NULL, 1)", "INSERT INTO device_tokens VALUES ('panel-ab12', 0, NULL)"]
    f = gather(store, base, backup_dir=str(tmp_path))
    assert f.panel_paired is True and f.panel_named is False
    g = gather(store, ["UPDATE panels SET name = 'Hall', room_id = 'gone'"], backup_dir=str(tmp_path))
    assert g.panel_named is False                         # a room id that no longer resolves is not "placed"


def test_probe_revoked_token_is_not_paired(store, tmp_path):
    f = gather(store, ["INSERT INTO panels VALUES ('p1', 'Hall', NULL, 1)", "INSERT INTO device_tokens VALUES ('p1', 1, NULL)"],
               backup_dir=str(tmp_path))
    assert f.panel_paired is False


def test_probe_child_role_without_the_minor_flag_is_not_marked(store, tmp_path):
    f = gather(store, [f"INSERT INTO auth_users VALUES ('{OWNER}', 'o', 'admin', 'hash', '1', 1)",
                       "INSERT INTO auth_users VALUES ('kid-b', 'k', 'child', NULL, '2', 1)"], backup_dir=str(tmp_path))
    assert f.child_marked is False


def test_probe_a_missing_table_is_unknown_not_no(store, tmp_path):
    f = gather(store, [f"INSERT INTO auth_users VALUES ('{OWNER}', 'o', 'admin', 'hash', '1', 1)", "DROP TABLE passcodes",
                       "DROP TABLE push_subscriptions", "DROP TABLE user_preferences", "DROP TABLE phone_devices"],
               backup_dir=str(tmp_path))
    assert f.owner_pin is None and f.phone_linked is None
    assert f.owner_account is True


def test_probe_defer_round_trip_and_expiry(store, tmp_path):
    seed = [f"INSERT INTO auth_users VALUES ('{OWNER}', 'o', 'admin', 'hash', '1', 1)"]

    async def go():
        async with aiosqlite.connect(store) as db:
            for s in seed:
                await db.execute(s)
            await db.commit()
            assert await probe.defer(OWNER, "backup_target", days=7, now=NOW, db=db)
            assert not await probe.defer(OWNER, "not_an_item", now=NOW, db=db)
            inside = await probe.gather(db=db, now=NOW + timedelta(days=6), backup_dir=str(tmp_path))
            after = await probe.gather(db=db, now=NOW + timedelta(days=8), backup_dir=str(tmp_path))
            async with db.execute("SELECT modality FROM enrollment_offers") as cur:
                modalities = [r[0] for r in await cur.fetchall()]
            return inside, after, modalities

    inside, after, modalities = asyncio.run(go())
    assert inside.deferred == frozenset({"backup_target"}) and after.deferred == frozenset()
    assert modalities == ["setup:backup_target"]          # never 'voice': the enrolment offer policy cannot see it


@pytest.mark.parametrize("panel_id", [None, "p1"])
@pytest.mark.parametrize("tokens, paired", [
    ([(0, (NOW - timedelta(seconds=1)).isoformat())], False),
    ([(0, NOW.isoformat())], False),
    ([(0, (NOW + timedelta(seconds=1)).isoformat())], True),
    ([(0, None)], True),
    ([(1, (NOW + timedelta(days=1)).isoformat())], False),
    ([(0, (NOW - timedelta(days=1)).isoformat()), (0, (NOW + timedelta(days=1)).isoformat())], True),
    ([(0, "unreadable")], False),
    ([(0, "2026-11-14 10:00:01")], True),
    ([(0, "2026-11-14T11:00:00+01:00")], False),
], ids=["expired", "expires-now", "valid", "no-expiry", "revoked", "expired-and-valid", "unreadable",
        "naive-utc", "offset-expires-now"])
def test_probe_panel_requires_a_valid_token(store, tmp_path, panel_id, tokens, paired):
    rows = ["INSERT INTO rooms VALUES ('r1', 'Hall', 'hall')",
            "INSERT INTO panels VALUES ('p1', 'Hall', 'r1', 1)"]
    rows.extend(("INSERT INTO device_tokens VALUES ('p1', ?, ?)", token) for token in tokens)
    f = gather(store, rows, panel_id=panel_id, backup_dir=str(tmp_path))
    assert f.panel_paired is paired
    assert f.panel_named is paired
    named = next(s for s in fb.evaluate(f) if s.id == "panel_named")
    assert (named.state == fb.DONE) is paired


@pytest.mark.parametrize("expires_at, usable", [(None, True), ("2999-01-01T00:00:00", True), ("2000-01-01T00:00:00", False),
                                                ("not a date", False)])
def test_probe_an_expired_owner_pin_is_not_a_pin(store, tmp_path, expires_at, usable):
    """zoe-auth refuses a passcode past its expires_at (``verify_passcode``), so the checklist must not mark the PIN step done."""
    f = gather(store, [f"INSERT INTO auth_users VALUES ('{OWNER}', 'o', 'admin', 'hash', '1', 1)",
                       (f"INSERT INTO passcodes VALUES ('{OWNER}', 1, ?)", (expires_at,))], backup_dir=str(tmp_path))
    assert f.owner_pin is usable


def test_probe_a_deleted_reminder_is_not_a_first_win(store, tmp_path):
    """``delete_reminder`` sets ``deleted`` and leaves ``is_active``: the owner's only (deleted) reminder is not a first win."""
    f = gather(store, [f"INSERT INTO auth_users VALUES ('{OWNER}', 'o', 'admin', 'hash', '1', 1)",
                       f"INSERT INTO reminders VALUES ('m-del', '{OWNER}', 1, 1)"], backup_dir=str(tmp_path))
    assert f.first_win is False


def test_probe_one_failed_phone_channel_is_unknown_not_unlinked(store, tmp_path):
    """Two channels read "not linked" and one read FAILS: the owner may be linked through the failed one, so the step is unknown."""
    f = gather(store, [f"INSERT INTO auth_users VALUES ('{OWNER}', 'o', 'admin', 'hash', '1', 1)", "DROP TABLE phone_devices"],
               backup_dir=str(tmp_path))
    assert f.phone_linked is None
