---
type: research
title: Omi pendant gap audit - pendant to memory, the product field, bystanders, memory quality, scale, recall (2026-10-17)
date: 2026-10-17
status: research and audit only. No code, flag, table, route or live system was changed. Live reads were read-only and count-only (row counts, sums, timestamps; no transcript text was read). Findings are ranked P0/P1/P2 in section 1; owner decisions are in section 9.
description: Owner request 2026-10-17, "maybe do a deep dive on anything you have missed", about Zoe hearing every conversation through the Omi pendant. Audits the whole path (pendant, Pi sync, transcription, storage, retention, backup, search, observability) and the two builds in flight (the day review and voice tagging), compares the ambient-memory products (Omi, Limitless, Bee, Plaud, Friend, open-source projects), consolidates the Western Australian law into one lawyer question, and lists what the day review must call into so it cannot bypass the memory rules. Every finding is tagged verified, secondary or unverified with its source.
tags: [omi, pendant, ambient, audit, privacy, memory, research]
evidence_labels: "[V] verified: read in source on origin/main acca1f2e1, in the cached Omi source (opensrc, tag Omi_CV1_v3.0.21), or measured read-only on the live box | [S] secondary: press, vendor marketing or a summarising fetch | [U] unverified: reasoned or estimated, not measured"
---

# Omi pendant gap audit (2026-10-17)

Read with [omi-integration-plan.md](../architecture/omi-integration-plan.md) (the sync-in-range build),
[memories-space.md](../architecture/memories-space.md) (the day review, tagging and learned voices) and
[omi-press-to-record-firmware-2026-10.md](omi-press-to-record-firmware-2026-10.md). It does not repeat them.

Two builds are in flight and this audit does not duplicate them: **(a) the day review** (candidates from a day's
transcripts, approve-before-write) and **(b) voice tagging** (`omi_segments`, unknown-voice clusters, the tag API,
learned voiceprints). Where a finding lands on one of them, it says which.

Public repository: no household names, no transcript content and no per-person data appear here. Every example is
invented. Live numbers are aggregates.

## 0. Live snapshot (2026-10-17, read-only)

| Fact | Value | Source |
|---|---|---|
| Firmware on the pendant | **3.0.21** since 2026-10-13 (DFU from the panel, confirmed). The sync takes the **ring** path. No `OMI_SYNC_ASSUME_CODEC` is set and no recording was undecodable, so the codec `0x0f` question no longer applies to this unit | [V] DFU record `~/.zoe/agent-tools/omi_dfu_2026-10-13.md`; Pi journal `sync[...] path=ring ... undecodable=0`; `.env.omi` keys |
| Recordings synced | 273 over 4 days (240 `done`, 33 `empty_transcript`), 13.6 h of audio. The busiest day was 704 min (a card drain). Median recording 32 s, p90 600 s (the cap) | [V] count-only queries on `omi_recordings` |
| Transcript volume | about 4.2 characters per second of audio; 200,599 characters in total; table 528 kB | [V] same |
| Time quality | 202 `device`, 71 `corrected`, 0 `estimated` (the device clock is now set) | [V] same |
| Reviewed / kept | 0 reviewed, 0 kept-audio, 0 deleted | [V] same |
| Pendant registry | 0 rows in `omi_devices` (attribution is by `ZOE_OMI_OWNER_USER_ID`) | [V] same; `.env` |
| Last upload | 2026-10-16 00:36 UTC; out of range since | [V] same |
| Pi while the pendant is away | about **85 failed connect attempts per hour**, about 256 `hcitool`/sudo journal lines per hour | [V] Pi journal, last hour |
| Pi spool | `spool.sqlite3` is 252 MB, of which 63,977 of 64,413 pages are free (no VACUUM); 3,103 records held (one open recording) | [V] read-only SQLite open |
| Jetson memory | MemAvailable 967 MB at one sample; live `ZOE_OMI_MIN_MEM_MB=1200`, so a new transcription job would have deferred at that moment | [V] `free -m`; `.env` |
| WAVs on disk | 75 MB (the 33 held empty-transcript WAVs, inside their 7-day expiry) | [V] `du` |

## 1. Findings, ranked

Rank = owner value times risk. Effort: **S** under half a day, **M** one to three days, **L** more.

### P0

**P0-1. Anyone within Bluetooth range can read the pendant's stored audio, and on stock firmware reading it erases
the owner's copy.** [V]
- The storage service's characteristics carry plain `BT_GATT_PERM_READ` / `BT_GATT_PERM_WRITE`, with no
  `_ENCRYPT` or `_AUTHEN` (`omi/firmware/omi/src/lib/core/storage.c:83-94`, tag `Omi_CV1_v3.0.21`). The bring-up
  found no pairing and no encrypted permissions on the real unit (plan 0.3a).
