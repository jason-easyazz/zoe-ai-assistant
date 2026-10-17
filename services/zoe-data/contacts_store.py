"""The contacts data model's store (migration 0053; design ``docs/architecture/contacts-data-model.md``).

A Zoe person (``people``) is the node. Around it, in the OWNING MEMBER's layer (identity design section 5.5, layer 2):

  * ``contact_fields``        - the multi-value card fields, each with provenance (``source``, ``source_contact_id``, ``provenance``,
                                ``provenance_ref`` - a pointer, never words - and ``read_only`` for externally owned values);
  * ``contact_sources`` / ``source_contacts`` - where synced contacts come from and each external card as last seen;
  * ``person_contact_links``  - one person <-> many source contacts;
  * ``contact_merge_reviews`` - every merge Zoe is not SURE of, for the member to settle;
  * ``person_important_dates`` (0006, + provenance columns) - BDAY / ANNIVERSARY, the birthday trigger's feed;
  * ``person_relationships`` (the people graph) - vCard RELATED / Google ``relations``, written through ``people_graph``;
  * ``learned_voiceprints``   - erased here (``forget_voiceprints`` / ``forget_user``); written by the Omi P4b builder.

The merge rule (``import_card``): link automatically ONLY on one unambiguous strong identifier (the card's ``urn:uuid`` of a Zoe
person, an email, a phone) whose owner's name is compatible with the card's. A name-only match, several candidates, or an
identifier whose owner has a different name is a REVIEW ITEM - never a silent merge, never a duplicate person. No candidate at all
creates a new person, ``visibility = 'personal'`` (a member's synced contacts are theirs, not the household's).

Every query filters on the owning member. A synced field never overwrites what a member said: they are separate rows with their own
provenance. Legacy ``people.email`` / ``phone`` / ``birthday`` / ``notes`` are READ as ``provenance = 'legacy'`` entries and never
written from a sync (a family-visible row would leak synced data to the household).

No sync engine here (that is the connectors work). No request-time DDL. No ``.delete`` attribute call anywhere (the memory-loss
scanner): removals are SQL statements on these tables, and the forget path's removals are ``forget_entity`` / ``forget_voiceprints``
/ ``forget_user``, each called only by the audited erase paths.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import contacts_vcard

logger = logging.getLogger(__name__)

PROVIDERS = ("local", "google", "microsoft", "carddav", "phone", "introduction", "omi_tag")
EXTERNAL_PROVIDERS = frozenset({"google", "microsoft", "carddav"})        # externally owned by default (read_only)
FILE_PROVIDERS = ("phone", "local")
FIELD_KINDS = ("name", "nickname", "email", "phone", "address", "org", "title", "url", "social", "im", "photo", "note",
               "group", "pronouns")
PROVENANCE_FOR_PROVIDER = {"google": "synced", "microsoft": "synced", "carddav": "synced", "phone": "file_import",
                           "local": "file_import", "introduction": "introduction", "omi_tag": "approved_from_pendant"}
MEMBER_PROVENANCES = ("said_by_member", "entered_by_member")
MAX_VALUE = 2000
CARD_META = ("unmapped", "photos_inline_dropped", "cards_skipped")     # codec counters, never stored
MAX_PARTS_JSON = 4000

# card list key -> field kind (``name`` / ``groups`` / ``dates`` / ``related`` are handled on their own)
_LIST_KINDS = (("nicknames", "nickname"), ("emails", "email"), ("phones", "phone"), ("addresses", "address"), ("orgs", "org"),
               ("titles", "title"), ("urls", "url"), ("socials", "social"), ("ims", "im"), ("photos", "photo"),
               ("notes", "note"), ("pronouns", "pronouns"))
_KIND_TO_LIST = {k: lst for lst, k in _LIST_KINDS}

# The role the OTHER person plays for this card's person (``routers.people.RELATIONSHIP_TYPES`` labels) -> vCard RELATED TYPE.
ROLE_TO_VCARD = {"Partner": "sweetheart", "Spouse": "spouse", "Ex-partner": "acquaintance", "Parent": "parent", "Child": "child",
                 "Sibling": "sibling", "Grandparent": "kin", "Grandchild": "kin", "Aunt/Uncle": "kin", "Niece/Nephew": "kin",
                 "Cousin": "kin", "In-law": "kin", "Friend": "friend", "Best friend": "friend", "Met through": "met",
                 "Introduced to": "met", "Colleague": "colleague", "Boss": "co-worker", "Report": "co-worker",
                 "Mentor": "contact", "Mentee": "contact", "Client": "contact", "Provider": "contact", "Pet owner": "contact",
                 "Pet": "contact"}
# vCard RELATED TYPE -> (rel_type, role of the other person), when the card carries no X-ZOE-REL. Anything else is a review item.
VCARD_TO_REL = {"spouse": ("spouse", "Spouse"), "sweetheart": ("partner", "Partner"), "parent": ("parent", "Parent"),
                "child": ("parent", "Child"), "sibling": ("sibling", "Sibling"), "friend": ("friend", "Friend"),
                "colleague": ("colleague", "Colleague"), "co-worker": ("colleague", "Colleague"),
                "met": ("met_through", "Met through")}


class ContactsError(Exception):
    """A request the store refuses (bad kind/value, read-only field, not the member's person). The router maps it to 4xx."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fold(text: Any) -> str:
    from people_graph import fold_name
    return fold_name(str(text or ""))


def norm_email(value: str) -> str:
    v = (value or "").strip().lower().removeprefix("mailto:")
    return v if "@" in v and " " not in v else ""


def norm_phone(value: str) -> str:
    """The phone MATCH KEY: the last 9 digits (so ``0412 000 001`` and ``+61 412 000 001`` meet), '' under 6 digits (an extension,
    a short code: not an identifier). Display keeps the value as given."""
    digits = re.sub(r"\D", "", value or "")
    return digits[-9:] if len(digits) >= 6 else ""


def value_norm(kind: str, value: str) -> str:
    if kind == "email":
        return norm_email(value)
    if kind == "phone":
        return norm_phone(value)
    return ""


def _hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]


def tombstone_external_id(source_id: str, external_id: str) -> str:
    """What a FORGOTTEN source contact keeps instead of its external id (a CardDAV href can carry a name): a hash the sync engine
    can recompute from the id it is about to import, to skip it."""
    return "forgotten:" + _hash(source_id, external_id)


async def _all(db, sql: str, params: Iterable = ()) -> list:
    cur = await db.execute(sql, tuple(params))
    return list(await cur.fetchall())


async def _run(db, sql: str, params: Iterable = ()) -> int:
    cur = await db.execute(sql, tuple(params))
    return int(getattr(cur, "rowcount", 0) or 0)


def _qs(n: int) -> str:
    return ",".join("?" * n)


