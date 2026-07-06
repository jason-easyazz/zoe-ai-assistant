#!/usr/bin/env python3
"""Provision the authenticated TEST user the voice-parity quality gate needs.

The 2026-07-03 flue-vs-prod parity run was quality-confounded because prod ran
as `guest` (contaminated memory) while flue ran as an empty-store env-bound
user — `parity-gate-user` could not be created at gate time (zoe-auth admin
write denied). This script is the sanctioned, operator-run path: it mints a
real zoe-auth account through AuthManager.create_user (full validation,
password history, audit log), so BOTH brains can run the gate as the same
fresh authenticated identity.

Guardrails (demo-users-only, per the Samantha build-plan):
- username MUST start with `parity-` or `test-` — this script refuses to mint
  anything that could be mistaken for a real household account;
- role is forced to `user` (never admin);
- the generated password is printed ONCE to stdout and stored only as a hash.

Run ON the Zoe host (needs zoe-auth's deps + the live POSTGRES_URL):

    cd /home/zoe/assistant && python3 scripts/maintenance/provision_parity_test_user.py

Then mint a session for the prod side of the gate:

    curl -s -X POST http://localhost:8002/api/auth/login \
         -H 'Content-Type: application/json' \
         -d '{"username": "parity-gate-user", "password": "<printed>"}'

and pass the returned session id as X-Session-ID; the flue side binds the same
user through the Seam-A identity envelope (ZOE_BRAIN_USER_ID / per-request id).
"""

import argparse
import os
import secrets
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ZOE_AUTH_DIR = REPO_ROOT / "services" / "zoe-auth"
ZOE_DATA_ENV = REPO_ROOT / "services" / "zoe-data" / ".env"

ALLOWED_PREFIXES = ("parity-", "test-")


def _ensure_postgres_url() -> None:
    """zoe-auth reads POSTGRES_URL at pool-init; outside the service unit the
    canonical value lives in services/zoe-data/.env (same database)."""
    if os.environ.get("POSTGRES_URL"):
        return
    if ZOE_DATA_ENV.is_file():
        for raw in ZOE_DATA_ENV.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("POSTGRES_URL=") and "=" in line:
                os.environ["POSTGRES_URL"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                return
    sys.exit("POSTGRES_URL is not set and services/zoe-data/.env has no value — aborting.")


def _mint_password() -> str:
    """Policy-compliant random password: one guaranteed character per policy
    class (upper/lower/digit/special) — token_urlsafe alone is base64url and
    can never satisfy require_special."""
    return (
        secrets.token_urlsafe(18)
        + secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ")
        + secrets.choice("abcdefghjkmnpqrstuvwxyz")
        + secrets.choice("23456789")
        + secrets.choice("!@#$%^&*")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--username", default="parity-gate-user")
    parser.add_argument(
        "--email", default=None,
        help="defaults to <username>@parity.zoe.invalid (never routable)",
    )
    parser.add_argument(
        "--rotate-password", action="store_true",
        help="if the user already exists (active OR deactivated), set a fresh "
        "password and reactivate — the recovery path for a lost credential. "
        "(create_user cannot be re-run: its _user_exists check matches "
        "deactivated rows too, so deletion-free recovery must go through an "
        "UPDATE.)",
    )
    args = parser.parse_args()

    username = args.username.strip().lower()
    if not username.startswith(ALLOWED_PREFIXES):
        sys.exit(
            f"Refusing: username must start with one of {ALLOWED_PREFIXES} "
            "(demo/test users only — this script never mints household accounts)."
        )
    email = args.email or f"{username}@parity.zoe.invalid"

    _ensure_postgres_url()
    sys.path.insert(0, str(ZOE_AUTH_DIR))
    import bcrypt  # noqa: E402 — zoe-auth's own hash dependency
    from core.auth import auth_manager  # noqa: E402 — needs sys.path + env first
    from models.database import auth_db  # noqa: E402

    # Match ANY row (active or deactivated): create_user would refuse both, so
    # the existing/recovery branches must see both.
    with auth_db.get_connection() as conn:
        cur = conn.execute(
            "SELECT user_id, is_active FROM auth_users WHERE username = ?",
            (username,),
        )
        row = cur.fetchone()

    if row:
        user_id = row["user_id"] if hasattr(row, "keys") else row[0]
        if not args.rotate_password:
            print(f"EXISTS: {username} → user_id={user_id} (password unchanged).")
            print("Lost the password? Re-run with --rotate-password to mint a fresh one.")
            return
        password = _mint_password()
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        with auth_db.get_connection() as conn:
            conn.execute(
                "UPDATE auth_users SET password_hash = ?, is_active = 1 WHERE user_id = ?",
                (password_hash, user_id),
            )
        print(f"ROTATED: {username} → user_id={user_id} (reactivated if needed)")
        print(f"  password: {password}")
        print("  ^ shown ONCE — feed it to the parity harness login, do not store it in the repo.")
        return

    password = _mint_password()
    ok, result = auth_manager.create_user(
        username=username,
        email=email,
        password=password,
        role="user",
        created_by="provision_parity_test_user.py",
    )
    if not ok:
        sys.exit(f"create_user failed: {result}")

    print(f"CREATED: {username}")
    print(f"  user_id:  {result}")
    print(f"  password: {password}")
    print("  ^ shown ONCE — feed it to the parity harness login, do not store it in the repo.")


if __name__ == "__main__":
    main()