- The ring holds up to about 34 hours of audio (P0-2). Stock firmware advances its read pointer as data is delivered
  (`sync_checkpoint_advance`, plan 0.3). So any phone running a generic BLE tool can download what the pendant holds,
  and the owner's copy is gone when it does. Command `0x13` CLEAR wipes the ring outright.
- The pendant takes one central at a time (`CONFIG_BT_MAX_CONN=1`), and `omi-sync` does not hold the link: it
  connects per visit and lets go (`runner.py`, `OMI_SYNC_SCAN_INTERVAL_S` 30 s) [V]. The pendant is therefore
  connectable to a stranger all the time while away, and between the Pi's visits at home, through walls included.
- The ledger already records the related OTA hole (the public MCUboot key, 2026-10-13). This read-and-erase exposure
  is not recorded anywhere.
- **Next action:** say it plainly in the operator pack. Fold the fix into phase-2 firmware: encrypted, authenticated
  permissions on the storage and audio services, and a single LE Secure Connections bond with the panel Pi. The Pi
  side must then pair once, which the plan avoided because of the marginal link; the link now reaches −73 dBm
  after repositioning (DFU record). Interim: a 3 s hold powers the pendant off when it is not wanted.
- **Effort:** S for the note; firmware M, riding the press-to-record build.

**P0-2. Audio can be lost silently while the pendant is out of range, and nothing tells anyone.** [V] for the
mechanism, [U] for the hours.
- The ring is a raw SD region of `MAX_STORAGE_BYTES = 0x1E000000` (480 MiB, `sd_card.h:9`) [V]. At the codec's
  32 kbit/s ceiling that is about 34 h of audio [U]. Hardware wake-on-sound (the T5838 sleeps after 10 s of quiet)
  stretches it in wall-clock time [U].
- When it is full the firmware **overwrites the oldest unread audio** and counts `dropped_packets`
  (`sd_card.c:407-420, 515-526`) [V].
- `omi-sync` reports that count only as a free-text note in the Pi's journal (`engine.py:208`) [V]. Battery is read
  only by the manual `check` (`check.py:47`) [V]. zoe-data never learns when the pendant was last seen, how full it
  is, how much it overwrote, or its battery level.
- The struggle detector reads conversation turns only (`struggle_signals.SIGNALS`) [V]. `zoe_ground_truth.sh` has
  no pendant check [V]. "No sync for 3 days" therefore goes unnoticed, as does "the pendant overwrote a day".
- Live now: last upload 2026-10-16 [V].
- **Next action:** one heartbeat per visit from `omi-sync`. Send `POST /api/omi/device-status` with
  `{last_seen, battery, ring_fill_pct, dropped_records, firmware, clock_offset_s}` (numbers only, device token).
  Store the latest row per pendant and return it on `GET /api/omi/recordings` and in Settings > Bluetooth.
  Add a ground-truth line. Add one counts-only pull item through the existing seams (never unprompted speech):
  "Your pendant hasn't synced since Thursday" after N days, or "Your pendant ran out of room and lost about 40
  minutes". Also add a struggle-style `pendant_gap` event, so the night mind sees it.
- **Effort:** M.

**P0-3. The day review design does not call the distress floor, and it does not re-check the forget ledger when it
writes.** [V] Lands on build (a), which is being written now, so this is time-critical.
- `memories-space.md` never mentions `distress_handoff` (0 matches). The digest and night-mind readers re-check text
  with `guarded_text` (module docstring). A first-person crisis line in the owner's pendant speech could therefore
  become a reminder or follow-up candidate, or a `person_memory`. A child's line overheard by the pendant is in the
  same position.
- The design redacts through the forget **cascade** (§5.4 "Forget works on all of it"), but it does not consult
  `memory_forgotten.spans` when a candidate is created, nor again when an approved candidate is applied. A forget
  that lands between candidate and approval would then be written back by the approval, which breaks
  "forgotten means forever". The transcript path already does this check on landing
  (`omi_recordings._redact_forgotten`) [V]. The candidate path must match it.
- **Next action:** the call list is in section 5. Hand it to build (a) before it merges.
- **Effort:** S.

### P1

**P1-1. Zoe cannot answer "what did <person> say about the weekend?" today, and the index this needs is Zoe's own,
not Hindsight.** [V]
- Nothing reads `omi_recordings` (the static guard `test_nothing_else_reads_pendant_transcripts`). The transcript is
  one joined string with no per-line time or speaker, and there is no text index on it.
- **The brief's premise is out of date.** The memory-system decision ended **KEEP_Z0** on 2026-10-08 (run 2: Zoe's
  own memory led every axis, and no engine was adopted). Hindsight was the conditional pick of 2026-10-05 and lost
  under the pre-registered rule. Source: `docs/research/memory-bakeoff-decision-2026-10-08.md` and the bake-off
  run record.