@asynccontextmanager
async def _atomic(db):
    """One transaction: on Postgres the asyncpg connection behind the compat wrapper (whose ``commit()`` is a no-op - each
    statement would otherwise be its own transaction), the same way ``people_graph.edge_transaction`` does it; on SQLite the
    implicit transaction, rolled back on any exception and committed on success."""
    from people_graph import _pg_conn

    conn = _pg_conn(db)
    if conn is not None:
        async with conn.transaction():
            yield
    elif hasattr(db, "rollback"):
        try:
            yield
        except BaseException:
            await db.rollback()
            raise
        await db.commit()
    else:
        yield
        await db.commit()


def _loads(raw: Any, default):
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


# -- people ------------------------------------------------------------------------------------------------------------------

_PERSON_COLS = ("id", "user_id", "name", "email", "phone", "birthday", "notes", "visibility", "deleted", "is_partial")


async def own_person(db, user_id: str, person_id: str) -> Optional[dict]:
    """The member's OWN live person row, or None. Contact cards are layer 2: another member's family-visible row is not yours."""
    rows = await _all(db, f"SELECT {', '.join(_PERSON_COLS)} FROM people WHERE id = ? AND user_id = ? "
                          "AND (deleted = 0 OR deleted IS NULL)", (person_id, user_id))
    return dict(zip(_PERSON_COLS, rows[0])) if rows else None


async def _live_people(db, user_id: str) -> list[dict]:
    rows = await _all(db, "SELECT id, name, email, phone, is_partial FROM people WHERE user_id = ? "
                          "AND (deleted = 0 OR deleted IS NULL)", (user_id,))
    return [dict(zip(("id", "name", "email", "phone", "is_partial"), r)) for r in rows]


async def _create_person(db, user_id: str, name: str, *, now: str) -> str:
    pid = str(uuid.uuid4())
    await _run(db, "INSERT INTO people (id, user_id, name, visibility, is_partial, deleted, created_at, updated_at) "
                   "VALUES (?, ?, ?, 'personal', 0, 0, ?, ?)", (pid, user_id, name, now, now))
    return pid


async def ensure_person(db, user_id: str, name: str, *, relationship: Optional[str] = None) -> tuple[str, bool]:
    """``(person_id, created)``: the member's live person whose name folds to ``name``, else a new PERSONAL row in their own layer
    (the pendant day review's approved "person" candidate). A relationship fills an empty one only, never overwrites."""
    key = fold(name)
    pid, created = next((p["id"] for p in await _live_people(db, user_id) if key and fold(p["name"]) == key), None), False
    if pid is None:
        pid, created = await _create_person(db, user_id, name, now=now_iso()), True
    if relationship:              # an existing person gains an EMPTY relationship too; a set one is never overwritten
        await _run(db, "UPDATE people SET relationship = ? WHERE id = ? AND user_id = ? AND relationship IS NULL",
                   (relationship[:60], pid, user_id))
    return pid, created


def names_compatible(a: str, b: str) -> bool:
    """Same person by name? One folded token set contains the other ("Dana" ~ "Dana Okafor"); an empty side is compatible."""
    ta, tb = set(fold(a).split()), set(fold(b).split())
    return not ta or not tb or ta <= tb or tb <= ta


# -- sources -----------------------------------------------------------------------------------------------------------------

async def ensure_source(db, user_id: str, provider: str, *, source_account_id: str = "", label: str = "",
                        read_only: Optional[bool] = None) -> dict:
    """The member's source row for (provider, account), created on first use. ``source_account_id`` is the connector's opaque
    account reference; tokens are the vault's, never stored here."""
    if provider not in PROVIDERS:
        raise ContactsError(f"unknown provider {provider!r}")
    rows = await _all(db, "SELECT id, read_only FROM contact_sources WHERE user_id = ? AND provider = ? AND source_account_id = ?",
                      (user_id, provider, source_account_id))
    if rows:
        return {"id": rows[0][0], "provider": provider, "read_only": bool(rows[0][1])}
    ro = (provider in EXTERNAL_PROVIDERS) if read_only is None else bool(read_only)
    sid, now = str(uuid.uuid4()), now_iso()
    await _run(db, "INSERT INTO contact_sources (id, user_id, provider, source_account_id, label, read_only, status, created_at, "
                   "updated_at) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
               (sid, user_id, provider, source_account_id, label, 1 if ro else 0, now, now))
    return {"id": sid, "provider": provider, "read_only": ro}


async def is_forgotten_external(db, user_id: str, source_id: str, external_id: str) -> bool:
    rows = await _all(db, "SELECT 1 FROM source_contacts WHERE user_id = ? AND source_id = ? AND external_id = ? AND state = 'forgotten'",
                      (user_id, source_id, tombstone_external_id(source_id, external_id)))
    return bool(rows)


# -- card <-> rows -----------------------------------------------------------------------------------------------------------

def _clip(v: Any) -> str:
    return str(v or "").strip()[:MAX_VALUE]


def _address_text(parts: dict) -> str:
    return ", ".join(str(parts[k]) for k in ("po_box", "extended", "street", "locality", "region", "postcode", "country")
                     if parts.get(k))


def card_field_rows(card: dict) -> list[dict]:
    """The ``contact_fields`` rows a card maps to (kind, value, value_norm, parts, types, label, pref). Pure."""
    rows: list[dict] = []
    name = {k: v for k, v in (card.get("name") or {}).items() if v}
    if card.get("full_name") or name:
        rows.append({"kind": "name", "value": _clip(card.get("full_name")), "parts": name, "types": [], "label": None,
                     "pref": None})
    for lst, kind in _LIST_KINDS:
        for it in card.get(lst) or []:
            parts = dict(it.get("parts") or {})
            value = _address_text(parts) if kind == "address" else _clip(it.get("value"))
            if not value:
                continue
            rows.append({"kind": kind, "value": value, "parts": parts, "types": list(it.get("types") or []),
                         "label": it.get("label"), "pref": it.get("pref")})
    for g in card.get("groups") or []:
        if _clip(g):
            rows.append({"kind": "group", "value": _clip(g), "parts": {}, "types": [], "label": None, "pref": None})
    seen: dict[str, int] = {}
    for r in rows:
        r["value_norm"] = value_norm(r["kind"], r["value"])
        r["ord"] = seen.get(r["kind"], 0)          # the card's own order within a kind (vCard order is meaningful)
        seen[r["kind"]] = r["ord"] + 1
    return rows


