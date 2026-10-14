"""0053 - the contacts data model (``contacts_store.py``, ``routers/contacts.py``; design ``docs/architecture/contacts-data-model.md``).

ADDITIVE. Five new tables, four nullable/defaulted columns on ``person_important_dates``, and NO change to ``people`` or
``person_relationships``: a deploy that has not run this migration degrades to "the contacts routes are off" (the router is
flag-dark, ``ZOE_CONTACTS_API``) and every store call is fail-soft on a missing table. The forget cascade's new ``contacts`` and
``voiceprints`` steps read a missing table as "nothing to reach".

A Zoe person (``people``) stays the node of the people graph. What this adds is the CONTACT CARD around it, aligned to RFC 6350
(vCard 4.0) / RFC 9553 (JSContact), in the owning member's own layer (identity design section 5.5, layer 2):

  * ``contact_sources``      - one row per (member, provider, account): where synced contacts come from. ``provider`` is one of
                               local | google | microsoft | carddav | phone | introduction | omi_tag. ``source_account_id`` is the
                               connector's opaque account reference: OAuth tokens live in the vault, NEVER here. ``sync_token`` is
                               the provider's cursor (Google ``nextSyncToken``, Graph ``@odata.deltaLink``, CardDAV ``sync-token``).
  * ``source_contacts``      - one row per external contact as last seen: ``external_id`` (Google ``resourceName``, Graph ``id``,
                               CardDAV href), ``etag`` (Google ``etag``, Graph ``changeKey``, CardDAV ``getetag``), the normalised card
                               (``card_json``, never a photo blob). ``state`` = active | deleted_upstream | forgotten: a FORGOTTEN row is
                               content-free (card cleared) and is kept only so the next sync does not re-import the person.
  * ``person_contact_links`` - one Zoe person <-> many source contacts. A source contact is linked to at most ONE person at a time
                               (partial unique index). ``method`` says why (created_from | uid | email | phone | member_confirmed).
  * ``contact_merge_reviews``- an ambiguous merge is NEVER resolved by Zoe: it becomes a review item the member settles.
  * ``contact_fields``       - the multi-value card fields (name parts + pronunciation, nicknames, emails, phones, addresses, org,
                               title, urls, socials, IM, photo REFERENCE, notes, groups, pronouns), each with ``pref`` (RFC 1..100,
                               1 = most preferred), ``types``, ``ord`` (the card's order within a kind), and PROVENANCE: ``source`` + ``source_contact_id`` + ``provenance`` +
                               ``provenance_ref`` (a pointer, never words) + ``read_only`` (externally owned).
  * ``learned_voiceprints``  - the voiceprint a member's Omi tagging teaches Zoe for one of THEIR people (owner decision 2026-10-14,
                               ``docs/architecture/memories-space.md`` section 2.6): keyed by the TAGGING member, FK to that member's own
                               ``people`` row, suggest-tags only, never auth. Created here so the forget cascade owns its erase from day
                               one; the Omi P4b builder fills it. Not ``speaker_profiles``.

Dates (BDAY / ANNIVERSARY) are NOT a new store: they stay in ``person_important_dates`` (the birthday trigger's feed), which gains
``source`` / ``source_contact_id`` / ``provenance`` / ``read_only``. Relationships (vCard RELATED, Google ``relations``) map onto the
existing ``person_relationships`` graph, never a second edge store.

Every table is keyed by the owning member (``user_id``; ``owner_user_id`` on ``learned_voiceprints``, as its design names it) and
every read filters on it: member B never sees member A's synced contacts. Timestamps are TEXT UTC like the rest of the people
tables. ``IF NOT EXISTS`` (tables, indexes, PostgreSQL columns - no live-bind inspection, so ``--sql`` renders) and an inspector check
(SQLite columns) make a rerun safe. The
downgrade drops the new tables and columns: LOSSY by design (the synced cards are re-fetched from the provider by the next sync;
member-entered extra fields go with the tables).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0053"
down_revision = "0050"
branch_labels = None
depends_on = None

PROVIDERS = ("local", "google", "microsoft", "carddav", "phone", "introduction", "omi_tag")
FIELD_KINDS = ("name", "nickname", "email", "phone", "address", "org", "title", "url", "social", "im", "photo", "note",
               "group", "pronouns")
PROVENANCES = ("said_by_member", "entered_by_member", "synced", "approved_from_pendant", "introduction", "file_import")
_DATE_COLUMNS = (
    ("source", "TEXT NOT NULL DEFAULT 'local'"),
    ("source_contact_id", "TEXT"),
    ("provenance", "TEXT"),
    ("read_only", "INTEGER NOT NULL DEFAULT 0"),
)
TABLES = ("learned_voiceprints", "contact_fields", "contact_merge_reviews", "person_contact_links", "source_contacts",
          "contact_sources")       # drop order (children first)


def _in(values) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def statements(dialect: str) -> list[str]:
    """The CREATE statements (tables + indexes), in dependency order. Pure: tests run them on aiosqlite too."""
    blob = "BYTEA" if dialect == "postgresql" else "BLOB"
    return [
        f"""CREATE TABLE IF NOT EXISTS contact_sources (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               provider TEXT NOT NULL CHECK (provider IN {_in(PROVIDERS)}),
               source_account_id TEXT NOT NULL DEFAULT '',
               label TEXT NOT NULL DEFAULT '',
               sync_token TEXT,
               sync_token_at TEXT,
               last_synced_at TEXT,
               read_only INTEGER NOT NULL DEFAULT 1,
               status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'disconnected')),
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL,
               UNIQUE (user_id, provider, source_account_id)
           )""",
        """CREATE TABLE IF NOT EXISTS source_contacts (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               source_id TEXT NOT NULL REFERENCES contact_sources(id) ON DELETE CASCADE,
               external_id TEXT NOT NULL,
               etag TEXT,
               uid TEXT,
               card_json TEXT NOT NULL DEFAULT '{}',
               state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'deleted_upstream', 'forgotten')),
               last_synced_at TEXT,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL,
               UNIQUE (source_id, external_id)
           )""",
        "CREATE INDEX IF NOT EXISTS idx_source_contacts_user ON source_contacts (user_id, state)",
        """CREATE TABLE IF NOT EXISTS person_contact_links (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
               source_contact_id TEXT NOT NULL REFERENCES source_contacts(id) ON DELETE CASCADE,
               method TEXT NOT NULL CHECK (method IN ('created_from', 'uid', 'email', 'phone', 'member_confirmed')),
               linked_by TEXT NOT NULL,
               linked_at TEXT NOT NULL,
               evidence TEXT NOT NULL DEFAULT '{}',
               unlinked_at TEXT
           )""",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_person_contact_links_live ON person_contact_links (source_contact_id) "
        "WHERE unlinked_at IS NULL",
        "CREATE INDEX IF NOT EXISTS idx_person_contact_links_person ON person_contact_links (user_id, person_id)",
        """CREATE TABLE IF NOT EXISTS contact_merge_reviews (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               kind TEXT NOT NULL CHECK (kind IN ('link', 'relation')),
               source_contact_id TEXT REFERENCES source_contacts(id) ON DELETE CASCADE,
               person_id TEXT REFERENCES people(id) ON DELETE CASCADE,
               candidate_ids TEXT NOT NULL DEFAULT '[]',
               reason TEXT NOT NULL CHECK (reason IN ('name_only', 'several_candidates', 'identifier_conflict',
                                                      'relation_unresolved')),
               detail TEXT NOT NULL DEFAULT '{}',
               status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'linked', 'kept_separate', 'dismissed')),
               resolved_person_id TEXT REFERENCES people(id) ON DELETE SET NULL,
               resolved_by TEXT,
               created_at TEXT NOT NULL,
               resolved_at TEXT
           )""",
        "CREATE INDEX IF NOT EXISTS idx_contact_merge_reviews_open ON contact_merge_reviews (user_id, status)",
        f"""CREATE TABLE IF NOT EXISTS contact_fields (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
               kind TEXT NOT NULL CHECK (kind IN {_in(FIELD_KINDS)}),
               value TEXT NOT NULL DEFAULT '',
               value_norm TEXT NOT NULL DEFAULT '',
               parts TEXT NOT NULL DEFAULT '{{}}',
               types TEXT NOT NULL DEFAULT '',
               label TEXT,
               pref INTEGER CHECK (pref IS NULL OR (pref >= 1 AND pref <= 100)),
               ord INTEGER NOT NULL DEFAULT 0,
               source TEXT NOT NULL DEFAULT 'local' CHECK (source IN {_in(PROVIDERS)}),
               source_contact_id TEXT REFERENCES source_contacts(id) ON DELETE CASCADE,
               provenance TEXT NOT NULL CHECK (provenance IN {_in(PROVENANCES)}),
               provenance_ref TEXT,
               read_only INTEGER NOT NULL DEFAULT 0,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_contact_fields_person ON contact_fields (user_id, person_id, kind)",
        "CREATE INDEX IF NOT EXISTS idx_contact_fields_match ON contact_fields (user_id, kind, value_norm)",
        "CREATE INDEX IF NOT EXISTS idx_contact_fields_source ON contact_fields (source_contact_id)",
        f"""CREATE TABLE IF NOT EXISTS learned_voiceprints (
               id TEXT PRIMARY KEY,
               owner_user_id TEXT NOT NULL,
               people_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
               model_id TEXT NOT NULL,
               channel TEXT NOT NULL DEFAULT 'pendant',
               embedding {blob} NOT NULL,
               n_segments INTEGER NOT NULL DEFAULT 0,
               n_recordings INTEGER NOT NULL DEFAULT 0,
               status TEXT NOT NULL DEFAULT 'provisional' CHECK (status IN ('provisional', 'confirmed')),
               learning_enabled INTEGER NOT NULL DEFAULT 1,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL,
               last_confirmed_at TEXT,
               expires_at TEXT NOT NULL,
               UNIQUE (owner_user_id, people_id, model_id)
           )""",
        "CREATE INDEX IF NOT EXISTS idx_learned_voiceprints_owner ON learned_voiceprints (owner_user_id, people_id)",
    ]


