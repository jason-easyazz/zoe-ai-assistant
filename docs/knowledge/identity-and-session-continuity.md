---
type: Reference
title: Identity from the account, and session continuity
description: Own-identity questions ("what's my name", "where do I live") are answered from the account never from memory; the brain prompt carries one identity line; a chat request with no session id continues the last chat; how the polluted name row was written and how to audit for more.
tags: [identity, memory, sessions, chat, flags, incident]
timestamp: 2026-10-05T12:00:00Z
---

# Identity from the account, and session continuity

Incident 2026-10-05, six web-chat messages from the owner's account, each in a NEW session:
"whats my name" returned a full name that belongs to nobody in the household; "who is the
prime minister" was followed by "How is your time in Australia going?"; "where do i live"
found nothing although the household location is configured.

## What was wrong (traced, no names)

| Symptom | Cause | Fix |
|---|---|---|
| Wrong name | A palace row `User's name is <X>` (status approved, `source`/`added_by` = `chat_regex`, `session_id` = an old Telegram session, `reviewed_by` = `digest`, `review_note` = "digest contradiction: superseded by newer turn", `supersedes_id` = the genuine name row). It was written at **03:00 local by the nightly digest** (`memory_digest.run_memory_digest`): the Gemma fact-extractor read a day transcript containing a speech-to-text fragment from the panel that named a third person, asserted it as the user's name, and the contradiction pass called `MemoryService.review(edit)`, which supersedes the genuine row **and carries its `source`/`session_id` forward** — which is why the row looked like a regex/Telegram write. The regex extractor was NOT the author (replaying the fragment through `extract_candidates` yields nothing). The anchor validation that guards the same branch (`user_relationship_claim_unsupported`) covers relationships, not identity. | `identity_facts.AUTOMATIC_SOURCES` can no longer ingest or supersede-into a "user's name is" assertion (`MemoryService.ingest` + `review(edit)`); explicit teach (`voice_fact`, `brain_tool`, `review_ui`) and operator actors still can. Log: `IDENTITY_FACT_BLOCKED user=… source=… kind=name`. |
| Name answered from recall | A recall store is written by extractors that mishear and contradict each other. | Deterministic identity tier (below). |
| "visitor" reply | The brain was never told who it is talking to or where the house is. | `ZOE_IDENTITY_BLOCK` (below). |
| "where do i live" found nothing; "i live here" didn't attach | The estate ask-box (`touch/home.html` `askZoe`, not a desktop page) posts `/api/chat/` with **no `session_id`**, and the route minted a fresh `web_<8hex>` per request. | `ZOE_STICKY_SESSION` (below). |

## 1. Identity tier (unflagged — a correctness fix)

`identity_facts.py`; run by `fast_tiers.resolve` right after the conversation-quality tier and
**before** recall/routing, for the `chat` and `telegram` profiles (`identity_tier`). Voice and
LiveKit are deliberately not in it: their scope gate runs after `resolve()` and personal facts
must not bypass it.

* Questions: name / first name ("what's my name", "do you know my name"), full name /
  surname (answered only when the account holds a multi-part name — a first-name-only account
  falls through to recall and never says "I have no surname"), "what do you call me", "who am
  I" (whole-utterance only — "who am I meeting tomorrow" is a calendar question), "where do I
  live" / "which city|state|country do I live in" (answered at the granularity asked),
  "what's my address". NOT claimed: past tense ("where did I live"), present LOCATION
  ("what city am I in" — a trip is not home) and finer-than-account shapes ("which suburb")
  — they fall to the brain. Pattern table: `_IDENTITY_PATTERNS`; add a positive AND a
  negative test with each new shape.
* Rename: an explicit first-person rename on chat/telegram ("call me Jay", "my name is Jay",
  "I go by Jay") writes `prefs["preferred_name"]` (atomic `set_pref`) and answers "I'll call
  you Jay." — the ONE field the identity answers, the brain-prompt line and the greeting all
  read (the greeting used to import a portrait helper nothing defines, so it never
  personalised). Stop-word names ("call me later", "call me a taxi") and questions never
  rename; a bare "actually it's Jay" is not matched. Voice has no tier, so a spoken
  "my name is Jay" is not written (the extractor copy is refused by the wall); log lines
  tell the two apart: `IDENTITY_RENAME … origin=explicit` vs `IDENTITY_FACT_BLOCKED …
  origin=automatic`.
* Sources (never a memory row): name = `user_preferences.prefs["preferred_name"]` →
  `auth_users.settings` `display_name`/`name` → `users.name` → `auth_users.username`;
  home = `weather_preferences` (this user) → `system_preferences["weather_default_location"]`
  → `ZOE_LOCATION_*` env (region from the stored `region`/`state`, else derived from an
  Australian IANA timezone). `weather_preferences.city` is a WEATHER location: with
  `use_current_location` set it follows the device, so it is ignored and the household
  default stands in; street address = `prefs["home_address"]` only (absent → "I only
  have your location, … not a street address"). Only a **registered account** (`auth_users`
  row) has an identity: guests, voice-guests and harness/demo ids fall through to the brain
  unchanged, so the bar / day-sim / replay corpora are untouched by construction.