async def _insert_field(db, user_id: str, person_id: str, row: dict, *, source: str, source_contact_id: Optional[str],
                        provenance: str, provenance_ref: Optional[str], read_only: bool, now: str) -> str:
    fid = str(uuid.uuid4())
    parts = json.dumps(row.get("parts") or {}, ensure_ascii=False, sort_keys=True)
    if len(parts) > MAX_PARTS_JSON:
        raise ContactsError("field parts too large")
    pref = row.get("pref")
    if pref is not None and not (1 <= int(pref) <= 100):
        raise ContactsError("pref must be 1..100 (1 = most preferred)")
    await _run(db, "INSERT INTO contact_fields (id, user_id, person_id, kind, value, value_norm, parts, types, label, pref, ord, "
                   "source, source_contact_id, provenance, provenance_ref, read_only, created_at, updated_at) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (fid, user_id, person_id, row["kind"], row["value"], row.get("value_norm") or value_norm(row["kind"], row["value"]),
                parts, ",".join(row.get("types") or []), row.get("label"), pref, int(row.get("ord") or 0), source,
                source_contact_id, provenance, provenance_ref, 1 if read_only else 0, now, now))
    return fid


async def _apply_source_card(db, user_id: str, person_id: str, sc_id: str, card: dict, *, source: str, read_only: bool,
                             now: str) -> None:
    """Replace what THIS source contact contributes to the person (fields + dates); member-said rows are untouched."""
    provenance = PROVENANCE_FOR_PROVIDER.get(source, "synced")
    ref = f"source_contact:{sc_id}"
    await _run(db, "DELETE FROM contact_fields WHERE user_id = ? AND source_contact_id = ?", (user_id, sc_id))
    await _run(db, "DELETE FROM person_important_dates WHERE user_id = ? AND source_contact_id = ?", (user_id, sc_id))
    for row in card_field_rows(card):
        await _insert_field(db, user_id, person_id, row, source=source, source_contact_id=sc_id, provenance=provenance,
                            provenance_ref=ref, read_only=read_only, now=now)
    for d in card.get("dates") or []:
        label = "Birthday" if d["kind"] == "birthday" else "Anniversary"
        await _run(db, "INSERT INTO person_important_dates (id, person_id, user_id, label, date_type, month, day, year, created_at, "
                       "source, source_contact_id, provenance, read_only) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (str(uuid.uuid4()), person_id, user_id, label, d["kind"], d["month"], d["day"], d.get("year"), now, source,
                    sc_id, provenance, 1 if read_only else 0))


# -- import (the merge model) ------------------------------------------------------------------------------------------------

@dataclass
class ImportOutcome:
    action: str                    # created | linked | updated | unchanged | review | dismissed | forgotten | skipped
    person_id: Optional[str] = None
    source_contact_id: Optional[str] = None
    review_id: Optional[str] = None
    reason: str = ""
    relation_reviews: int = 0
    edges: int = 0


async def _strong_candidates(db, user_id: str, card: dict, live: list[dict]) -> dict[str, set]:
    """{person_id: {'uid'|'email'|'phone', ...}} among the member's live people, from the card's strong identifiers."""
    live_ids = {p["id"] for p in live}
    hits: dict[str, set] = {}
    uid = str(card.get("uid") or "")
    m = re.fullmatch(r"urn:uuid:([0-9a-fA-F-]{36})", uid)
    if m and m.group(1).lower() in live_ids:
        hits.setdefault(m.group(1).lower(), set()).add("uid")
    keys = {("email", norm_email(e.get("value", ""))) for e in card.get("emails") or []}
    keys |= {("phone", norm_phone(p.get("value", ""))) for p in card.get("phones") or []}
    keys = {(k, v) for k, v in keys if v}
    for kind, v in keys:
        for (pid,) in await _all(db, "SELECT person_id FROM contact_fields WHERE user_id = ? AND kind = ? AND value_norm = ?",
                                 (user_id, kind, v)):
            if pid in live_ids:
                hits.setdefault(pid, set()).add(kind)
    for p in live:                                   # the legacy single-value columns count too
        if p.get("email") and ("email", norm_email(p["email"])) in keys:
            hits.setdefault(p["id"], set()).add("email")
        if p.get("phone") and ("phone", norm_phone(p["phone"])) in keys:
            hits.setdefault(p["id"], set()).add("phone")
    return hits


async def _open_review(db, user_id: str, *, kind: str, reason: str, source_contact_id: Optional[str] = None,
                       person_id: Optional[str] = None, candidates: Iterable[str] = (), detail: Optional[dict] = None,
                       now: str) -> str:
    if source_contact_id and kind == "link":
        rows = await _all(db, "SELECT id FROM contact_merge_reviews WHERE user_id = ? AND source_contact_id = ? AND kind = 'link' "
                              "AND status = 'open'", (user_id, source_contact_id))
        if rows:
            await _run(db, "UPDATE contact_merge_reviews SET candidate_ids = ?, reason = ? WHERE id = ?",
                       (json.dumps(sorted(candidates)), reason, rows[0][0]))
            return rows[0][0]
    rid = str(uuid.uuid4())
    await _run(db, "INSERT INTO contact_merge_reviews (id, user_id, kind, source_contact_id, person_id, candidate_ids, reason, detail, "
                   "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
               (rid, user_id, kind, source_contact_id, person_id, json.dumps(sorted(candidates)), reason,
                json.dumps(detail or {}, ensure_ascii=False), now))
    return rid


async def _link(db, user_id: str, person_id: str, sc_id: str, *, method: str, linked_by: str, evidence: dict, now: str) -> None:
    await _run(db, "INSERT INTO person_contact_links (id, user_id, person_id, source_contact_id, method, linked_by, linked_at, evidence) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
               (str(uuid.uuid4()), user_id, person_id, sc_id, method, linked_by, now, json.dumps(evidence, sort_keys=True)))


async def _close_stale_links(db, user_id: str, sc_id: str, now: str) -> None:
    """A live link whose person is soft-deleted (the People page's delete) is closed, so the card can be matched afresh
    instead of tripping the one-live-link index."""
    await _run(db, "UPDATE person_contact_links SET unlinked_at = ? WHERE user_id = ? AND source_contact_id = ? AND unlinked_at IS NULL "
                   "AND person_id IN (SELECT id FROM people WHERE deleted = 1)", (now, user_id, sc_id))


async def _linked_person(db, user_id: str, sc_id: str) -> Optional[str]:
    rows = await _all(db, "SELECT l.person_id FROM person_contact_links l JOIN people p ON p.id = l.person_id "
                          "WHERE l.user_id = ? AND l.source_contact_id = ? AND l.unlinked_at IS NULL "
                          "AND (p.deleted = 0 OR p.deleted IS NULL)", (user_id, sc_id))
    return rows[0][0] if rows else None


def _rel_spec(rel_type: str):
    from routers.people import RELATIONSHIP_TYPES
    for group, entries in RELATIONSHIP_TYPES.items():
        for key, lbl_a, lbl_b in entries:
            if key == rel_type:
                return group, lbl_a, lbl_b
    return None