- The template exists: `exact_words.py`. It is a per-user Postgres index of the owner's own turns. It is
  forget-aware on read and on write, fires only on an evidence question ("what did I say", "when"), and stays off the
  critical path. A `pendant_words` sibling over `omi_segments.text` (build b), with a Postgres `tsvector` GIN index,
  gives word search with day and time. Add semantic search later through the Chroma verbatim tier (MiniLM,
  hit@5 0.933 in the bake-off). It needs the residue scrub first, because a Chroma API delete leaves text in
  SQLite/FTS (bake-off HM finding).
- There are two tensions, and they need owner decision OD5.
  - `exact_words` deliberately refuses quoted third parties. "What did <person> say" asks for exactly that.
  - `memories-space.md` §6.6 keeps transcript text out of the brain's context, to block prompt injection from
    overheard speech.
- Recommended shape:
  - a deterministic search tier returns snippets as a private card (phone, desktop, or a verified voice session);
  - the brain gets at most counts and the card id;
  - other people's lines are searchable only by the wearer, and only with the learned-voice suggestion shown as a
    suggestion.
- **Effort:** M (word search) to L (semantic, voice-scoped).

**P1-2. Time and day can be wrong exactly when the pendant is away.** [U] for the trigger, [V] for the consequence.
- If the battery runs flat on a trip, the RTC across a full power loss is unverified (plan 0.3a, item 8). Records
  written after a reset carry the "seconds since boot" flag. They then fall back to `estimated`: receive time minus
  the audio queued behind them (plan 4.2).
- Days of audio would then land on the sync day. That corrupts the day review's `day_local` batches (memories-space
  §4.1) and any "on Tuesday" recall.
- `day_local` is household-local. While the wearer is in another time zone, "today" splits at the wrong midnight.
- **Next action:**
  - Measure the RTC across a power-off when the owner is home.
  - In build (a), never batch `estimated` recordings into a dated day: give them an "undated" bucket, with a review
    line "these are from your trip; I'm not sure of the days".
  - Take the travel time zone from the phone or Home Assistant later.
- **Effort:** S.

**P1-3. There is no "pause" that works on stock firmware, and no way to delete a time range.** [V]
- Only the 3 s power-off stops capture. The GATT mic-gain level 0 is −20 dB, not a mute (ledger 2026-10-13). Delete
  is per recording id only (`DELETE /api/omi/recordings/{id}`). A bystander's "please delete what I said" means
  finding rows by hand.