def _date_columns(bind) -> set:
    insp = sa.inspect(bind)
    if not insp.has_table("person_important_dates"):
        return set()
    return {c["name"] for c in insp.get_columns("person_important_dates")}


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    for sql in statements(dialect):
        op.execute(sql)
    if dialect == "postgresql":
        # server-side guards, no live-bind inspection: ``alembic upgrade head --sql`` must render (test_migrations_offline_render)
        for name, ddl in _DATE_COLUMNS:
            op.execute(f"ALTER TABLE person_important_dates ADD COLUMN IF NOT EXISTS {name} {ddl}")
    else:
        have = _date_columns(bind)
        if not have:    # the table is 0006's; a bare lab database without it has nothing to extend
            return
        for name, ddl in _DATE_COLUMNS:
            if name not in have:
                op.execute(f"ALTER TABLE person_important_dates ADD COLUMN {name} {ddl}")
    op.execute("CREATE INDEX IF NOT EXISTS idx_person_important_dates_source ON person_important_dates (source_contact_id)")


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP INDEX IF EXISTS idx_person_important_dates_source")
    if bind.dialect.name == "postgresql":
        for name, _ddl in reversed(_DATE_COLUMNS):
            op.execute(f"ALTER TABLE person_important_dates DROP COLUMN IF EXISTS {name}")
    else:
        have = _date_columns(bind)
        for name, _ddl in reversed(_DATE_COLUMNS):
            if name in have:
                op.execute(f"ALTER TABLE person_important_dates DROP COLUMN {name}")
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