async def write_relation(db, user_id: str, person_id: str, other_id: str, rel_type: str, role: str, *, sc_id: str) -> bool:
    """One edge on the people graph saying ``other`` is ``role`` to ``person`` - only when the pair has NO current edge either way
    (what a member said is never superseded by a sync). Evidence = the source contact, origin = ``contact_sync``."""
    import people_graph as pg
    spec = _rel_spec(rel_type)
    if spec is None or person_id == other_id:
        return False
    group, lbl_a, lbl_b = spec
    if role == lbl_a:
        a, b = person_id, other_id
    elif role == lbl_b:
        a, b = other_id, person_id
    else:
        return False
    if await pg.current_edge(db, user_id, a, b) or await pg.current_edge(db, user_id, b, a):
        return False
    return await pg.insert_edge(db, user_id=user_id, edge_id=str(uuid.uuid4()), person_a_id=a, person_b_id=b,
                                spec=pg.EdgeSpec(rel_type=rel_type, rel_a_to_b=lbl_a, rel_b_to_a=lbl_b, rel_group=group,
                                                 origin="contact_sync", evidence=pg.Evidence(turn_id=f"source_contact:{sc_id}")))


async def _import_relations(db, user_id: str, person_id: str, sc_id: str, card: dict, live: list[dict], *,
                            now: str) -> tuple[int, int]:
    edges = reviews = 0
    for r in card.get("related") or []:
        name = (r.get("name") or "").strip()
        if not name:
            continue
        mapped = None
        if r.get("zoe_rel") and r.get("zoe_role") and _rel_spec(r["zoe_rel"]):
            mapped = (r["zoe_rel"], r["zoe_role"])
        else:
            for t in r.get("types") or []:
                if t in VCARD_TO_REL:
                    mapped = VCARD_TO_REL[t]
                    break
        hits = [p["id"] for p in live if fold(p["name"]) == fold(name) and p["id"] != person_id]
        if mapped and len(hits) == 1:
            if await write_relation(db, user_id, person_id, hits[0], mapped[0], mapped[1], sc_id=sc_id):
                edges += 1
            continue
        await _open_review(db, user_id, kind="relation", reason="relation_unresolved", source_contact_id=sc_id, person_id=person_id,
                           candidates=hits, detail={"name": name, "types": r.get("types") or [],
                                                    "rel_type": mapped[0] if mapped else None,
                                                    "role": mapped[1] if mapped else None}, now=now)
        reviews += 1
    return edges, reviews


_UNSET: Any = object()


async def forgotten_pattern(user_id: str):
    """The member's forget-ledger matcher (``memory_forgotten.LedgerPattern``: salted hashes, no names), or None (ledger off, empty,
    or unreachable). An incoming card is checked against it so a sync can never bring a forgotten person back."""
    try:
        import memory_forgotten
        return await memory_forgotten.ledger_pattern(user_id)
    except Exception as exc:  # noqa: BLE001 - the ledger being down never blocks an import; the tombstones still hold
        logger.warning("contacts_store: forget ledger unreadable for an import (%s)", type(exc).__name__)
        return None


async def _forget_source_contact(db, user_id: str, sc_id: str, source_id: str, external_id: str, now: str) -> None:
    """Make one source card a content-free FORGOTTEN tombstone (its fields, synced dates and links go). If a tombstone for the same
    external id already exists, this row is removed instead (``UNIQUE (source_id, external_id)``)."""
    tomb = tombstone_external_id(source_id, external_id) if not external_id.startswith("forgotten:") else external_id
    await _run(db, "DELETE FROM contact_fields WHERE user_id = ? AND source_contact_id = ?", (user_id, sc_id))
    await _run(db, "DELETE FROM person_important_dates WHERE user_id = ? AND source_contact_id = ?", (user_id, sc_id))
    await _run(db, "DELETE FROM person_contact_links WHERE user_id = ? AND source_contact_id = ?", (user_id, sc_id))
    await _run(db, "DELETE FROM contact_merge_reviews WHERE user_id = ? AND source_contact_id = ?", (user_id, sc_id))
    if await _all(db, "SELECT 1 FROM source_contacts WHERE source_id = ? AND external_id = ? AND id != ?", (source_id, tomb, sc_id)):
        await _run(db, "DELETE FROM source_contacts WHERE id = ? AND user_id = ?", (sc_id, user_id))
        return
    await _run(db, "UPDATE source_contacts SET state = 'forgotten', card_json = '{}', uid = NULL, etag = NULL, external_id = ?, "
                   "updated_at = ? WHERE id = ? AND user_id = ?", (tomb, now, sc_id, user_id))


async def _tombstone_incoming(db, user_id: str, source_id: str, external_id: str, now: str) -> None:
    tomb = tombstone_external_id(source_id, external_id)
    async with _atomic(db):
        live = await _all(db, "SELECT id FROM source_contacts WHERE user_id = ? AND source_id = ? AND external_id = ?",
                          (user_id, source_id, external_id))
        if live:                                    # the card was here before the forget reached it: convert, never a second row
            await _forget_source_contact(db, user_id, live[0][0], source_id, external_id, now)
        elif not await _all(db, "SELECT 1 FROM source_contacts WHERE source_id = ? AND external_id = ?", (source_id, tomb)):
            await _run(db, "INSERT INTO source_contacts (id, user_id, source_id, external_id, card_json, state, created_at, updated_at) "
                           "VALUES (?, ?, ?, ?, '{}', 'forgotten', ?, ?)", (str(uuid.uuid4()), user_id, source_id, tomb, now, now))


