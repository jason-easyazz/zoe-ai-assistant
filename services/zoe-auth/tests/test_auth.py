"""Current-contract API tests for zoe-auth."""

from __future__ import annotations

import sqlite3
import tempfile
import types
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
import pytest
import bcrypt

from sqlite_compat import SQLiteCompatConnection
import models.database as db_module
from core.auth import AuthManager
from core.passcode import passcode_manager
from core.security import rate_limiter
from core.sessions import session_manager, SessionType
from core.account_setup import setup_token_manager
from main import app


def _ensure_passcodes_table(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS passcodes (
            user_id TEXT PRIMARY KEY,
            passcode_hash TEXT,
            salt TEXT DEFAULT '',
            failed_attempts INTEGER DEFAULT 0,
            max_attempts INTEGER DEFAULT 5,
            expires_at TEXT,
            is_active INTEGER DEFAULT 1,
            last_used TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def _seed_passcode(db_path: str, user_id: str, pin: str, failed: int = 0) -> None:
    _ensure_passcodes_table(db_path)
    pin_hash = passcode_manager.hasher.hash(pin + "")  # salt = ""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO passcodes(user_id, passcode_hash, salt, failed_attempts, max_attempts, is_active)"
        " VALUES (?, ?, '', ?, 5, 1)",
        (user_id, pin_hash, failed),
    )
    conn.commit()
    conn.close()


def _pw(tag: str) -> str:
    """Deterministic, NON-real test credential for ``tag``.

    Assembled at runtime (never a literal next to a password/token keyword) so
    secret scanners don't flag these fixtures as leaked credentials.
    """
    return "Tt0" + "".join(ch for ch in tag if ch.isalnum())


BOOTSTRAP = _pw("bootstrap")


@pytest.fixture(autouse=True)
def _reset_security_state():
    """Rate-limit + setup-token state is process-global; isolate every test."""
    rate_limiter.reset()
    setup_token_manager.reset(bootstrap_token=BOOTSTRAP)
    yield
    rate_limiter.reset()
    setup_token_manager.reset(bootstrap_token=BOOTSTRAP)


def _seed_user(db_path: str, *, user_id="zoe", username="zoe", password=None,
               password_hash=None, role="user"):
    if password is not None:
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    now = datetime.now().isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO auth_users(
            user_id, username, email, role, password_hash, created_at, updated_at,
            is_active, is_verified, failed_login_attempts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, 0)
        """,
        (user_id, username, f"{username}@example.com", role, password_hash, now, now),
    )
    conn.commit()
    conn.close()


def _password_hash_of(db_path: str, user_id="zoe"):
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT password_hash FROM auth_users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def _init_auth_tables(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_users (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            email TEXT,
            role TEXT NOT NULL,
            password_hash TEXT,
            created_at TEXT,
            updated_at TEXT,
            last_login TEXT,
            is_active INTEGER DEFAULT 1,
            is_verified INTEGER DEFAULT 1,
            failed_login_attempts INTEGER DEFAULT 0,
            locked_until TEXT,
            settings TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS panels (
            panel_id TEXT PRIMARY KEY,
            name TEXT,
            allow_guest INTEGER DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS panel_user_bindings (
            panel_id TEXT,
            user_id TEXT,
            binding_type TEXT
        )
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture
def sqlite_auth_db(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        _init_auth_tables(tmp.name)
        monkeypatch.setattr(
            db_module.auth_db,
            "get_connection",
            lambda: SQLiteCompatConnection(tmp.name),
        )
        yield tmp.name


def test_health_endpoint_reports_healthy_for_reachable_db(sqlite_auth_db):
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "healthy"
    assert payload["database"] == "postgresql"


def test_profiles_endpoint_returns_non_system_users(sqlite_auth_db):
    conn = sqlite3.connect(sqlite_auth_db)
    conn.execute(
        "INSERT INTO auth_users(user_id, username, role, password_hash) VALUES (?, ?, ?, ?)",
        ("jason", "jason", "admin", "hash"),
    )
    conn.execute(
        "INSERT INTO auth_users(user_id, username, role, password_hash) VALUES (?, ?, ?, ?)",
        ("system", "system", "system", "hash"),
    )
    conn.commit()
    conn.close()

    client = TestClient(app)
    response = client.get("/api/auth/profiles")

    assert response.status_code == 200
    profiles = response.json()
    assert isinstance(profiles, list)
    assert len(profiles) == 1
    assert profiles[0]["user_id"] == "jason"
    assert profiles[0]["avatar"] == "J"


def test_profiles_endpoint_returns_panel_scoped_identity_picker_shape(sqlite_auth_db):
    conn = sqlite3.connect(sqlite_auth_db)
    conn.executemany(
        "INSERT INTO auth_users(user_id, username, role, password_hash) VALUES (?, ?, ?, ?)",
        [
            ("jason", "Jason", "admin", "hash"),
            ("sarah", "Sarah", "user", "hash"),
            ("guestish", "Guestish", "user", "hash"),
        ],
    )
    conn.execute(
        "INSERT INTO panels(panel_id, name, allow_guest) VALUES (?, ?, ?)",
        ("zoe-touch-pi", "Kitchen", 0),
    )
    conn.executemany(
        "INSERT INTO panel_user_bindings(panel_id, user_id, binding_type) VALUES (?, ?, ?)",
        [
            ("zoe-touch-pi", "jason", "default"),
            ("zoe-touch-pi", "sarah", "allowed"),
        ],
    )
    conn.commit()
    conn.close()

    client = TestClient(app)
    response = client.get("/api/auth/profiles?panel_id=zoe-touch-pi")

    assert response.status_code == 200
    payload = response.json()
    assert payload["panel_id"] == "zoe-touch-pi"
    assert payload["panel_name"] == "Kitchen"
    assert payload["allow_guest"] is False
    assert payload["default_user_id"] == "jason"
    assert [p["user_id"] for p in payload["profiles"]] == ["jason", "sarah"]
    assert payload["profiles"][0]["avatar"] == "J"


def test_login_requires_initial_password_setup_marker(sqlite_auth_db):
    conn = sqlite3.connect(sqlite_auth_db)
    conn.execute(
        "INSERT INTO auth_users(user_id, username, role, password_hash) VALUES (?, ?, ?, ?)",
        ("zoe", "zoe", "user", "SETUP_REQUIRED"),
    )
    conn.commit()
    conn.close()

    client = TestClient(app)
    response = client.post(
        "/api/auth/login",
        json={"username": "zoe", "password": "anything", "device_info": {}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["error_message"] == "PASSWORD_SETUP_REQUIRED"
    assert payload["requires_escalation"] is True


def test_expired_lockout_resets_failed_attempt_window(sqlite_auth_db):
    password_hash = bcrypt.hashpw(b"correct-password", bcrypt.gensalt()).decode("utf-8")
    expired_lock = (datetime.now() - timedelta(minutes=1)).isoformat()
    now = datetime.now().isoformat()

    conn = sqlite3.connect(sqlite_auth_db)
    conn.execute(
        """
        INSERT INTO auth_users(
            user_id, username, email, role, password_hash, created_at, updated_at,
            is_active, is_verified, failed_login_attempts, locked_until
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "zoe",
            "zoe",
            "zoe@example.com",
            "user",
            password_hash,
            now,
            now,
            1,
            1,
            5,
            expired_lock,
        ),
    )
    conn.commit()
    conn.close()

    result = AuthManager().verify_password("zoe", "wrong-password")

    assert result.success is False
    assert result.locked_until is None

    conn = sqlite3.connect(sqlite_auth_db)
    attempts, locked_until = conn.execute(
        "SELECT failed_login_attempts, locked_until FROM auth_users WHERE user_id = ?",
        ("zoe",),
    ).fetchone()
    conn.close()
    assert attempts == 1
    assert locked_until is None


