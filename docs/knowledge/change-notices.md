---
type: Reference
title: Change notices P1 (ZOE_CHANGE_NOTICES, flag-dark)
description: The owner's Telegram feed of Zoe PRs that were raised and went live - how the poller reads GitHub and the deploy marker, the per-member communication preferences, the delivery rules (quiet hours, cap, batching, digest, at most once), the defaults chosen from the design, and the operator steps to link Telegram, run a shadow week and turn it on.
tags: [telegram, notifications, change-notices, preferences, github, owner]
timestamp: 2026-10-18T00:00:00Z
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
- **Rendering** is deterministic English. Each PR gets its title plus one or two sentences from the PR body: the
  `For-The-Household:` trailer when present, otherwise the first paragraph after the first heading, otherwise the
  title. No model is used and no PR text goes to any model or external API. (Telegram itself is the destination. PR
  text is already public in this repository, which is design rule 8.)
- **Recipients are owner only.** A recipient must be a `users` row with role `admin` or `family-admin`, have a numeric
  `telegram_id` in their own `user_prefs` (written by `telegram_link`), and have preferences that allow the
  category. The bot then refuses any chat that is not a linked member (403), and the module never retries that.

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
| 4 | Attribution trailers | `For-The-Household:` is read when present; nothing requires it yet, and the advisory pr-hygiene note is not built |
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

## Operator steps

1. Land PR #2108, so the bot serves `/internal/notify` with `ZOE_INTERNAL_TOKEN` in its env, then this PR.
2. Link Telegram for the owner, signed in as the owner. On the desktop, open Settings, go to Telegram, and press
   "Connect Telegram". Scan the QR code, or tap the link, then send the message it opens in the bot. Check:
   `GET /api/change-notices/preferences` shows `"telegram_linked": true`.
3. Run a shadow week. Add `ZOE_CHANGE_NOTICES=shadow` to `services/zoe-data/.env`, restart zoe-data
   (`systemctl --user restart zoe-data`), and poll `/health`. Read what it would have sent with
   `grep shadow_send ~/.zoe/change-notices/audit.jsonl`.
4. Turn it on: set `ZOE_CHANGE_NOTICES=on` and restart.
5. Optionally, change preferences with `PUT /api/change-notices/preferences`, for example
   `{"preset": "important_only"}`, `{"raised": "list"}`, `{"digest": {"daily_at": "18:00"}}` or
   `{"categories": {"fixed": {"cadence": "instant"}}}`.
6. Roll back with `POST /api/change-notices/kill {"on": true}` (immediate), or set `ZOE_CHANGE_NOTICES=off` and
   restart.

Tests: `services/zoe-data/tests/test_change_notices.py` (ci_safe). They use a fake GitHub and a fake `/internal/notify`,
and each rule has a negative control.
