"""0056 - the latest pendant health row per device (``omi_health.py``; gap audit P0-2 "loss is silent").

ONE table, ONE row per pendant (the 16-hex ``device_id`` the Pi derives from the BLE address), overwritten by every report the panel Pi
sends. NUMBERS AND TIMESTAMPS ONLY: no audio, no transcript, no address, no free text. A history is deliberately not kept - the table
answers "how is the pendant now"; the warnings it raises are notifications.

  * ``user_id``                   - the pendant's owner when the row was last written (device-owners registry, or the legacy configured
                                    owner). Reads re-check the CURRENT owner, so a reassignment hides the row from the previous owner at once.
  * ``reported_at``               - the last report from the Pi (the panel-side sync service is alive); ``first_reported_at`` the first.
  * ``reachable`` / ``last_seen_at`` - did the latest attempt connect / when did one last connect.
  * ``last_sync_at``              - the last CLEAN sync (derived from the Pi's reported age, so the Pi's clock never matters).
  * ``battery_pct`` / ``battery_at`` - the standard BLE Battery Service level and when it was read.
  * ``ring_fill_pct``, ``ring_fill_start_pct``, ``ring_stored_bytes``, ``ring_capacity_bytes``, ``ring_at`` - how full the pendant's ring is
                                    after the last drain (and when we connected), from the ring INFO.
  * ``dropped_total``             - the pendant's CUMULATIVE count of records it overwrote before they were read (absolute, as reported);
                                    ``overwritten_records_total`` / ``last_overwritten_records`` / ``last_overwrite_at`` are what Zoe derived from
                                    its changes (so a repeated report cannot double count).
  * ``firmware``, ``consecutive_misses``.
  * ``warn_state``                - JSON ``{kind: {"since": iso|null, "notified_at": iso|null}}``: which warnings are active and when each last
                                    produced a notification (transition detection + a re-notify cooldown). Never message text.

Conventions as 0045 / 0047 / 0055: TEXT ids, UTC ISO timestamps, no FK to a users table (the erase cascade is explicit:
``omi_health.forget_user``). ``IF NOT EXISTS``; a deploy that has not run this migration yet degrades to "no health" (every read and write
is fail-soft and the feature is flag-dark behind ``ZOE_OMI_SYNC_ENABLED``). Downgrade drops the table (lossy by design: it is a cache of
the latest report).
"""

from __future__ import annotations

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def statements(dialect: str) -> list[str]:
    """The CREATE statements. Pure: the unit tests run them on SQLite too."""
    return [
        """CREATE TABLE IF NOT EXISTS omi_device_health (
               device_id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               reported_at TEXT NOT NULL,
               first_reported_at TEXT NOT NULL,
               reachable INTEGER NOT NULL DEFAULT 0,
               last_seen_at TEXT,
               last_sync_at TEXT,
               battery_pct INTEGER,
               battery_at TEXT,
               ring_fill_pct REAL,
               ring_fill_start_pct REAL,
               ring_stored_bytes BIGINT,
               ring_capacity_bytes BIGINT,
               ring_at TEXT,
               dropped_total BIGINT,
               overwritten_records_total BIGINT NOT NULL DEFAULT 0,
               last_overwritten_records BIGINT NOT NULL DEFAULT 0,
               last_overwrite_at TEXT,
               firmware TEXT,
               consecutive_misses INTEGER NOT NULL DEFAULT 0,
               warn_state TEXT NOT NULL DEFAULT '{}'
           )""",
        "CREATE INDEX IF NOT EXISTS idx_omi_device_health_user ON omi_device_health (user_id)",
    ]


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    for sql in statements(dialect):
        op.execute(sql)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS omi_device_health")