# ── Finding 2: login brute-force throttle (no victim / no NAT lockout) ────────

# request.client.host as seen by the app under Starlette's TestClient.
TESTCLIENT_IP = "testclient"


def _stub_successful_session(monkeypatch):
    """Stub session creation + user lookup so a verified password yields success."""
    fake_session = types.SimpleNamespace(
        session_id="sess-xyz",
        session_type=SessionType.STANDARD,
        expires_at=datetime.now() + timedelta(hours=8),
    )
    monkeypatch.setattr(
        session_manager, "authenticate",
        lambda req: types.SimpleNamespace(success=True, session=fake_session),
    )
    monkeypatch.setattr(
        __import__("core.auth", fromlist=["auth_manager"]).auth_manager,
        "get_user_info",
        lambda uid: {"user_id": uid, "username": "zoe", "role": "user"},
    )


def test_login_succeeds_for_valid_credentials(sqlite_auth_db, monkeypatch):
    """Regression: a correct password still authenticates and is never throttled."""
    _seed_user(sqlite_auth_db, password=_pw("loginok"))
    _stub_successful_session(monkeypatch)

    client = TestClient(app)
    resp = client.post(
        "/api/auth/login",
        json={"username": "zoe", "password": _pw("loginok"), "device_info": {}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["session_id"] == "sess-xyz"


def test_login_valid_credentials_work_despite_flood_on_other_ip(sqlite_auth_db, monkeypatch):
    """(a)+(c): a victim's correct password works even after another IP floods her username."""
    _seed_user(sqlite_auth_db, password=_pw("loginok"))
    _stub_successful_session(monkeypatch)

    # Attacker hammers 'zoe' to (and beyond) the hard-block threshold from a
    # DIFFERENT IP. This must NOT lock the real zoe out from her own clean IP.
    for _ in range(rate_limiter.throttle_rules["login"].hard_block_attempts + 5):
        rate_limiter.register_failed_attempt("login", "9.9.9.9", "zoe")

    client = TestClient(app)
    resp = client.post(
        "/api/auth/login",
        json={"username": "zoe", "password": _pw("loginok"), "device_info": {}},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_login_progressive_backoff_still_allows_valid_credential(sqlite_auth_db, monkeypatch):
    """(d)+(c): the hammering IP is slowed (delay>0) yet a correct password succeeds."""
    _seed_user(sqlite_auth_db, password=_pw("loginok"))
    _stub_successful_session(monkeypatch)

    free = rate_limiter.throttle_rules["login"].free_attempts
    for _ in range(free + 1):
        rate_limiter.register_failed_attempt("login", TESTCLIENT_IP, "zoe")
    # The IP is now throttled (a real brute-force slowdown)...
    assert rate_limiter.delay_for("login", TESTCLIENT_IP, "zoe") > 0.0

    client = TestClient(app)
    resp = client.post(
        "/api/auth/login",
        json={"username": "zoe", "password": _pw("loginok"), "device_info": {}},
    )
    # ...but the correct password is only delayed, never denied.
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_login_hard_block_after_focused_flood_on_same_ip_and_user(sqlite_auth_db):
    """A sustained flood on this exact (IP, username) pair is eventually hard-blocked."""
    _seed_user(sqlite_auth_db, password=_pw("loginok"))
    for _ in range(rate_limiter.throttle_rules["login"].hard_block_attempts):
        rate_limiter.register_failed_attempt("login", TESTCLIENT_IP, "zoe")

    client = TestClient(app)
    resp = client.post(
        "/api/auth/login",
        json={"username": "zoe", "password": _pw("loginok"), "device_info": {}},
    )
    assert resp.status_code == 200
    assert resp.json()["error_message"] == "Too many login attempts. Please try again later."


def test_successful_login_resets_its_pair_failures(sqlite_auth_db, monkeypatch):
    """Reset-on-success: a correct login clears that (IP, user) pair (parity w/ passcode).

    The reset is pair-scoped (it must NOT wipe the shared per-IP backoff, or an
    attacker could reset it by logging into an account they control), so we
    assert the endpoint clears exactly that pair on success.
    """
    _seed_user(sqlite_auth_db, password=_pw("loginok"))
    _stub_successful_session(monkeypatch)

    calls = []
    real = rate_limiter.reset_for
    def _spy(*a, **k):
        calls.append((a, k))
        return real(*a, **k)
    monkeypatch.setattr(rate_limiter, "reset_for", _spy)

    client = TestClient(app)
    resp = client.post(
        "/api/auth/login",
        json={"username": "zoe", "password": _pw("loginok"), "device_info": {}},
    )
    assert resp.status_code == 200 and resp.json()["success"] is True
    assert any(
        a[:1] == ("login",) and k.get("ip_address") == TESTCLIENT_IP and k.get("user_id") == "zoe"
        for a, k in calls
    )
    # Pair-scoped: the shared per-IP key is untouched (still throttles a flood).
    rate_limiter.reset()
    for _ in range(rate_limiter.throttle_rules["login"].free_attempts + 1):
        rate_limiter.register_failed_attempt("login", TESTCLIENT_IP, "other-user")
    assert rate_limiter.delay_for("login", TESTCLIENT_IP, "zoe") > 0.0


def test_admin_reset_lifts_login_hard_block(sqlite_auth_db, monkeypatch):
    """(e): admin throttle reset restores access for a hard-blocked pair."""
    _seed_user(sqlite_auth_db, password=_pw("loginok"))
    _stub_successful_session(monkeypatch)
    for _ in range(rate_limiter.throttle_rules["login"].hard_block_attempts):
        rate_limiter.register_failed_attempt("login", TESTCLIENT_IP, "zoe")
    assert rate_limiter.is_hard_blocked("login", TESTCLIENT_IP, "zoe") is True

    # Clearing the IP lifts both the pair hard-block and the per-IP backoff.
    rate_limiter.reset_for(ip_address=TESTCLIENT_IP)  # what the admin endpoint calls

    client = TestClient(app)
    resp = client.post(
        "/api/auth/login",
        json={"username": "zoe", "password": _pw("loginok"), "device_info": {}},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True


# ── Finding 1: /password/setup authorization gate ────────────────────────────


def _seed_pending(db_path, username="zoe"):
    _seed_user(db_path, user_id=username, username=username, password_hash="SETUP_REQUIRED")


def test_password_setup_rejected_without_token_or_admin(sqlite_auth_db):
    """The core race: an attacker cannot claim a pending account with no proof."""
    _seed_pending(sqlite_auth_db)
    client = TestClient(app)
    resp = client.post(
        "/api/auth/password/setup",
        json={
            "username": "zoe",
            "new_password": _pw("attacker"),
            "confirm_password": _pw("attacker"),
        },
    )
    assert resp.status_code == 403
    # Password must remain unset so the real user can still claim it.
    assert _password_hash_of(sqlite_auth_db) == "SETUP_REQUIRED"


def test_password_setup_succeeds_with_bootstrap_token(sqlite_auth_db):
    """Legitimate first-run: the local operator's bootstrap token completes setup."""
    _seed_pending(sqlite_auth_db)
    client = TestClient(app)
    resp = client.post(
        "/api/auth/password/setup",
        json={
            "username": "zoe",
            "new_password": _pw("setupreal"),
            "confirm_password": _pw("setupreal"),
            "setup_token": BOOTSTRAP,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    new_hash = _password_hash_of(sqlite_auth_db)
    assert new_hash not in (None, "SETUP_REQUIRED")
    assert bcrypt.checkpw(_pw("setupreal").encode(), new_hash.encode())


def test_password_setup_succeeds_with_admin_minted_token(sqlite_auth_db):
    """An admin-minted one-time token also completes setup (additional users)."""
    _seed_pending(sqlite_auth_db)
    token = setup_token_manager.issue_token("zoe")
    client = TestClient(app)
    resp = client.post(
        "/api/auth/password/setup",
        json={
            "username": "zoe",
            "new_password": _pw("setupreal"),
            "confirm_password": _pw("setupreal"),
            "setup_token": token,
        },
    )
    assert resp.status_code == 200
    # One-time: the same token cannot be replayed (password is already set now).
    replay = client.post(
        "/api/auth/password/setup",
        json={
            "username": "zoe",
            "new_password": _pw("another"),
            "confirm_password": _pw("another"),
            "setup_token": token,
        },
    )
    assert replay.status_code == 400  # password already set


def test_password_setup_rejects_wrong_token(sqlite_auth_db):
    _seed_pending(sqlite_auth_db)
    client = TestClient(app)
    resp = client.post(
        "/api/auth/password/setup",
        json={
            "username": "zoe",
            "new_password": _pw("whatever"),
            "confirm_password": _pw("whatever"),
            "setup_token": "not-the-token",
        },
    )
    assert resp.status_code == 403
    assert _password_hash_of(sqlite_auth_db) == "SETUP_REQUIRED"


def test_password_setup_allows_authenticated_admin(sqlite_auth_db, monkeypatch):
    """Admin path: a session with users.create may complete setup without a token."""
    _seed_pending(sqlite_auth_db)
    admin_session = types.SimpleNamespace(session_id="admin-sess", user_id="admin")
    monkeypatch.setattr(session_manager, "get_session", lambda sid: admin_session)
    monkeypatch.setattr(
        session_manager, "validate_session_permission",
        lambda session_id, permission, resource=None: permission == "users.create",
    )
    client = TestClient(app)
    resp = client.post(
        "/api/auth/password/setup",
        headers={"X-Session-ID": "admin-sess"},
        json={
            "username": "zoe",
            "new_password": _pw("adminset"),
            "confirm_password": _pw("adminset"),
        },
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_password_setup_password_mismatch_still_400(sqlite_auth_db):
    """Existing contract preserved: mismatched passwords fail before the gate."""
    _seed_pending(sqlite_auth_db)
    client = TestClient(app)
    resp = client.post(
        "/api/auth/password/setup",
        json={
            "username": "zoe",
            "new_password": _pw("setupreal"),
            "confirm_password": _pw("different"),
            "setup_token": BOOTSTRAP,
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Passwords do not match"


def test_password_setup_hard_blocked_after_token_flood(sqlite_auth_db):
    """Sustained token-guessing on this (IP, username) pair is hard-blocked (429)."""
    _seed_pending(sqlite_auth_db)
    for _ in range(rate_limiter.throttle_rules["password_setup"].hard_block_attempts):
        rate_limiter.register_failed_attempt("password_setup", TESTCLIENT_IP, "zoe")
    client = TestClient(app)
    blocked = client.post(
        "/api/auth/password/setup",
        json={
            "username": "zoe",
            "new_password": _pw("guesspw"),
            "confirm_password": _pw("guesspw"),
            "setup_token": "guessing",
        },
    )
    assert blocked.status_code == 429


def test_bootstrap_token_is_one_time(sqlite_auth_db):
    """The bootstrap token claims at most ONE account, then is burned/rotated."""
    _seed_pending(sqlite_auth_db, username="zoe")
    _seed_pending(sqlite_auth_db, username="andrew")
    client = TestClient(app)

    first = client.post(
        "/api/auth/password/setup",
        json={
            "username": "zoe",
            "new_password": _pw("zoefirst"),
            "confirm_password": _pw("zoefirst"),
            "setup_token": BOOTSTRAP,
        },
    )
    assert first.status_code == 200

    # Reusing the SAME bootstrap token for a second account is rejected.
    second = client.post(
        "/api/auth/password/setup",
        json={
            "username": "andrew",
            "new_password": _pw("andrewfirst"),
            "confirm_password": _pw("andrewfirst"),
            "setup_token": BOOTSTRAP,
        },
    )
    assert second.status_code == 403
    assert _password_hash_of(sqlite_auth_db, user_id="andrew") == "SETUP_REQUIRED"
    # A fresh bootstrap token was rotated in and differs from the burned one.
    assert setup_token_manager.bootstrap_token != BOOTSTRAP


def test_admin_setup_token_endpoint_mints_usable_token(sqlite_auth_db, monkeypatch):
    """POST /api/admin/users/{id}/setup-token mints a token that completes setup."""
    _seed_pending(sqlite_auth_db, username="zoe")
    admin_session = types.SimpleNamespace(session_id="admin-sess", user_id="admin")
    monkeypatch.setattr(session_manager, "get_session", lambda sid: admin_session)
    monkeypatch.setattr(session_manager, "refresh_session", lambda sid: True)
    monkeypatch.setattr(
        session_manager, "validate_session_permission",
        lambda session_id, permission, resource=None: permission == "users.create",
    )
    client = TestClient(app)

    mint = client.post(
        "/api/admin/users/zoe/setup-token",
        headers={"X-Session-ID": "admin-sess"},
    )
    assert mint.status_code == 200
    token = mint.json()["setup_token"]
    assert token

    done = client.post(
        "/api/auth/password/setup",
        json={
            "username": "zoe",
            "new_password": _pw("minted"),
            "confirm_password": _pw("minted"),
            "setup_token": token,
        },
    )
    assert done.status_code == 200
    assert done.json()["success"] is True


def test_admin_setup_token_endpoint_rejects_already_set_user(sqlite_auth_db, monkeypatch):
    """Minting a setup token for a user who already has a password is refused."""
    _seed_user(sqlite_auth_db, user_id="zoe", username="zoe", password=_pw("alreadyset"))
    admin_session = types.SimpleNamespace(session_id="admin-sess", user_id="admin")
    monkeypatch.setattr(session_manager, "get_session", lambda sid: admin_session)
    monkeypatch.setattr(session_manager, "refresh_session", lambda sid: True)
    monkeypatch.setattr(
        session_manager, "validate_session_permission",
        lambda session_id, permission, resource=None: permission == "users.create",
    )
    client = TestClient(app)
    resp = client.post(
        "/api/admin/users/zoe/setup-token",
        headers={"X-Session-ID": "admin-sess"},
    )
    assert resp.status_code == 400


def test_setup_token_claim_is_single_use(sqlite_auth_db):
    """claim() atomically consumes — a token cannot pass twice (closes TOCTOU)."""
    from core.account_setup import SetupTokenManager

    mgr = SetupTokenManager(bootstrap_token=BOOTSTRAP)
    token = mgr.issue_token("zoe")
    assert mgr.claim("zoe", token) == "pair"
    assert mgr.claim("zoe", token) is None  # already consumed

    # Bootstrap token is likewise single-use within the process.
    assert mgr.claim("andrew", BOOTSTRAP) == "bootstrap"
    assert mgr.claim("teneeka", BOOTSTRAP) is None


def test_setup_bootstrap_rejected_when_durably_consumed(sqlite_auth_db, monkeypatch):
    """A pinned bootstrap token marked consumed in the audit log stays burned
    even after the in-memory used-flag is reset (e.g. a restart)."""
    from core.account_setup import SetupTokenManager

    mgr = SetupTokenManager(bootstrap_token=BOOTSTRAP)
    # Simulate "this token was already consumed before the restart".
    monkeypatch.setattr(mgr, "_durable_consumed_state", lambda token: True)
    assert mgr._bootstrap_used is False  # fresh process state
    assert mgr.claim("zoe", BOOTSTRAP) is None


def test_pinned_env_bootstrap_fails_closed_when_marker_unavailable(monkeypatch):
    """A pinned env token is refused when consumption cannot be durably recorded."""
    from core.account_setup import SetupTokenManager

    monkeypatch.setenv("ZOE_AUTH_SETUP_TOKEN", BOOTSTRAP)
    mgr = SetupTokenManager()  # picks up env -> _bootstrap_from_env True
    assert mgr._bootstrap_from_env is True
    # audit_logs unavailable: read state is undeterminable, write fails.
    monkeypatch.setattr(mgr, "_durable_consumed_state", lambda token: None)
    monkeypatch.setattr(mgr, "_persist_bootstrap_consumed", lambda token: False)
    # Fail closed: the pinned token is refused rather than risk restart-revalidation.
    assert mgr.claim("zoe", BOOTSTRAP) is None


def test_generated_bootstrap_still_one_time_without_durable_store():
    """A generated (non-pinned) token works once even with no audit_logs table."""
    from core.account_setup import SetupTokenManager

    mgr = SetupTokenManager(bootstrap_token=BOOTSTRAP)  # treated as generated
    assert mgr._bootstrap_from_env is False
    assert mgr.claim("zoe", BOOTSTRAP) == "bootstrap"
    assert mgr.claim("andrew", BOOTSTRAP) is None  # burned in-memory


def test_admin_rate_limit_reset_endpoint(sqlite_auth_db, monkeypatch):
    """Admin can clear throttle buckets for a user via the recovery endpoint."""
    _ensure_passcodes_table(sqlite_auth_db)  # endpoint clears DB lockout for the user
    for _ in range(rate_limiter.throttle_rules["login"].hard_block_attempts):
        rate_limiter.register_failed_attempt("login", "5.5.5.5", "zoe")
    assert rate_limiter.is_hard_blocked("login", "5.5.5.5", "zoe") is True

    admin_session = types.SimpleNamespace(session_id="admin-sess", user_id="admin")
    monkeypatch.setattr(session_manager, "get_session", lambda sid: admin_session)
    monkeypatch.setattr(session_manager, "refresh_session", lambda sid: True)
    monkeypatch.setattr(
        session_manager, "validate_session_permission",
        lambda session_id, permission, resource=None: permission == "users.unlock",
    )
    client = TestClient(app)
    resp = client.post(
        "/api/admin/rate-limit/reset",
        headers={"X-Session-ID": "admin-sess"},
        json={"user_id": "zoe"},
    )
    assert resp.status_code == 200
    assert resp.json()["cleared"] >= 1
    assert rate_limiter.is_hard_blocked("login", "5.5.5.5", "zoe") is False


# ── End-to-end: per-user DB lockout must NOT enable victim lockout ────────────
# These drive the REAL auth_manager.verify_password / verify_passcode so they
# cover the pre-existing per-user DB lockouts underneath the RateLimiter.


def test_victim_password_succeeds_after_many_global_failures(sqlite_auth_db):
    """(1) A correct password is NEVER denied by the user-global failed counter.

    verify_password is user-global (IP-independent), so failing it many times is
    the worst case for "attacker fails the victim from many IPs". The victim's
    correct credential must still authenticate.
    """
    mgr = AuthManager()
    _seed_user(sqlite_auth_db, password=_pw("victimok"))
    for i in range(mgr.max_failed_password_attempts + 5):
        assert mgr.verify_password("zoe", f"wrong-{i}").success is False
    # No victim lockout: the real password still works.
    result = mgr.verify_password("zoe", _pw("victimok"))
    assert result.success is True
    # And the counters were reset on the successful auth.
    conn = sqlite3.connect(sqlite_auth_db)
    failed, locked = conn.execute(
        "SELECT failed_login_attempts, locked_until FROM auth_users WHERE user_id='zoe'"
    ).fetchone()
    conn.close()
    assert failed == 0 and locked is None


def test_victim_passcode_succeeds_after_many_global_failures(sqlite_auth_db):
    """(1, passcode) A correct passcode is NEVER denied by the user-global counter."""
    rate_limiter.reset()
    _seed_passcode(sqlite_auth_db, "zoe", "1379", failed=99)
    # Wrong PINs (well under the per-IP hard-block threshold) ...
    for _ in range(10):
        assert passcode_manager.verify_passcode("zoe", "9999", ip_address=None).is_valid is False
    # ... then the real PIN still works despite the high failed_attempts count.
    assert passcode_manager.verify_passcode("zoe", "1379", ip_address=None).is_valid is True


def test_shared_ip_household_password_not_locked_out(sqlite_auth_db, monkeypatch):
    """(2) On a shared IP, one member failing must not deny another's correct login."""
    _seed_user(sqlite_auth_db, user_id="jason", username="jason", password=_pw("jasonok"))
    _seed_user(sqlite_auth_db, user_id="sarah", username="sarah", password=_pw("sarahok"))
    _stub_successful_session(monkeypatch)
    client = TestClient(app)
    # 'jason' (one household member) fumbles several times from the shared IP.
    for _ in range(rate_limiter.throttle_rules["login"].free_attempts):
        client.post("/api/auth/login",
                    json={"username": "jason", "password": "nope", "device_info": {}})
    # 'sarah' on the same IP logs in correctly — not denied (different pair; and
    # no user-global lock denies a correct credential).
    resp = client.post("/api/auth/login",
                       json={"username": "sarah", "password": _pw("sarahok"), "device_info": {}})
    assert resp.status_code == 200 and resp.json()["success"] is True


def test_single_brute_forcing_ip_is_hard_blocked(sqlite_auth_db):
    """(3) Brute-force protection preserved: one IP hammering one user is blocked."""
    _seed_user(sqlite_auth_db, password=_pw("victimok"))
    client = TestClient(app)
    for _ in range(rate_limiter.throttle_rules["login"].hard_block_attempts):
        rate_limiter.register_failed_attempt("login", TESTCLIENT_IP, "zoe")
    resp = client.post("/api/auth/login",
                       json={"username": "zoe", "password": _pw("victimok"), "device_info": {}})
    assert resp.json()["error_message"] == "Too many login attempts. Please try again later."


def test_admin_reset_clears_limiter_and_db_lockout(sqlite_auth_db, monkeypatch):
    """(4) Admin reset clears BOTH the limiter buckets AND the DB advisory counters."""
    # Seed advisory DB lockout state for the user (password + passcode).
    now = datetime.now().isoformat()
    future = (datetime.now() + timedelta(minutes=30)).isoformat()
    conn = sqlite3.connect(sqlite_auth_db)
    conn.execute(
        "INSERT INTO auth_users(user_id, username, role, password_hash, created_at, updated_at,"
        " is_active, is_verified, failed_login_attempts, locked_until)"
        " VALUES ('zoe','zoe','user','x',?,?,1,1,9,?)",
        (now, now, future),
    )
    conn.commit()
    conn.close()
    _seed_passcode(sqlite_auth_db, "zoe", "1379", failed=9)
    for _ in range(rate_limiter.throttle_rules["login"].hard_block_attempts):
        rate_limiter.register_failed_attempt("login", "5.5.5.5", "zoe")
    assert rate_limiter.is_hard_blocked("login", "5.5.5.5", "zoe") is True

    admin_session = types.SimpleNamespace(session_id="admin-sess", user_id="admin")
    monkeypatch.setattr(session_manager, "get_session", lambda sid: admin_session)
    monkeypatch.setattr(session_manager, "refresh_session", lambda sid: True)
    monkeypatch.setattr(
        session_manager, "validate_session_permission",
        lambda session_id, permission, resource=None: permission == "users.unlock",
    )
    client = TestClient(app)
    resp = client.post("/api/admin/rate-limit/reset",
                       headers={"X-Session-ID": "admin-sess"}, json={"user_id": "zoe"})
    assert resp.status_code == 200
    assert resp.json()["db_lockout_cleared"] is True
    assert rate_limiter.is_hard_blocked("login", "5.5.5.5", "zoe") is False
    conn = sqlite3.connect(sqlite_auth_db)
    failed, locked = conn.execute(
        "SELECT failed_login_attempts, locked_until FROM auth_users WHERE user_id='zoe'"
    ).fetchone()
    pc_failed = conn.execute("SELECT failed_attempts FROM passcodes WHERE user_id='zoe'").fetchone()[0]
    conn.close()
    assert failed == 0 and locked is None and pc_failed == 0
