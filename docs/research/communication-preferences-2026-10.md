---
type: research
title: Communication preferences and change notices - field research and Zoe survey (2026-10-15)
date: 2026-10-15
status: research-only; nothing built. Design is docs/architecture/communication-preferences.md.
description: Research record behind the communication-preferences design. Covers how platforms let each person choose what they hear about, on which channel and how often (Apple, Android, Alexa, Home Assistant, Slack, GitHub, Linear, Duolingo); the notification-fatigue evidence (batching, interruption cost, repetition); self-hosted push transports (Apprise, ntfy, Gotify, Pushover, Matrix); which channels can send first and on what terms (Telegram, WhatsApp, Messenger, Twilio SMS, Resend email, Discord, Web Push); how self-changing software reports what it changed (Keep a Changelog, Home Assistant release notes, Copilot coding agent, Renovate, Amershi et al. guidelines, Lee and See on calibrated trust); and a read-only survey of every outbound path Zoe has today, with file:line and the live state on 2026-10-15.
---

# Communication preferences and change notices: research (2026-10-15)

Owner ask, in gist: *"How hard would it be to keep me updated with PRs that are raised and then merged, with a
description, on Telegram? I'm thinking some communication preferences might be good as we move to
self-evolution: 'I've found this problem and have fixed it' sort of stuff. I'm guessing people (other household
members) will not be raising PRs."* Then: *"Do a deep dive first for some research, people might want it via
different connectors."*

The design built on this record is [communication-preferences.md](../architecture/communication-preferences.md).
No code, service or flag was touched to write it. No household data appears here.

**Evidence tags.** **[verified]** = read from the primary page, or from the code or the live box, in this session.
**[secondary]** = a search summary, a third-party article, or a page that was only partly readable.
**[unverified]** = could not be confirmed. Vendor prices and limits change often: everything here was read on
2026-10-15 and must be re-checked before a build PR depends on it.

The earlier record [pull-not-push-inbox-2026-10-04.md](pull-not-push-inbox-2026-10-04.md) already covers the
companion apps, the interruptibility literature and the "ring plus what's up?" voice pattern in depth. This record
cites it rather than repeating it, and adds what is new for **change notices** and **per-member channels**.

## 0. Answer first

- **Telegram PR updates for the owner are small.** Merged PRs already arrive on the box with their full description:
  every merge is a squash whose commit message carries the PR title, number and body, and the deploy job writes
  `~/.zoe/deploy/last-deployed-sha` only after a healthy deploy ([verified] `.github/workflows/deploy.yml:661`, and
  `git log` on `main`). A job that diffs "last notified" against "last deployed" can tell the owner exactly what
  went live, with no GitHub webhook and no public endpoint. "Raised" PRs need one `gh pr list` call to GitHub,
  which the box already talks to as a self-hosted runner.
- **But one message per PR would be noise.** `main` took 4 to 47 merges a day over the last fortnight (median
  about 16) [verified, `git log --since="14 days ago"`], and about 18 PRs were opened a day. The fatigue evidence
  (section 2) says batch: a digest, with only "needs your decision" and "something broke" sent at once.
- **Every PR is authored by the owner's GitHub account** (260 of 277 PRs since 2026-10-01; the rest are
  Dependabot) [verified, `gh pr list`]. So "who made this change" (Zoe herself, a coding agent, the owner) cannot
  come from the PR author. It has to come from a label or a commit trailer.
- **zoe-data cannot send a Telegram message on the live box today.** The only general sender,
  `auth_handoff._telegram_send`, reads `ZOE_TELEGRAM_BOT_TOKEN`, and that key is in neither of zoe-data's
  environment files [verified, live, counts only]. The two things that do reach Telegram are the bot sidecar
  (replies only) and the crash-loop watcher (a separate script with its own copy of the token).
- **Other members need a different product, not a different channel.** Plain language, user-visible effects only,
  weekly, on the panel or as a first-turn mention, never spoken unprompted. Most PRs have no user-visible effect at
  all, and the honest default for a non-technical member is to hear nothing about them.
