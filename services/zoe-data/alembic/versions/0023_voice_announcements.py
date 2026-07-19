"""0023 — voice_announcements: the daemon-facing spoken-announcement queue (P-W2.3).

W2's spoken morning brief (#1412/#1414) "succeeded" twice while producing no
audio: the kiosk BROWSER was the speaker (`panel_announce` → fire-and-forget
fetch of /api/voice/speak), but the kiosk is a guest session with no device
token, so /speak 401'd and four silent swallow points ate the failure. The
proven audio path is the Pi voice DAEMON (device token, pyaudio playback,
barge-in, echo-suppression) — this table is the missing server→daemon lane:

  * the proactive engine ENQUEUES a row per spoken announcement;
  * the daemon POLLS `GET /api/voice/announcements` (device-token auth) which
    claims rows atomically (`delivered_at` NULL→set, rowcount-checked) so
    poll overlap can never double-speak;
  * `expires_at` is a short TTL (~120 s): a stale "good morning" at noon is
    worse than silence, so expired rows are marked (`expired = 1`), never
    returned, never played.

Timestamps are TEXT UTC (``%Y-%m-%dT%H:%M:%SZ``), matching the proactive
tables' convention (ISO-Z strings compare in time order). ``CREATE TABLE /
INDEX IF NOT EXISTS`` is supported by both PostgreSQL (production) and SQLite
(test DBs), so no dialect branch is needed and reruns are safe (0021/0022
convention).
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS voice_announcements (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               panel_id TEXT,
               message TEXT NOT NULL,
               trigger_type TEXT DEFAULT '',
               created_at TEXT NOT NULL,
               expires_at TEXT NOT NULL,
               delivered_at TEXT,
               delivered_to TEXT,
               expired INTEGER NOT NULL DEFAULT 0
           )"""
    )
    # The claim query filters pending rows by expiry; the table stays tiny
    # (TTL ~120s + expiry marking) so one plain index is enough.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_voice_announcements_pending "
        "ON voice_announcements (expires_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_voice_announcements_pending")
    op.execute("DROP TABLE IF EXISTS voice_announcements")