async def import_card(db, user_id: str, source: dict, card: dict, *, external_id: str, etag: Optional[str] = None,
                      relations: bool = True, forgotten: Any = _UNSET) -> ImportOutcome:
    """Bring one external card in for ``user_id`` (the merge model; see the module doc). Atomic per card. ``relations=False`` defers
    the RELATED lines to the caller (a batch resolves them after every card is in, so the order of a file does not matter).
    ``forgotten`` is the member's forget-ledger matcher (fetched when not given): a card that IS a forgotten person is refused and
    tombstoned; any other card loses the entries that name one (a note, a RELATED line) before anything is stored."""
    now = now_iso()
    sid = source["id"]
    if await is_forgotten_external(db, user_id, sid, external_id):
        return ImportOutcome("forgotten", reason="forgotten")
    rx = await forgotten_pattern(user_id) if forgotten is _UNSET else forgotten
    if rx is not None:
        if _card_is_about(card, rx):
            await _tombstone_incoming(db, user_id, sid, external_id, now)
            return ImportOutcome("forgotten", reason="forgotten")
        card = _redact(card, rx)
    full = (card.get("full_name") or "").strip()
    if not full:
        return ImportOutcome("skipped", reason="no_name")
    card_json = json.dumps({k: v for k, v in card.items() if k not in CARD_META},
                           ensure_ascii=False, sort_keys=True)
    read_only = bool(source.get("read_only"))
    provider = source["provider"]
    async with _atomic(db):
        rows = await _all(db, "SELECT id, card_json, etag, state FROM source_contacts WHERE source_id = ? AND external_id = ?",
                          (sid, external_id))
        if rows:
            sc_id, old_json, old_etag = rows[0][0], rows[0][1], rows[0][2]
            unchanged = old_json == card_json and (etag is None or etag == old_etag)
            await _run(db, "UPDATE source_contacts SET card_json = ?, etag = ?, uid = ?, state = 'active', last_synced_at = ?, "
                           "updated_at = ? WHERE id = ?", (card_json, etag, card.get("uid"), now, now, sc_id))
        else:
            sc_id, unchanged = str(uuid.uuid4()), False
            await _run(db, "INSERT INTO source_contacts (id, user_id, source_id, external_id, etag, uid, card_json, state, last_synced_at, "
                           "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
                       (sc_id, user_id, sid, external_id, etag, card.get("uid"), card_json, now, now, now))
        live = await _live_people(db, user_id)
        pid = await _linked_person(db, user_id, sc_id)
        if not pid:
            await _close_stale_links(db, user_id, sc_id, now)
            if await _all(db, "SELECT 1 FROM contact_merge_reviews WHERE user_id = ? AND source_contact_id = ? AND kind = 'link' "
                              "AND status = 'dismissed'", (user_id, sc_id)):
                return ImportOutcome("dismissed", source_contact_id=sc_id)      # the member said no: never asked again
        if pid:
            if unchanged:
                return ImportOutcome("unchanged", person_id=pid, source_contact_id=sc_id)
            await _apply_source_card(db, user_id, pid, sc_id, card, source=provider, read_only=read_only, now=now)
            edges, rrev = await _import_relations(db, user_id, pid, sc_id, card, live, now=now) if relations else (0, 0)
            return ImportOutcome("updated", person_id=pid, source_contact_id=sc_id, relation_reviews=rrev, edges=edges)
        names = {p["id"]: p["name"] for p in live}
        strong = await _strong_candidates(db, user_id, card, live)
        if len(strong) == 1:
            (cand, how), = strong.items()
            if names_compatible(names.get(cand, ""), full):
                method = "uid" if "uid" in how else ("email" if "email" in how else "phone")
                await _link(db, user_id, cand, sc_id, method=method, linked_by="zoe", evidence={"matched": sorted(how)}, now=now)
                await _apply_source_card(db, user_id, cand, sc_id, card, source=provider, read_only=read_only, now=now)
                edges, rrev = await _import_relations(db, user_id, cand, sc_id, card, live, now=now) if relations else (0, 0)
                return ImportOutcome("linked", person_id=cand, source_contact_id=sc_id, reason=method, relation_reviews=rrev,
                                     edges=edges)
            rid = await _open_review(db, user_id, kind="link", reason="identifier_conflict", source_contact_id=sc_id,
                                     candidates=[cand], detail={"matched": sorted(how)}, now=now)
            return ImportOutcome("review", source_contact_id=sc_id, review_id=rid, reason="identifier_conflict")
        if len(strong) > 1:
            rid = await _open_review(db, user_id, kind="link", reason="several_candidates", source_contact_id=sc_id,
                                     candidates=list(strong), now=now)
            return ImportOutcome("review", source_contact_id=sc_id, review_id=rid, reason="several_candidates")
        by_name = [p["id"] for p in live if fold(p["name"]) == fold(full)]
        if by_name:
            reason = "name_only" if len(by_name) == 1 else "several_candidates"
            rid = await _open_review(db, user_id, kind="link", reason=reason, source_contact_id=sc_id, candidates=by_name, now=now)
            return ImportOutcome("review", source_contact_id=sc_id, review_id=rid, reason=reason)
        pid = await _create_person(db, user_id, full, now=now)
        await _link(db, user_id, pid, sc_id, method="created_from", linked_by="zoe", evidence={}, now=now)
        await _apply_source_card(db, user_id, pid, sc_id, card, source=provider, read_only=read_only, now=now)
        edges, rrev = (await _import_relations(db, user_id, pid, sc_id, card, live + [{"id": pid, "name": full}], now=now)
                       if relations else (0, 0))
        return ImportOutcome("created", person_id=pid, source_contact_id=sc_id, relation_reviews=rrev, edges=edges)


def vcard_external_id(card: dict) -> str:
    """A file import's external id: the card's UID; else a hash of its name + identifiers (stable across re-imports); a card with
    neither hashes its whole content, so two different "John Smith" cards never collapse into one source card (a silent merge)."""
    if card.get("uid"):
        return str(card["uid"])
    ids = [norm_email(e.get("value", "")) for e in card.get("emails") or []] + \
          [norm_phone(p.get("value", "")) for p in card.get("phones") or []]
    ids = sorted(i for i in ids if i)
    if ids:
        return "vcf:" + _hash(fold(card.get("full_name")), *ids)
    body = json.dumps({k: v for k, v in card.items() if k not in CARD_META}, sort_keys=True,
                      ensure_ascii=False)
    return "vcf:" + _hash("content", body)


async def import_vcf(db, user_id: str, text: str, *, provider: str = "phone", source_account_id: str = "file") -> dict:
    """Import every card of a .vcf for the member; a per-action count plus the review ids. Inline photos are dropped (counted).
    A file is ``phone`` or ``local`` (provenance ``file_import``): it is never labelled as synced from an account it did not come
    through."""
    if provider not in FILE_PROVIDERS:
        raise ContactsError("a file import is provider phone or local")
    source = await ensure_source(db, user_id, provider, source_account_id=source_account_id, label="vCard file")
    await db.commit()
    report: dict[str, Any] = {"cards": 0, "created": 0, "linked": 0, "updated": 0, "unchanged": 0, "review": 0, "forgotten": 0,
                              "dismissed": 0, "cards_skipped": 0,
                              "skipped": 0, "edges": 0, "relation_reviews": 0, "photos_inline_dropped": 0, "unmapped": 0,
                              "review_ids": [], "person_ids": []}
    landed: list[tuple[str, str, dict]] = []
    rx = await forgotten_pattern(user_id)
    for card in contacts_vcard.parse_vcards(text):
        report["cards"] += 1
        report["photos_inline_dropped"] += card.get("photos_inline_dropped", 0)
        report["unmapped"] += card.get("unmapped", 0)
        report["cards_skipped"] += card.get("cards_skipped", 0)
        out = await import_card(db, user_id, source, card, external_id=vcard_external_id(card), relations=False, forgotten=rx)
        if rx is not None and out.action not in ("forgotten", "skipped"):
            card = _redact(card, rx)
        report[out.action] += 1
        if out.review_id:
            report["review_ids"].append(out.review_id)
        if out.person_id:
            report["person_ids"].append(out.person_id)
            if out.action != "unchanged" and card.get("related"):
                landed.append((out.person_id, out.source_contact_id, card))
    if landed:                       # RELATED after every card is in: "Tove" later in the file still resolves
        live = await _live_people(db, user_id)
        for pid, sc_id, card in landed:
            async with _atomic(db):
                edges, rrev = await _import_relations(db, user_id, pid, sc_id, card, live, now=now_iso())
            report["edges"] += edges
            report["relation_reviews"] += rrev
    return report


