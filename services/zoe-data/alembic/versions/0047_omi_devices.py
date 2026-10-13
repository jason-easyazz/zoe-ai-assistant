"""0047 - omi_devices: which household member owns which Omi pendant (``omi_recordings.py``; flag ``ZOE_OMI_DEVICE_OWNERS``).

ONE NEW table and no change to any existing one (``omi_recordings`` keeps its own ``user_id`` per row, which is what makes a reassignment
unable to move old recordings). Until ``ZOE_OMI_DEVICE_OWNERS`` is switched on nothing reads or writes it, so a deploy that has not run
this migration yet degrades to "the legacy single-owner upload" (every access in the module is fail-soft).

A pendant is identified by ``device_id``: the 16-hex salted hash the Pi derives from the BLE address (``omi_sync.uploader.device_hash`` ==
``panel_bt.model.device_id`` - ONE identity, so the id Settings > Bluetooth shows is the id uploads carry). The address itself never leaves
the Pi. Why a table of its own rather than a general ``ble_device_owners``: ownership only matters to ONE thing today - attributing
recordings - and that is Omi-specific (the upload header, the recording rows, the erase cascade). A second owned device type can
generalise it then; a generic table now would be a schema with one tenant and no second consumer.

  * ``user_id`` is the CURRENT owner, '' while unassigned. ``active`` is 1 only while it is assigned. An unassigned device keeps its row
    (that is what makes the one-time seed from ``ZOE_OMI_OWNER_USER_ID`` idempotent forever, including after an owner is erased).
  * ``label`` is a short admin-chosen name ("Kitchen pendant"); ``registered_by`` is the admin who made the current assignment;
    ``registered_at`` / ``updated_at`` are UTC text (``%Y-%m-%dT%H:%M:%SZ``) like ``omi_recordings``.
  * No FK to ``users`` (the per-user erase path sweeps rows by exact id, like ``omi_recordings``).

``IF NOT EXISTS`` runs unchanged on PostgreSQL and SQLite, so reruns are safe. The downgrade drops the table.
"""

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS omi_devices (
               device_id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL DEFAULT '',
               label TEXT NOT NULL DEFAULT '',
               registered_by TEXT NOT NULL DEFAULT '',
               registered_at TEXT NOT NULL,
               updated_at TEXT NOT NULL DEFAULT '',
               active INTEGER NOT NULL DEFAULT 1
           )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_omi_devices_user ON omi_devices (user_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS omi_devices")
