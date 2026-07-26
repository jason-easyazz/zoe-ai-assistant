"""Ownership scoping for biometric profiles (`biometric_scope.py`).

`_require_voice_auth` answers "may this caller reach the voice surface at
all" — device token OR any non-guest session. It is an AUTHENTICATION gate.
Until this module existed there was no AUTHORISATION half, so on a shared
household LAN any signed-in member could:

1. DELETE another member's voiceprint or faceprint (`DELETE ... WHERE id=?`
   with no user scoping) — a silent, unrecoverable wipe of someone else's
   biometric enrolment;
2. LIST every member's profiles, including who has consented;
3. ENROL their own voice/face under ANOTHER member's `user_id`, because the
   routers took `payload["user_id"]` verbatim — identity takeover of the
   whole biometric layer, not merely a bad row.

What this file pins:

- a non-owner CANNOT delete someone else's profile, and no DELETE reaches
  the DB (403, after the row proves the caller is not the owner);
- an owner CAN delete their own;
- an unknown id is 404, NOT 403 — 404-before-403, the order
  `routers/proactive.py` `delete_schedule` and `routers/lists.py` use;
- an admin may act household-wide, EXPLICITLY (`auth.is_admin_role`, which
  honours the `family-admin` alias and is fail-closed on anything else);
- a device token owns nothing: it is a shared panel credential, so it can
  neither list nor delete (its legitimate feed is `/profiles/sync`);
- listing is scoped in the SQL, not post-filtered;
- enrol targets the caller unless the caller is an admin, while a device
  token still enrols on behalf of the person standing at the panel.

No models, no network, no live DB — the compat-DB context manager is faked,
so this runs in the slim GitHub ``ci_safe`` lane.
"""

from __future__ import annotations

import base64
import contextlib
import sys
import types

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import routers.face_id as face_id
import routers.voice_tts as voice_tts
from auth import is_admin_role
from biometric_scope import (
    authorize_profile_access,
    require_person_scope,
    resolve_enroll_target,
)

DEVICE = {"source": "device", "panel_id": "zoe-touch-pi", "user_id": "voice-daemon"}
OWNER = {"source": "session", "user_id": "jason", "role": "user"}
OTHER = {"source": "session", "user_id": "zoe-kid", "role": "user"}
ADMIN = {"source": "session", "user_id": "parent", "role": "admin"}
FAMILY_ADMIN = {"source": "session", "user_id": "parent", "role": "family-admin"}

EMB_512 = b"\x01\x02\x03\x04" * 512


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __await__(self):
        async def _done():
            return self

        return _done().__await__()


