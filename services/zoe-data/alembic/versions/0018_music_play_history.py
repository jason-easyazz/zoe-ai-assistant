"""0018 — music_play_history: Zoe's per-user listening journal.

Append-only play events with ZOE-side user attribution. Music Assistant's own
playlog cannot do this: it upserts one row per item/user with 90-day retention,
and every Zoe-initiated play collapses to the single household MA token user.
This journal is where "who actually asked for this" lives:

  - source='initiated': captured at the moment Zoe triggers playback
    (music_service.search_and_play / play_media), attributed to the resolved
    acting user when identity threading provides one, else the configurable
    household default (ZOE_MUSIC_DEFAULT_USER). Speaker-ID attribution for
    anonymous voice turns is a Samantha-roadmap follow-up, not built here.
  - source='observed': back-filled by a light poll of MA's
    ``music/recently_played_items`` for plays Zoe did not start (radio-mode
    auto-continuation, queue rollover), deduped against initiated events.

Feeds per-user taste profiles for the weekly music discovery batch
(music_discovery.py). Timestamps are ISO-8601 TEXT per repo convention;
``IF NOT EXISTS`` everywhere keeps the migration rerun-safe. Production is
PostgreSQL; the SQLite branch exists for test DBs (0016's pattern).
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

_COLUMNS = """
    played_at TEXT NOT NULL,
    zoe_user_id TEXT NOT NULL,
    source TEXT NOT NULL,
    track TEXT NOT NULL DEFAULT '',
    artist TEXT NOT NULL DEFAULT '',
    album TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    uri TEXT NOT NULL DEFAULT '',
    media_type TEXT NOT NULL DEFAULT '',
    player_id TEXT NOT NULL DEFAULT ''
"""


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "CREATE TABLE IF NOT EXISTS music_play_history ("
            "id BIGSERIAL PRIMARY KEY," + _COLUMNS + ")"
        )
    else:  # sqlite (tests)
        op.execute(
            "CREATE TABLE IF NOT EXISTS music_play_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT," + _COLUMNS + ")"
        )
    op.execute(
        "CREATE INDEX IF NOT EXISTS music_play_history_user_time "
        "ON music_play_history (zoe_user_id, played_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS music_play_history_uri_time "
        "ON music_play_history (uri, played_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS music_play_history")
