---
type: Reference
title: Change notices P1 (ZOE_CHANGE_NOTICES, flag-dark)
description: The owner's Telegram feed of Zoe PRs that were raised and went live - how the poller reads GitHub and the deploy marker, how the messages read (an assistant's voice, the For-The-Household / Numbers / Owner-Step / If-No-Answer trailers), the per-member communication preferences, the delivery rules (quiet hours, cap, batching, digest, at most once), why a tick finds no recipient, and the steps to link Telegram and turn it on.
tags: [telegram, notifications, change-notices, preferences, github, owner, trailers]
timestamp: 2026-11-16T00:00:00Z
---

# Change notices, Phase 1

Design: [communication-preferences.md](../architecture/communication-preferences.md) (PR #2075). This page records what
Phase 1 built and how to run it. The owner asked on 2026-10-15 for Telegram updates about PRs that are raised and then
merged, each with a description.

## What it does

`services/zoe-data/change_notices.py` runs one tick every `ZOE_CHANGE_NOTICES_INTERVAL_S` (600 s) inside zoe-data.
The loop starts only when `ZOE_CHANGE_NOTICES` is `shadow` or `on`.

| Source | How it is read | Notice |
|---|---|---|
| Raised | `gh pr list --state open` | a `needs-owner` label makes it a `decision` (instant); any other open PR is one count line in the digest |
| Merged | `gh pr list --state merged --search merged:>=<2 days ago>` | nothing yet: the merge waits in `pending_live` |
| Live | `gh api repos/<repo>/compare/<merge sha>...<deployed sha>`, where the deployed sha comes from `~/.zoe/deploy/last-deployed-sha` (deploy.yml writes it only after a healthy deploy) | "went live", sorted by the conventional-commit type: `feat` = New, `fix`/`perf`/`security` = Fixed, `Revert` = broke, `docs`/`test`/`chore`/`ci`/`build`/`refactor`/`deps` = under the hood |
| Stuck | a merge that the deployed sha definitely does not contain, after `ZOE_CHANGE_NOTICES_STALE_MIN` (60) | one `broke` notice per deployed sha, linked to the last deploy run |

- **The first tick is silent.** Everything already open or merged is marked seen, so turning the flag on never sends the backlog.
- **Rendering** is deterministic English in an assistant's voice; see [How the messages read](#how-the-messages-read).
  No model is used and no PR text goes to any model or external API. (Telegram itself is the destination. PR text is
  already public in this repository, which is design rule 8.)
- **Live, not merged, is the moment.** A change is announced once, when it is merged AND deployed ("is in and
  running"). There is deliberately no second notice at merge time: a merge that never deploys is not news for the
  household, and if it stays undeployed past the stale window the owner gets the "did not go live" notice instead.
- **Recipients are owner only.** A recipient must be a `users` row with role exactly `admin` or `family-admin`, have a
  numeric `telegram_id` in their own `user_prefs` (written by `telegram_link`), and have preferences that allow the
  category. The bot then refuses any chat that is not a linked member (403), and the module never retries that.
  When a tick has new notices and nobody to send them to, the `no_recipients` audit line says why, counts only:
  `reason` is `no_admin` (no user has an owner role), `owner_role_spelling` (a role matches only ignoring case, e.g.
  `Admin`; the match is exact), `no_link` (an owner exists but has no linked Telegram id), `no_prefs` (linked, but
  the preferences document gives that role nothing), plus `admins`, `linked` and `owner_role_other_case` counts.
  Notices made while nobody could receive them are never replayed later: linking starts the feed from the next one.

## How the messages read

The owner asked (2026-11-16) for messages that "seem a bit more human, not like reading a log", "more like getting an
update from an assistant". The rules, all in `change_notices.py` and pinned by exact-text tests:

- **What is different for the household comes first**, in the PR author's own words: the `For-The-Household:`
  sentence (at most two sentences). No conventional-commit syntax, no check names, no commit hashes, no backticks.
- **The PR number appears at most once**: on the "Open #N" button for a single message, in brackets at the end of
  each line in a batch or a digest (layer 2, so only at `technical` detail).
- **No trailer, or "nothing visible", is housekeeping, said honestly**: one line from the title's subject
  (`type(scope):`, `Revert "..."` and `(#N)` stripped, sentence case): "Some housekeeping went in: Pin a date in the
  calendar test." Several are collapsed: "Plus two bits of housekeeping (#11, #12)."
- **Bad news first and plainly**: a revert or a stuck deploy leads, never buried under good news in a batch.
- **Every went-live message ends with what the owner must do**: the `Owner-Step:` sentence, or "Nothing for you to
  do." A decision (`needs-owner`) is its own message: one question, and what happens if he leaves it. A step from a
  revert counts the same as any other; a stuck deploy (no PR body, so no step) ends on "It may need a look."
- **An owner step is never lost to truncation.** A long batch first shortens household lines, then drops the
  numbers, then folds housekeeping and other lines into "...and N more"; bad news, decisions and steps are never
  dropped. A notice counts as delivered only if its step was shown (or it has none): one whose step does not fit is
  left queued and follows in the next message (instant: the same tick, up to 3 messages; digest: the next tick,
  after "The rest of what needs you follows shortly.").
- **The base timing is unchanged; a follow-up never steps around a limit.** The selection at the start of a tick
  (quiet hours, daily cap, 30-minute same-category spacing) and the messages it produces (one per notice, or one
  batch for 3 or more) are exactly as before this change, pinned by a table test and, where `origin/main` is
  available, a differential test against it. Only the NEW follow-up messages (a notice whose step did not fit) are
  rechecked, each one before it is sent: the kill switch file (stops; the rest stays queued), quiet hours (stops;
  held), the spacing (not for an env-defaulted owner), the daily cap (counting this tick's messages) and at most
  `MAX_CHUNKS` (3) instant messages a tick. What does not fit rolls into the next digest with the same
  `rolled_to_digest` event (`reason` `spacing`, `cap` or `rate`). Within a tick the single messages now go bad news
  first; the set of messages is the same.
- **Trailers in code are not trailers.** A trailer inside a ``` or ~~~ fence (closed or not) or an HTML comment is
  ignored; a 4-space indented line never matches, because a trailer must start at column 0.
- **Notices stored before this change** (with only the old `summary`, no `household` field) use that summary as
  their line, through the same de-jargon path, instead of becoming housekeeping.
- **Batches and digests in the same voice**: "Three things went in this morning." / "Good evening. Here's today: ...".
  Sections only where they help: "Waiting on you", "For you to do", "What went in". Every size bound holds (990
  characters a message, 3 messages, "...and N more on GitHub" so every notice lands in exactly one message), and the
  pointer-only mode is unchanged. The greeting and "this morning" come from the household's local time; no names.
- **Variety without randomness**: openers rotate by the per-member counter in `state.json` (`opener`), shared by
  instant messages and digests, so two messages in a row never start the same way and tests stay exact.
- **Numbers are layer 2.** The `Numbers:` line shows only at `technical` detail, which by default is the owner alone.
  A later phase that reaches other members at `plain` detail gets layer 1 only (design rules 3-4, fail closed).

### PR-body trailers it reads

One line each, anywhere in the body at the start of a line (not inside a code block or an HTML comment). They are
never mistaken for prose. Write them in plain words; invented examples:

```
For-The-Household: Long messages no longer make Zoe pause before she answers.
Numbers: scrubbing a long message 4.5 s -> 0.03 s; tests that guard it 603
Owner-Step: Switch ZOE_REMINDER_SNOOZE on when you want to try it.
If-No-Answer: It stays as it is.
```

| Trailer | Rule |
|---|---|
| `For-The-Household:` | one or two plain sentences, first person as Zoe's assistant; `nothing visible` = housekeeping |
| `Numbers:` | optional; items split by `;`, `->` or `→` both accepted (shown as `→`); only items with a digit, at most 3, each clipped to 70 characters, markdown and links stripped; a line with no digit is ignored; only numbers that were really measured |
| `Owner-Step:` | optional; one sentence, the one thing the owner must do (e.g. a switch to flip); for a decision it is the question |
| `If-No-Answer:` | optional, decisions only; what happens if the owner does not answer |

### Before and after (invented PRs)

Before, one fix going live:

```
Live now: #2001 fix(memory): widen the scrubber
• Widen the scrubber so long messages are fast
```

After (button "Open #2001"):

```
A fix is in and running. Long messages no longer make Zoe pause before she answers.
By the numbers: scrubbing a long message 4.5 s → 0.03 s; tests that guard it 603.
Nothing for you to do.
```

Three at once:

```
This morning, three changes went live.
• Long messages no longer make Zoe pause before she answers. (#2001)
   By the numbers: scrubbing a long message 4.5 s → 0.03 s; tests that guard it 603.
• You can snooze a reminder from the wall screen now. (#2002)
And one bit of housekeeping (#2003).
For you:
• Switch ZOE_REMINDER_SNOOZE on when you want to try it.
```

A revert, and a stuck deploy:

```
Heads up first: "Snooze from the panel" was rolled back and is no longer running. The snooze button is gone again for now.
Nothing for you to do.
```

```
Heads up: "Repeat on weekdays only" did not go live. It was merged over an hour ago and is not running yet. The last deploy failed. It may need a look. (#2005)
```

The evening digest:

```
Good evening. Here's today: five changes went live.
One new change is open and not merged yet (one is a draft).
Heads up first: "Snooze from the panel" was rolled back and is no longer running. The snooze button is gone again for now. (#2004)

Waiting on you:
• The hall panel would dim itself at 9pm. Should it dim at 9pm, or stay bright? If you leave it, it stays as it is. (#2010)

For you to do:
• Switch ZOE_REMINDER_SNOOZE on when you want to try it.

What went in:
• Long messages no longer make Zoe pause before she answers. (#2001)
   By the numbers: scrubbing a long message 4.5 s → 0.03 s; tests that guard it 603.
• You can snooze a reminder from the wall screen now. (#2002)
Plus two bits of housekeeping (#2003, #2006).
```

## Preferences

The design's model (section 3.1) is stored in `user_prefs`, key `communication`, and validated by
`services/zoe-data/comm_prefs.py`. A malformed field falls back to the default one field at a time. The floors are
applied last: `security` and the owner's `decision` can never be set to `never`. Any role that is not an owner role
gets `None`, which means nothing is sent to them.

Routes for admin persons only (a panel token is refused): `GET`/`PUT /api/change-notices/preferences` (`PUT` takes a
partial update), `GET /api/change-notices/status` (counts only), and `POST /api/change-notices/kill {"on": true|false}`.

## Delivery rules

- **Transport.** Messages go through the bot's loopback `POST /internal/notify` (`telegram_notify.py`: `Bearer
  ZOE_INTERNAL_TOKEN`, default `http://127.0.0.1:3582/internal/notify`, `ZOE_TELEGRAM_NOTIFY_URL=off` disables it).
  Only the bot holds the Telegram token. The route comes from PR #2108. If the route is not configured, deliveries
  stay queued and no retries are spent.
- **Quiet hours.** By default these are the household's `ZOE_QUIET_START_HOUR`/`_END_HOUR` (22 to 7); a member can
  set their own. During quiet hours every instant notice is held and goes out once, at the end of the window. Nobody
  is woken. A digest time that falls inside quiet hours moves to the end of the window.
- **Cap and spacing.** The cap is `daily_instant_cap` (6) instant messages per day. Two instant notices of the same
  category within 30 minutes are not both sent instantly: the second rolls into the digest, so nothing is dropped.
- **Batching.** When 3 or more instant notices are ready at once, they go out as one message. The digest is at most 3
  messages of up to 990 characters each, because the route caps text at 1000. Anything past that becomes
  "...and N more on GitHub". An empty digest is not sent.
- **At most once, never silently lost.** Before each send, its deliveries are claimed as `sending` and saved to disk.
  - A send that definitely did not go out (no connection, 401, 404, 5xx) is retried with backoff (5, 10, 20 minutes,
    up to 6 hours, 8 attempts).
  - A timeout after the request was sent, or a crash mid-send, becomes `uncertain`. It is never sent again on its own.
    The next digest mentions it once: "This may not have reached you earlier: #N".
- **Dedup.** Each notice has one key (`pr:N:raised`, `pr:N:decision`, `pr:N:live`, `deploy:stuck:<sha>`), recorded
  as seen when it is first observed. A merge replaces its PR's raised line in any digest that has not been sent yet.
- **Kill switch.** The `KILL` file in the state dir, set by the route above, stops all collection and sending at the
  next tick. No restart is needed.
- **State and audit.** State lives in `~/.zoe/change-notices/state.json` (mode 0600, replaced atomically). The audit
  log is `~/.zoe/change-notices/audit.jsonl`: one line per notice, send, hold, rollover, failure or kill. It records
  message text only in `shadow` mode.

## Defaults chosen (the owner's open decisions, design section 10)

| # | Decision | Default used |
|---|---|---|
| 1 | Digest time | one evening digest at 19:00, skipped when empty |
| 2 | What is instant | `decision`, `broke`, `security`; merged work goes in the digest |
| 3 | Raised PRs | only `needs-owner` PRs are instant; the rest are one count line in the digest (`raised: list` lists them, `none` hides them) |
| 4 | Attribution trailers | `For-The-Household:`, `Numbers:`, `Owner-Step:` and `If-No-Answer:` are read when present; nothing requires them yet, and the advisory pr-hygiene note is not built |
| 5 | Undo from Telegram | not built (P2) |
| 6 | Telegram transport | the bot's loopback route; this path reads no bot token |
| 7, 8, 9, 13 | Other adults, children, the one ask, the struggle feed | not built: Phase 1 is owner only |
| 10 | Per-member quiet hours | yes; the household's hours are the default |
| 11 | Waking the owner | never; quiet hours hold `broke` too |
| 12 | Paid channels | none; Telegram only |
| 14 | Languages | English only; `language` accepts only `en` |

Also chosen: the `needs-owner` label marks a PR as waiting on the owner; a merge counts as stuck after 60 minutes;
the poll runs every 10 minutes.

## Not built yet

- The `deploy.yml` `if: failure()` marker step. That file is load-bearing, so the step goes in its own PR. Until then,
  a failed deploy shows up as "merged but not live after 60 min".
- Callback buttons (Details, Quieter, Undo, Not now) and the other channels: panel, first turn, web push, Apprise.
  These are P2 and P3.
- The out-of-band lane. The loop runs inside zoe-data, and the bot resolves the recipient through zoe-data, so
  nothing is sent while zoe-data is down. Messages are delayed, not lost.
- A local-brain summary. Rendering is template-only; the brief allowed the local brain when idle, but it is not needed.
- `auth_handoff` still has its own copy of the notify call (from #2108). Once #2108 merges, fold it into
  `telegram_notify.send`.

## To turn this on (why nothing arrives today)

Since 2026-10-18 the loop has built notices on every tick and reported `no_recipients` each time: nobody passes
`load_recipients`. Reading the code, a recipient needs all three of: a `users` row whose role is exactly `admin` or
`family-admin`, a numeric `telegram_id` in that same user's `user_preferences`, and a role that gets preferences (both
owner roles do). The most likely gap is the second (the owner account was never linked from Settings), or a linked
account that is not the admin row; the audit line's `reason` now says which. These are the owner's and the landing
session's steps; nothing here is done automatically.

1. After this lands and zoe-data restarts, read the reason from the next empty tick (counts only, no text):

   ```bash
   grep '"no_recipients"' ~/.zoe/change-notices/audit.jsonl | tail -n 1
   ```

2. `no_admin` or `owner_role_spelling`: see the roles as counts, then the owner decides whether to correct the role
   (an `UPDATE users SET role = 'admin' WHERE id = '<owner id>'` is a live DB write: owner only):

   ```bash
   docker exec zoe-database psql -U zoe -d zoe -Atc "SELECT role, count(*) FROM users GROUP BY role"
   ```

3. `no_link`: the owner links Telegram while signed in as himself on the desktop: Settings, Telegram, "Connect
   Telegram", scan the QR code (or tap the link) and send the message it opens to the bot. Check, as a count:

   ```bash
   docker exec zoe-database psql -U zoe -d zoe -Atc "SELECT count(*) FROM user_preferences p JOIN users u ON u.id = p.user_id WHERE u.role IN ('admin','family-admin') AND p.prefs::jsonb ->> 'telegram_id' ~ '^[0-9]+$'"
   ```

   It prints `1` when linked. The next tick writes no `no_recipients` line for its new notices.

4. While the flag is `shadow`, read what would have been sent (this prints message text, so read it on the box):

   ```bash
   grep '"shadow_send"' ~/.zoe/change-notices/audit.jsonl | tail -n 3
   ```

5. Switch on: set `ZOE_CHANGE_NOTICES=on` in `services/zoe-data/.env` and restart zoe-data (*Operator steps* below).

What the owner then sees: nothing about the past (earlier notices are not replayed), then, for the next change that
goes live, a message like the samples above: instantly for decisions and bad news, at 19:00 in the evening digest
for the rest, or instantly for everything with the `everything` owner preset.

## Operator steps

1. Land PR #2108, so the bot serves `/internal/notify` with `ZOE_INTERNAL_TOKEN` in its env, then this PR.
2. Link Telegram for the owner, signed in as the owner. On the desktop, open Settings, go to Telegram, and press
   "Connect Telegram". Scan the QR code, or tap the link, then send the message it opens in the bot. Check:
   `GET /api/change-notices/preferences` shows `"telegram_linked": true`.
3. Run a shadow week. Add `ZOE_CHANGE_NOTICES=shadow` to `services/zoe-data/.env`, restart zoe-data
   (`systemctl --user restart zoe-data`), and poll `/health`. Read what it would have sent with
   `grep shadow_send ~/.zoe/change-notices/audit.jsonl`.
4. Turn it on: set `ZOE_CHANGE_NOTICES=on` and restart.
5. Owner env default, a message for every landing (Telegram delivery is the bot's job; notices queue while it is
   down). Append `ZOE_CHANGE_NOTICES_OWNER_PRESET=everything` and `ZOE_CHANGE_NOTICES_INSTANT_CAP=50` to
   `services/zoe-data/.env` and restart zoe-data. They are the DEFAULT only for an owner who has not saved
   preferences (any saved document wins, even an empty one); the security and decision floors still apply, and an
   env-defaulted owner skips the 30-minute same-kind spacing (the cap still holds). Each "went live" message reads
   as described in [How the messages read](#how-the-messages-read), at most 1000 characters, no model. Quiet hours (22:00-07:00) still hold
   everything overnight by design, so nobody is woken; held notices go out when the window ends.
6. Optionally, change preferences with `PUT /api/change-notices/preferences`, for example
   `{"preset": "important_only"}`, `{"raised": "list"}`, `{"digest": {"daily_at": "18:00"}}` or
   `{"categories": {"fixed": {"cadence": "instant"}}}`.
7. Roll back with `POST /api/change-notices/kill {"on": true}` (immediate), or set `ZOE_CHANGE_NOTICES=off` and
   restart.

Tests: `services/zoe-data/tests/test_change_notices.py` (ci_safe). They use a fake GitHub and a fake `/internal/notify`,
and each rule has a negative control.
