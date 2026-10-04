---
type: Reference
title: Synthetic users, proactive recipients and kiosk presence (2026-09-27)
description: Who the nightly memory passes and the proactive triggers treat as a real user — the is_synthetic_user rule and its allowlist flag, the household recipient rule that replaced "created a chat session in 7 days", guest-owned kiosk presence, the flag-dark brief-on-arrival, the flag-dark proactivity selector and open-loop lifecycle, the nightly purge of probe chat sessions, and why the spoken morning brief was silent from 08-16.
tags: [memory, dreaming, proactive, morning-brief, brief-on-arrival, presence, test-data, zoe-data]
timestamp: 2026-09-27T12:00:00Z
---

# Synthetic users, proactive recipients and kiosk presence

Tracker rows: B3.2, B2.1 and "Speaks first" in [the program](../architecture/beat-the-bar-2026-program.md).

## Synthetic users are filtered at read time

`services/zoe-data/user_filters.py::is_synthetic_user` is true for guest sentinels (`guest`,
`anonymous`, `voice-guest`, `voice-daemon`, empty) and ids matching
`^(test|probe|demo|ci|e2e|bench)[-_]` (case-insensitive; the separator keeps "Christine" or
"testa" real). `ZOE_SYNTHETIC_USER_ALLOWLIST` (comma list, default empty) opts pattern ids
back in for a lab, never a guest sentinel. It is applied in the dreaming, weekly
consolidation, music-taste and portrait passes and in the morning, evening,
evolution-digest and emotional follow-up triggers, each logging one `<pass>: users kept=N skipped_synthetic=M` line
(counts only). The 03:00 memory digest is deliberately **not** filtered: its selection must
match its independent "was there input" probe, or a probe turn raises a false
`no_eligible_users_despite_input` alert. The purge below covers it instead.

The dead `svc.list_users()` call was removed rather than implemented. MemoryService never
had it, so the chat-owner fallback query was already what ran every night.

## Proactive recipients and kiosk presence

`proactive/recipients.py::proactive_recipients`:
- **Active users** are owners of a user turn in `chat_messages` in the last 7 days. The
  evening wind-down and the emotional follow-up use only these.
- **Household**, for the morning brief and the evolution digest, adds the users named by a
  panel's `default` binding. The evolution digest then keeps admins only
  (`recipients.admins_only`, `auth.is_admin_role`): its approve/defer links are
  `require_admin`-gated.
- Synthetic ids are removed from both.

It replaced "created a `chat_sessions` row in 7 days". Telegram turns reach `chat_messages`
through `/api/chat`, but they reuse one session per chat.

`proactive/presence.py::panel_presence_tier` returns a tier. The kiosk reclaims the panel
row as `guest` 300 s after the owner goes quiet, so from 08-16 the brief was refused
`panel=none` even with the panel on. It was not created at all from 09-11.

| tier | condition | what is spoken |
|---|---|---|
| `owner` | a fresh foreground row owned by the member (written by sign-in or PIN, kept fresh by their own turns) | the full brief |
| `bound_guest` | a fresh `guest`-owned row on a panel whose `default` binding names the member | only the trigger's `spoken_guest_safe` line (first name, no calendar, loops or memories); the full brief stays in push and `proactive_pending` |
| `absent` | anything else | nothing |

Face-ID and voice-ID claims have no timestamped server-side record yet. They are accepted
per turn in `routers/voice_tts.py` and kept only as an in-memory session binding that
carries across rollovers. So they do not raise a panel to `owner`; wiring them in is a
voice-path change and needs the replay gate.

## Brief on arrival (B2.1, flag-dark)

`proactive/arrival.py` speaks a missed 07:30 brief once, when its member turns up.
It needs `ZOE_PROACTIVE_BRIEF_ON_ARRIVAL=1` and the master `ZOE_PROACTIVE_SPOKEN=1`;
both default off, and with either off nothing is read or queued.

