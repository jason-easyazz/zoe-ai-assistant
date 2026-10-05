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

* Questions: name / first name ("what's my name", "do you know my name"), surname ("what's my
  full name" → honest "no surname on your account"), "what do you call me", "who am I"
  (whole-utterance only — "who am I meeting tomorrow" is a calendar question), "where do I
  live", "what city am I in", "what's my address". Pattern table: `_IDENTITY_PATTERNS`; add a
  positive AND a negative test with each new shape.
* Sources (never a memory row): name = `user_preferences.prefs["preferred_name"]` →
  `auth_users.settings` `display_name`/`name` → `users.name` → `auth_users.username`;
  home = `weather_preferences` (this user) → `system_preferences["weather_default_location"]`
  → `ZOE_LOCATION_*` env (region from the stored `region`/`state`, else derived from an
  Australian IANA timezone); street address = `prefs["home_address"]` only (absent → "I only
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

One line in the brain prompt, right behind the ` zoe-uid:` envelope and ahead of the recall /
offer blocks: `You are talking to <name>, a member of this household in <city>, <region>,
<country>.` Default ON because it carries only what the account already shows its owner,
costs ~25 tokens per turn, is cached per user (2 min; failures 15 s so a hung DB costs one
timeout per window) and removes a guaranteed failure (the visitor assumption). It is a plain
line, not a bracketed block, so `_FLUE_CONTEXT_BLOCKS` (pinned equal to the sidecar's
`context-blocks.ts`) is unchanged and no sidecar deploy is needed. Off = byte-identical
outbound message (pinned). **Voice-path file:** `zoe_flue_client.py` is on the replay-gate
list.

## 3. `ZOE_STICKY_SESSION` — default ON (`ZOE_STICKY_SESSION_MINUTES`, default 20)

`session_continuity.resolve_session_id`, used by `POST /api/chat/` and
`POST /api/chat/whatsapp/connect`. Only a request with NO `session_id` is affected: it reuses
the user's most recent `web_…` session if its `updated_at` is within the window, else mints.
Never across users, never for `guest`/`voice-guest`, never for another channel tag or another
prefix (`telegram-…`, `voice-panel-…`, `session_…`), and `POST /api/chat/sessions/` ("New Chat")
still mints unconditionally. Default ON because the no-id case is today a guaranteed context
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
(zoe-data stopped — the purge writes the palace in-process) supersedes the **name** rows via
`MemoryService.review(edit)` (old row → `superseded`, new `User's name is <account name>.`
row); home rows are never purged by the tool. Tests: `test_identity_facts.py`,
`test_identity_block_prompt.py`, `test_session_continuity.py`, `test_identity_memory_audit.py`.