# -- reviews -----------------------------------------------------------------------------------------------------------------

_REVIEW_COLS = ("id", "kind", "source_contact_id", "person_id", "candidate_ids", "reason", "detail", "status", "resolved_person_id",
                "created_at", "resolved_at")


async def list_reviews(db, user_id: str, status: str = "open") -> list[dict]:
    rows = await _all(db, f"SELECT {', '.join(_REVIEW_COLS)} FROM contact_merge_reviews WHERE user_id = ? AND status = ? "
                          "ORDER BY created_at", (user_id, status))
    out = []
    for r in rows:
        d = dict(zip(_REVIEW_COLS, r))
        d["candidate_ids"] = _loads(d["candidate_ids"], [])
        d["detail"] = _loads(d["detail"], {})
        if d["source_contact_id"]:
            sc = await _all(db, "SELECT card_json FROM source_contacts WHERE id = ? AND user_id = ?", (d["source_contact_id"], user_id))
            card = _loads(sc[0][0], {}) if sc else {}
            d["incoming"] = {"full_name": card.get("full_name", ""), "emails": [e.get("value") for e in card.get("emails") or []],
                             "phones": [p.get("value") for p in card.get("phones") or []]}
        out.append(d)
    return out


async def resolve_review(db, user_id: str, review_id: str, action: str, *, person_id: Optional[str] = None) -> dict:
    """The member settles a review: ``link`` (to ``person_id``), ``new`` (a separate person), ``dismiss``. Only the member's own
    reviews and people; a resolved review cannot be resolved again."""
    rows = await _all(db, f"SELECT {', '.join(_REVIEW_COLS)} FROM contact_merge_reviews WHERE id = ? AND user_id = ?",
                      (review_id, user_id))
    if not rows:
        raise ContactsError("review not found", 404)
    rv = dict(zip(_REVIEW_COLS, rows[0]))
    if rv["status"] != "open":
        raise ContactsError("review already resolved", 409)
    if action not in ("link", "new", "dismiss"):
        raise ContactsError("action must be link, new or dismiss")
    now = now_iso()
    resolved_pid = None
    async with _atomic(db):
        if action == "dismiss":
            status = "dismissed"
        elif rv["kind"] == "relation":
            if action != "link" or not person_id or not await own_person(db, user_id, person_id):
                raise ContactsError("a relation review is settled by linking it to one of your people", 400)
            detail = _loads(rv["detail"], {})
            if not detail.get("rel_type") or not detail.get("role"):
                raise ContactsError("this relation has no type Zoe can map; add it on the person instead", 400)
            await write_relation(db, user_id, rv["person_id"], person_id, detail["rel_type"], detail["role"],
                                 sc_id=rv["source_contact_id"] or "")
            status, resolved_pid = "linked", person_id
        else:
            sc_rows = await _all(db, "SELECT s.card_json, c.provider, c.read_only FROM source_contacts s JOIN contact_sources c "
                                     "ON c.id = s.source_id WHERE s.id = ? AND s.user_id = ?", (rv["source_contact_id"], user_id))
            if not sc_rows:
                raise ContactsError("the incoming contact is gone", 410)
            card, provider, ro = _loads(sc_rows[0][0], {}), sc_rows[0][1], bool(sc_rows[0][2])
            await _close_stale_links(db, user_id, rv["source_contact_id"], now)
            if await _linked_person(db, user_id, rv["source_contact_id"]):
                raise ContactsError("this contact is already linked to one of your people", 409)
            if action == "link":
                if not person_id or not await own_person(db, user_id, person_id):
                    raise ContactsError("link needs one of your people", 400)
                target, status = person_id, "linked"
            else:
                target, status = await _create_person(db, user_id, card.get("full_name") or "Unnamed", now=now), "kept_separate"
            await _link(db, user_id, target, rv["source_contact_id"], method="member_confirmed", linked_by=user_id,
                        evidence={"review": review_id}, now=now)
            await _apply_source_card(db, user_id, target, rv["source_contact_id"], card, source=provider, read_only=ro, now=now)
            await _import_relations(db, user_id, target, rv["source_contact_id"], card, await _live_people(db, user_id), now=now)
            resolved_pid = target
        await _run(db, "UPDATE contact_merge_reviews SET status = ?, resolved_person_id = ?, resolved_by = ?, resolved_at = ? "
                       "WHERE id = ? AND user_id = ?", (status, resolved_pid, user_id, now, review_id, user_id))
    return {"id": review_id, "status": status, "person_id": resolved_pid}


# -- member edits ------------------------------------------------------------------------------------------------------------

def _validate_field(kind: str, value: str, parts: Any, types: Any, pref: Any) -> dict:
    if kind not in FIELD_KINDS:
        raise ContactsError(f"kind must be one of {', '.join(FIELD_KINDS)}")
    if not isinstance(parts, dict):
        raise ContactsError("parts must be an object")
    value = _clip(value) or (_address_text(parts) if kind == "address" else "")
    if not value:
        raise ContactsError("value is required")
    if kind == "photo" and not re.match(r"^(https://|zoe-media:)", value):
        raise ContactsError("a photo is a reference (https:// or zoe-media:<id>), never inline data")
    if kind == "email" and not norm_email(value):
        raise ContactsError("not an email address")
    if not isinstance(types, list) or not all(isinstance(t, str) and re.fullmatch(r"[a-z0-9-]{1,24}", t) for t in types):
        raise ContactsError("types are short lower-case words")
    if pref is not None and (not isinstance(pref, int) or not 1 <= pref <= 100):
        raise ContactsError("pref must be 1..100 (1 = most preferred)")
    return {"kind": kind, "value": value, "value_norm": value_norm(kind, value), "parts": parts, "types": types, "pref": pref}