- **Trigger:** a foreground `POST /api/ui/panel/bind` or `/api/ui/state/sync` from the
  member's own session (`routers/ui_actions.py::_note_owner_presence`). Guests and
  device tokens never count. The check runs as a background task, at most every 30 s
  per member. No voice-path file is involved; face and voice claims will join once
  they have a server-side record (see above).
- **Speaks** the latest stored `proactive_pending` text of today's brief, through the
  same two lanes as the 07:30 brief, on the panel where the member was seen.
- **Only if all hold:**
  - 07:00–11:00 local, and not quiet hours;
  - tier `owner` on that panel; not a synthetic or guest id;
  - a brief exists today and none of today's rows was opened in chat;
  - nothing from today was played, except a known `bound_guest` teaser. All of
    today's rows count, so a later brief cannot hide an earlier delivery, and an
    unrecognised text counts as heard;
  - nothing is still queued;
  - no user turn from the member in the last 2 minutes.
- **Panel-scoped:** the row's trigger is in `voice_announce.PANEL_SCOPED_TRIGGERS`,
  so only the daemon whose device-token panel matches may play it, even in the default
  claim-any mode. If the presence panel has no live device token (a browser alias), no
  daemon could claim it; nothing is queued and it logs `outcome=unscoped_panel`.
- **Once per member per local day, across both paths.** The claim is a
  `proactive_responses` row (migration `0030`,
  `UNIQUE (user_id, claim_key, local_date)`, `claim_key = morning_brief_full`).
  With the flag on, the 07:30 path takes the same claim before speaking the full brief
  (`arrival.claim_scheduled_brief`). A lost claim logs `outcome=already_spoken`. A claim
  DB error fails closed (`outcome=claim_error`): the push is still sent, but the full brief
  is not spoken. So the
  full brief is spoken once whichever path, panel or worker gets there first. With both
  this flag and `ZOE_BRIEF_ON_FIRST_TURN` off, the 07:30 path is unchanged and takes no
  claim. A failed speak is not retried.
- **Log:** `PROACTIVE_SPOKEN trigger=morning_checkin_arrival user= panel= outcome=
  daemon_queue= tier=owner missed=absent|guest_teaser|expired`.
- **Response signal (for B2.2):** the announcement id is linked to the claim in the
  announcement's own transaction. Once a claim row's window has passed, the slow loop
  sets its `outcome`:
  - `accepted`: a member user turn within `ZOE_PROACTIVE_ARRIVAL_RESPONSE_S`
    (default 120 s) of the daemon playing it;
  - `ignored`: no such turn;
  - `undelivered`: the linked row expired unplayed;
  - `unknown`: no announcement is linked. This is never `undelivered`.

  Each is logged as `PROACTIVE_RESPONSE`.

## Brief on the first turn of the day (flag-dark)

**Live state (2026-09-29):** ON on the live box (`ZOE_BRIEF_ON_FIRST_TURN=1`), with
`ZOE_PROACTIVE_SPOKEN=0` by owner decision (no unprompted spoken brief).

No unprompted spoken brief: `brief_first_turn.py` folds the day's context into the
member's first brain turn of the morning, so Zoe mentions it the way a human assistant
would. `ZOE_BRIEF_ON_FIRST_TURN=1` enables it (default off, read per call).

- **Where:** both brain lanes call it. The Flue seam
  (`zoe_flue_client.run_flue_brain_streaming`) appends a `[Today 2026-09-29]` …
  `[END Today]` block after the user's words, where the continuity block rides. The
  label is dated because Flue keeps every message it was sent and a session can
  outlive a household day (a voice panel session rolls only after 5 min of silence,
  `routers/voice_tts._get_or_create_voice_session`; a caller-supplied or chat session id
  never rolls). The body says `Today is <date>` and to ignore an earlier block with a
  different date. The core seam folds the same body in as a `[Today]` context block just
  before `[The user just said]` (`_CONTEXT_BLOCKS`, mirrored in zoe-core `memory.ts`),
  where older copies are stripped anyway.
  Tier-0 and keyword intents never reach a brain and never count.
