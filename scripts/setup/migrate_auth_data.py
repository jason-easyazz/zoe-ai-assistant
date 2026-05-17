#!/usr/bin/env python3
"""
One-time migration: copy auth tables from SQLite (zoe.db) to PostgreSQL.
Run from the Jetson after deploying the updated zoe-auth:
    python3 scripts/setup/migrate_auth_data.py
"""
import sqlite3
import psycopg2
import os

SQLITE_PATH = os.getenv("SQLITE_PATH", "./data/zoe.db")
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://zoe:CHANGE_ME@localhost:5432/zoe")

AUTH_TABLES = [
    "users", "auth_users", "roles", "permissions", "auth_sessions",
    "passcodes", "passcode_history", "password_history", "audit_logs",
    "panels", "panel_user_bindings", "rate_limits", "guest_codes",
    "oauth_states", "oauth_device_codes", "service_accounts", "api_keys", "sessions"
]

def main():
    print(f"Connecting to SQLite: {SQLITE_PATH}")
    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row

    print("Connecting to PostgreSQL...")
    pg_conn = psycopg2.connect(POSTGRES_URL)
    pg_conn.autocommit = False

    for table in AUTH_TABLES:
        try:
            cur = sqlite_conn.execute(f"SELECT * FROM {table}")
            rows = cur.fetchall()
            if not rows:
                print(f"  {table}: empty, skipping")
                continue

            columns = [d[0] for d in cur.description]
            pg_cur = pg_conn.cursor()

            placeholders = ", ".join(["%s"] * len(columns))
            col_names = ", ".join(columns)

            for row in rows:
                values = [row[c] for c in columns]
                pg_cur.execute(
                    f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                    values
                )

            pg_conn.commit()
            print(f"  {table}: migrated {len(rows)} rows")

        except Exception as e:
            pg_conn.rollback()
            print(f"  {table}: SKIPPED — {e}")

    sqlite_conn.close()
    pg_conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    main()
