---
type: Architecture / design
title: "The contacts data model: where synced contacts live and how they join Zoe's people knowledge (design + build, 2026-10-14)"
status: BUILT (storage + minimal API, flag-dark). Migration 0052, contacts_store.py, contacts_vcard.py, routers/contacts.py (ZOE_CONTACTS_API, default off). No sync engine (that is the connectors work). Nothing live changes until the migration runs and the flag is turned on.
date: 2026-10-14
description: A member's contacts from Google, Microsoft/Outlook, Apple/CardDAV, a phone export, an introduction or an Omi tag land in one standards-aligned card model (RFC 6350 vCard 4.0 / RFC 9553 JSContact) around the existing people node, in that member's own private layer, with provenance on every field. One Zoe person links to many source contacts; an ambiguous merge is a review item, never a silent merge. Dates feed the existing birthday store, relationships the existing people graph, learned voiceprints the owner's Omi decision. "Forget <name>" removes all of it, through the audited cascade.
evidence_labels: "[src] read in this worktree (origin/main 8c144cc7e) | [branch] read from an unmerged branch (#2029 abilities, #2051 memories space) | [doc] an official specification or API reference, linked | [proposal] a choice made here"
---

# The contacts data model

## 0. Answer first

- **Where it lives.** A Zoe person stays the `people` row (the people-graph node). Around it, five new tables hold the
  CONTACT CARD and where it came from: `contact_fields` (multi-value fields), `contact_sources` (one per member per
  account), `source_contacts` (each external card as last seen), `person_contact_links` (one person, many source cards)
  and `contact_merge_reviews` (the member settles what Zoe is not sure of). Plus `learned_voiceprints` for the Omi
  tagging decision. Migration **0052**, additive.