async def add_field(db, user_id: str, person_id: str, *, kind: str, value: str = "", parts: Optional[dict] = None,
                    types: Optional[list] = None, pref: Optional[int] = None, label: Optional[str] = None,
                    provenance: str = "entered_by_member", provenance_ref: Optional[str] = None) -> dict:
    """A field the MEMBER gives (typed, or said in a turn): ``source = 'local'``, never read-only."""
    if provenance not in MEMBER_PROVENANCES:
        raise ContactsError("a member edit is said_by_member or entered_by_member")
    if not await own_person(db, user_id, person_id):
        raise ContactsError("person not found", 404)
    row = _validate_field(kind, value, parts or {}, types or [], pref)
    row["label"] = (label or None) and str(label)[:64]
    n = await _all(db, "SELECT COUNT(*) FROM contact_fields WHERE user_id = ? AND person_id = ? AND kind = ?", (user_id, person_id, kind))
    row["ord"] = int(n[0][0] or 0) if n else 0        # after what is already there
    fid = await _insert_field(db, user_id, person_id, row, source="local", source_contact_id=None, provenance=provenance,
                              provenance_ref=provenance_ref, read_only=False, now=now_iso())
    await db.commit()
    return {"id": fid, **row, "source": "local", "provenance": provenance, "read_only": False}


async def remove_field(db, user_id: str, field_id: str) -> bool:
    """Remove one of the member's own fields. A read-only (synced) field is the provider's: change it there."""
    rows = await _all(db, "SELECT read_only FROM contact_fields WHERE id = ? AND user_id = ?", (field_id, user_id))
    if not rows:
        raise ContactsError("field not found", 404)
    if int(rows[0][0] or 0):
        raise ContactsError("this field is synced and read-only: change it in the account it came from", 409)
    await _run(db, "DELETE FROM contact_fields WHERE id = ? AND user_id = ? AND read_only = 0", (field_id, user_id))
    await db.commit()
    return True


# -- read / export -----------------------------------------------------------------------------------------------------------

_FIELD_COLS = ("id", "kind", "value", "parts", "types", "label", "pref", "source", "source_contact_id", "provenance",
               "provenance_ref", "read_only", "updated_at")


def _pref_key(it: dict):
    return (it.get("pref") is None, it.get("pref") or 0, it.get("provenance") not in MEMBER_PROVENANCES)


async def card_for_person(db, user_id: str, person_id: str) -> Optional[dict]:
    """The member's card for one of THEIR people: every field with its provenance, dates, current relationships, links, plus the
    legacy single-value columns as ``provenance = 'legacy'`` entries where no field already carries the same value."""
    import people_graph as pg
    person = await own_person(db, user_id, person_id)
    if person is None:
        return None
    card = contacts_vcard.empty_card()
    card.pop("unmapped"), card.pop("photos_inline_dropped")
    card["uid"] = f"urn:uuid:{person_id}"
    card["full_name"] = person["name"]
    card["person_id"] = person_id
    names: list[dict] = []
    for r in await _all(db, f"SELECT {', '.join(_FIELD_COLS)} FROM contact_fields WHERE user_id = ? AND person_id = ? "
                            "ORDER BY ord, created_at, id", (user_id, person_id)):
        it = dict(zip(_FIELD_COLS, r))
        it["parts"] = _loads(it["parts"], {})
        it["types"] = [t for t in (it["types"] or "").split(",") if t]
        it["read_only"] = bool(it["read_only"])
        if it["kind"] == "name":
            names.append(it)
        elif it["kind"] == "group":
            if it["value"] not in card["groups"]:
                card["groups"].append(it["value"])
        else:
            card[_KIND_TO_LIST[it["kind"]]].append(it)
    if names:
        best = sorted(names, key=lambda n: (n["provenance"] not in MEMBER_PROVENANCES, n["updated_at"] or ""))[0]
        card["name"] = best["parts"]
        card["name_sources"] = [{"id": n["id"], "value": n["value"], "source": n["source"], "provenance": n["provenance"],
                                 "read_only": n["read_only"]} for n in names]
    seen = {(k, value_norm(k, it["value"]) or it["value"]) for lst, k in _LIST_KINDS for it in card[lst]}
    for col, kind in (("email", "email"), ("phone", "phone"), ("notes", "note")):
        v = (person.get(col) or "").strip()
        if v and (kind, value_norm(kind, v) or v) not in seen:
            card[_KIND_TO_LIST[kind]].append({"id": None, "value": v, "types": [], "pref": None, "parts": {}, "source": "local",
                                              "provenance": "legacy", "read_only": False})
    for lst, _k in _LIST_KINDS:
        card[lst].sort(key=_pref_key)
    for r in await _all(db, "SELECT id, date_type, month, day, year, source, provenance, read_only FROM person_important_dates "
                            "WHERE person_id = ? AND user_id = ? ORDER BY month, day", (person_id, user_id)):
        if r[1] in ("birthday", "anniversary") and r[2] and r[3]:
            card["dates"].append({"id": r[0], "kind": r[1], "month": r[2], "day": r[3], "year": r[4], "source": r[5] or "local",
                                  "provenance": r[6] or "legacy", "read_only": bool(r[7])})
    if person.get("birthday") and not any(d["kind"] == "birthday" for d in card["dates"]):
        d = contacts_vcard.parse_date(str(person["birthday"]))
        if d:
            card["dates"].append({"id": None, "kind": "birthday", "year": d[0], "month": d[1], "day": d[2], "source": "local",
                                  "provenance": "legacy", "read_only": False})
    for e in await pg.edges_for_person(db, user_id, person_id):
        other = e["person_b_id"] if e["person_a_id"] == person_id else e["person_a_id"]
        role = e["rel_a_to_b"] if e["person_a_id"] == person_id else e["rel_b_to_a"]
        o = await own_person(db, user_id, other)
        if o is None:
            continue
        card["related"].append({"name": o["name"], "uri": None, "person_id": other, "types": [ROLE_TO_VCARD.get(role, "contact")],
                                "zoe_rel": e["rel_type"], "zoe_role": role, "edge_id": e["id"]})
    card["links"] = [dict(zip(("source_contact_id", "method", "linked_by", "linked_at", "provider"), r)) for r in await _all(
        db, "SELECT l.source_contact_id, l.method, l.linked_by, l.linked_at, c.provider FROM person_contact_links l "
            "JOIN source_contacts s ON s.id = l.source_contact_id JOIN contact_sources c ON c.id = s.source_id "
            "WHERE l.user_id = ? AND l.person_id = ? AND l.unlinked_at IS NULL", (user_id, person_id))]
    return card


async def export_vcard(db, user_id: str, person_id: str) -> Optional[str]:
    card = await card_for_person(db, user_id, person_id)
    return None if card is None else contacts_vcard.to_vcard(card)


# -- forget / erase (called ONLY by memory_forget_cascade and MemoryService.delete_user) -------------------------------------

def _names(rx, *values: Any) -> bool:
    return any(v and rx.search(str(v)) for v in values)


def _redact(card: dict, rx) -> dict:
    """A card that is NOT about the forgotten person keeps everything but the entries naming them (a note, a RELATED line)."""
    out = {}
    for k, v in card.items():
        if isinstance(v, list):
            out[k] = [x for x in v if not _names(rx, json.dumps(x, ensure_ascii=False) if not isinstance(x, str) else x)]
        else:
            out[k] = v
    return out


