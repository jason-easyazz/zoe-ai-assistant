---
type: Runbook
title: Omi day review - measured state, why nothing was approved, the Telegram digest, and what to build next
description: Why every pendant day-review candidate since 2026-10-15 stayed pending, the chunk-budget fix, the once-a-day Telegram digest (ZOE_OMI_REVIEW_TELEGRAM, flag-dark), the owner flips, and a ranked list of further uses of pendant recordings.
tags: [omi, pendant, day-review, telegram, runbook]
timestamp: 2026-10-25T00:00:00Z
---

# Omi day review - measured state (2026-10-25, read-only)

- The pendant records about 12 h/day (745 min on 10-22, about 95 recordings), all transcribed into `omi_recordings` / `omi_segments`.
- The nightly review (`omi_day_reviews`, lane night, the 12B) yielded 5-10 candidates a day (reminder, list_item, follow_up, person, owner_fact, calendar_event).
- Full days pack to 11-13 chunks; the old hard cap of 8 calls skipped 3-5 of them (`chunks_skipped`) with no log line.
- Every candidate since 2026-10-15 was still `pending`: nothing reached reminders, lists or memory. Approve-before-write is an owner rule and stays.

## Why the owner never acted on them (live process env, flag names/values only)

1. `ZOE_OMI_DAY_REVIEW=shadow` on zoe-data. Shadow means "no mention, no raise, no voice" by the review's own contract, so the first-turn mention could not fire.
2. `ZOE_DAY_REVIEW_RAISE` unset (default off) - the raise/pull (`day_review_raise`) was never switched on. The app log has zero `DAY_REVIEW_*` lines.
3. `ZOE_PANEL_REVIEW_CARDS` unset - no "Your day" card on the panel.
4. `ZOE_PUBLIC_URL` is not set in zoe-data's environment, so even the existing phone send would carry no link to the phone page ("Review your day" on `pendant.html`).
5. No notification channel existed at all: the phone page only helps someone who already goes looking.

## What changed

- **Chunk budget.** `DEFAULT_MAX_CALLS` 8 -> 16 (a ceiling; `ZOE_OMI_DAY_REVIEW_MAX_CALLS` still overrides). A normal full day is now sent whole. Anything still over the ceiling is in `chunks_skipped` and is now logged at WARNING. Night-window time is unchanged: a day whose estimated cost (`day_cost_s`) does not fit the time left is NOT trimmed, it is not started (`no_time`) and the next night retries it (oldest first, within `ZOE_OMI_DAY_REVIEW_MAX_AGE_DAYS`) - nothing is dropped for time. At the 12B's measured rates a call costs roughly 90 s, so a 13-chunk day is about 20 min: it fits `--end-by 03:55` only when the earlier jobs leave room; otherwise it carries to the next night.
- **Telegram digest** (`omi_review_digest.py`). Once per member per household day, from `ZOE_OMI_REVIEW_TELEGRAM_HOUR` (default 8, local, never after 22:00): up to 5 plain-language items, the pending total, and a "Review your day" link button to the Pendant page. Only when there is something new since the last digest; after 3 digests with no decision it waits for one. Only a linked real account, only that member's own candidates, nothing is written. Logs are counts only.
- **Not done (honest limit):** per-item inline Approve/Skip buttons. The bot's loopback `/internal/notify` carries text plus one link button only; buttons need a callback handler in `labs/flue-zoe-telegram-2x`. `omi_review_digest.act(member, "approve"|"skip", cid)` is the tested seam for it (approve = `omi_review_apply.approve` once; skip = `omi_day_review.dismiss`, no other write; another member's id is "not found"). Today approval happens on the linked phone page.

## Owner flips (all default off)

1. `ZOE_OMI_DAY_REVIEW=on` (from `shadow`; the digest requires `on`).
2. `ZOE_OMI_REVIEW_TELEGRAM=1` (optionally `ZOE_OMI_REVIEW_TELEGRAM_HOUR`).
3. `ZOE_PUBLIC_URL=<the phone-reachable base URL>` so the button has a destination.
4. The member must have linked Telegram (`telegram_link`). Optionally `ZOE_DAY_REVIEW_RAISE=1` for the spoken first-turn mention.

Then restart zoe-data (the digest loop starts at boot only when the flag is on).

## Further uses, ranked (value for effort)

1. **Inline Approve/Skip buttons in Telegram.** Needs a callback_query handler in the Flue Telegram bot that calls a new internal-token route wrapping `omi_review_digest.act`. Closes the loop in one tap. Small.
2. **Commitment detection to a reminder offer** ("I'll send it by Friday"). The reminder kind exists; needs a tighter first-person-commitment verifier and the digest wording to offer a date. Small-medium, already approval-gated.
3. **"What did X say about Y" recall over transcripts.** Needs per-member FTS or embeddings over `omi_segments` (speaker-tagged turns), a query tool for the brain, and the same privacy floors (own recordings only, distress and forget cascade applied). Medium; the highest everyday value.
4. **People learning with approval.** `person` / `person_fact` candidates already exist; needs the owner to tag voices (see `omi-voice-tagging.md`) and the digest to surface them. Small once tagging is used.
5. **Per-day summary on request** ("what did I do Tuesday?") from the same chunks, counts first. Needs a second 12B prompt per day; night budget permitting.
6. **Carry skipped chunks instead of trimming** (re-queue only the dropped chunks next night). Needs a per-chunk ledger on `omi_day_reviews`; only worth it if `chunks_skipped` WARNINGs still appear at the new ceiling.