* Reply: one sentence. If the account lacks the fact, the tier returns `None` and the brain
  answers as before.
* Memory is consulted only AFTER the answer, in the background, to log
  `IDENTITY_CONFLICT user=<id> kind=name|home` (ids and labels, never text). A conflicting row
  cannot change the answer (pinned by a negative control).
* Caveat: moving house is a Settings change (weather location), not something said in chat.

## 2. `ZOE_IDENTITY_BLOCK` — default ON

One block in the brain prompt, right behind the ` zoe-uid:` envelope and ahead of the recall /
offer blocks: `[Today — household context]` / `You are talking to <name>, a member of this
household in <city>, <region>, <country>.` / `[END Today]`. Default ON because it carries
only what the account already shows its owner, is cached per user, and removes a guaranteed
failure (the visitor assumption). It rides the EXISTING pinned `[Today` block family in
`_FLUE_CONTEXT_BLOCKS`, so the sidecar's `elideStaleBlocks` removes it from every older user
message (proved against the real `context-blocks.ts` in a test) and no sidecar change is
needed. Two limits: (1) the sidecar's tool-group keyword scan reads the whole message,
blocks included, so a place containing a trigger word ("Cold Lake", "Hot Springs") is left
out of the line (name kept; `_TRIGGER_RE`, drift-pinned against `tool-groups.ts`); (2) a
cold lookup is ONE joined query bounded to 0.3 s on this path — over budget the turn goes
without the block while the load finishes in the background (next turn is warm); an expired
entry is the fallback when a refresh fails. Off = byte-identical outbound message (pinned). **Voice-path file:** `zoe_flue_client.py` is on the replay-gate
list.

## 3. `ZOE_STICKY_SESSION` — default ON (`ZOE_STICKY_SESSION_MINUTES`, default 20)

`session_continuity.resolve_session_id`, used by `POST /api/chat/` and
`POST /api/chat/whatsapp/connect`. Only a request with NO `session_id` is affected, and it
lives in its OWN namespace: minted as `ask_<8hex>`, reused (most recent `ask_` row of that
user whose `updated_at` is inside the window) by the next id-less request. It never attaches
to a `web_` session — `POST /api/chat/sessions/` ("New Chat", the desktop chat page) mints
those, and an open desktop conversation must not absorb the ask-box, the music page's
fire-and-forget commands or the planner's natural-language input. Never across users, never
for `guest`/`voice-guest`, never for another channel tag or prefix (`telegram-…`,
`voice-panel-…`, `session_…`), and never a session whose turn is in flight: the route passes
its per-session lock probe, so a second id-less request that arrives mid-answer gets a fresh
`ask_` session instead of `session_busy`. Known and accepted: two ask-box tabs/panels of one
user inside the window share one `ask_` transcript. The Pi voice daemon never reaches this
code (it posts only `/api/voice/*`). Flag off = the old per-request `web_` mint. Default ON because the no-id case is today a guaranteed context
loss, so nothing that worked can change. Log: `SESSION_STICKY user=… session=…`.
Harnesses (`samantha_bar.py`, `samantha_day_sim.py`, the parity gates, the Telegram bridge)
all pass explicit ids. The desktop chat page now persists the id it sends (it used to send a
throwaway `session_<ms>` when `currentSessionId` was null). `touch/home.html` still posts
with no id — covered server-side; a one-line client fix is possible there but touch/ was not
edited in this change.

## 4. Auditing for more pollution

```
python3 scripts/maintenance/identity_memory_audit.py --dry-run            # read-only, safe while zoe-data runs
python3 scripts/maintenance/identity_memory_audit.py --dry-run --redact   # no names in the output
```

Lists approved rows asserting the user's own name/home that conflict with the account, with
provenance (source, reviewer, session, time). `--purge --i-have-reviewed --i-stopped-zoe-data`
(zoe-data stopped — checked via :8000 AND `systemctl --user is-active`; the purge writes the
palace in-process) supersedes the **name** rows via `MemoryService.review(edit)` (old row →
`superseded`, new `User's name is <account name>.` row). A name that shares no token with the
account is a **conflict** only when an automatic writer put it there; a plausible nickname
(3+ letter prefix of the name) or a row from an explicit teach (`voice_fact`/`brain_tool`/
`review_ui`) is **NEEDS REVIEW** — listed, never purged. Home rows are never purged. `--only`
ids are validated against the report (unknown / home / needs-review → exit 2, nothing purged). Tests: `test_identity_facts.py`,
`test_identity_block_prompt.py`, `test_session_continuity.py`, `test_identity_memory_audit.py`.
