"""0056 - the relay client-certificate registry (``relay_ingress.py``, ``routers/panel_relay.py``; flag ``ZOE_RELAY_INGRESS``,
default off). Design: ``docs/architecture/panel-anywhere.md`` sections 5.2 and 6.1.

ONE new table, so a deploy that has not run this migration degrades to "a relayed request is refused (503)" - the relay is
flag-dark and nothing else reads the table:

``panel_client_certs``: which client certificate (by the fingerprint nginx forwards as ``X-Zoe-Client-Cert-Fp``: lower-case hex
SHA-1, 40 chars; 64 for a SHA-256 form) belongs to which panel. ``fingerprint`` is the primary key, so a certificate is bound
to ONE panel for good; ``revoked_at`` is the stolen-panel switch (a revoked fingerprint is refused at once and is never
un-revoked - a new certificate gets a new fingerprint). Rows go with their panel (``ON DELETE CASCADE``). TEXT timestamps like
the rest of the panel tables, so the SQLite unit lane runs it. ``IF NOT EXISTS`` makes a rerun safe; the downgrade drops the table.

REVISION CHAIN NOTE: ``down_revision`` is ``0055`` (``0055_omi_segments_voices``, #2092, landing on ``main``). This PR is stacked
on #2089 (``fix/ha-control-member-or-panel``), whose base predates ``0054`` and ``0055``, so on THIS branch the chain has two heads
and ``tests/test_relay_ingress.py::test_migration_chain`` is red until main (0054, 0055) is merged in. Expected; do not change
``down_revision`` to make it green.
"""

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS panel_client_certs (
               fingerprint TEXT PRIMARY KEY,
               panel_id TEXT NOT NULL,
               note TEXT NOT NULL DEFAULT '',
               created_at TEXT NOT NULL,
               revoked_at TEXT,
               FOREIGN KEY (panel_id) REFERENCES panels(panel_id) ON DELETE CASCADE
           )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_panel_client_certs_panel ON panel_client_certs (panel_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_panel_client_certs_panel")
    op.execute("DROP TABLE IF EXISTS panel_client_certs")