def _card_is_about(card: dict, rx) -> bool:
    """An incoming card IS the forgotten person when its name or a nickname names them (a note that mentions them does not)."""
    name = card.get("name") or {}
    return _names(rx, card.get("full_name"), *(v for v in name.values() if isinstance(v, str)),
                  *(n.get("value") for n in card.get("nicknames") or []))


async def _person_ids_named(db, user_id: str, rx) -> list[str]:
    """Every person row of the member whose name the forgotten entity matches - live AND already soft-deleted (the people step
    may have run first, or in an earlier pass)."""
    return [str(r[0]) for r in await _all(db, "SELECT id, name FROM people WHERE user_id = ?", (user_id,)) if _names(rx, r[1])]


async def forget_entity(db, user_id: str, rx) -> int:
    """"Forget <name>": the contact model's part. RAISES on a failure (the cascade records it); a missing table raises the driver's
    no-such-table error, which the cascade reads as nothing to reach. ``rx`` is the name pattern or the ledger's pattern (retry).

      * every contact field of the forgotten person(s), and any field of ANY of the member's people that names them (a note, a
        nickname) - hard-deleted;
      * their links, and every review that names them or lists them as a candidate - deleted;
      * the synced dates their source contacts brought - deleted (the member-said dates stay with the soft-deleted person row, as
        the existing cascade + undo keep them);
      * their source contacts (and any unlinked incoming card that names them) - made content-free FORGOTTEN tombstones keyed by a
        hash of the external id, so the next sync does not bring them back; a card linked to someone else is redacted instead.
    """
    fields = await _all(db, "SELECT id, person_id, value, parts, label FROM contact_fields WHERE user_id = ?", (user_id,))
    ids = set(await _person_ids_named(db, user_id, rx))
    n = 0
    doomed = [r[0] for r in fields if r[1] in ids or _names(rx, r[2], r[3], r[4])]
    for i in range(0, len(doomed), 200):
        chunk = doomed[i:i + 200]
        n += await _run(db, f"DELETE FROM contact_fields WHERE user_id = ? AND id IN ({_qs(len(chunk))})", (user_id, *chunk))
    sc_rows = await _all(db, "SELECT s.id, s.source_id, s.external_id, s.card_json, l.person_id FROM source_contacts s "
                             "LEFT JOIN person_contact_links l ON l.source_contact_id = s.id AND l.unlinked_at IS NULL "
                             "WHERE s.user_id = ? AND s.state != 'forgotten'", (user_id,))
    now = now_iso()
    tombstoned: set = set()
    for sc_id, source_id, external_id, card_json, linked in sc_rows:
        if linked in ids or (not linked and _card_is_about(_loads(card_json, {}), rx)):
            await _forget_source_contact(db, user_id, sc_id, source_id, external_id, now)
            tombstoned.add(sc_id)
            n += 1
        elif _names(rx, card_json):
            await _run(db, "UPDATE source_contacts SET card_json = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                       (json.dumps(_redact(_loads(card_json, {}), rx), ensure_ascii=False, sort_keys=True), now, sc_id, user_id))
            n += 1
    if ids:
        idl = sorted(ids)
        n += await _run(db, f"DELETE FROM person_contact_links WHERE user_id = ? AND person_id IN ({_qs(len(idl))})", (user_id, *idl))
        n += await _run(db, f"DELETE FROM person_important_dates WHERE user_id = ? AND source_contact_id IS NOT NULL "
                            f"AND person_id IN ({_qs(len(idl))})", (user_id, *idl))
    for rid, pid, rpid, cands, detail, sc in await _all(db, "SELECT id, person_id, resolved_person_id, candidate_ids, detail, "
                                                            "source_contact_id FROM contact_merge_reviews WHERE user_id = ?",
                                                            (user_id,)):
        if pid in ids or rpid in ids or ids.intersection(_loads(cands, [])) or _names(rx, detail) or sc in tombstoned:
            n += await _run(db, "DELETE FROM contact_merge_reviews WHERE id = ? AND user_id = ?", (rid, user_id))
    await db.commit()
    return n


async def forget_voiceprints(db, user_id: str, rx) -> int:
    """"Forget <name>" erases the voiceprint the member's tagging taught Zoe for that person (owner decision 2026-10-14): only the
    TAGGING member's rows, every model. RAISES on a failure; a missing table is nothing to reach."""
    rows = await _all(db, "SELECT id, people_id FROM learned_voiceprints WHERE owner_user_id = ?", (user_id,))
    if not rows:
        return 0
    ids = set(await _person_ids_named(db, user_id, rx))
    doomed = [r[0] for r in rows if r[1] in ids]
    if not doomed:
        return 0
    n = await _run(db, f"DELETE FROM learned_voiceprints WHERE owner_user_id = ? AND id IN ({_qs(len(doomed))})", (user_id, *doomed))
    await db.commit()
    return n


FORGET_USER_TABLES = (("learned_voiceprints", "owner_user_id"), ("contact_fields", "user_id"), ("contact_merge_reviews", "user_id"),
                      ("person_contact_links", "user_id"), ("source_contacts", "user_id"), ("contact_sources", "user_id"))


async def forget_user(user_id: str, *, db=None) -> int:
    """The member is erased: every contact-model row they own (their synced cards, links, reviews, fields, sources) and every
    voiceprint their tagging taught. A table that is not there yet is skipped; any other failure is logged at ERROR and re-raised
    so the caller does not report a clean erase."""
    async def go(conn) -> int:
        n = 0
        for table, col in FORGET_USER_TABLES:
            try:
                n += await _run(conn, f"DELETE FROM {table} WHERE {col} = ?", (user_id,))
            except Exception as exc:  # noqa: BLE001
                if type(exc).__name__ == "UndefinedTableError" or "no such table" in str(exc).lower():
                    continue
                raise
        try:
            n += await _run(conn, "DELETE FROM person_important_dates WHERE user_id = ? AND source_contact_id IS NOT NULL", (user_id,))
        except Exception as exc:  # noqa: BLE001 - an unmigrated dates table has no synced rows to remove
            if "source_contact_id" not in str(exc) and "no such table" not in str(exc).lower():
                raise
        await conn.commit()
        return n

    try:
        if db is not None:
            return await go(db)
        from db_compat import get_compat_db
        async with get_compat_db() as conn:
            return await go(conn)
    except Exception as exc:
        if db is None and isinstance(exc, RuntimeError):
            try:
                from db_pool import pool_ever_initialised
                if not pool_ever_initialised():       # a lab / bare unit test: no store to clear
                    return 0
            except Exception:  # noqa: BLE001
                pass
        logger.error("contacts_store: contact rows NOT erased for a forgotten user (%s)", type(exc).__name__)
        raise

