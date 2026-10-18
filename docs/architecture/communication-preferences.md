---
type: design
title: Communication preferences and change notices - who hears what Zoe changed, where and when
date: 2026-10-15
status: P1 built 2026-10-18 (flag-dark, ZOE_CHANGE_NOTICES; see docs/knowledge/change-notices.md for what it built and where it differs from section 9); P2-P4 design only. Every phase ships flag-dark and is lab-proven before prod.
evidence: docs/research/communication-preferences-2026-10.md (field research with sources and tags; survey of Zoe's outbound paths with file:line and the live state)
builds_on: docs/architecture/zoe-abilities.md (PR #2029 - Flue channels, Apprise, the manifest notify block), docs/architecture/struggle-detector.md, docs/architecture/memories-space.md (5.1 never unprompted), docs/architecture/identity-presence-engine.md (crowded room, roles R9), docs/research/pull-not-push-inbox-2026-10-04.md
---

# Communication preferences and change notices

> Owner ask (2026-10-15), in gist: *"How hard would it be to keep me updated with PRs that are raised and then
> merged, with a description, on Telegram? I'm thinking some communication preferences might be good as we move to
> self-evolution: 'I've found this problem and have fixed it' sort of stuff. I'm guessing people (other household
> members) will not be raising PRs."* Then: *"Do a deep dive first ... people might want it via different
> connectors."*
>
> Evidence, sources and the code survey are in
> [communication-preferences-2026-10.md](../research/communication-preferences-2026-10.md) (cited below as
> "research N"). Names in examples are invented.

## 0. Answer first

- **The owner's Telegram PR feed is small: one phase, one or two PRs (P1).** Merged PRs reach the box with their
  whole description already: each merge is a squash commit carrying the PR title, number and body, and the deploy
  job writes `~/.zoe/deploy/last-deployed-sha` only after a healthy deploy (`.github/workflows/deploy.yml:661`). A
  small timer job diffs "last told" against "last deployed" and tells the owner what went live. One `gh pr list`
  call covers PRs that were raised. No webhook, no public endpoint, no new model.
- **It must be a digest, not a message per PR.** `main` took a median of about 16 merges a day over the last
  fortnight, peaking at 47 (research 0). Batching beats both instant alerts and silence (research 2). So the owner
  gets **at once** only what waits on him or what broke, and gets **one evening digest** for the rest.
- **For everyone else it is a different product, not a different channel.** They hear only user-visible effects,
  in one plain sentence each, weekly, on the panel or at the start of a conversation they began. Most PRs change
  nothing they can see, and for those the right notice is none.
- **Preferences are per member and per category**, each with a channel list, a cadence and a detail level, plus
  quiet hours and language. Defaults come from the member's role; a few floors cannot be turned off.
- **Zoe never speaks unprompted to deliver any of this.** That rule (owner, 2026-09-29) holds for every phase.
- **Today zoe-data cannot send a Telegram message on the live box** (research 6.1: no bot token in its
  environment), and the bot cannot receive button presses (`allowed_updates: ['message']`). P1 sidesteps both by
  sending from outside zoe-data, as the crash-loop watcher already does; P2 fixes them properly.

## 1. Rules that shape everything below

1. **Pull by default, push by exception.** A notice waits in the member's queue (orb count, "what's up?", the next
   digest) unless its category is set to instant for that member. Instant is for what **waits on the person** or
   **broke**. GitHub's mobile push list is the model: mentions, review requests, deployment approvals (research 1.3).
2. **Never unprompted speech.** Spoken delivery happens only inside a turn the member started: the `[Today]` block
   (`brief_first_turn.py`) or the selector's `[RAISE]` on an open turn. The announcement lane
   (`ZOE_PROACTIVE_SPOKEN`) is never used for notices, whatever that flag says.
3. **Two layers, never mixed.** Layer 1 is one plain sentence about the effect, for anyone the effect reaches.
   Layer 2 is technical (PR number, link, gate results, undo) and goes **only to members whose preference says
   technical**, which by default is the owner alone.
4. **No plain sentence, no notice for non-owners.** If a change has no human-written plain sentence, it is not shown
   to anyone but the owner. Code never invents a user-facing claim from a diff (fail closed).
5. **The member owns the level; the system owns the category** (the Android channel model, research 1.1). Zoe
   defines categories and starting defaults; the member can turn any of them down, and a few up; Zoe never raises a
   level on its own.
6. **Every notice carries its own controls**: details, quieter, stop this kind, and for the owner undo (Renovate's
   checkbox and Amershi G8, G9, G16, G17; research 5). Where a channel cannot carry a reply, the notice says the
   spoken or tapped equivalent.
7. **Calibrated, not cheerful.** "I fixed X" is said only with evidence ("re-asks about tomorrow's weather: 5 last
   week, 0 this week"), and "I can now do X" says what it cannot do yet (Lee and See; Amershi G1, G2; research 5).
8. **Content follows the destination.** On-box surfaces may carry the plain sentence. An off-box destination carries
   what the member opted that destination in to, exactly as the abilities `notify` block rules (research 6.3). The
   owner's technical layer may leave the box because PR text is already public in this repository; no other content
   does without opt-in.
9. **Volume is capped in code.** Per member per day, per channel per minute, quiet hours, and "do not send the same
   kind twice in a row" (Duolingo's recovering arms, research 1.4). Overflow rolls into the next digest; nothing is
   dropped silently.

## 2. Categories

Seven categories. Each notice belongs to exactly one, chosen in code from its source (section 5.1), never by a
model.

| Category | What it is | Example (layer 1) | Tier |
|---|---|---|---|
| `decision` | Something waits on this member: a PR needing the owner, a proposal to approve, a deploy refused by the voice gate, a "may I?" | "A fix for the morning brief is ready but needs your OK." | instant-capable |
| `broke` | Something failed or was rolled back: deploy failed, crash loop, health check red, a change reverted | "Last night's update didn't start properly, so I went back to yesterday's version." | instant-capable |
| `security` | Something about this member's account or privacy: a new channel linked, an account connected or disconnected, a PIN changed, a failed sign-in burst | "Your Telegram was linked to Zoe just now. If that wasn't you, tell me." | instant-capable, has a floor |
| `fixed` | Zoe fixed something that affected this member, with evidence | "I kept mishearing 'turn on the hall light'. I've fixed that." | digest |
| `ability` | Zoe can do something new, and what it cannot do yet | "I can keep an eye on email now, if you connect it. I can't reply to email yet." | digest or weekly |
| `household` | A change everyone can see: a new panel screen, a changed routine, something removed | "The Memories tile replaced Ask Zoe on the wall screen." | weekly |
| `under_the_hood` | Merged work with no user-visible effect: refactors, tests, docs, dependency bumps | (owner only) "+12 housekeeping: 7 tests, 3 docs, 2 dependency bumps" | digest, owner only |

The **digest** is not a category. It is a cadence: notices of every category whose cadence for that member is
`digest` or `weekly` are collected and sent together at the member's digest time.

## 3. The preference model

### 3.1 Shape

One JSON document per member under the existing `user_prefs` store (`services/zoe-data/user_prefs.py`,
`read_prefs` / `set_pref`), key `communication`, schema-versioned and validated in code. No migration is needed for
the preferences themselves (P2's notice tables do need one; section 5.6).

```yaml
communication:
  version: 1
  preset: owner_technical          # owner_technical | important_only | weekly | everything | nothing | custom
  language: en                     # the language notices are written in
  quiet_hours: {start: "21:30", end: "07:00"}   # absent = the household's ZOE_QUIET_START_HOUR / _END_HOUR
  digest: {daily_at: "19:00", weekly_on: "sun", weekly_at: "10:00"}
  daily_instant_cap: 6             # instant notices beyond this roll into the next digest
  categories:
    decision:       {cadence: instant, channels: [telegram, panel], detail: technical}
    broke:          {cadence: instant, channels: [telegram],        detail: technical}
    security:       {cadence: instant, channels: [telegram, panel], detail: plain}
    fixed:          {cadence: digest,  channels: [telegram],        detail: technical}
    ability:        {cadence: digest,  channels: [telegram],        detail: technical}
    household:      {cadence: weekly,  channels: [first_turn, panel], detail: plain}
    under_the_hood: {cadence: digest,  channels: [telegram],        detail: technical}
  asked: {offered_at: null, answer: null}   # the one gentle ask (section 8.3)
```

- **`cadence`**: `instant` | `digest` (daily) | `weekly` | `never`.
- **`channels`**: tried **in order** until one delivers: `telegram`, `whatsapp`, `sms`, `email`, `push` (ntfy or
  Pushover through Apprise, P3), `web_push`, `panel` (the orb's quiet count and a card on pull; no toast, no sound),
  `first_turn` (the `[Today]` / `[RAISE]` mention in a conversation the member started), `none`. A channel is only
  usable once the member has linked it (`telegram_link.py` today; the abilities `connected_accounts` later).
- **`detail`**: `technical` (layer 1 + layer 2), `plain` (layer 1 only), `pointer` (content-free: "There's an update
  from Zoe; ask me 'what's new?'"). An off-box channel is capped at `pointer` for any content the member has not
  opted that destination in to (rule 8).
- **Presets** are the voice-sized way to set all of this at once (section 8). Any per-category edit makes the
  preset `custom`.

### 3.2 Floors (not overridable)

- **`security` about your own account cannot be set to `never`.** The member can pick the channel; the lowest it can
  go is "held for you": it waits for the member's next **identified** turn (voice session or fresh PIN, through
  `first_turn`) or their signed-in desktop or phone. It is never shown, not even as a pointer or a count, on a shared
  surface to a turn that is not that member's (an account event's existence is itself private).
- **`decision` for the owner cannot be `never`** while anything waits on him; he can make it `digest`.
- **Nobody is ever woken.** Quiet hours defer every instant notice to the end of the window, including `broke`.
  Whether a crash loop may break through is owner decision 11.
- **Guests have no preferences and receive nothing**: a guest is the absence of an identity
  (identity-presence-engine.md 4.1).

### 3.3 Defaults by role

| Role | Preset | What they get |
|---|---|---|
| **Owner** (`admin`) | `owner_technical` | Telegram, technical. Instant: `decision`, `broke`, `security`. One evening digest (default 19:00) with `fixed`, `ability`, `household`, `under_the_hood` grouped and collapsed. Raised PRs: those waiting on him are `decision`; the rest are a count line in the digest |
| **Other adults** (`user`; and `family_member` once R9 is decided) | `weekly` | `household`, `ability` and `fixed`-that-affected-them, plain, **weekly**, on `first_turn` then `panel`. `security` about their own account on `panel` + `first_turn`. No external channel until they choose one. Never technical unless they ask |
| **Children** (`child`) | `weekly`, restricted | Only `household` notices that carry a `For-Children` sentence (5.2) and `fixed` notices about their own use ("I got better at hearing you"), plain, on `first_turn` only. Never `under_the_hood`, never technical, never an external channel. `security` events about a child's account go to the admin. **Blocked on identity R9** (identity-presence-engine.md section 9; its owner decision 13): until `child` stops collapsing to the guest matrix in zoe-data, a child is a guest here and gets nothing |
| **Guests** | none | Nothing, ever |

Children's rules follow `docs/governance/emotional-safety-note.md` section 4 (only an admin changes a minor's
settings).

## 4. What it looks like

Illustrative only: the PR numbers and the plain sentences below are examples, not real notices.

**Owner, Telegram, instant `decision`** (layer 1 + 2, a URL button and, from P2, callback buttons):

> Needs your OK: **#2081 fix(brief): first-turn brief skips a cancelled event**
> Waiting on: voice-gate replay against this head (red: 1 clip regressed).
> [Open PR] [Details] [Not now]

**Owner, Telegram, evening digest** (one message, split at 4,096 characters; research 4):

> **Today on main: 14 merged, all live** (deployed 18:42, health OK)
> **Fixed (3)**
> - #2071 voice-probe: retry only brain-wording can't-do clips. *For you: fewer false voice-gate failures.*
> - #2072 person_health: pinned the date in a test (CI only)
> - ...
> **New (1)** #2063 local STT accuracy score (weekly proxy word-error rate). *Not yet: no live use.*
> **Household (1)** #2051 Memories tile design (docs only, nothing visible yet)
> **Under the hood: 9** (4 tests, 3 docs, 2 dependency bumps) [List]
> **Raised, not merged: 5** (2 drafts) [List]
> [Open #2071] [Open #2063] [All of today's PRs]
>
> (P1 carries URL buttons only. From P2 the same digest carries Details / Undo / Quieter buttons, section 5.7.)

**Another adult, the next conversation they start after the weekly digest is due** (spoken, inside their own
turn, through `[RAISE]`; the brain voices a code-composed line, as Memories-space 5.1 does):

> "Before I forget: two new things this week. The wall screen has a Memories tile now, and I'm better at telling
> 'hall light' from 'all lights'. Want the details on the screen?"

**A child, first turn after the weekly digest is due**: "I've got better at hearing you when you ask for music."
(Only if a `fixed` notice was about their own use. Otherwise nothing.)

## 5. The change-notice pipeline

```
 sources ──► normalise to ChangeNotice ──► audience + category (code) ──► compose layers
   │            (dedup_key, attribution)                                     │
   │                                                                         ▼
   │                         per-member outbox (notice_deliveries, unique per member+notice+channel)
   │                                   │                       │
   │                         instant (cap, quiet hours)   digest / weekly composer
   │                                   └──────────┬────────────┘
   │                                              ▼
   │                         transport: telegram | web_push | panel | first_turn | (P3) apprise, flue channels
   │                                              │
   └──────────── audit row per delivery ◄─────────┘          replies: details / undo / quieter / stop
```

### 5.1 Sources

| Source | How it is read | Category | Phase |
|---|---|---|---|
| **Merged and live** | `git log <last-notified>..<last-deployed-sha>` on the live checkout; each squash commit gives `(#N)`, the title, the body and the trailers | **owner** grouping by the conventional-commit type in the title (`feat` -> New, `fix` -> Fixed, `docs`/`test`/`chore`/`deps`/`ci` -> `under_the_hood`; a `Revert "..."` commit -> `broke`). **Any other member's** category comes only from the explicit `Zoe-Notice:` trailer (5.2); no trailer = owner only. The commit holds no GitHub labels, and none are needed | P1 |
| **Merged, not live** | `origin/main` ahead of `last-deployed-sha` for longer than the deploy normally takes, plus the deploy run's conclusion | `broke` (deploy failed) or `decision` (deploy refused by the voice gate: re-run the probe) | P1 |
| **Deploy failed** | a new `if: failure()` step in `deploy.yml` writes `~/.zoe/deploy/last-failed` (run URL, target sha, failing step) | `broke` | P1 |
| **Raised** | `gh pr list --state open --json number,title,isDraft,labels,headRefName,reviewDecision` | `decision` if labelled `needs-owner` (or a confirm-first PR), else a count in the digest | P1 |
| **Crash loop** | the existing `zoe_crash_loop_watch.py` (unchanged; it already sends) | `broke` | exists |
| **Evolution proposals** | `evolution_proposals` rows becoming `pending` (needs approval) or `validated` | `decision` / `fixed` | P2 (folds in `evolution_weekly_digest`) |
| **Abilities** | `ability_audit`: connect, disconnect, scope change (zoe-abilities A0) | `security` for that member; a newly shipped ability -> `ability` for those allowed it | P2, with A0 |
| **Channel linking** | `telegram_link` consume, and C-track pairing | `security` | P2 |
| **Flag flips** | zoe-data at start-up compares the `ZOE_*` flags named in `docs/knowledge/flag-inventory.json` with the last snapshot. **Values are recorded only for flags whose value is a mode** (`off`/`shadow`/`on`, `0`/`1`); any key that looks like a secret (`*TOKEN*`, `*SECRET*`, `*KEY*`, `*PASSWORD*`) is never read | `household` if the flag has a plain sentence registered, else `under_the_hood` | P2 |
| **Struggle detector "I noticed and fixed"** | a fix PR carrying `Struggle-Cluster: <signal>|<domain>|<pattern>`; after it is live, the cluster's count over the following 7 days is compared with the 7 days before | `fixed`, sent only when the count really fell (section 5.7) | P4 |

### 5.2 Attribution

Every PR is opened from the owner's GitHub account (research 0), so the notice cannot say who made a change from
the author field. P1 adds one commit trailer, written by whoever opens the PR and carried into the squash:

```
Zoe-Origin: self | agent | owner | dependabot
Zoe-Notice: household | ability | fixed | none
For-The-Household: <one plain sentence, or "nothing visible">
For-Children: <one plain sentence, only if it affects them>
```

- `Zoe-Notice` is the category for non-owners, chosen by the author, never inferred from the diff or the commit type.
  Missing, `none`, or contradicted by `For-The-Household: nothing visible` = owner only (fail closed).

- `self` = it came from Zoe's own evolution proposal or struggle cluster. Those notices may say "I"; everything else
  says "Zoe was updated" or names the change. A notice never claims Zoe did something she did not.
- `For-The-Household` is the layer 1 sentence for non-owners (rule 4). A missing trailer means owner only. Trailers
  are part of the squash commit body, which GitHub fills from the PR description, so they are immutable once merged.
- `pr-hygiene.yml` gains an **advisory** note when `Zoe-Origin` is missing. It is not a blocking check: blocking gates
  stay deterministic and minimal (AGENTS.md review pipeline).
- Dependabot PRs get `dependabot` from their author and are always `under_the_hood`.

### 5.3 Composing the two layers

- **Layer 2 (owner, technical)** is deterministic: `#N`, the PR title, the first paragraph of the body (cut at 300
  characters), the gate results the source knows (deploy health, voice gate), and a link. No model.
- **Layer 1 (plain)** comes from the trailer, never from the diff. Before it is used, a **jargon guard** checks it in
  code: a deny list (`PR`, `merge`, `flag`, `router`, `lane`, `zoe-data`, `Flue`, `sidecar`, `migration`, `regex`,
  file paths, `#` numbers, backticks) and a length cap (160 characters). A sentence that fails is shown to the owner
  as "plain sentence rejected: <reason>", and to nobody else.
- **Language.** Notices are English until a member's `language` says otherwise. Translating a plain sentence is a
  later, separate decision (owner decision 14), done by the resident brain in an idle window with no tools, and
  checked by the same guard.
- **Wording varies.** The digest composer picks among a few fixed openers and never starts two digests in a row the
  same way (research 1.4). The facts and counts are fixed by code.

### 5.4 De-duplication and batching

- Every notice has a `dedup_key` (`pr:<N>:merged`, `deploy:<sha>:failed`, `ability:<id>:<member>:connected`, ...). A
  PR that appears as "raised" and later "merged" is two notices; the merge notice supersedes the raised one in any
  undelivered digest.
- A deploy that ships 14 PRs is one digest section, not 14 messages. A crash loop that keeps looping is one notice
  until it recovers (the watcher's existing cooldown).
- **Delivery is at most once per channel** (5.6), never silently lost.
- **Already seen elsewhere is not sent again** (Linear's rule, research 1.3): an item the member pulled on the panel
  or heard on a first turn is marked delivered on every channel.
- An empty digest is not sent. "Nothing changed today" is noise.

### 5.5 Delivery lanes and transports

Two lanes, because the thing that reports a breakage must not depend on the thing that broke:

- **Out-of-band lane** (owner only; `broke` and `decision` from the deploy and GitHub sources; the evening digest in
  P1): a standalone script on a systemd user timer, like `zoe_crash_loop_watch.py`. It reads the bot token from the
  bot sidecar's env file, as the crash watcher does, and sends straight to the Telegram Bot API. It works when
  zoe-data is down, mid-deploy, or inside the night window. The crash watcher's sender moves into a shared
  `scripts/maintenance/telegram_alert.py` so the two cannot drift, and it reads `ZOE_ALERT_TELEGRAM_CHAT_IDS`
  first (live today it falls back to the retired `TELEGRAM_ALLOWED_USERS`; research 6.1).
- **In-band lane** (everyone; P2): inside zoe-data, where member preferences, roles, the pull queue and the first-turn
  seams live. One `notify_transport` interface, `send(member, channel, rendered, controls) -> Delivered | Deferred |
  Failed(reason)`, with backends:

| Backend | Uses | Phase |
|---|---|---|
| `telegram` | a loopback-only route on the bot sidecar (`POST /internal/notify`, internal-token auth, `127.0.0.1`), so **one process holds the bot token**, enforces Telegram's per-chat rate (about 1 message a second, research 4) and receives the button presses. Alternative: give zoe-data the token (`auth_handoff` already expects `ZOE_TELEGRAM_BOT_TOKEN`). Owner decision 6 | P2 |
| `web_push` | `routers/push.send_push_to_user` (VAPID) through `fire_notification` **with spoken delivery bypassed** | P2 |
| `panel` | a `proactive_candidates` row of kind `whats_new` (counts and plain sentences only), shown by the orb's quiet count and delivered on "what's up?" (`proactive/pull.py`), **only inside that member's identified session** (the pull queue is already per member; the household orb never shows another member's count). `security` rows are never on the panel unless the speaker is verified | P2 |
| `first_turn` | the same row carried by `[RAISE]` on an open turn, with the seam changes memories-space 5.1 proposes for `day_review` (exempt from the nightly expiry, a 12 h cooldown after an unanswered raise, a verified-speaker gate for `security`); proposed there, not built | P2 |
| `apprise` | Apprise with `enable_only(...)` to an allowlist (`ntfys://`, `pover://`, `mailtos://`), URLs in the abilities vault; send-only, so no reply controls | P3 |
| Flue channels | the abilities C-track sidecar: email (Resend) for a weekly digest; WhatsApp **only as a reply inside the member's open 24-hour window**, or a pointer-only utility template if owner decision 12 accepts the cost; SMS only for opted-in `broke`/`security` | P3 |

### 5.6 Storage and audit (P2)

- `change_notices`: id, `dedup_key` (unique), source, ref (PR number, sha, ability id), category, origin, audience
  (`owner` | `household` | `member:<id>` | `children`), layer 1 text, layer 2 text, `user_visible`, created_at.
- `notice_deliveries`: notice id, member, channel, detail level actually sent, status (`queued`, `deferred_quiet`,
  `capped`, `sent`, `failed`, `seen_elsewhere`), sent_at, provider message id. Unique on (notice, member, channel),
  written through an outbox. **No external channel offers an idempotency key** (Telegram's `sendMessage` has none), so
  "exactly once" is impossible across a crash. The rule is **at most once, never silently lost**: the row is claimed
  (`sending`) and committed *before* the provider call; success records `sent` and the provider message id. On
  restart a `sending` row is never re-sent; it becomes `uncertain` and the member's next digest lists it in one line
  ("this may not have reached you: ..."). On-box channels (panel, first turn) are written in the same transaction as
  the status and are exactly once.
- A member sees their own deliveries ("what have you told me lately?"); the admin sees counts per member, never
  another member's text.
- Both tables join `MemoryService.delete_user` and `forget_user` (a member's deliveries are erased with them;
  household notices are not personal data and stay). Retention: 90 days.

### 5.7 Replies and controls

| Control | Telegram (P2: `callback_query` added to `allowed_updates`) | Panel / voice | Effect |
|---|---|---|---|
| **Details** | button | "tell me more" | layer 2 for the owner; for others, the plain sentence plus "it's on the desktop under What's new" |
| **Quieter** | button | "only tell me about big stuff" | moves this category one step down (instant -> digest -> weekly -> never), within the floors |
| **Stop this kind** | button | "stop telling me about updates" | cadence `never` for the category (floors apply) |
| **Undo** (owner only) | button / `undo <N>` | "undo that change" (identified owner, PIN or voice session) | **opens a revert PR** for that squash commit through the normal gate. Never a push to `main`, never `--admin`. A voice-path revert still needs the replay gate, and the reply says so |
| **Not now** | button | "not now" | snoozes a `decision` until the next digest |

- Callback data is a short opaque id (Telegram allows 64 bytes, research 4), looked up server-side, single-use,
  and accepted **only from the Telegram id linked to the member the notice was sent to**. A forwarded message's
  buttons do nothing for anyone else.
- A text reply that is not a control goes to the brain as an ordinary message, as today.
- In P1 there are no callback buttons: notices carry URL buttons (open the PR, the deploy run) and say "reply in
  chat" for the rest. That keeps P1 free of the sidecar change the action-agent approvals also need.

### 5.8 "I noticed and fixed" (struggle detector, P4)

- A fix PR born from a struggle cluster carries `Zoe-Origin: self` and `Struggle-Cluster:`. At merge the owner gets a
  normal `fixed` digest line.
- **The member hears about it only after the evidence is in.** A raw count falling is not enough (people may simply
  have used the feature less). All three must hold:
  1. **Replay:** the cluster's stored cases replay clean on the deployed code (the fix PR's own replay-gate result and
     `struggle_replay_eval.py` on that cluster).
  2. **Rate, not count:** the cluster's events **per opportunity** (per turn routed to that domain, by that member)
     over the 14 days after going live fell by at least half against the 14 days before.
  3. **Enough data:** at least 5 cluster events before and at least as many domain turns after as before; otherwise
     the window extends, up to 6 weeks, then closes as "not enough data".
  Then a `fixed` notice goes to the members whose events were in the cluster: "I kept mishearing 'turn on the hall
  light'. I've fixed that." It never quotes another member's words, and only quotes a member's own words when it
  delivers to that member on a private surface or in their own turn.
- If replay passed but the rate test did not, or the data ran out, the owner gets the numbers ("hall light mishears:
  4 per 100 turns -> 3 per 100, not enough to call it fixed") and nobody else hears anything. Calibrated trust needs
  the misses too (rule 7).

## 6. Privacy and safety

- **No household data in outbound text beyond what that member may see.** Layer 1 comes from a trailer written in a
  public repository, which by the repo's own rule holds no household data. Struggle-derived notices carry only
  counts and the code-only `pattern` for the owner, and the member's own words only to that member.
- **Member isolation.** A notice addressed to `member:<id>` is delivered only to that member. The admin sees counts.
  Cell CN2.
- **Restraint and crowded room.** A `first_turn` delivery goes through the same restraint and speaker checks as any
  raise: no `security` notice is spoken unless the speaker is verified (voice session or PIN), and if a second voice
  is present the spoken line becomes "I've got an update for you; I'll put it on your phone" (identity-presence-engine.md
  section 7, the crowded-room cell; memories-space 5.3).
- **Channel identity.** A channel is used only for the member it is linked to (`telegram_link`; abilities
  `connected_accounts`). Unlinked senders never reach the brain and cannot change anyone's preferences (abilities W18).
- **Egress is declared.** The change-notice feature gets a manifest in the abilities shape (`kind: notifier`,
  `id: zoe-updates`) whose `notify` block lists every destination with its content level, so the consent text and the
  Connected-accounts page show it. Until the abilities registry exists (A0), the same list lives in this document's
  section 5.5 and in the P1 script's header.
- **Rate caps.** Per member: `daily_instant_cap` (owner 6, others 1). Per Telegram chat: at most 1 message a second
  and at most 3 a minute from Zoe. Same-kind spacing: no two instant notices of one category within 30 minutes; the
  second joins the digest.
- **No secret leaves in a notice.** Flag notices never read secret-like keys; the deploy-failure notice names the
  failing step, never its log.

## 7. Setting preferences

### 7.1 Voice (panel, any identified member)

Small, fixed phrases mapped to presets or one category, handled by a deterministic tier (not the brain), and
therefore on the voice path and replay-gated:

- "Only tell me about big stuff" -> `important_only` (instant `decision`/`broke`/`security`, everything else weekly).
- "Tell me once a week" -> `weekly`. "Tell me everything" -> `everything`. "Stop telling me about updates" -> every
  category `never`, floors kept, and Zoe says which floor remains ("I'll still tell you if your account changes").
- "Send updates to my phone" -> puts `telegram` first for the member's enabled categories, or, if Telegram is not
  linked, offers the QR to link it (VISION principle 8).
- "What have you told me lately?" -> the member's own deliveries.

Changing your own preferences needs an identified speaker (voice session or a fresh PIN), never a guest; changing a
child's needs an admin.

### 7.2 Panel Settings (touch, no keyboard)

One card, "Updates from Zoe": five preset tiles, then per-category rows with three taps each (channel, how often,
how much detail) as chips. Quiet hours by a time wheel. Nothing typed.

### 7.3 Desktop Settings

The full matrix, the digest times, language, linked channels (with the abilities Connected-accounts page once it
exists), and the member's delivery history with "why did I get this?" per row.

### 7.4 The one gentle ask

The first time a non-owner adult would receive a notice, Zoe asks once, in a conversation they started: "Now and
then I change or learn something. By default I mention it once a week when we talk. Want it on your phone instead,
or not at all?" It reuses the enrolment offer's pure gate shape (`enrollment_state.decide_offer`: adults only, not a
guest, outside quiet hours, one claim per member). **A "no" is final** for this ask: `asked.answer` is stored and Zoe
never asks again. The member can still change it by voice or in Settings. Asking again later is itself a
notification (owner decision 9).

## 8. Measurement: lab cells with negative controls

Lab-only, synthetic members and a synthetic git history; no real chat is contacted (a fake Bot API on loopback).
Every cell has a negative control that must turn it red.

| Cell | Asserts | Negative control |
|---|---|---|
| CN1 digest correctness | a synthetic history of 14 squash commits produces one digest with the right groups and counts, and every `#N` appears once | drop the dedup key: duplicates, red |
| CN2 isolation | a notice for member A never reaches member B on any channel or in B's history | drop the member filter: red |
| CN3 instant cap | the 7th instant notice in a day for the owner goes to the digest | remove the cap: 7 sends, red |
| CN4 quiet hours | an instant `broke` at 23:00 is delivered at quiet end, once | ignore quiet hours: red |
| CN5 never spoken unprompted | with `ZOE_PROACTIVE_SPOKEN=1`, no notice ever enqueues an announcement | route a notice through `fire_notification` with spoken on: red |
| CN6 plain-language fail-closed | a change with no `For-The-Household` trailer reaches only the owner | compose layer 1 from the PR title: red |
| CN7 jargon guard | "Merged #2071 to fix the router lane" is rejected for non-owners | empty the deny list: red |
| CN8 at most once | a kill after the claim and before the send, and after the send and before the status update, never yields two messages; the uncertain notice appears once in the next digest | re-send `sending` rows on restart: a duplicate, red |
| CN9 guests and children | a guest receives nothing; a child receives no `under_the_hood` and nothing technical | map `child` to the adult defaults: red |
| CN10 no secrets | a fake flag `ZOE_FAKE_TOKEN=canary` never appears in any notice, log or audit row | read flag values without the secret filter: red |
| CN11 controls bound to the member | a callback from a different Telegram id than the one the notice was sent to changes nothing | skip the sender check: red |
| CN12 undo is a PR | "undo" creates a revert branch and PR, and `main` is never pushed | call `git push origin main` in the handler: the fake remote records it, red |
| CN13 out-of-band survives | with zoe-data stopped, a `deploy failed` marker still produces the owner's message | send through zoe-data: red |
| CN14 off-box content | a member who never opted Telegram in to content gets only a pointer on Telegram | send layer 1 to Telegram by default: red |
| CN15 evidence before "fixed" | a cluster whose raw count fell only because domain turns fell (same rate) produces no member notice, and the owner gets the numbers | compare raw counts instead of rates: a member notice, red |

The **Samantha bar** gains one scenario in P2 ("what's new?" from a member after a household change, on voice and
chat). The **voice replay gate** applies to the P2 voice tier and to any change in `fast_tiers.py` or the
`[RAISE]` path.

## 9. Phases and flags

| Phase | What | Flags (default) | Depends on | Effort |
|---|---|---|---|---|
| **P1** | Owner Telegram change feed, out-of-band: `scripts/maintenance/change_notice.py` + `zoe-change-notice.timer` (every 10 min); sources: merged-and-live (deploy marker + git), deploy failed (new `if: failure()` marker step in `deploy.yml`), merged-not-live, raised (`gh pr list`); instant `decision`/`broke`, evening digest; URL buttons only; state in `~/.zoe/change-notice/`; shared `telegram_alert.py` with the crash watcher; `Zoe-Origin` / `For-The-Household` trailers documented and an advisory pr-hygiene note; `ZOE_ALERT_TELEGRAM_CHAT_IDS` set by the operator | `ZOE_CHANGE_NOTICE=off\|shadow\|on` (off; `shadow` composes and logs to `~/.zoe-logs/change-notice.log` without sending) | nothing | S (1-2 PRs; the `deploy.yml` step is load-bearing, so that PR gets the full review chain) |
| **P2** | Per-member preferences (`user_prefs.communication`), `change_notices` + `notice_deliveries` (migration), in-band lane and `notify_transport` (telegram via the sidecar's loopback route, web push, panel, first turn), `callback_query` controls (shared with action-agent approvals), presets by voice + panel card + desktop matrix, the one ask, `evolution_weekly_digest` folded in (and its dead approve/defer links gone), flag-flip and ability/linking sources | `ZOE_COMM_PREFS` (off), `ZOE_CHANGE_NOTICE_MEMBERS` (off), `ZOE_TELEGRAM_CALLBACKS` (off) | P1; identity R9 for children; action-agent token rotation before callbacks | M (3-5 PRs) |
| **P3** | More channels: Apprise (ntfy, Pushover, email), Flue C-track email digest, WhatsApp in-window replies (template only on a yes), SMS opt-in for `broke`/`security`; the `zoe-updates` notifier manifest | `ZOE_NOTIFY_APPRISE` (off), the C-track channel flags | P2; abilities A0 (vault, manifests) and C0/C1 | S each |
| **P4** | Struggle-detector "I noticed and fixed" feed with the 7-day evidence check | `ZOE_CHANGE_NOTICE_STRUGGLE` (off) | struggle detector Phases 2-3; P2 | S |

Operator steps per phase (set `ZOE_ALERT_TELEGRAM_CHAT_IDS`, enable the timer, the first shadow week) go into an
operator pack in `docs/knowledge/` when the phase is built.

## 10. Decisions for the owner

1. **Digest time.** One evening digest at 19:00 (recommended), or morning, or two a day. Skipped when empty.
2. **What is instant for you.** Recommended: `decision`, `broke`, `security`. Not every merge, and not every raised
   PR.
3. **Raised PRs.** Only the ones waiting on you as instant, the rest as a count with a link in the digest
   (recommended), or each raised PR listed.
4. **Attribution trailers.** Adopt `Zoe-Origin` and `For-The-Household` for every PR (agents write them; advisory
   check)? Recommended: yes. Without them other members can never hear about changes.
5. **Undo from Telegram.** May "undo" open a revert PR from your linked Telegram (it still has to pass the gate and be
   merged)? Recommended: yes, owner only.
6. **Telegram transport for zoe-data.** A loopback route on the bot sidecar so one process holds the token
   (recommended), or the token in zoe-data's env too (simpler; `auth_handoff`'s "send to my phone" would also start
   working). Either way, setting it fixes the live gap in research 6.1.
7. **Other adults' default.** Weekly, plain, at the start of a conversation they began, plus the panel's quiet count,
   no external channel until they choose (recommended).
8. **Children.** Only changes that affect them, plain, first turn only, after identity R9 is decided; until then
   nothing (recommended).
9. **The one ask.** Ask each adult once and treat "no" as final (recommended), or re-offer after 60 days like voice
   enrolment.
10. **Per-member quiet hours.** Members may set their own, defaulting to the household's (recommended).
11. **Waking you.** Never (recommended), or let a crash loop break through quiet hours (today the crash watcher sends
    at any hour).
12. **Paid channels.** No WhatsApp templates or SMS for change notices (recommended); WhatsApp only as a reply inside
    a window the member opened.
13. **The struggle feed's audience.** Tell the member whose struggle it was (recommended, plain, after evidence), or
    the owner only.
14. **Languages.** English only until someone asks (recommended); translation by the resident brain later.

## 11. Risks, and is this too complicated?

- **Fatigue is the main risk**, not missed news. A household that mutes Zoe's updates also stops hearing about the
  security floor. The defaults are deliberately quiet, the caps are in code, and CN3/CN4 measure them.
- **Plain sentences will be missing or bad at first.** Agents write PRs for reviewers. The fail-closed rule means the
  cost of a missing trailer is silence, not a confusing message; the advisory check makes the gap visible.
- **Overclaiming "I fixed it".** Section 5.8 only says it after the numbers move.
- **Telegram is a hosted provider.** PR text is public anyway, but a `security` notice ("your Telegram was linked")
  is itself personal; it goes to Telegram only as a pointer unless the member opted in.
- **Is this too complicated?** As a whole, yes, if built at once. The owner's ask is **P1 alone**: one timer script,
  one marker step in the deploy job, a shared Telegram sender, two commit trailers. That is useful on day one and
  needs no database, no zoe-data change and no new model. P2 is where the household starts to benefit, and it waits
  on a measured P1 shadow week and on the R9 roles decision. Not built here: a notification marketplace, per-member
  model-written summaries, any unprompted speech, any change that bypasses the merge gate.
