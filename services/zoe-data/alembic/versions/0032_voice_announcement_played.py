"""0032 — ``voice_announcements.played_at``: the daemon's playback ACK.

``delivered_at`` is set when the Pi daemon CLAIMS a row
(``voice_announce.claim_announcements``), before it fetches TTS or plays
anything — a failed TTS fetch, a deferral past the TTL or a daemon that stops
after polling all leave ``delivered_at`` set on audio nobody heard. The daemon
now POSTs ``/api/voice/announcements/{id}/played`` after playback returns, which
sets ``played_at``. It is the only evidence that an announcement was HEARD
(``arrival.scheduled_brief_delivery``).

Idempotent (``ADD COLUMN IF NOT EXISTS``); the downgrade drops the column.
"""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE voice_announcements ADD COLUMN IF NOT EXISTS played_at TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE voice_announcements DROP COLUMN IF EXISTS played_at")