class _FakeDB:
    """Compat-DB double: records queries+params, serves canned rows."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.queries: list[str] = []
        self.params: list[tuple] = []

    def execute(self, sql, params=()):
        self.queries.append(sql)
        self.params.append(tuple(params))
        return _FakeCursor(self.rows)

    async def commit(self):
        return None


def _install_fake_db(monkeypatch, rows=()):
    db = _FakeDB(rows)

    @contextlib.asynccontextmanager
    async def fake_ctx():
        yield db

    mod = types.ModuleType("db_compat")
    mod.get_compat_db = fake_ctx
    monkeypatch.setitem(sys.modules, "db_compat", mod)
    return db


def _deletes(db) -> list[str]:
    return [q for q in db.queries if q.strip().upper().startswith("DELETE")]


@pytest.fixture
def face_enabled(monkeypatch):
    monkeypatch.setenv("ZOE_FACE_ID_ENABLED", "true")


# Both biometric routers are driven through the SAME table of cases so the
# speaker and face surfaces cannot drift apart. `owned_by` is the row the
# fake DB returns for the ownership SELECT.
def _delete_face(profile_id, caller):
    return face_id.face_profile_delete(profile_id, caller=caller)


def _delete_voice(profile_id, caller):
    return voice_tts.voice_profile_delete(profile_id, caller=caller)


DELETERS = [pytest.param(_delete_face, id="face"), pytest.param(_delete_voice, id="speaker")]


# ── 1. THE BUG: a non-owner must not delete someone else's biometrics ──────

@pytest.mark.parametrize("delete", DELETERS)
async def test_non_owner_cannot_delete_another_users_profile(monkeypatch, face_enabled, delete):
    db = _install_fake_db(monkeypatch, rows=[("jason",)])  # profile belongs to jason

    with pytest.raises(HTTPException) as exc:
        await delete("pid-1", dict(OTHER))  # a different signed-in household member

    assert exc.value.status_code == 403
    # The security property is not the status code — it is that nothing was deleted.
    assert _deletes(db) == []


@pytest.mark.parametrize("delete", DELETERS)
async def test_owner_can_delete_their_own_profile(monkeypatch, face_enabled, delete):
    db = _install_fake_db(monkeypatch, rows=[("jason",)])

    out = await delete("pid-1", dict(OWNER))

    assert out["ok"] is True and out["deleted"] == "pid-1"
    assert len(_deletes(db)) == 1
    assert db.params[-1] == ("pid-1",)


@pytest.mark.parametrize("delete", DELETERS)
async def test_unknown_profile_is_404_not_403(monkeypatch, face_enabled, delete):
    db = _install_fake_db(monkeypatch, rows=[])  # no such row

    with pytest.raises(HTTPException) as exc:
        await delete("nope", dict(OWNER))

    assert exc.value.status_code == 404
    assert _deletes(db) == []


@pytest.mark.parametrize("delete", DELETERS)
@pytest.mark.parametrize("admin", [ADMIN, FAMILY_ADMIN], ids=["admin", "family-admin"])
async def test_admin_may_delete_household_wide(monkeypatch, face_enabled, delete, admin):
    db = _install_fake_db(monkeypatch, rows=[("jason",)])

    out = await delete("pid-1", dict(admin))

    assert out["ok"] is True
    assert len(_deletes(db)) == 1


@pytest.mark.parametrize("delete", DELETERS)
async def test_device_token_cannot_delete_anyones_profile(monkeypatch, face_enabled, delete):
    """A device token is a SHARED panel credential, not a person.

    Nothing on the Pi calls delete — the daemons only pull `/profiles/sync` —
    so refusing it closes a hole without removing a capability.
    """
    db = _install_fake_db(monkeypatch, rows=[("jason",)])

    with pytest.raises(HTTPException) as exc:
        await delete("pid-1", dict(DEVICE))

    assert exc.value.status_code == 403
    assert db.queries == []  # rejected before the DB is touched at all


# ── 2. listing is scoped too — who is enrolled is biometric metadata ───────

async def test_face_list_is_sql_scoped_to_the_caller(monkeypatch, face_enabled):
    db = _install_fake_db(monkeypatch, rows=[])
    await face_id.face_profiles(caller=dict(OTHER))
    assert "user_id=?" in db.queries[0]
    assert db.params[0] == ("zoe-kid",)


async def test_voice_list_is_sql_scoped_to_the_caller(monkeypatch):
    db = _install_fake_db(monkeypatch, rows=[])
    await voice_tts.voice_profiles(caller=dict(OTHER))
    assert "user_id=?" in db.queries[0]
    assert db.params[0] == ("zoe-kid",)


async def test_admin_list_is_household_wide(monkeypatch, face_enabled):
    db = _install_fake_db(monkeypatch, rows=[])
    await face_id.face_profiles(caller=dict(ADMIN))
    assert "user_id=?" not in db.queries[0]
    assert db.params[0] == ()


@pytest.mark.parametrize("lister", [
    pytest.param(lambda c: face_id.face_profiles(caller=c), id="face"),
    pytest.param(lambda c: voice_tts.voice_profiles(caller=c), id="speaker"),
])
async def test_device_token_cannot_enumerate_profiles(monkeypatch, face_enabled, lister):
    db = _install_fake_db(monkeypatch, rows=[])
    with pytest.raises(HTTPException) as exc:
        await lister(dict(DEVICE))
    assert exc.value.status_code == 403
    assert db.queries == []


# ── 3. enrolment may not be aimed at another household member ─────────────

def _face_payload(**over):
    p = {
        "embedding_base64": base64.b64encode(EMB_512).decode(),
        "user_id": "jason",
        "display_name": "Jason",
        "model_name": "buffalo_sc/w600k_mbf",
        "dim": 512,
        "consent": True,
    }
    p.update(over)
    return p


async def test_session_caller_cannot_enroll_under_another_users_id(monkeypatch, face_enabled):
    """The impersonation vector: enrol MY face under "jason" and the panel greets me as Jason."""
    db = _install_fake_db(monkeypatch, rows=[])

    out = await face_id.face_enroll(_face_payload(user_id="jason"), caller=dict(OTHER))

    assert out["user_id"] == "zoe-kid"  # forced back to the caller
    insert = next(p for q, p in zip(db.queries, db.params) if "INSERT INTO face_profiles" in q)
    assert insert[1] == "zoe-kid"  # (id, user_id, ...) — the stored row, not just the response


async def test_admin_may_enroll_on_behalf_of_another_member(monkeypatch, face_enabled):
    _install_fake_db(monkeypatch, rows=[])
    out = await face_id.face_enroll(_face_payload(user_id="jason"), caller=dict(ADMIN))
    assert out["user_id"] == "jason"


async def test_device_token_still_enrolls_the_person_at_the_panel(monkeypatch, face_enabled):
    """The guided enrolment flow runs on the panel and names its subject."""
    _install_fake_db(monkeypatch, rows=[])
    out = await face_id.face_enroll(_face_payload(user_id="jason"), caller=dict(DEVICE))
    assert out["user_id"] == "jason"


# ── 4. the shared rules themselves ────────────────────────────────────────

@pytest.mark.parametrize("role,expected", [
    ("admin", True),
    ("family-admin", True),
    ("user", False),
    ("guest", False),
    ("", False),
    (None, False),
    (True, False),          # a non-string must never pass
    (["admin"], False),
])
def test_is_admin_role_is_fail_closed(role, expected):
    assert is_admin_role(role) is expected


@pytest.mark.parametrize("caller", [
    DEVICE,
    {"source": "device", "user_id": "jason"},   # a device token claiming a real id
    {"source": "session", "user_id": ""},       # no resolved identity
    {},
])
def test_require_person_scope_rejects_non_persons(caller):
    with pytest.raises(HTTPException) as exc:
        require_person_scope(dict(caller))
    assert exc.value.status_code == 403


def test_authorize_profile_access_orders_404_before_403():
    # Missing row: 404 even for a non-owner, so "not yours" never implies "exists".
    with pytest.raises(HTTPException) as missing:
        authorize_profile_access(None, "zoe-kid", False, kind="face")
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as denied:
        authorize_profile_access("jason", "zoe-kid", False, kind="face")
    assert denied.value.status_code == 403

    assert authorize_profile_access("jason", "jason", False, kind="face") is None
    assert authorize_profile_access("jason", "zoe-kid", True, kind="face") is None


@pytest.mark.parametrize("caller,requested,expected", [
    (OTHER, "jason", "zoe-kid"),       # session: forced to self
    (OTHER, None, "zoe-kid"),          # session: defaults to self
    (ADMIN, "jason", "jason"),         # admin: may name a target
    (FAMILY_ADMIN, "jason", "jason"),  # the alias counts too
    (ADMIN, None, "parent"),           # admin naming nobody is still self
    (DEVICE, "jason", "jason"),        # panel enrols its subject
    (DEVICE, None, None),              # panel naming nobody -> router validation
])
def test_resolve_enroll_target(caller, requested, expected):
    assert resolve_enroll_target(requested, dict(caller)) == expected