- **Channels differ in whether they may speak first.** Telegram: yes, once the person has started the bot.
  WhatsApp: only inside a 24-hour window opened by the person, otherwise a paid, pre-approved template. Messenger:
  a 24-hour window with most "update" tags now deprecated. SMS: costs per segment and needs business registration.
  Email: cheap and fine for a weekly digest. Web Push: works, but only for an installed home-screen app on iOS.

## 1. How platforms let each person choose

### 1.1 Operating systems and voice platforms

- **Apple** has four interruption levels: passive (silent, into the notification list), active (default),
  time-sensitive (breaks through Focus and the scheduled summary) and critical (needs an Apple entitlement; plays
  sound when muted) [secondary: the developer page
  <https://developer.apple.com/documentation/usernotifications/unnotificationinterruptionlevel> renders by script].
  **Scheduled Summary** (iOS 15+) batches non-urgent notifications into times the user picks (about 8 AM and 6 PM by
  default); time-sensitive ones are delivered at once, and the user can turn time-sensitive off per app [secondary:
  <https://developer.apple.com/videos/play/wwdc2021/10091>,
  <https://www.macworld.com/article/631034/how-to-reduce-notifications-by-controlling-time-sensitivity-in-ios-ipados-15.html>].
  iOS 18.2+ can decide breakthrough from content ("Intelligent Breakthrough & Silencing") [verified:
  <https://support.apple.com/en-us/105112>].
- **Android** requires a **notification channel** per kind of message since API 26. The app sets the starting
  importance; **the user has final say** and the app cannot raise it later, only deep-link to the channel's settings
  [verified: <https://developer.android.com/develop/ui/views/notifications/channels>]. This is the clearest
  precedent for "a category the system defines, a level the person owns".
- **Alexa** proactive notifications are **fixed schemas only** (weather alert, order status, message alert, trash
  collection, ...), need the user to enable notifications for the skill, and are capped per customer per skill per
  24 hours (number not published) [verified:
  <https://developer.amazon.com/en-US/docs/alexa/smapi/proactive-events-api.html>,
  <https://developer.amazon.com/en-US/docs/alexa/smapi/schemas-for-proactive-events.html>]. Free text like "I
  upgraded myself" cannot be sent that way. The yellow ring ("there's something, ask me") is covered in the
  pull-not-push record, section 1.3.

### 1.2 Home Assistant (the nearest relative)

- `persistent_notification` stays in the frontend until dismissed; a fixed `notification_id` overwrites an earlier
  one, which is a ready-made de-duplication key [verified:
  <https://www.home-assistant.io/integrations/persistent_notification/>].
- `notify` is a building block: `notify.send_message` targets entities, devices, areas or labels, and notify groups
  fan out [verified: <https://www.home-assistant.io/integrations/notify/>].
- **Repairs** (Settings > System > Repairs) lists problems the system found, some with a guided fix flow
  [verified: <https://www.home-assistant.io/integrations/repairs/>]. It is the closest shipped analogue to "I
  found a problem; here is the fix; do you want it?".
- Release notes carry a **"Backward-incompatible changes"** section per integration with the action the user must
  take [secondary: <https://www.home-assistant.io/blog/2026/09/02/release-20269/>].

### 1.3 Work tools

- **Slack**: a notification schedule (outside it, notifications pause), per-channel overrides on a
  "mentions and DMs" default, desktop and mobile set separately [verified:
  <https://slack.com/help/articles/201355156-Configure-your-Slack-notifications>].
- **GitHub** separates **participating** from **watching**, routes each to email or the web inbox, lets the user pick
  per repository (all / ignore / custom event types), and keeps mobile push to a short list: mentions, assignments,
  review requests, **deployment approvals**, inside working hours [verified:
  <https://docs.github.com/en/account-and-profile/managing-subscriptions-and-notifications-on-github/setting-up-notifications/configuring-notifications>].
  That last list is a useful model for the owner's "instant" set: only things that wait on him.
- **Linear** sends email immediately or as a **digest that is skipped if the item was already read** elsewhere
  [secondary: <https://linear.app/docs/notifications>]. Cross-channel "already seen" suppression is worth copying.

### 1.4 Cadence: Duolingo's bandit

Yancey and Settles (KDD 2020, DOI 10.1145/3394486.3403351) model reminder templates as **recovering** (a template's
effect drops when it is reused and recovers with time) and **sleeping** (not every template is eligible for every
user); the result was +0.5 % daily actives and +2 % new-user retention over a strong baseline [verified:
<https://research.duolingo.com/papers/yancey.kdd20.pdf>]. Lesson for Zoe: **vary the wording and do not send the
same kind of notice back to back**; repetition desensitises.

## 2. Notification fatigue

- **Batching works; hourly does not; none at all is worse.** Fitz et al. 2019 (Computers in Human Behavior 101,
  DOI 10.1016/j.chb.2019.07.016): a two-week field experiment, about 200 people, four arms. Three batches a day
  (9, 15, 21 h) beat the control on attention, productivity, stress and mood; hourly batching gave little benefit;
  no notifications at all brought more anxiety [verified summary by the authors:
  <https://communities.springernature.com/posts/batching-notifications-can-improve-attention-well-being-and-productivity>;
  exact sample size secondary].
- **Alerts cost attention.** Kushlev, Proulx and Dunn (CHI 2016, N=221, within-subject): a week with alerts on
  produced more self-reported inattention and hyperactivity, which predicted lower productivity and wellbeing
  [verified: <https://www.interruptions.net/literature/Kushlev-CHI16.pdf>].
- **People see notifications within minutes even on silent** (Pielot, Church and de Oliveira, MobileHCI 2014, about
  63 a day) [secondary:
  <https://ic.unicamp.br/~oliveira/doc/MHCI2014_An-in-situ-study-of-mobile-phone-notifications.pdf>]. So a
  low-priority notice still interrupts: cheapness for the sender is not cheapness for the reader.
- The pull-not-push record section 1.2 adds the interruptibility and "should I speak" literature with numbers.

**What follows:** a small number of **priority tiers**, **digests by default**, **instant only for what waits on the
person**, **quiet hours with a narrow, explicit breakthrough rule**, and a **per-person daily cap** that rolls the
overflow into the next digest.

## 3. Self-hosted and low-cost transports

| Transport | What it is | Facts that matter | Tag |
|---|---|---|---|
| **Apprise** | Python library, BSD-2-Clause, one URL scheme per service (`tgram://`, `ntfy://`, `pover://`, `mailto://`, `matrix://`, ...) | 100+ services (the README publishes no total; a count of its tables is about 160). **Send-only**: it has no inbound side, so "reply undo" cannot come back through it. The abilities design already proposes it with `enable_only(...)` to a scheme allowlist | [verified] <https://github.com/caronc/apprise>; send-only is a property of the API shown there |
| **ntfy** | self-hostable pub/sub push | Priorities 1-5; action buttons `view`, `http`, `copy` (and `broadcast` on Android); 4,096-byte messages. **iOS** instant delivery needs `upstream-base-url: https://ntfy.sh`, which relays only the message id, a topic hash and the fixed text "New message" through APNs; without it delivery "can take hours" | [verified] <https://docs.ntfy.sh/publish/>, <https://docs.ntfy.sh/config/> |
| **Gotify** | self-hosted push server | Android app, web UI, CLI; WebSocket. **No iOS client** | [verified] <https://gotify.net/> |
| **Pushover** | hosted push, very reliable | US$4.99 once per platform; priorities -2..2 (2 repeats until acknowledged, `retry` >= 30 s, `expire` <= 10,800 s); quiet hours downgrade normal messages; 1,024-char body; 10,000 messages a month | [verified] <https://pushover.net/api>, <https://pushover.net/pricing> |
| **Matrix** | federated chat; bots are ordinary accounts | E2EE (Olm/Megolm) needs persistent key storage in the bot; a bot invites the user to a DM rather than opening one | [secondary] |

## 4. Which channels may speak first, and on what terms

| Channel | Can Zoe send first? | Limits and costs | Reply affordance | Tag |
|---|---|---|---|---|
| **Telegram bot** | Yes, to a person who has started the bot. A bot that has never been started gets "Forbidden: bot can't initiate conversation with a user" | About 1 message/s per chat, 20/min in groups, about 30/s overall before 429s; free by default. Text 1-4,096 characters after entity parsing | Inline keyboards: URL buttons, and **callback buttons** (`callback_data` 1-64 bytes) that arrive as `callback_query` updates | [verified] <https://core.telegram.org/bots/faq>, <https://core.telegram.org/bots/api> (sendMessage, InlineKeyboardButton); the "can't initiate" rule [secondary]: <https://community.make.com/t/telegram-bot-error-bot-cant-initiate-conversation-with-a-user/47720> |
| **WhatsApp Business (Cloud API)** | Only inside the **24-hour customer-service window** a person's own message opens; outside it, **only a pre-approved template**, charged per delivered message since 1 July 2025. Categories are marketing, utility and authentication; utility templates inside an open window are free | Per-message pricing by country and category | Free-form replies inside the window; quick-reply buttons in templates | [verified] <https://developers.facebook.com/docs/whatsapp/pricing/> |
| **Messenger** | 24-hour standard window; the `HUMAN_AGENT` tag (7 days) bars automated content; `ACCOUNT_UPDATE`, `CONFIRMED_EVENT_UPDATE` and `POST_PURCHASE_UPDATE` reported deprecated in 2026 (sources disagree on the date) | - | Quick replies | [secondary] <https://developers.facebook.com/docs/messenger-platform/reference/send-api>, <https://singhamandeep.com/?p=1586> |
| **SMS (Twilio)** | Yes | US$0.0083 per segment plus carrier fees (US list); a segment is 160 GSM-7 characters; US business traffic needs A2P 10DLC registration. AU pricing and sender rules differ and were not read | Text replies | [verified] <https://www.twilio.com/en-us/sms/pricing/us>, <https://www.twilio.com/docs/messaging/compliance/a2p-10dlc>, <https://www.twilio.com/docs/glossary/what-is-gsm-7-character-encoding> |
| **Email (Resend)** | Yes | Free tier 3,000 a month and 100 a day; needs SPF/DKIM/DMARC on the sending domain (the DNS records are [secondary]) | Reply by email needs inbound receiving (the abilities design's C2) | [verified] <https://resend.com/pricing> |
| **Discord** | A bot can open a DM, but the docs warn against DMing everyone in a server | Rate limits; slash commands are the sanctioned interface | Buttons, slash commands | [verified] <https://docs.discord.com/developers/resources/user#create-dm> |
| **Slack** | Bot DMs to workspace members | Not researched in this session | - | [unverified] |
| **Web Push** | Yes, to a subscribed browser. **iOS/iPadOS 16.4+ only for a web app added to the Home Screen**, permission asked after a user gesture; delivered via the vendor's push service | VAPID JWT `exp` at most 24 h; `TTL` and `Urgency` headers | Notification actions (open a URL) | [verified] <https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/>, <https://web.dev/articles/push-notifications-web-push-protocol> |

**What follows for Zoe.** Telegram is the only external channel that is free, already linked per member, allowed
to send first, and able to carry buttons back. WhatsApp and Messenger suit **replying**, not announcing: a change
notice is closer to a marketing message than to a utility one, and sending it outside a window costs money and
policy risk. SMS is a fallback for "something broke" at most. Email is a good **weekly digest** channel. ntfy or
Pushover (through Apprise) suit a member who wants phone push without Telegram.

## 5. How self-changing software reports what it changed

- **Changelogs are for humans.** Keep a Changelog groups entries as Added, Changed, Deprecated, Removed, Fixed,
  Security, newest first, dated [verified: <https://keepachangelog.com/en/1.1.0/>]. Those groups map directly onto
  notice categories.
- **Home Assistant** separates the full release post from a short "Backward-incompatible changes" list with the
  action each user must take [secondary, link in 1.2]. The pattern: a long technical log for those who want it, a
  short "what you must know" list for everybody.
- **GitHub Copilot coding agent** opens one PR per task and keeps session logs that "show the work and tools used";
  GitHub's own docs say the logs "do not replace your own review and testing" [verified:
  <https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent>].
- **Devin** reports into a chosen Slack channel and wakes on PR comments [secondary:
  <https://docs.devin.ai/release-notes/2024>].
- **Renovate** puts the human's controls *in the notice itself*: a checkbox in the PR body or the Dependency
  Dashboard requests a rebase or retry; automerge is off by default [verified, partial read:
  <https://docs.renovatebot.com/configuration-options/>]. Closest precedent for "reply undo".
- **Amershi et al. 2019, Guidelines for Human-AI Interaction** [verified list:
  <https://www.microsoft.com/en-us/research/articles/guidelines-for-human-ai-interaction-eighteen-best-practices-for-human-centered-ai-design/>]:
  G1 make clear what the system can do; G2 how well it can do it; G3 time services based on context; G8 support
  efficient dismissal; G9 support efficient correction; G11 make clear why the system did what it did; G14 update
  and adapt cautiously; G16 convey the consequences of user actions; G17 provide global controls; **G18 notify users
  about changes**. A self-evolving assistant that does not tell people what changed breaks G18 by construction.
- **Lee and See 2004** (Human Factors 46(1), DOI 10.1518/hfes.46.1.50_30392): trust should be *calibrated* to what
  the automation can really do [secondary]. So a "new ability" notice should also say what it is not good at yet;
  "fixed" notices should cite the evidence (the re-ask count fell), not just claim it.
- Not read this session: Apple "What's New" sheets, OVOS/Mycroft update notices, Gmail "Undo send", Dependabot
  PR-body format [unverified].

**What follows:** two layers per notice. Layer 1, for everyone it concerns: one plain sentence about the effect,
in Keep-a-Changelog terms, with what it cannot do yet. Layer 2, owner only: PR number, link, gate results, and how
to undo. Every notice carries its own controls (details, undo, quieter, stop).

## 6. Zoe today (read-only survey, 2026-10-15)

Code read on `origin/main` at `e5b3ee316`; live state read with `scripts/maintenance/zoe_ground_truth.sh` and the
running zoe-data process environment (key names and counts only, never values).

### 6.1 Outbound paths that exist

| Path | Where | Reaches | Live state |
|---|---|---|---|
| `fire_notification` | `services/zoe-data/proactive/engine.py:80` | Web Push (VAPID, `routers/push.py`), the WebSocket bell, a `proactive_pending` row for tap-to-chat, an in-app `notifications` row for reminders when nobody is subscribed; optionally the spoken lanes | Live. Quiet hours are **household-wide env** (`ZOE_QUIET_START_HOUR` / `ZOE_QUIET_END_HOUR`, `engine.py:34`); inside them the message is rescheduled for the end of the window |
| Spoken delivery | `engine.py:292` `_maybe_speak_notification` | Panel toast + the voice daemon's announcement queue | **Off by owner decision**: `ZOE_PROACTIVE_SPOKEN=0` [verified, live env] |
| `[Today]` first-turn brief | `services/zoe-data/brief_first_turn.py` | Folded into the member's first brain turn of the morning | On: `ZOE_BRIEF_ON_FIRST_TURN=1` [verified, live env] |
| `[RAISE]` on an open turn | `proactive/selector.py` (`ZOE_PROACTIVE_SELECTOR`) | One raise per conversation from `proactive_candidates` | On: `ZOE_PROACTIVE_SELECTOR=1` [verified, live env] |
| Pull queue and orb | `proactive/pull.py:279` `pending_state` (`ZOE_PULL_NOT_PUSH`, default on) | The orb's quiet count; "what's up?" delivers | On by default (unset in env = on) |
| Telegram "send to my phone" | `services/zoe-data/auth_handoff.py:403` `_telegram_send` (URL button) | A linked member's private chat | **Not available live**: `ZOE_TELEGRAM_BOT_TOKEN` is absent from both zoe-data env files [verified, count 0], so `telegram_available()` is false and the QR is the only path. The Memories-space crowded-room rule and the abilities design both lean on this path |
| Telegram bot sidecar | `labs/flue-zoe-telegram-2x` (`:3582`) | Replies to linked senders; an unlinked sender only gets the link prompt (`src/handler.ts:58`) | Live. `allowed_updates: ['message']` (`src/app.ts:201`): **no `callback_query`, so no working buttons**. No local "send this to member X" route |
| Crash-loop alert | `scripts/maintenance/zoe_crash_loop_watch.py` + `zoe-crash-loop-watch.timer` | Owner chat ids, plain text | Live (timer active, hourly "healthy" lines). Deliberately **outside zoe-data** so it survives zoe-data's death. Recipients come from the **retired** `TELEGRAM_ALLOWED_USERS` fallback because `ZOE_ALERT_TELEGRAM_CHAT_IDS` is not set (`:92`) [verified, counts only] |
| Evolution weekly digest | `proactive/triggers/evolution_weekly_digest.py` (Friday 18:00, admins only) | `fire_notification` | Registered live. Its "approve" and "defer" links are **the same URL** and point at `POST /api/agent/evolution/proposals/{id}/action` (`routers/system.py:2053`), which a tapped link (a GET) cannot call. The links are dead |
| Struggle detector report | `GET /api/system/struggles`, nightly `STRUGGLE_SUMMARY`, an admin morning-brief line in mode `on` | Owner only | `ZOE_STRUGGLE_DETECT` default `shadow`: reports exist, nothing user-facing |

### 6.2 What does not exist

- **No per-member communication preference anywhere.** No table, no `user_prefs` key, no Settings section. Quiet
  hours are implemented four times, all from household env: `proactive/engine.py:34`, `voice_delivery.py:112`,
  `voice_enrollment.py:309`, `proactive/pull.py:270`.
- **No change feed.** Nothing tells anyone that a PR merged or deployed. The deploy job's only durable output is the
  `last-deployed-sha` marker.
- **No attribution.** All PRs are opened under the owner's GitHub account; agents add `Co-Authored-By` trailers,
  Zoe's own evolution proposals become board issues (`evolution_autonomy.py:26`: only `intent_pattern` and
  `user_frustration` may auto-execute after an admin approval), but nothing in a merged commit says "Zoe proposed
  this".
- **No plain-language field.** PR bodies are written for reviewers.
- **Roles.** zoe-auth knows `admin`, `user`, `family_member`, `child`, `guest`, but zoe-data's capability matrix
  maps `family_member` and `child` to the guest matrix ([identity-presence-engine.md](../architecture/identity-presence-engine.md)
  R9, owner decision 13). A "children get only what affects them" rule cannot be enforced until that is decided.

### 6.3 Planned elsewhere, and how this work must fit

- **Abilities and connectors** (PR #2029, `docs/architecture/zoe-abilities.md`, unmerged): Flue channel packages
  for WhatsApp, SMS (Twilio), email (Resend), Slack, Discord and Messenger in one channels sidecar (C-track); Apprise
  for notifications out; every manifest has a `notify` block naming each destination and its content level; an
  off-box destination carries a content-free **pointer** unless the member opted that destination in to content;
  an unlinked sender never reaches the brain.
- **Action agent** (`docs/research/zoe-action-agent-2026-10.md`): `callback_query` approvals on Telegram
  (`ZOE_TELEGRAM_APPROVALS`), the approval object, bot-token rotation first.
- **Memories space** (`docs/architecture/memories-space.md` 5.1): never unprompted; counts-only lines composed in
  code; verified owner only; crowded room sends to the phone.
- **Identity engine** (`docs/architecture/identity-presence-engine.md`): assurance levels; crowded room; guests are
  the absence of an identity.
- **Struggle detector** (`docs/architecture/struggle-detector.md`): Phase 2 turns clusters into proposals, Phase 3
  proves fixes by replay; its rows carry a member's own words only for that member.