- **A firmware-free pause is possible on the Zoe side:**
  - the owner declares an **off-the-record window** by voice, Telegram or phone, after the fact ("that last hour was
    private") or ahead of time ("off the record until 3");
  - zoe-data stores it;
  - any upload overlapping the window is tombstoned on arrival, never transcribed. The same check runs in
    `transcribe_pending` for rows already waiting.
- The same mechanism gives **auto-mute in sensitive places** without location hardware: a calendar event marked
  private (or of a chosen kind), or a Home Assistant zone visit from the phone's presence, becomes such a window.
- A **time-range delete** route covers bystander requests.
- Honest limit: the audio still sits on the pendant until it syncs (see P0-1). This governs what Zoe keeps, not what
  the microphone heard. It depends on P1-2 for correct times.
- **Effort:** M.

**P1-4. Deleting or forgetting does not reach the backups for about five weeks.** [V]
- `postgres-nightly-backup.sh` keeps 7 daily and 4 weekly dumps, on the box. `redact_backups.py` covers the palace
  exports and chat, not the Postgres dumps.
- The design already excludes the **biometric** tables' data from the dump (memories-space §2.6 item 1). It does not
  exclude transcript text: `omi_recordings.transcript`, and soon `omi_segments.text` and the candidate
  `payload`/`evidence`.
- A deleted recording, a forgotten name, or a bystander's words therefore stay restorable for up to about 35 days.
  That conflicts with "forgotten means forever".
- **Next action:** owner decision OD3. Option (i): `--exclude-table-data` for the pendant text tables. Transcripts
  are derived from audio that no longer exists, so a restore loses them; that is the honest cost. Option (ii): keep
  them and state the 35-day bound on the Memories page.
- **Effort:** S.

**P1-5. The pendant hears Zoe, the owner's own commands to Zoe, and the television.** [U] as a measured rate,
[V] as a design gap.
- At home the pendant picks up:
  - the panel's Kokoro replies ("Reminder: ...");
  - the owner's spoken commands, already executed by the voice path;
  - radio, TV and podcast speech.
- The day review would re-propose done items, and treat media speech as `other` command-shaped lines.
  `memories-space.md` has list dedup but nothing for TTS or media.
- Omi solved the media half with a speaker-tag answer `not_a_person` ("TV, podcast, music: stop asking about this
  voice", `backend/models/speaker_tag_prompts.py`) [V].
- **Next action:**
  - For build (b): a `not_a_person` tag on unknown-voice clusters, which then drops matching segments.
  - For build (a): drop pendant segments that overlap a logged panel voice turn or a TTS playback of the same owner.
    Time-aligned on `device`/`corrected` time only.
- **Effort:** S-M.

**P1-6. A trip leaves a large backlog, and every gate defers it.** [V] for the gates, [U] for the throughput.
- On return, the drain might be 2-3 days of talk, about 30 h of audio [U].
- New transcription jobs wait for all three gates, sharing one Moonshine with the panel:
  - MemAvailable at or above the floor (1,200 MB live; one sample read 967 MB);
  - 120 s of panel quiet;
  - outside the night window.
- The evening day-review lane (4B) competes with the same idle evening.
- The firmware drain throughput over the marginal link is unmeasured (plan 0.3a item 5). The real-time factor under
  deferral is unmeasured.
- The 2026-10-15 drain of 704 minutes was transcribed the same day [V]. That gives a lower bound, not a plan.
- **Next action:** measure the drain rate and the transcription rate (the deferral logs and `transcription.backlog`
  exist since #2077). Then decide whether a catch-up mode is needed. One option: transcription allowed inside the
  night window on nights the 12B is not running. How that interacts with the
  [12B night window](../knowledge/night-window.md) priority is the owner's call.
- **Effort:** S to measure.

**P1-7. No sync while away.** [V] that none exists, [S] for the options.
- The phone app is rejected (plan 0.2). Options:
  - (i) A **Web Bluetooth** page served by Zoe over the private link home ([panel-remote-access](../architecture/panel-remote-access.md),
    #2083). Chrome on Android exposes GATT notify to a page [S]; Safari on iOS does not [S]. It would drain the ring and
    upload with the same custody rule. It needs a device credential for the phone.
  - (ii) A Pi Zero 2 W travel dock (~AU$40) running the same `omi-sync`, uploading over the tailnet.
  - (iii) Accept the 34 h ring and rely on P0-2's warning.
- Owner decision OD2.
- **Effort:** (i) L, (ii) M, (iii) none.

**P1-8. The law is unsettled and still unasked: one consolidated question.** [V] Act text, [S] commentary. Section 4.

### P2

**P2-1. The Pi retries every few seconds while the pendant is away.** [V] About 85 `hcitool lecc` attempts and about
256 journal lines per hour. That is radio time and SD-card writes for nothing.
- **Next action:** exponential back-off after N consecutive misses (to about 5 min), or a passive advert scan first,
  with a connect only once an advert is seen. **Effort:** S.

**P2-2. The Pi spool never shrinks.** [V] It is 252 MB on disk with 99% free pages (`PRAGMA freelist_count`). SQLite
only reuses pages, so the largest drain ever sets the file size for good.
- **Next action:** `PRAGMA auto_vacuum=INCREMENTAL` with an `incremental_vacuum` after a drain, or a `VACUUM` when
  idle. **Effort:** S.

**P2-3. Stale documentation.** [V]
- Problem 1: the integration plan's header, §0.3a and several ledger lines describe the pendant as 3.0.18 with codec
  `0x0f`. It has been 3.0.21 on the ring path since 2026-10-13.
  - Fix: this PR adds a dated note to the plan header. The ledger lines are left for their owners to close with the
    `check` output.
- Problem 2: the ring-path summary prints the note `legacy firmware: the pendant frees records itself`. It means
  "stock (non-custody) ring firmware" and reads as if the legacy 3.0.18 path ran.
  - Fix: reword the note in `engine.py`. **Effort:** S.

**P2-4. What the pendant never woke for is never recorded.** [V] mechanism, [U] size. The T5838 wake threshold
(250) decides capture before anything else. Its false wakes are the 11 s click recordings: 33 of 273 rows came back
empty (#2081). Its misses (quiet speech at distance) leave no trace at all, so recall has an unmeasured ceiling.
- **Next action:** a controlled test with the owner home: scripted sentences at 1, 2 and 4 m and at two loudness
  levels, counted on the next drain. **Effort:** S.

**P2-5. Battery is not monitored.** [V] Battery is read only in the manual check. The vendor claims 10-14 h, and the
2026 changelog says more than 15 h [S]. When the pendant dies, capture stops silently.
- Covered by the P0-2 heartbeat. Add a low-battery item only if the owner wants one. **Effort:** none extra.

**P2-6. Transcripts have no retention limit.** [V] Audio expires after 7 days. Transcripts stay until deleted. That
matches Omi and Plaud. Whether the household wants an upper bound, for example 12 months unless reviewed or kept, is
owner decision OD4. **Effort:** S.

**P2-7. People pages, a daily recap, and mood.** The field's most-used surfaces (section 3) are not in any Zoe plan
for pendant data. They are pinned as ideas (`docs/IDEAS.md`), not planned work.

## 2. The pipeline end to end, with failure modes

| Stage | Where | What can go wrong | State |
|---|---|---|---|
| Capture | pendant mic, T5838 wake-on-sound | Quiet speech below the wake threshold is never recorded (P2-4). False wakes record 11 s of click and noise (#2081). Mic gain 0 is not a mute | [V] firmware, [U] size |
| Buffer | 480 MiB raw SD ring, about 34 h [U] | Full means oldest overwritten, counted in `dropped_packets` and invisible (P0-2). Readable and erasable by anyone in range (P0-1) | [V] |
| Clock | RTC set by `OMI_SYNC_SET_DEVICE_TIME` | Survives a full power loss? [U]. `estimated` times collapse days (P1-2). Time zone while away | [V] flag on, [U] RTC |
| Battery | 150 mAh | Dies silently; not monitored (P2-5) | [V] |
| Link | panel Pi BLE, `hcitool lecc` | Marginal range (−73 dBm after repositioning). Retry churn while away (P2-1). One central at a time | [V] |
| Spool | Pi SQLite, fsync per 64 records | The Pi's copy is the only copy between delivery and Zoe's confirmation on stock firmware. File never shrinks (P2-2). Not backed up (by design: short-lived) | [V] |
| Upload | `POST /api/omi/recordings`, sha256 echo | Idempotent, tombstone-aware, owner from env (registry off) | [V] |
| Transcribe | Moonshine in zoe-data, 20 s chunks | Defers on RAM, voice and night. Post-trip backlog throughput unmeasured (P1-6). Empty transcripts held with audio (#2057, #2081) | [V] gates, [U] rate |
| Store | `omi_recordings` (Postgres) | One joined string, no per-line speaker or time (build b fixes). No transcript expiry (P2-6) | [V] |
| Retain / delete | audio 7 days; delete per id; forget by name or user | No time-range delete or off-the-record window (P1-3). Backups keep text about 35 days (P1-4). `erase_entity` redacts `done` rows only; a `failed` row's audio may still hold a forgotten name until its expiry | [V] |
| Backup | nightly `pg_dump` local, 7 daily + 4 weekly | On the same disk as the live data (no off-box copy of anything, not pendant-specific) | [V] |
| Search / recall | none | P1-1 | [V] |
| Observability | Pi journal, `OMI_TRANSCRIBE_DEFERRED`, `transcription` on GET | Nothing on last-seen, fill, dropped, battery (P0-2) | [V] |
| Security | BLE GATT | Unauthenticated read and erase (P0-1); public OTA key (ledger 2026-10-13) | [V] |

## 3. The product field: what they do that Zoe doesn't

Status and privacy model first. Source: a field scan this session, with Omi read in source.

| Product | Status 2026 | Privacy model | Note |
|---|---|---|---|
| **Omi** (BasedHardware, MIT) | alive, very active | Cloud: transcripts in Firebase, audio in GCS, embeddings in Pinecone, text to OpenAI. "Enhanced" encryption is server-held HKDF, not end-to-end. `store_recording_permission` defaults off. Delete per conversation, memory, person, summary, account. No bystander logic, and the LED is a connection light [V] | The richest feature set; the backend is readable source |
| **Limitless** (ex-Rewind) | Acquired by Meta, announced 2025-12-05. Sales stopped; support "throughout 2026"; withdrawn from the EU, UK and others [V limitless.ai] | Cloud. "Consent Mode" (records only voices that consented) is claimed [S/U] | End-of-life; the consent-mode idea is the piece |
| **Bee** | Acquired by Amazon (2025) and still sold [S] | Vendor claims: audio processed then deleted, private by default [V vendor page, marketing] | Facts that learn patterns, a daily timeline with places, to-dos turned into drafts |
| **Plaud** (NotePin, Note Pro) | alive; strongest for meetings | Regional "private cloud", no training on data, per-file deletion; bystanders get a "let others know" notice only [V privacy policy] | Large template library for meeting notes |
| **Friend** | alive, marginal (about 3k units [S]) | Cloud LLM, claims no retention [S] | A companion, not a memory tool; nothing to borrow |
| **Owl** (OwlAIProject) | experimental | Can run fully local (Whisper + Ollama) [V README] | Closest in spirit; VAD segmentation, learns your voice |
| **Screenpipe** | alive, desktop | Local store, optional cloud LLM [V own page] | Not a wearable; an MCP/REST read API is the piece |

What Omi does that Zoe's design does not, read in its source (`/home/zoe/.opensrc/repos/github.com/BasedHardware/omi/main`) [V]:

1. **Action items carry how they were captured, whose they are, and how sure.**
   - The fields: `capture_kind` (explicit_command, clear_commitment, direct_request, inferred_next_step),
     `capture_owner` (user / other / unknown), `ownership_confidence`, `due_certainty` (confirmed / tentative) and
     `source_segment_ids` (`backend/models/structured.py`).
   - A tentative plan is never also a confirmed event.
   - This maps one-to-one onto the day review's "commitment-strength rule" and preselect. Build (a) can use these
     fields as its schema.
2. **A discard gate before extraction.**
   - Over 100 words is kept. Under that, cheap rules decide first, then one small model call. Under 2 minutes, only
     "clearly actionable" survives. A neighbour hint discards fragments that only continue the previous
     conversation (`backend/utils/conversations/relevance.py`).
   - Zoe's equivalent is the cue filter in memories-space §4.3 step 1. The 2-minute bar is worth copying.
3. **Speaker-tag prompts with a "not a person" answer** (P1-5), and a measured match rule.
   - The rule: cosine distance under 0.65, a margin of at least 0.10 over the runner-up, and at least 5 s of
     audio (`backend/utils/stt/speaker_match.py`).
   - Same-user cross-session distance is 0.40-0.53, versus 0.93 for others. EER is 17% at 2 s and 10% at 5 s.
   - A prior for build (b)'s thresholds, which today are 2 s windows.
4. **A daily summary shape.** Headline, stats, highlights per topic, prioritised action items,
   **unresolved questions**, **decisions made**, memories learned (ids for a review card), and locations
   (`backend/models/daily_summary.py`). The recap fires at 22:00 local by default.
5. **Memory attribution and decay.** `subject_attribution` (user / third_party / unknown), uncertainty reasons,
   `half_life_days` / `valid_to`, and per-memory evidence. Identity rule: "if uncertain about a person's identity, DO
   NOT extract", never "Speaker 0".
6. **Entity pages as derived views**: corrections go to the ledger, and the page is never a second source of truth.
7. **Commitment follow-up jobs** with a revision stamp that drops stale nudges.
8. **A nightly "dream" agent** whose edits are `shadow / suggest_only / applied`.

What nobody ships: mood or energy tracking, a real recording indicator, a bystander mechanism beyond a notice (the
Limitless claim aside), and a fully local pipeline with a pendant (only Owl is close).

**Zoe's position.**
- Ahead on privacy: local STT, no cloud, approve-before-write, forget-forever, restraint.
- Behind on surfaces: no recap, no search, no people page, no follow-up on what others asked of the owner.
- Behind on device telemetry: P0-2.

The borrowable pieces are pinned in `docs/IDEAS.md` (section "Ambient-memory field scan").

## 4. Bystanders and the law (Western Australia)

Not legal advice. Act text read on the WA legislation site (consolidation with amendments to 5 April 2023; the
current compilation was not confirmed) [V]; commentary [S].

- **WA requires the consent of every principal party, including when the recorder is a party.** Under s 5(1), a
  person must not use a listening device:
  - (a) to record a private conversation they are not a party to; or
  - (b) to record one they **are** a party to.

  For the party-recorder, the everyday exception is s 5(3)(c), the express or implied consent of **each** principal
  party. There is also s 5(3)(d): one principal party consents and the recording is reasonably necessary to protect
  that party's lawful interests. "Lawful interests" is undefined. Penalty for an individual: $5,000 or 12 months.
  An always-on pendant engages both limbs: the wearer's own conversations and the ones overheard.
- **"Private conversation"** excludes conversations where the parties ought reasonably to expect to be overheard. A
  family kitchen is likely private; a café queue may not be.
- **s 9** separately prohibits communicating or publishing a record of a private conversation, with s 9(2) exceptions.
  A lawful recording still needs one of them to be shared. Whether moving it to one's own server is "communicating"
  is untested [U]. Zoe never shares, but a card or a Telegram message to the owner's phone is worth asking about.
- **Privacy Act 1988 (Cth).** The household exemption (s 16) and the individual exemption (s 7B(1)) likely take a
  private person's local storage, voiceprints included, outside the APPs [S]. **The statutory tort for serious
  invasions of privacy (Schedule 2, in force 10 June 2025) applies to individuals.** The household exemption does
  not shield from it [S, OAIC page]. Consent and lawful authority are defences. A recording unlawful under the WA Act
  weakens both.
- **Children.** No WA-specific rule was found. Whether a child can consent, or a parent can consent for them, is
  untested [U]. Under-18 plaintiffs have until their 21st birthday under the tort [S].
- **No WA reform found** for 2024-2026 [U]. A lawyer should confirm.

**The one lawyer question (D1, consolidated; send as is):**

> I wear an always-on audio pendant in Western Australia. It records conversations around me, including with
> household members (some of them children), visitors and bystanders. A server on my own hardware transcribes it,
> extracts reminders and notes I approve, and keeps voiceprints of people I tag, for suggestions only. Nothing is
> published or sent off my hardware.
> (1) Under the Surveillance Devices Act 1998 (WA), is this unlawful under s 5(1)(a) and (b) unless every principal
> party consents under s 5(3)(c)? Can that consent be implied by household awareness, a visible recording light, a
> notice, or an easy pause? Can a parent consent for a child? Does s 5(3)(d) "lawful interests" ever cover day-to-day
> memory-aid use?
> (2) Does moving the recording to my own server, or showing an extract on my own phone, "communicate" it under s 9?
> (3) How do the Privacy Act's statutory tort (from 10 June 2025) and the household exemption apply to me as an
> individual, including for voiceprints of non-household people?
> (4) Is there 2024-2026 WA reform or case law I should know about?
> (5) Which safeguards would you want in place to keep the criminal and civil risk low: consent records, retention
> limits, deletion on request, not recording in others' homes or workplaces?

**Practical posture that fits the owner's decisions.** The owner rejected a consent gate for voice learning
(2026-10-14), and nothing here re-proposes it. These measures sit beside that decision:
- **Visible indicator**: the stock LED is solid red when *disconnected* and blue when connected, so "red" is in
  practice "recording to the card", and nobody around knows that. Phase-2 firmware turns the LED into a recording
  light (firmware research §8.4).
- **The household is told.**
- **A pause that works**: the 3 s power-off today; P1-3's off-the-record window on the Zoe side; press-to-record
  later.
- **Auto-drop in sensitive places**: P1-3, calendar and zones.
- **Delete on request**: P1-3's time-range delete.
- **Kids**: no child wears a pendant (D14); a child's voice is not learned by default (D16).
- **Not worn in others' homes or workplaces** until the lawyer answers (D2's "alone or with people who know").

## 5. Memory quality: what the day review must call into

The rules: user-stated beats confirmed beats inferred; forgotten means forever; restraint for sensitive classes; the
distress hand-off; affect on for the household. To keep them, build (a) must call these existing seams rather than
reimplement them. Rows marked **NEW** are not in `memories-space.md` today.

| Seam | When | Why |
|---|---|---|
| `distress_handoff` (`guarded_text` / the tier classifier) **NEW** | on every packed line, before the model sees it | A crisis line is never a candidate, and the turn is off the record (`memory_provenance.mark_turn` pattern). Whether a pendant distress line should ever reach a human is owner decision OD6. Default: no autonomous action from a recording hours old, but keep it out of every writer |
| `memory_forgotten.spans` + `forget_match.redact` **NEW at create and at apply** | candidate creation, and again inside `omi_review_apply` before each write | A forget between the candidate and the approval must win (P0-3) |
| `own_words.analyze` **NEW** | on `you` lines used as evidence | The owner quoting someone ("she said add bread") or reading text aloud is not the owner's statement. `exact_words` already filters this |
| `memory_authority` (`origin="omi_review"` → `user_confirmed`) | at write | Pendant speech is never `user_stated` to Zoe. Unapproved, it is never stored at all. Approval is what makes it confirmed |
| `restraint.classify_full` | candidate verify step | Designed (§4.3 step 3, §6.4) |
| Contradiction → `held_conflict` | apply | Designed (§5.4). A pendant-derived row never supersedes a user-stated one |
| `erase_gate`, `user_filters.is_synthetic_user` | apply | Designed |
| People resolver (`people_graph`) | `person_memory` and tags | Never invent a person (Omi's identity rule); resolve to the owner's layer |
| Affect | **tension, owner decision** | The household rule keeps emotional context for every member. The day review drops `affect` candidates (§6.4). D11 already asks whether the owner may opt in for themself; this audit adds that the default contradicts the household affect decision for the owner's own speech, so D11 should be answered with that in view |

Not to do: feed pendant text into `exact_words` automatically. It is the owner's words, but not said to Zoe, and not
speaker-verified at the panel's standard. A pendant index should be its own table (P1-1), so a pendant forget, a
retention limit or an off-the-record window can act on it alone.

## 6. Scale and cost on the Jetson

Numbers from the live aggregates (section 0) plus arithmetic [U where marked]:

| Item | Per day of heavy wear (about 12 h of recorded audio) | Notes |
|---|---|---|
| Opus on the pendant / Pi spool | about 170 MB [U] (32 kbit/s ceiling) | The spool file keeps its high-water size (P2-2). The Pi SD has 13 GB free [V] |
| WAV staged in zoe-data | about 1.4 GB transient (16 kHz s16 = 115 MB/h) | Deleted after a non-empty transcript. The disk has 1.3 TB free [V] |
| Transcript text | about 180 KB (4.2 chars/s [V]) | About 66 MB a year. The table is 528 kB today [V] |
| Segment embeddings (build b) | a few MB to about 40 MB [U] (depends on window and hop; 14-day TTL) | Excluded from backups by design |
| Moonshine time | unmeasured under the gates (P1-6) | Shares the voice model; a running job yields per chunk |
| 4B evening lane | at most 8 calls per batch (design) | Cancel-on-turn and the prewarm loss are unmeasured (E9) |
| 12B night window | already full (30-60 min of jobs in about 52 min) | The pendant must not take it. Transcription stops at `WINDOW_OPEN` [V] |

RAM is the ceiling, as everywhere. Pendant work adds no resident model today: Moonshine is shared and the 4B is
shared. Build (b)'s embedder inside zoe-data (cell T8) is the first new tenant. Measure it before
`ZOE_OMI_SPEAKER_TAG=on`.

## 7. Search and recall

See P1-1. In short:
- Today: no. Nothing reads the table.
- Minimum: `omi_segments` (build b) + a `pendant_words` Postgres FTS index on the `exact_words` pattern (per user,
  forget-aware, evidence-question trigger) + a deterministic card answer.
- Later: semantic search through Zoe's Chroma verbatim tier, after the residue scrub.
- Not Hindsight: the decision was KEEP_Z0.
- The policy on searching other people's words is OD5.

## 8. Other things plainly missed

- **The pendant overhears Zoe** (P1-5). Without a fix, every reminder Zoe speaks becomes a candidate the next day.
- **Prompt injection by speech is fenced only in the design.** A guest who says "Zoe, add ... to the shopping list"
  near the pendant becomes, at worst, an unticked candidate (memories-space §6.6). Keep the static test that
  `omi_day_review.py` imports no write service (E7). It is the whole defence.
- **A lost or stolen pendant** holds up to about 34 h of audio, readable without pairing (P0-1). There is no remote
  wipe. CLEAR exists but needs a connection.
- **The panel and the pendant share one radio and one lock** (`radio_lock.py`), so a long drain and a Bluetooth
  speaker or an update contend. Not measured.
- **Several pendants** (the registry, plan 4.11) multiply every number in section 6, and make P0-2's heartbeat
  per-device.

## 9. Owner decisions

1. **OD1 Wear posture while P0-1 stands.** The pendant is readable without pairing whenever the Pi is not connected:
   keep wearing it as now, power it off in public places, or prioritise the phase-2 firmware (encrypted storage plus a
   bond with the Pi)?
2. **OD2 Sync while away** (P1-7): Web Bluetooth page on an Android phone, a travel dock, or accept the ring and the
   warning?
3. **OD3 Backups of pendant text** (P1-4): exclude it from the nightly dump (recommended; no 35-day tail), or keep it
   and show the bound?
4. **OD4 Transcript retention** (P2-6): until deleted (today) or an upper bound unless reviewed or kept?
5. **OD5 Searching what other people said** (P1-1): allowed for the wearer as a private card, words only, or not at
   all?
6. **OD6 Distress in a recording** (P0-3): kept out of every writer (recommended), and is anyone ever told?
7. **OD7 Off-the-record windows and auto-drop** (P1-3): build it, and which triggers: a phrase, the calendar, Home
   Assistant zones?
8. **OD8 The lawyer question** (section 4): send it as written?

Already open elsewhere and not repeated here: D1-D16 in `memories-space.md` §11, and the firmware decisions in the
press-to-record research §8.

## 10. Sources

- Repo (origin/main acca1f2e1):
  - `services/zoe-data/omi_recordings.py`, `routers/omi_recordings.py`;
  - `services/omi-sync/omi_sync/{engine,check,ble_link}.py`;
  - `services/zoe-data/{exact_words,distress_handoff,restraint,struggle_signals}.py`;
  - `scripts/maintenance/{postgres-nightly-backup.sh,redact_backups.py}`;
  - the two design docs; `docs/knowledge/open-problems.md`;
  - PR #2081 (open).
- Omi source (opensrc cache):
  - tag `Omi_CV1_v3.0.21`: `omi/firmware/omi/src/lib/core/storage.c`, `src/sd_card.c`, `src/lib/core/sd_card.h`;
  - `main`: `backend/models/{structured,daily_summary,speaker_tag_prompts,memories,entity_pages,dream_report}.py`,
    `backend/utils/conversations/relevance.py`, `backend/utils/stt/speaker_match.py`, `backend/utils/encryption.py`.
- Live, read-only:
  - count-only SQL on `omi_recordings` / `omi_devices`;
  - the Pi `omi-sync` journal and spool (`mode=ro`);
  - `systemctl --user list-timers`; `free -m`; `du`.
- Web:
  - [WA Surveillance Devices Act 1998](https://www.legislation.wa.gov.au/legislation/statutes.nsf/law_a1919.html);
  - [OAIC statutory tort](https://www.oaic.gov.au/privacy/your-privacy-rights/more-privacy-rights/statutory-tort-for-serious-invasions-of-privacy);
  - [OAIC biometrics](https://www.oaic.gov.au/privacy/your-privacy-rights/surveillance-and-monitoring/biometric-scanning);
  - [Privacy Act s 16](https://20.austlii.edu.au/au/legis/cth/consol_act/pa1988108/s16.html);
  - [limitless.ai](https://limitless.ai/); [bee.computer](https://www.bee.computer/);
  - [Plaud privacy policy](https://www.plaud.ai/pages/privacy-policy); [Omi privacy](https://www.omi.me/pages/privacy);
  - [Omi changelog](https://omi.featurebase.app/changelog/goals-tracking-long-awaited-folders-daily-recaps-and-much-more);
  - [Owl](https://github.com/OwlAIProject/Owl); [Screenpipe vs Omi](https://docs.screenpipe.com/vs-omi).