- **No second store for what already has one.** Birthdays and anniversaries go to `person_important_dates` (the birthday
  trigger's feed), relationships to `person_relationships` (the people graph, through `people_graph.insert_edge`).
- **Private by default.** Every row is keyed by the owning member and every read filters on it. A person Zoe creates from
  a member's synced contacts is `visibility = 'personal'`. Member B never sees member A's synced contacts, even on a
  family-visible person (B gets a 404, admin role does not widen it, a panel device token is refused).
- **Provenance on every field.** `source` (local | google | microsoft | carddav | phone | introduction | omi_tag),
  `source_contact_id`, `provenance` (said_by_member | entered_by_member | synced | approved_from_pendant | introduction |
  file_import), `provenance_ref` (a pointer, never words) and `read_only` (the provider owns it). A synced value never
  overwrites what the member said: they are separate rows.
- **Never a silent merge.** Zoe links an incoming card to an existing person ONLY on one unambiguous strong identifier
  (Zoe's own `urn:uuid`, an email, a phone) whose owner has a compatible name. A name-only match, two candidates, or a
  shared identifier under a different name becomes a review item, and the card waits (nothing merged, no duplicate).
- **Forget means forever.** "Forget Marisol" deletes her contact fields, links, synced dates, reviews and the learned
  voiceprint, and turns her synced cards into content-free tombstones so the next sync does not bring her back. Two new
  fail-loud steps in `memory_forget_cascade` (`contacts`, `voiceprints`); the member erase reaches all of it.

## 1. What Zoe has today [src]

| Store | What | Gap for contacts |
|---|---|---|
| `people` (0001, 0006, 0007) | one row per person per member: `name`, single `email` / `phone` / `birthday`, free-text `notes`, `visibility` default `'family'` | one value per kind, no types/preference, no provenance, the family default puts private knowledge where every member reads it (identity design section 5.5, "Today") |
| `people_field_values` (0001) | admin-defined custom fields, `value_json` | untyped, not per-source, not standards-shaped |
| `person_important_dates` (0006) | birthday / anniversary, `reminder_days_before`; read by `proactive/triggers/people_birthday.py`, `person_health.py`, `zoe_memory_compose` | no source / provenance |
| `person_relationships` (0007, 0015, 0037, 0043) | typed, temporal, invalidate-never-delete edges with evidence pointers (`ADR-relationship-memory.md`) | none: the right home for vCard RELATED |
| `person_merge.py` (#1036) | folds a stub into a real person, re-points satellites | did not know the contact tables (fixed here) |
| `memory_forget_cascade.py` | "forget X" soft-deletes the person row (15-minute undo), closes its edges, clears derived text | no contact-card or voiceprint step (added here) |

There was no place for a second email, a work phone, an address, a pronunciation, a synced account, an external id, an
etag or a sync cursor.

## 2. Standards and the provider mappings [doc]

Sources: RFC 6350 vCard 4.0 (<https://www.rfc-editor.org/rfc/rfc6350>), RFC 9554 vCard extensions for JSContact
(<https://www.rfc-editor.org/rfc/rfc9554>: 7-component N, `PHONETIC` / `SCRIPT` parameters, `PRONOUNS`, `SOCIALPROFILE`),
RFC 9553 JSContact (<https://www.rfc-editor.org/rfc/rfc9553>), RFC 6352 CardDAV and RFC 6578 WebDAV sync, Google People
API Person (<https://developers.google.com/people/api/rest/v1/people>) and `people.connections.list`
(<https://developers.google.com/people/api/rest/v1/people.connections/list>), Microsoft Graph v1.0 contact
(<https://learn.microsoft.com/en-us/graph/api/resources/contact?view=graph-rest-1.0>).

**Preference** follows RFC 6350 / RFC 9553: `pref` 1..100, 1 = most preferred, absent = least, compared within a kind.
Google `metadata.primary = true` maps to `pref = 1`; Graph's `primaryEmailAddress` likewise; vCard 3 `TYPE=pref` reads
as 1. **Types** keep the vCard TYPE words (`home`, `work`, `cell`, `voice`, `fax` ...); JSContact `contexts`
(`private` = home, `work`) and phone `features` (`mobile` = cell) are a fixed translation.

| Zoe (`contact_fields.kind`, or store) | vCard 4.0 | JSContact (RFC 9553) | Google People API | Microsoft Graph contact |
|---|---|---|---|---|
| `name` (value = display name; `parts`: given, given2, surname, surname2, title, credential, generation) | `FN`; `N` (RFC 9554 7 components) | `name.components[]` kinds given / given2 / surname / surname2 / title / credential / generation; `name.full` | `names[]`: givenName, middleName, familyName, honorificPrefix, honorificSuffix, displayName | givenName, middleName, surname, title (honorific), generation, displayName |
| `name.parts.phonetic{given,surname,...}`, `phonetic_system`, `phonetic_script` | `N;ALTID=1;PHONETIC=ipa\|jyut\|piny;SCRIPT=..`; Apple `X-PHONETIC-FIRST-NAME` / `-LAST-NAME` (read and written for interop) | `name.phoneticSystem`, `phoneticScript`, `components[].phonetic` | phoneticGivenName, phoneticFamilyName, phoneticMiddleName | yomiGivenName, yomiSurname (Japanese only) |
| `name.parts.say_as` - the TTS pronunciation hint | `X-ZOE-SAY-AS` (private extension) | (none; carried in `name.components[].phonetic` when it is a real phonetic system) | (none) | (none) |
| `nickname` | `NICKNAME` | `nicknames{}` | `nicknames[].value` | nickName |
| `email` (value_norm = lower-case) | `EMAIL;TYPE;PREF` | `emails{}`: address, contexts, pref | `emailAddresses[]`: value, type, metadata.primary | emailAddresses[].address (+ name) |
| `phone` (value_norm = last 9 digits) | `TEL;TYPE;PREF` (text or `tel:` URI) | `phones{}`: number, features, contexts, pref | `phoneNumbers[]`: value, canonicalForm (E.164), type | businessPhones[] (work), homePhones[] (home), mobilePhone (cell) |
| `address` (parts: po_box, extended, street, locality, region, postcode, country; label) | `ADR` (7 components) + `LABEL` | `addresses{}`: components, contexts, full | `addresses[]`: poBox, extendedAddress, streetAddress, city, region, postalCode, country, countryCode, formattedValue, type | homeAddress / businessAddress / otherAddress: street, city, state, postalCode, countryOrRegion |
| `org` (parts.units) | `ORG` | `organizations{}`: name, units | `organizations[]`: name, department | companyName, department, officeLocation |
| `title` (parts.role) | `TITLE`, `ROLE` | `titles{}`: name, kind title \| role | `organizations[].title` | jobTitle, profession |
| `url` | `URL` | `links{}` | `urls[]` | businessHomePage |
| `social` (parts.service, username) | `SOCIALPROFILE;SERVICE-TYPE;USERNAME` (RFC 9554) | `onlineServices{}`: service, uri, user | (via `urls[]` / `imClients[]`) | (none) |
| `im` | `IMPP` | `onlineServices{}` | `imClients[]`: username, protocol | imAddresses[] |
| `photo` - a REFERENCE (`https://...` or `zoe-media:<id>`), never inline bytes | `PHOTO` (URI; an inline `data:` / `ENCODING=b` photo is dropped and counted) | `media{}` kind photo, uri | `photos[]`: url (`default: true` is Google's placeholder: skipped) | `/contacts/{id}/photo/$value` - the bytes go to the media store, the field holds `zoe-media:<id>` |
| `note` | `NOTE` | `notes{}` | `biographies[].value` | personalNotes |
| `group` | `CATEGORIES` | `keywords{}` | `memberships[].contactGroupMembership` (group name resolved via `contactGroups`) | categories[] |
| `pronouns` | `PRONOUNS` (RFC 9554) | `speakToAs.pronouns{}` | (none) | (none) |
| `person_important_dates` (date_type birthday / anniversary) | `BDAY`, `ANNIVERSARY` (`YYYYMMDD`, `--MMDD`) | `anniversaries{}` kind birth / wedding | `birthdays[].date`, `events[]` type anniversary | birthday (DateTimeOffset, date part). **No anniversary property on v1.0 contact.** |
| `person_relationships` (via `people_graph`) | `RELATED;TYPE;VALUE=text` (+ `X-ZOE-REL`, `X-ZOE-ROLE` for a lossless Zoe round trip) | `relatedTo{uid: {relation: {...}}}` | `relations[]`: person (a name), type | spouseName, children[], manager, assistantName (names only) |
| `source_contacts.external_id` | `UID` / CardDAV href | `uid` | `resourceName` (`people/c...`) | `id` (request `Prefer: IdType="ImmutableId"`, or the id changes when the contact moves folder) |
| `source_contacts.etag` | CardDAV `getetag` | `updated` | `etag` | changeKey |
| `contact_sources.sync_token` | CardDAV `sync-token` (RFC 6578) | (n/a) | `nextSyncToken` (only on the last page; expires 7 days after the full sync: `EXPIRED_SYNC_TOKEN` means a full resync) | `@odata.deltaLink` from `/contactFolders/{id}/contacts/delta` |
| `source_contacts.state = 'deleted_upstream'` | CardDAV 404 in a sync report | (n/a) | `metadata.deleted` on a sync read | `@removed` in a delta page |

Not mapped (counted in a vCard import's `unmapped`, ignored from APIs): `GENDER` / `genders`, `KIND`, `LANG`, `GEO`, `TZ`,
`KEY`, `SOUND`, `LOGO`, `CLIENTPIDMAP`, Google `sipAddresses` / `interests` / `skills` / `occupations` / `locales` /
`userDefined` / `clientData` / `coverPhotos`, Graph `initials` / `fileAs` / `yomiCompanyName`. Each is one row in this
table and one `kind` in the CHECK when a member needs it.

## 3. The data model (migration 0052)

```
contact_sources     (id, user_id, provider CHECK IN (local,google,microsoft,carddav,phone,introduction,omi_tag),
                     source_account_id,          -- the connector's connected_accounts id; tokens are in the vault, NEVER here
                     label, sync_token, sync_token_at, last_synced_at, read_only, status, created_at, updated_at,
                     UNIQUE (user_id, provider, source_account_id))
source_contacts     (id, user_id, source_id FK contact_sources CASCADE, external_id, etag, uid,
                     card_json,                  -- the normalised card as last seen; never a photo blob
                     state CHECK IN (active, deleted_upstream, forgotten), last_synced_at, created_at, updated_at,
                     UNIQUE (source_id, external_id))
person_contact_links(id, user_id, person_id FK people CASCADE, source_contact_id FK source_contacts CASCADE,
                     method CHECK IN (created_from, uid, email, phone, member_confirmed), linked_by ('zoe' | member id),
                     linked_at, evidence (which identifier kinds matched, never the value), unlinked_at)
                     UNIQUE (source_contact_id) WHERE unlinked_at IS NULL      -- a card belongs to ONE person at a time
contact_merge_reviews(id, user_id, kind CHECK IN (link, relation), source_contact_id FK CASCADE, person_id FK CASCADE,
                     candidate_ids (people ids), reason CHECK IN (name_only, several_candidates, identifier_conflict,
                     relation_unresolved), detail, status CHECK IN (open, linked, kept_separate, dismissed),
                     resolved_person_id FK SET NULL, resolved_by, created_at, resolved_at)
contact_fields      (id, user_id, person_id FK people CASCADE, kind CHECK IN (name, nickname, email, phone, address, org,
                     title, url, social, im, photo, note, group, pronouns), value, value_norm, parts (JSON), types, label,
                     pref CHECK 1..100, ord, source CHECK, source_contact_id FK source_contacts CASCADE,
                     provenance CHECK IN (said_by_member, entered_by_member, synced, approved_from_pendant, introduction,
                     file_import), provenance_ref, read_only, created_at, updated_at)
learned_voiceprints (id, owner_user_id, people_id FK people CASCADE, model_id, channel, embedding, n_segments,
                     n_recordings, status CHECK IN (provisional, confirmed), learning_enabled, created_at, updated_at,
                     last_confirmed_at, expires_at, UNIQUE (owner_user_id, people_id, model_id))
person_important_dates + source (default 'local'), source_contact_id, provenance, read_only (default 0)
```

- **Why one `contact_fields` table, not one per kind.** Every kind needs the same eight provenance / preference columns;
  JSContact itself is "maps of typed objects". One table keeps provenance uniform and the forget sweep single. Kind-specific
  structure lives in `parts` (JSON, 4 KB cap), validated in the store, never free-form from a sync.
- **`ord`** keeps the card's own order within a kind (a vCard's order is meaningful when no `PREF` is given).
- **`learned_voiceprints` is created here**, exactly as `docs/architecture/memories-space.md` section 2.6 [branch, #2051]
  designs it, so the forget cascade owns its erase from day one. The Omi P4b builder fills it and must not create it again.
  `learning_enabled` is `INTEGER` (0/1) for SQLite parity, where that design wrote `boolean`.
- Timestamps are TEXT UTC like the rest of the people tables. No request-time DDL (a test scans the modules).

## 4. Layers and who sees what

This model is **layer 2** of the identity design (section 5.5): each member's own knowledge about a person.

| Who | Sees | Enforced by |
|---|---|---|
| the owning member | their own cards, fields (with provenance), links, reviews, voiceprint list (P4b) | every query filters `user_id` / `owner_user_id`; `contacts_store.own_person` requires `people.user_id = caller` |
| another member | nothing of it - not even on a family-visible `people` row (404) | the same filters; `test_member_b_never_sees_member_a_synced_contacts` |
| an admin | only their own (role never widens a member's private data) | the router's member check; `test_api_...member_isolation` |
| a panel (device token) | nothing | `routers/contacts._member` refuses `device_token_role` / `panel:` |
| a guest | nothing | `require_signed_in` |

- A person Zoe creates from a sync is `visibility = 'personal'`. The legacy `people.email` / `phone` / `birthday` /
  `notes` columns are never written from a sync: on a family-visible row they would publish the member's synced data
  to the household.
- **Layer 1** (`person_directory`, the household "this person exists" record) and **layer 3** (`person_self_facts`)
  are the identity design's Phase 4 and are not built. When `people.directory_id` lands, a contact card stays layer 2:
  nothing in a member's card is copied into the directory. Sharing a card is the identity design's explicit
  `person_knowledge_shares` grant, never a default.
- The restraint classes (`health`, `money`, ...) are not special-cased in the card store: a card field is the member's
  own private row, never relayed. A future "share this card" must drop restraint-class notes (identity design rule 4).

## 5. Provenance per field

| `provenance` | Means | `source` | `read_only` | memory-authority equivalent |
|---|---|---|---|---|
| `said_by_member` | the member said it in a turn (`provenance_ref` = the turn id) | local | 0 | `user_stated` |
| `entered_by_member` | typed in Zoe's UI / API | local | 0 | `user_stated` |
| `synced` | from a connected Google / Microsoft / CardDAV account (`provenance_ref` = `source_contact:<id>`) | google / microsoft / carddav | 1 | below `user_stated`: it is what the member's account says, not what they told Zoe |
| `file_import` | a `.vcf` the member imported (phone export) | phone / local | 0 | as `synced` |
| `approved_from_pendant` | the member approved it in the Omi day review | omi_tag | 0 | `user_confirmed` |
| `introduction` | captured in the introduction flow | introduction | 0 | `user_stated` (by the introducer) |
| `legacy` (read only, never stored) | the old single-value `people` column | local | 0 | as stored |

Rules: a re-sync replaces only the rows of THAT source contact; a member edit never touches a synced row (the API
refuses to remove a `read_only` field: 409, "change it in the account it came from"); display order is `pref`, then
member-said before synced, then the card's `ord`.

## 6. Merge and dedup: one person, many source cards

```
incoming card (source S, external_id E)
  E is a forgotten tombstone of S?                       -> skip ("forgotten")
  the card IS a person the member forgot (forget ledger)? -> tombstone E, skip ("forgotten"); else drop entries naming one
  (S, E) already linked to a live person?                -> replace S's fields on that person ("updated"/"unchanged")
  strong identifiers among the member's OWN live people:
      Zoe urn:uuid UID | email (lower-case) | phone (last 9 digits); legacy columns count
    exactly one person, name compatible                  -> LINK (method uid|email|phone, linked_by 'zoe')
    exactly one person, name NOT compatible              -> REVIEW identifier_conflict   (a shared family email)
    two or more people                                   -> REVIEW several_candidates
  else same folded full name among the member's people:
    one or more                                          -> REVIEW name_only | several_candidates
  else                                                   -> CREATE a personal person + LINK (created_from)
```

- **"Name compatible"** = one folded token set contains the other ("Tove" ~ "Tove Lindqvist"). Folding is
  `people_graph.fold_name` (case, accents, punctuation, any script).
- **A review holds the card, not a guess.** Until the member answers, the card is in `source_contacts` only: no fields
  attached, no person created, no link. Re-importing the same card updates the open review instead of adding another.
- **Settling a review** (`POST /api/contacts/reviews/{id}`): `link` (to one of the member's people: method
  `member_confirmed`, `linked_by` = the member), `new` (a separate personal person), `dismiss` (the card stays unlinked).
- **Person merge** (`person_merge.merge_person`, two Zoe people the member says are one) now re-points
  `contact_fields`, `person_contact_links` and `contact_merge_reviews`. A learned voiceprint is never averaged: the
  survivor keeps its own for a model; the duplicate's moves only where the survivor has none; the colliding one is
  erased (a wrong merge must not poison a voiceprint).
- **Phone matching** uses the last 9 digits so `0400 555 101` and `+61 400 555 101` meet without a region database.
  Fewer than 6 digits is not an identifier. The risk is two different countries' numbers sharing 9 trailing digits under
  compatible names, which is why a phone match still requires a compatible name.

## 7. Dates, relationships, groups, photos

- **Dates** - BDAY / ANNIVERSARY (Google `birthdays` / `events`, Graph `birthday`) become `person_important_dates` rows
  with the source columns, so `PeopleBirthdayTrigger` (which notifies the owning member only) and `person_health` pick them
  up with no change. A year-less date (`--MMDD`) keeps `year` NULL.
- **Relationships** - a RELATED line resolves by exact folded name among the member's own people. One hit with a mapped
  type writes an edge through `people_graph.insert_edge` (`origin = 'contact_sync'`, evidence `turn_id =
  source_contact:<id>`) **only if the pair has no current edge in either direction**: what the member said is never
  superseded by a sync. No hit, several hits, or an unmapped type (`kin`, `crush` ...) is a `relation` review item. No
  stub person is ever created from a RELATED name. A batch import resolves relations after every card is in.
  Export writes `RELATED;TYPE=<vCard type>;VALUE=text;X-ZOE-REL=<rel_type>;X-ZOE-ROLE=<label>:<name>`.
- **Groups** - one `group` field per label. Google `contactGroups` names are resolved by the sync engine.
- **Photos** - a field holds a reference only. Remote URLs (`https://`) are exported; `zoe-media:<id>` (a file the sync
  engine downloaded) never leaves the box. Inline photos in a `.vcf` are dropped and counted.

## 8. Learned voiceprints (owner decision 2026-10-14)

Owner: "Zoe needs to learn" - a member's Omi tags teach Zoe the voice of a person in THAT member's life, no spoken-consent
gate, with five guardrails (`memories-space.md` section 2.6 [branch]). What this model guarantees:

- `learned_voiceprints.owner_user_id` is the TAGGING member and `people_id` FKs that member's own `people` row. Two
  members who tag the same person hold two voiceprints, never shared (`test_forget_...`: B's survives A's forget).
- Local only: the table is not in any export, sync or the memory export job; it is not `speaker_profiles`, so the voice
  sync / identify / adaptation paths cannot reach it. Suggest-tags only, never authentication or presence (the static
  test V3 in that design is the Omi builder's).
- **"Forget <name>" erases it** (`voiceprints` step); deleting the person row cascades; the member erase deletes all of
  theirs; a person merge never averages two. "Stop learning this voice" (`learning_enabled = 0`) and expiry
  (`expires_at`) are columns the P4b builder drives.

## 9. Forget and erase: every removal path

| Path | What it removes | Where |
|---|---|---|
| "Forget <name>" (member) | the forgotten person's `contact_fields`, `person_contact_links`, synced `person_important_dates`, every review naming them or listing them; any other field of the member that names them (a note on someone else); their source cards become `state = 'forgotten'` tombstones with `card_json = '{}'`, no uid/etag, and `external_id` replaced by `forgotten:<sha256(source_id, external_id)>` (a CardDAV href can carry a name); a card linked to someone else is redacted instead | `memory_forget_cascade` step `contacts` -> `contacts_store.forget_entity` |
| same | the member's `learned_voiceprints` for that person | step `voiceprints` -> `contacts_store.forget_voiceprints` |
| same, the next sync | the tombstoned card is not re-imported (`import_card` returns `forgotten`); a RE-CREATED card for the person (a new external id) is refused by the forget ledger (`memory_forgotten.ledger_pattern`: salted hashes, no names) and tombstoned; any other incoming card loses the entries that name them (a note, a RELATED line) before anything is stored | `contacts_store.is_forgotten_external`, `contacts_store.forgotten_pattern` in `import_card` |
| a step that fails | recorded in `CascadeResult.failed`, the forget is NOT confirmed, a durable retry marker is left (no name); the retry re-runs the step from the ledger's hash pattern | existing cascade machinery; both steps find their person rows themselves, so a failed `people` step never makes them "succeed" over nothing |
| member erase (`MemoryService.delete_user`) | every row the member owns in the six tables, and their synced dates | `contacts_store.forget_user` |
| person row hard-deleted | `contact_fields`, links, reviews, voiceprints by `ON DELETE CASCADE` | 0052 FKs |
| the member removes one field | that field, unless it is synced (`read_only`) | `contacts_store.remove_field` |
| a re-sync of a card | that card's previous fields and dates (replaced) | `contacts_store._apply_source_card` |

The existing behaviour is kept for the `people` row itself: it is soft-deleted (15-minute "keep the contact" undo). The
undo puts back the name row and its edges; the card fields, links and voiceprint are gone for good (forgotten means
forever), and the People panel can add details again. No module calls a `.delete` attribute (the memory-loss scanner,
`test_memory_loss_class.py`); every removal is SQL on these tables inside the functions above.

## 10. What the connectors work (sync engine) plugs into

Built for it, not built here:

- `ensure_source(user, provider, source_account_id)` per connected account (`connected_accounts` row, #2029 [branch]);
  the OAuth / app-password secret stays in the vault (`account_secrets`), never in `contact_sources`.
- `import_card(user, source, card, external_id, etag)` per changed card - idempotent, atomic per card, returns
  `created | linked | updated | unchanged | review | forgotten | skipped`. The engine maps Google / Graph JSON to the card
  shape using section 2's table.
- Cursor: store `nextSyncToken` / `@odata.deltaLink` / CardDAV `sync-token` in `contact_sources.sync_token`; Google's
  expires after 7 days (`EXPIRED_SYNC_TOKEN` -> full resync, which `import_card` makes harmless: unchanged cards are no-ops).
- Upstream delete -> `state = 'deleted_upstream'` and drop that card's synced fields; the person stays (it may carry the
  member's own knowledge).
- Disconnect (#2029 cell W3: "no vault row, no cursor, no items") -> delete the `contact_sources` row: `source_contacts`,
  their links and synced fields cascade. Open question for the owner (section 12, C3): what happens to people Zoe created
  from that account and that carry nothing else.
- Write-back (Zoe editing Google) is out of scope: synced fields are `read_only`.

## 11. Migration and back-compat plan

1. **0052 is additive** (`down_revision` = the head at push time; one head, pinned by a test). It creates the tables and
   adds four columns to `person_important_dates`; existing dates read as `source = 'local'`, `read_only = 0`. Rerun-safe
   on PostgreSQL and SQLite (`IF NOT EXISTS`, inspector check for columns). The downgrade drops what it added (lossy by
   design: synced data is re-fetched by the next sync).
2. **No backfill.** The legacy `people.email` / `phone` / `birthday` / `notes` stay where they are and are READ into the
   card as `provenance = 'legacy'` entries (deduplicated against real fields by normalised value). Nothing in a running
   household changes when the migration runs.
3. **`/api/people` is unchanged.** The card API is new and flag-dark (`ZOE_CONTACTS_API`, default off). The People UI can
   move to the card API later; until then both read the same `people` row.
4. **Operator steps** (none automatic): run the migration; turn `ZOE_CONTACTS_API` on for a demo member first; the forget
   cascade's new steps run regardless of the flag (an erase is never flag-gated) and read a missing table as nothing to
   reach.
5. **Retire later:** once the People UI writes through the card API, the legacy single-value columns become a read-only
   mirror, then go (retire by removing), in a later migration with its own backfill.

## 12. Owner decisions

Honoured (standing):

- **Tagged voices are learned** (2026-10-14): learned voiceprints in the tagging member's layer, local only, suggest-tags
  only, never auth, "forget <name>" erases them (section 8). Not re-proposing the consent gate.
- **Memory must be flawless**: provenance on every field; a synced value never supersedes a member-stated one; an
  ambiguous merge is asked, never guessed.
- **Forgotten means forever**: tombstones keep a forgotten card from returning on the next sync.
- **Adopt, don't rebuild**: the card is RFC 6350 / RFC 9553 shaped, relationships stay on the people graph, dates on
  `person_important_dates`; the small vCard codec exists only because no vCard library is installed.
- **Public repo**: every fixture person, number and address is invented.

Open (for the owner; none blocks the storage):

- **C1.** Should a member be able to share a whole synced card with the household (a "family contact")? Proposed: only
  through the identity design's explicit share grant, never a default.
- **C2.** When a synced birthday and a member-said birthday disagree, which reminds? Proposed: the member's (it is
  `user_stated`); the synced one is shown as "Google says ...".
- **C3.** On Disconnect, delete the people Zoe created from that account that carry nothing else? Proposed: yes, after a
  confirmation listing them.
- **C4.** Pronunciation: should `say_as` drive Kokoro when Zoe says the name? Proposed: yes, behind the replay gate.

## 13. Verification

`services/zoe-data/tests/test_contacts_data_model.py` (ci_safe, invented data, real SQLite built by the real 0052): migration
up / rerun / down and one head; CHECK refusals; vCard parse of the invented fixture
(`tests/fixtures/contacts/invented.vcf`), lossless round trip, UTF-8-safe folding, RFC 9554 7-component N + phonetic;
store round trip through export and Zoe-UID relink; per-source replace + read-only refusal; strong-identifier link;
name-only / several-candidate / identifier-conflict reviews and their resolution; legacy columns read, never written;
member B isolation (cards, reviews, fields, links); the forget cascade (fields, links, dates, reviews, tombstone, voiceprint,
re-import suppressed, other member untouched); fail-loud step; member erase; person merge re-point; the flag-dark router.
Each negative control is named in the test module's docstring. `test_memory_loss_class.py` stays green.

## 14. Open items (ledger)

- `correction_apply` updates `person_important_dates` in place; on a synced (read-only) date it should write a member row
  instead (C2).
- The forget cascade still leaves `person_activities` / `person_gift_ideas` / `person_bucket_list` on the soft-deleted
  person (pre-existing, not contact data).
- A JSContact (`application/jscontact+json`) exporter is a straight mapping from section 2 when a consumer needs it.
