"""0026 — Zoe-owned rooms: a room model Zoe owns, optionally linked to HA.

Rooms exist in Zoe today only as an ILLUSION. ``smart_home_service._room_of()``
reads a room word out of a device's friendly name ("Living Room Light" → Living
Room) purely to group tiles; nothing is stored, nothing is user-editable, and a
device whose name omits its room is invisible to it. The live house already
breaks that: the first real Grid Connect switch arrived named "Bedroom 1 Switch
1", which name-parsing classifies as a *switch* rather than a light.

This makes rooms real and USER-OWNED, per the product doctrine: Zoe is the
product, Home Assistant is an organ she hides for a normal user and opens for a
power user. So:

  - A room is a ZOE record. Creating one requires no Home Assistant at all.
  - ``ha_area_id`` is an OPTIONAL link for a power user who already keeps areas
    in HA (it lets a later import pull that area's devices in). It is
    enrichment, never the source of truth — a NULL here is a completely normal
    room, not an unconfigured one.

Schema notes:
  - ``room_devices.entity_id`` is UNIQUE across the whole table, not just within
    a room: a device is in exactly ONE room. Without that, "the light in here"
    could resolve to a device claimed by two rooms and the voice path would have
    no non-arbitrary way to choose.
  - ``entity_id`` is stored as free TEXT and is NOT validated against a domain
    allow-list — the same rule ``panel_config.py`` documents. This house has
    zero ``light.*``; its controls are ``input_boolean.*``/``switch.*``/
    ``scene.*``, so an allow-list would reject every real device.
  - ``rooms.slug`` is the stable key a panel or an intent matches on, so it is
    UNIQUE. ``name`` stays free text for display and may be renamed freely.
  - ``panels.room_id`` follows 0021's precedent (a new panel-scoped fact goes in
    the ``panels`` row, never a second store). It is the STRUCTURED link;
    ``panels.location`` remains the free-text label it has always been and is
    deliberately left in place, so nothing that reads it today regresses. Where
    both exist, ``room_id`` is authoritative.

Nothing reads these tables yet — this migration is additive and inert. The
Rooms surface and ``_group_rooms()`` keep their name-derived behaviour until a
later change migrates them, so this cannot regress the panel.

``IF NOT EXISTS`` keeps it rerun-safe. ``ADD COLUMN IF NOT EXISTS`` is
PostgreSQL (production) and SQLite 3.35+ only, so the SQLite branch probes
``PRAGMA table_info`` instead — the same split 0021 uses.
"""

from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

_PANEL_COLUMNS = ("room_id",)


def _sqlite_existing_columns(bind) -> set[str]:
    rows = bind.exec_driver_sql("PRAGMA table_info(panels)").fetchall()
    return {str(r[1]) for r in rows}


def upgrade() -> None:
    op.execute("""
CREATE TABLE IF NOT EXISTS rooms (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    ha_area_id TEXT,
    sort INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
    # entity_id is globally UNIQUE: one device lives in exactly one room.
    op.execute("""
CREATE TABLE IF NOT EXISTS room_devices (
    room_id TEXT NOT NULL,
    entity_id TEXT NOT NULL UNIQUE,
    sort INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (room_id, entity_id),
    FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
)
""")
    op.execute("CREATE INDEX IF NOT EXISTS idx_room_devices_room ON room_devices(room_id)")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE panels ADD COLUMN IF NOT EXISTS room_id TEXT")
    else:
        existing = _sqlite_existing_columns(bind)
        for col in _PANEL_COLUMNS:
            if col not in existing:
                op.add_column("panels", sa.Column(col, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE panels DROP COLUMN IF EXISTS room_id")
    else:
        existing = _sqlite_existing_columns(bind)
        for col in _PANEL_COLUMNS:
            if col in existing:
                op.drop_column("panels", col)
    op.execute("DROP TABLE IF EXISTS room_devices")
    op.execute("DROP TABLE IF EXISTS rooms")
