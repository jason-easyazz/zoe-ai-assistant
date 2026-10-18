"""0059 - the relay client-certificate registry (``relay_ingress.py``, ``routers/panel_relay.py``; flag ``ZOE_RELAY_INGRESS``,
default off). Design: ``docs/architecture/panel-anywhere.md`` sections 5.2 and 6.1.

ONE new table, so a deploy that has not run this migration degrades to "a relayed request is refused (503)" - the relay is
flag-dark and nothing else reads the table:

``panel_client_certs``: which client certificate (by the fingerprint nginx forwards as ``X-Zoe-Client-Cert-Fp``: lower-case hex
SHA-1, 40 chars; 64 for a SHA-256 form) belongs to which panel. ``fingerprint`` is the primary key, so a certificate is bound
to ONE panel for good; ``revoked_at`` is the stolen-panel switch (a revoked fingerprint is refused at once and is never
un-revoked - a new certificate gets a new fingerprint). Rows go with their panel (``ON DELETE CASCADE``). TEXT timestamps like
the rest of the panel tables, so the SQLite unit lane runs it. ``IF NOT EXISTS`` makes a rerun safe; the downgrade drops the table.

REVISION CHAIN NOTE: ``down_revision`` is ``0057`` (#2109, struggle proposals), after ``0055`` (#2092) and ``0056`` (#2098, omi device
health). If #2109 has not merged when this lands, the chain shows two heads and ``tests/test_relay_ingress.py::test_migration_chain``
is red until it does; do not change ``down_revision`` to make it green.

RENUMBERED 0058 -> 0059 (2026-10-19, landing train): #2141 (``0058_turn_outcomes``) and #2146 (``0058_phone_app``) also took
0058. Revision ids are now distinct; ``down_revision`` stays on main's head (``0057``). Whichever of them lands FIRST, the next
one re-chains its ``down_revision`` onto it (the single-head test goes red until it does).
"""

from alembic import op

revision = "0059"
down_revision = "0057"
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