- **Only if all hold:** real member (`user_filters.is_synthetic_user`); local time in
  `ZOE_BRIEF_WINDOW_START`–`ZOE_BRIEF_WINDOW_END` (default `05:00`–`12:00`,
  `ZOE_TIMEZONE`); not a continuity (emotional statement) turn; today's
  `morning_brief_full` claim is not taken; the day context is not empty.
- **Day context:** `morning_checkin._build_morning_context(..., include_board=False)`
  (the same gatherer as the 07:30 brief). Items are decided in code: today's events
  whose STORED end has not passed (`end_time`, else start + `duration` minutes; the
  voice writer stores neither, so an event with no end stays listed), open loops, and at
  most one recent emotional moment. The portrait
  and the engineering board are never items. Empty means nothing changes and no claim.
  The gathered context is cached in-process for 5 minutes.
- **Shape (`turn_shape`, phrase-gated):** a greeting or open turn ("morning", "hey zoe,
  what's up", "let's talk") gets the full list with a "mention it naturally, once"
  instruction. Any other turn gets one line, and only for an event starting within 2 h
  or an overdue loop. Otherwise nothing is injected and the claim stays free for the
  next open turn.
- **Claim:** the SAME `proactive_responses` row as the 07:30 and arrival paths
  (`trigger_type = brief_first_turn`). It is settled in each lane's stream `finally`,
  so it is taken once any reply text went out, including when the stream then errors,
  the client disconnects or a barge-in cancels the turn (the write is shielded). A turn
  that emitted no text (an error before the first token, the canned fallback) takes
  nothing. Under `ZOE_LOOP_LIFECYCLE` the same settle marks the loops and moment the brief
  mentioned as surfaced for the selector ([Open-loop lifecycle](#open-loop-lifecycle-flag-dark)).
- **The 07:30 spoken path with only this flag on** checks the claim read-only
  (`arrival.claim_scheduled_brief`): if the first-turn brief already went out it is not
  spoken; otherwise it is queued WITHOUT a claim, because queueing is not delivery.
  - **Heard = the daemon's playback ACK.** `delivered_at` is set when the daemon CLAIMS a
    row, before TTS and playback. The daemon now POSTs
    `/api/voice/announcements/{id}/played` only when the audio actually played (the player
    exited 0, or a barge-in stopped it), which sets `played_at`. It retries 3 times over
    ~10 s on a background thread; if every attempt fails the heard brief reads as unheard
    and the first turn repeats it (accepted: a repeat beats a lost brief)
    (migration `0032`, `voice_announce.mark_played`, only the claiming panel, once). Only
    `played_at` counts (`arrival.scheduled_brief_delivery`); the first-turn check then
    takes the shared claim.
  - **Waiting is bounded.** A queued row holds the conversational brief until its
    `expires_at` (`ZOE_ANNOUNCE_TTL_S`, default 120 s); a claimed but unacknowledged row
    until `expires_at` + 180 s. After that the first turn gets the brief. The guest
    teaser never counts.
  - **The read-then-queue race** (07:30 reads a free claim, a first turn injects, the
    07:30 row is queued anyway) is closed at the daemon claim
    (`voice_announce.claim_announcements` → `brief_first_turn.scheduled_row_gate`): a
    `morning_checkin` row is held pending while ANY of that member's conversational briefs
    is mid-reply (one in-process hold per turn, ≤120 s each; zoe-data runs one uvicorn
    worker). `settle` writes the claim before releasing its own hold, and keeps the hold
    if the write fails. The row is marked `expired` without playing once the first-turn
    brief holds the claim. Inert with the flag off.
  - **Deploy order:** the daemon's ACK ships in `scripts/setup/zoe_voice_daemon.py` +
    `zoe_voice_announce.py`, deployed to the Pi by the operator separately. Until it is,
    no row is ever ACKed, so a played 07:30 brief is treated as unheard after the bounded
    wait and the first turn repeats it. Deploy the daemon before enabling
    `ZOE_PROACTIVE_SPOKEN` together with this flag.
  - With `ZOE_PROACTIVE_BRIEF_ON_ARRIVAL` on, arrival's contract applies unchanged
    (claim before queueing, no retry).
  Two concurrent first turns can both see the block; only one claim lands.
- **Log:** `BRIEF_FIRST_TURN user= items= shape=greeting|command injected=0|1 claimed=0|1`,
  one line per decision on a non-empty day.
- **Pinned by:** `services/zoe-data/tests/test_brief_first_turn.py`.

## Proactivity selector (flag-dark)

Research gap #5 ([context audit](../research/zoe-context-audit-2026-09-29.md)): precompute
"things worth raising" nightly, surface at most one per conversation. `ZOE_PROACTIVE_SELECTOR=1`
enables both halves (default off, read per call; off = no I/O). Code: `proactive/selector.py`.

- **Nightly (dreaming phase 1.6, after open-loop extraction):** per real member, rank
  unresolved `open_loops` whose follow-up is due within 48 h (or undated), recent emotional
  rows (the continuity recency read, 72 h, `is_emotional_memory` + `fact_has_topic`, minus
  moments the `emotional_followup` push already spoke), and the member's events in the next
  48 h. `salience = importance × recency × relevance`:
  importance = `emotional_weight/5` (loops), `candidate_intensity` in [0.3, 1] default 0.6
  (moments), 0.6 (events); recency = `0.5^(age_h/72)` (events 1.0); relevance = 1.0 due ≤24 h,
  0.6 ≤48 h, 0.7 undated (loops; later-due loops decay under `ZOE_LOOP_LIFECYCLE`, see
  [Open-loop lifecycle](#open-loop-lifecycle-flag-dark)), 0.8 (moments), 1.0 ≤24 h / 0.7 ≤48 h (events). Drop < 0.1,
  one per topic (content-token containment), cap 5. No model call. Upserted into
  `proactive_candidates` (migration 0033) so cooldown/count survive the recompute; dropped
  rows are expired, deleted once out of cooldown. Log `PROACTIVE_SELECT user= candidates= kept=`.
- **Runtime (both lanes, `prepare` before the turn, `settle` in the stream `finally`):** a
  turn qualifies when `brief_first_turn.turn_shape` is `greeting` (any candidate with
  `on_open`), or it is not a deterministic intent (`intent_router.detect_intent`: commands,
  acks, meta — never) and one of a candidate's `cue_words` (its concrete anchors) is in the
  user's words. Highest salience wins, skipping expired rows, rows in cooldown (3 days) and
  rows raised twice. Never: with the `[Today]` brief on the same turn (the brief wins, logged
  `reason=brief`), on a continuity turn, a second time in the same session
  (`last_surfaced_session`, durable), or for a synthetic id (`is_synthetic_user`; the one
  exception is below).
- **Per-member spacing (both flags read per call):** at most `ZOE_PROACTIVE_RAISE_PER_DAY`
  raises (default 2; 0 = no cap) per local day (`ZOE_TIMEZONE`), at least
  `ZOE_PROACTIVE_RAISE_GAP_S` apart (default 7200; 0 = no gap; capped at the 3-day cooldown),
  across conversations. Before this, two conversations seconds apart each got a raise
  (2026-09-30 09:21:33 / :36, `bar-s5-open-1/-2`, different candidates). The evidence is
  durable — each candidate's `last_surfaced_at`, set at settle; a raised row outlives its
  3-day cooldown, so one stamp per row counts the day — and survives a restart. While a
  raised turn is still streaming, an in-process per-member hold covers the overlap. Blocked
  turns log `PROACTIVE_RAISE … injected=0 settled=0 reason=gap|daily_cap|held`. The brief
  check runs first, so the `[Today]` brief still wins its turn. Flue appends
  `[RAISE — once, naturally, only if it fits; otherwise ignore]` … `[END RAISE]` after the
  user's words (and defers a pending contact offer that turn, `SEAM_OFFER … reason=raise`;
  the offer ager skips it); core folds `[RAISE]` before `[The user just said]`. Both pairs are
  in the elide tables. The candidate is marked surfaced only once reply text went out. Log
  `PROACTIVE_RAISE user= kind= shape=greeting|cue injected=0|1 settled=0|1`.
- **Harness exception:** a harness-minted `demo_<tag>_<hex>` id may hold and receive
  candidates, but only through `POST /api/proactive/selector/run-synthetic/{id}` (internal
  token, the `forget-synthetic` guards: harness shape, not allowlisted, not a registered
  account, fail closed) — the Samantha bar's S5 hook. The nightly pass never sees one.
- **Junk never surfaces:** loops and moments must pass `open_loop_quality.loop_is_concrete`
  (the open-loop calibration, #1790); their anchor words are the candidate's `cue_words`.

## Delivery ledger (flag-dark)

Pull-not-push inbox, **PR 1 of 6** ([record](../research/pull-not-push-inbox-2026-10-04.md)
§3.1 / §3.5 / §5). `ZOE_PROACTIVE_LEDGER=1` (default off, read per call; off = byte-identical,
no DB access). Code: `proactive/ledger.py`; migration `0036`
(`proactive_deliveries`). It records evidence only: it changes no reply, no block, no spacing
rule, no candidate row, and never speaks (`ZOE_PROACTIVE_SPOKEN` stays 0).

Why: `proactive_candidates` says a block went out with a reply, not whether the reply voiced it
(PR #1821 found the brain voicing a greeting raise 0/5 times, indistinguishable from success
in production) and not what the person did next. Nothing live wrote an accepted / ignored label
for the lane that is actually used (record §2.5).

- **Writer.** One OPEN row per item a conversation carried: `selector._settle` (a raise that
  settled with reply text) and `selector.mark_brief_surfaced` (a `[Today]` brief line; needs
  `ZOE_PROACTIVE_SELECTOR` + `ZOE_LOOP_LIFECYCLE` like the brief mark itself). Idempotent on
  `idem_key = user|session|kind|source_ref|delivered_by|local date` (UNIQUE, `ON CONFLICT DO
  NOTHING`): a retried settle inserts nothing, but a later delivery of the same item (a permanent
  Telegram session, a raise again after the 3-day cooldown) is a new row.
- **The brain lanes are untouched, on purpose.** `zoe_core_client.py` / `zoe_flue_client.py` /
  `routers/voice_tts.py` are `VOICE_PATH_PATTERNS`: an edit there needs a Jetson replay-gate run
  bound to the PR head (serial, one shared artifact slot). The record (§3.5) sketched the
  voiced check in the settle path; this PR does it in the sweep from what chat already
  persisted: both lanes save the reply the person HEARD to `chat_messages` (the streaming
  voice lane saves what was spoken), the user's turn before the stream (chat, streaming voice)
  or together with the reply after it (non-streaming voice, which also saves a SECOND copy of
  the triggering utterance milliseconds before the reply). So the delivery's REPLY is the first
  assistant row of its session STRICTLY AFTER the settle (never before it: that is the previous
  turn's; assistant rows within 5 s behind it count as part of it), and the person's "next
  turn" is their first user row strictly after that reply at MICROSECOND precision, skipping
  any copy of the triggering utterance (`trigger_key`, a digest, no text), whichever
  order it was saved in. `tests/test_proactive_ledger.py` pins that no voice-path file mentions
  the ledger. Reply text is checked and discarded, never stored.
- **State machine.** `outcome` NULL = surfaced, awaiting the sweep. Closed outcomes:
  `undelivered` (the reply carried none of the item's anchor words, the #1821 failure),
  `accepted`, `ignored`, `unknown`. `voiced` (1 / 0 / NULL; anchors and reply are both stemmed, so plural/singular and
  possessives never decide it) is written when the row closes;
  NULL (no anchors, or no reply was ever found) is **unknown, never undelivered**, because
  nothing proves it was not voiced. `expires_at` = surfaced + 24 h: a row the sweep could not
  judge by then closes `unknown`, so nothing strands.
- **Sweep** (`ledger.sweep`, engine slow loop step 4, 300 s, paused in quiet hours). No
  persisted reply yet: the row waits. A reply without an anchor: `undelivered` at once. An
  `event` (Notify, no answer expected) voiced: `accepted`. Anything else (a Question) waits
  `RESPONSE_WINDOW_S` = 600 s after the reply, then the member's FIRST next turn decides: a
  deterministic intent, or a turn with no anchor word, is `ignored`; a turn sharing an anchor
  is `accepted`; no turn is `ignored`; an unverified item that was not taken up is `unknown`.
  A chat read or intent-router error leaves the row open for the next tick. The chat reads are
  Postgres-only SQL (`arrival._first_user_turn` shape; the next turn is member-wide, the reply
  session-bound), so unit tests fake `ledger._reply_after` / `ledger._user_turns`. Re-running
  is a no-op.
- **Logs** (counts and kinds only, never item or reply text): `PROACTIVE_LEDGER user= kind=
  shape= by=` (written), `PROACTIVE_LEDGER_OUTCOME user= kind= outcome= voiced=` (closed).
- **Not in this PR** (record §5 order): classes (Notify / Question / Review), the inbox read
  and `GET`/`POST` endpoints, the orb `has` state and `inbox_pending` sync field, the "what's
  up?" pull and `[INBOX]` block, the back-off, `delivery.py` consolidation, the P10 head. The
  ledger's `delivered_by` ('turn' | 'brief') leaves room for `pull`. The brief's `[Today]`
  calendar events are not marked surfaced today, so they get no ledger row.
- **Measure it.** After enabling: `SELECT outcome, count(*) FROM proactive_deliveries GROUP BY
  1` — the `undelivered` share is the live voiced-rate the day-sim could only sample, and the
  first two weeks are the W16 baseline. The head (P10) needs >=200 labelled rows with >=40
  positives (record §3.5). Pinned by `tests/test_proactive_ledger.py`.

## Open-loop lifecycle (flag-dark)

`ZOE_LOOP_LIFECYCLE=1` (default off, read per call; off = byte-identical) closes four gaps the
week-in-the-life simulation (`scripts/perf/samantha_day_sim.py`, live run 2026-10-03) found.
Code: `open_loop_lifecycle.py` plus the owners below. Voice path: replay-gate before enabling.

- **Raise phrasing (`proactive/selector.py` `ask_phrasing`).** The `[RAISE]` block rides in
  the USER message, and a bare hint read as the user asking: the first raise came back as "I
  don't have any information about how your dentist appointment went". For loops and moments
  the body now says to ask ONE short, gentle question in Zoe's own words, quotes the loop's
  `follow_up_hint` as the example when it is a question, and forbids the "no information"
  disclaimer. Events keep the old body; the flag is snapshotted at `prepare`. Probe on the
  live brain (bare system prompt, hint "How did the dentist appointment go?", 3 samples each):
  old body 1/3 disclaimers and 1/3 statements; new body 6/6 questions across two hints.
- **The brief marks what it said (`brief_first_turn.mentioned`, `selector.mark_brief_surfaced`).**
  The `[Today]` brief listed a loop and nothing recorded it, so the next conversation could
  raise it again. The morning context now carries each loop's `id` and the moment ids; at
  settle, once reply text went out, every loop or moment whose rendered line is in the brief
  is marked surfaced like a raise (count, 3-day cooldown, this session, one shared
  `last_surfaced_at`). An item the nightly pass never ranked gets an already-expired row that
  carries the cooldown, so a later night cannot select it fresh. The brief's stamp also
  starts the member gap (no raise within `ZOE_PROACTIVE_RAISE_GAP_S` of it), and the daily
  cap counts DISTINCT stamps, so one brief is one delivery. Lanes pass `session_id` to `prepare`. Log
  `PROACTIVE_RAISE user= kind=brief … marked=N`.
- **Corrections close loops (`resolve_for_supersede`).** A retired fact closes the open loops
  resting on it: every `MemoryService.review(edit)` (after the lock, `ended=False`) and the
  implicit supersede at write time and nightly (`memory_supersede`, `ended=True`). Pure rule
  `retired_match`: the loop names an anchor (`open_loop_quality.loop_anchors`) the
  retirement took away ("Ballarat" when it became Bendigo), or — for an ending/replacement —
  shares an anchor with the old fact and half its topic (`memory_supersede.topic_tokens`). An
  edit that only adds detail closes nothing. The closed loop's candidate is expired at once,
  and the extractor dedupes against loops resolved in the last 2 days so the next night does
  not re-extract them from the turns that made them. Log `OPEN_LOOPS user= resolved_by_supersede=N source=`.
- **Horizon.** The extractor's prompt gave only `"follow_up_in_days": 0-14`, and the model
  spread its picks over it (3/5/7/10 days on the day-sim seeds; live rows 1/3/5/7), while
  the selector excluded anything due beyond 48 h — nights 1–2 kept nothing. Relevance now
  decays instead: 0.4 due within 7 days, 0.25 beyond (still salience-ranked, still capped 5).
  The prompt adds when a caring friend would check in (1 day for a worry, health concern or
  strong feeling; the day after a dated event; 2–3 otherwise). Replayed on the d1 seeds:
  migraine 5→1 day, race 10→7, mum 3, Kestrel 7. The migraine worry was ALSO dropped by
  `loop_is_concrete` (no anchor named a symptom, logged `discarded_meta=1`), so named health
  conditions (`HEALTH_NOUNS` — migraine, headache, insomnia… never "pain" or "sick", because
  anchors are cue words) count as anchors. The gate itself stays.

Pinned by `tests/test_open_loop_lifecycle.py`.

## Probe chat rows are purged nightly

The leaked rows come from probes that call the live `/api/chat` as a fixed identity and do
not delete their sessions:
- `test-route-probe`: perf and route probes such as the brain-flags `chat_check.py` (22
  sessions and 64 turns on 2026-09-27).
- `test-sec-b-<6 hex>`: the Flue parity security gate (2026-07-07).

`scripts/maintenance/purge_orphaned_test_data.py` hard-deletes the `chat_sessions` those
exact ids own, together with their messages and AG-UI runs. It first locks the candidate
sessions, so no turn can land mid-purge. It then writes a re-parsed, count-verified JSON
backup to `~/.zoe/backups/purge/<stamp>-chat.json`. Only then does it delete exactly the
backed-up rows, checking the deleted count per table; any mismatch rolls back. Old backups
are removed only by hand, with `--prune-backups` (see `scripts/AGENTS.md`). It keeps any session with
a turn whose metadata names another owner. The nightly `self-hosted-tests` run (00:30 AWST)
executes it. On 2026-09-27, 130 sessions and 343 messages matched. A new probe identity
must be added there as an exact, anchored id, or tear its sessions down with
`DELETE /api/chat/sessions/{id}`.
A harness's MEMORY rows for a `demo_`/`test_` id are hard-deleted through the internal-token
`POST /api/memories/users/{id}/forget-synthetic` ([contract](samantha-bar.md)); allowlisted ids are
treated as real and refused there.

## Verify

- **07:30:** `grep -E "T07:3.*(morning_checkin|PROACTIVE_SPOKEN)" ~/.zoe-logs/zoe-data.app.log`.
  Expect `morning_checkin: users kept=N skipped_synthetic=M`, then `fired for N user(s)` with
  the bound member included.
  - Panel off: `panel=none outcome=absent`, which still proves the brief was created.
  - Panel on (kiosk idle as guest): `panel=<id> outcome=enqueued` and a new
    `voice_announcements` row.
- **No wait:** `POST /api/proactive/trigger-morning` as the bound member with the panel on.
- **Arrival (flag on):** `grep -E "morning_checkin_arrival|PROACTIVE_RESPONSE" ~/.zoe-logs/zoe-data.app.log`
  and `SELECT local_date, trigger_type, missed, outcome, responded FROM proactive_responses ORDER BY created_at DESC`.
- **Skip counts** (read-only, live DB, 2026-09-27):

  | pass | kept | skipped |
  |---|---|---|
  | dreaming / consolidation | 4 | 21 (10 after the purge) |
  | portrait | 5 | 22 |
  | music | 2 | 1 (a guest sentinel) |
  | morning / evolution | 1 | 1 |
  | evening | 0 | 1 |
