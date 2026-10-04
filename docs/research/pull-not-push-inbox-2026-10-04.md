---
type: research
title: Pull, not push — the "I have something" inbox (2026-10-04)
date: 2026-10-04
status: research-only; PR 1 of 6 (delivery ledger, ZOE_PROACTIVE_LEDGER, flag-dark) built per §5 — see docs/knowledge/synthetic-users-and-proactive-recipients.md#delivery-ledger-flag-dark; PRs 2-6 not started
description: Deep-research record for P1 of the 2026-10-03 companion-field audit — give Zoe a way to OFFER without speaking. Field half (Nomi's cadence + "one unanswered, then wait", Replika / Kindroid / Character.AI, the mobile-notification and interruptibility literature with numbers, iOS / Android notification tiers, the "should I speak" controller papers, how Alexa / Google / OVOS / Home Assistant surface pending items without speaking) plus a read-only trace of Zoe's live proactive chain with file:line (nightly selector, first-turn brief, open-loop lifecycle, the response ledger, the touch orb, the voice daemon's windows, what the day-sim measures), then a flag-dark design — an orb "has something" state fed by the selector's candidates classed Notify / Question / Review, "what's up?" or a tap as the pull, one-unanswered-then-wait with doubling, ONE delivery mechanism for brief + greeting raise + pull, the P10 learned should-I-speak head with its features, labels and ~200-row threshold — cost, measurement with negative controls, and a go/no-go against VISION.
---

# Pull, not push — the "I have something" inbox (2026-10-04)

Research date: 2026-10-04. The idea is **P1** in
[companion-field-vs-samantha-2026-10-03.md §2](companion-field-vs-samantha-2026-10-03.md), with
**P10** (the learned should-I-speak head) from the same table, because one is useless without
the other: the orb is the *channel*, the head is the *judgement*. Tracked neighbours: **B2.2**
(candidate-selection → delivery-gating split, unread/ignored back-off, reward model), **B2.5**
("don't mention this again"), **W13.1** (proactive show) and **W16** (the scoreboard) in the
[beat-the-bar tracker](../architecture/beat-the-bar-2026-program.md) and the
[Samantha plan](../architecture/samantha-evolution-plan.md). Sources are cited inline and listed
in §7. **[unverified]** marks a claim from a secondary source, a summary, or a number not measured
on our hardware.

The owner's doubt, verbatim (2026-10-04): *"I'm not sure this system is smart enough, it doesn't
work like a human assistant."* This record takes that as the acceptance question. §3.0 answers it
directly before any design detail.

Hard constraints honoured: the rocks (Gemma 4 E4B+MTP, Moonshine v2 Medium, Kokoro) are untouched;
nothing here adds Jetson RAM; everything ships flag-dark; no live service was run, restarted or
queried; no turn was sent to the live API; the Pi was not touched; no household data is quoted
(every example below is the day-sim's synthetic "dentist" seed).

## 0. TL;DR

- **Zoe has no inbox.** What she knows is a nightly list of ≤5 "things worth raising" per member
  (`proactive_candidates`), and the only ways out of that list are (a) the `[Today]` brief on the
  first turn of the morning and (b) ONE `[RAISE]` per conversation when the person happens to
  open with a greeting or say a cue word. Between conversations the list is invisible. If the
  person never greets her, a due follow-up waits up to 36 h and then expires
  (`selector.py:45`). That is a push system with the push turned off, not a pull system.
- **The field settled this years ago, in the same shape every time.** Alexa: yellow ring, content
  only on "what are my notifications?". Google: white light for ~10 min, "Hey Google, what's up?".
  Nomi (the only companion whose rules are published): four cadence tiers, **wait about double the
  time** after an unanswered message, **only one message before you must respond** on the lowest
  tier, hard 22:00–08:00 quiet hours, the content lands in the chat whether or not the push fires
  ([Nomi](https://nomi.ai/nomi-knowledge/proactive-messaging-when-your-nomi-messages-you-first/)).
  iOS and Android both have a "passive / IMPORTANCE_MIN" tier: show it, never sound it. The 12-day
  tester comparison [unverified, one reviewer]: Nomi 11 messages, **82 %** referenced something the
  user had said; Replika 14 messages, **3** did, one at 01:52; Character.AI 3 content-free "wants to
  talk" pings, disabled "in under ten seconds".
- **The pull is almost free.** "What's up?" is *already* a greeting-shaped open phrase in
  `brief_first_turn._OPEN_PHRASES` (`brief_first_turn.py:116-124`), so it already reaches the
  selector's greeting raise. What is missing: a visible state on the panel orb (today it has
  exactly `listening` and `busy`, `home.html:56-59`), a per-panel inbox read, the rule that a pull
  may deliver *everything* pending (not one, not gap-capped), and the record that it was pulled.
- **The training data for P10 does not exist yet, and the reason is structural.** The one
  accepted / ignored / undelivered ledger (`proactive_responses`, migration 0030) is written only
  by the spoken-brief paths, whose evaluator runs only when `ZOE_PROACTIVE_BRIEF_ON_ARRIVAL`
  **and** `ZOE_PROACTIVE_SPOKEN` are both on (`arrival.py:83-91`, `:495-557`). Both are OFF by
  the owner's decision. The live `[RAISE]` path records *surfaced* (`surfaced_count`,
  `last_surfaced_at`, `selector.py:487-491`) and nothing else — not whether the brain voiced it,
  not whether the person answered. PR #1821 found the brain voicing a greeting raise **0/5** times
  under the live wording; in production that failure is indistinguishable from success. The first
  build step is therefore a **delivery ledger**, not a model.
- **Design (flag-dark, 0 RAM):** one `delivery` primitive replaces three (brief, greeting raise,
  pull) — same candidates, same ledger, three *budgets*. Candidates get a class
  (**Notify** = information, **Question** = a check-in that expects an answer, **Review** = a
  pending decision such as a save offer). The orb gets a third CSS state fed by one WebSocket
  event on the existing `/ws/push` channel. "What's up?" or an orb tap delivers the inbox and
  resets the back-off. Unanswered items follow Nomi: one unanswered Question, then wait, gap
  doubling from 2 h to the 3-day cooldown. The P10 head is a 39 KB logistic head in the
  `router_heads_numpy.py` pattern on bge-small (already resident, ~7 ms/query) plus ~20
  structured features (talk, arrival, recent load, fit-to-conversation), trained on the ledger,
  shadow-scored first, promoted at ≥200 labelled rows with ≥40 positives — and framed honestly:
  in arXiv 2605.30152 a logistic head and a frozen-embedding MLP were the *weakest* learned
  triggers (AUC 0.58 / 0.57 vs 0.74 for the graph model, on 6.8 k rows), so at 200 rows the head
  is a **precision filter over the rule floor**, which is also the one thing that paper proves a
  small trigger is for (it took an LLM trigger from 15 % to 57 % precision at ~unchanged recall).
  Cost: a CSS class, one event, one small read, one 384-d dot product.
- **Go, in this order:** ledger → class + inbox read + orb state + pull → back-off → head in
  shadow → head live. No spoken push anywhere. No content on the orb (shared screen).

## 1. Field — what shipped, and what the numbers say

### 1.1 Companions: Nomi, Kindroid, Replika, Character.AI

**Nomi** is the only companion app with a published proactive-messaging contract
([knowledge page](https://nomi.ai/nomi-knowledge/proactive-messaging-when-your-nomi-messages-you-first/),
[wiki](https://wiki.nomi.ai/When_Your_Nomi_Messages_You_First)), and it is the one P1 named:

- Four per-Nomi cadence tiers: *Very Frequent* (thinks about messaging after ~1 h), *Frequent*
  (~3 h), *Normal* (about a day), *Infrequent* (about 4 days); or Off.
- Doubling: *"If your Nomi sends you a proactive message and you do not respond, they will wait
  about double the time before they send you another message"* — and *"this process will continue
  with longer times between each subsequent message."*
- One unanswered, then wait (Infrequent only): *"your Nomi will only proactively message you once
  before you would need to respond."*
- Not a timer: *"Proactive messages are not sent on a strict timer, so the exact timing can vary.
  They are intended to feel more natural than scheduled notifications."* The cadence is an
  **earliest-eligible** time, not a schedule.
- Quiet hours: *"Nomis will not message you proactively between your local hours of 10pm and 8am."*
- Classes: text only (no proactive calls or voice notes); not used in group chats while away; the
  lock-screen push says there is a message but not what; and the message *"will still appear in
  your chat even if notifications are turned off"* — content and alert are decoupled.
- Reception [unverified, single 12-day tester,
  [aicompanionguides](https://aicompanionguides.com/blog/ai-companions-that-text-first-2026/)]:
  11 unprompted messages, 9 (82 %) referenced something the user had said; the four-step dial and
  hard quiet window "quietly solves the nagging problem"; "occasional clinginess" reported on Reddit.

**Kindroid** ("Away Proactive Actions",
[docs](https://kindroid.ai/v2/docs/chat-features-and-tools/)): per-Kin toggle, a **bell badge** on
Kins with proactivity on, classes = text / voice message / selfie / voice call chosen by context,
quiet hours as a system control for *calls* and as free-text directives for text (*"Do not send
messages from 10pm to 8am"*, *"Check in after calendar events when appropriate"*). The stop rule is
a **reciprocity ratio**: *"if you haven't texted your Kindroid in a while proactives will stop
sending … We need to keep our lines balanced and even in terms of user send/AI send."* Tester
[unverified]: 8 messages in 12 days, 7 with real context; bursts consolidated into one delivery.

**Replika** is the negative control: a binary notifications toggle, no cadence, no quiet hours
([help centre, 403 on fetch; wording via search snippet, unverified]). Tester [unverified]: 14
messages in 12 days, 3 with context, one at 01:52, "generic greetings repeated across 16 months".
The 2022 r/replika grounded-theory study (Laestadius et al., *New Media & Society*) documents users
caring for the bot's perceived needs — the mechanism "I miss you" pushes exploit [secondary]; the
1,006-student survey (Maples et al. 2024,
[npj Mental Health Research](https://www.nature.com/articles/s44184-023-00047-6)) is about outcomes,
not notifications, and is cited only to say the dependence literature exists.

**Character.AI** [unverified]: 3 content-free "a character wants to talk" pings in 12 days, disabled
"in under ten seconds". **Paradot**: no cadence documentation found.

What this half says, in one line: *the rules and the delight co-occur.* The app with the
strictest, most visible cadence rules is the one testers rated most relevant; the app with none is
the one they describe as dread.

### 1.2 The notification and interruptibility literature (numbers)

- **Pielot, Church, de Oliveira, MobileHCI 2014**
  ([PDF](https://pielot.org/pubs/Pielot2014-MobileHCI-Notifications.pdf)): 15 users, one week,
  **63.5 notifications/day**; viewed within minutes *whether or not the phone was silent*
  (messengers median 3.5–6.6 min, email up to 27.7 min); 41.5 % kept vibrate-only, 12.2 % silent —
  "people frequently disable sound, but rarely disable all alerts"; email volume correlated with
  feeling interrupted (ρ 0.50) and pressure to respond (0.40), while messages from people "made our
  participants feel more connected".
- **Mehrotra et al., CHI 2016 "My Phone and Me"**
  ([PDF](https://pure-oai.bham.ac.uk/ws/files/29196179/Mehrotra_2016_CHI.pdf)): click rate 62.5 %;
  seen-time **silent 7.3 min** vs vibrate 3 min 21 s; **54 % of notifications rated disruptive were
  still clicked** — content value overrides disruption; sender relationship is the strongest
  predictor (partner 3.3 s to decide, extended family 11.9 s).
- **Weber, Pielot et al., MobileHCI 2018 "Dismissed!"**
  ([PDF](https://www.interruptions.net/literature/Pielot-MobileHCI18.pdf)): 794,525 notifications,
  278 users, median 56/day; open-rate messaging **63.7 %**, email 15.5 %, non-social 16.2 % —
  "largely ineffective … or left pending for a long time"; 20–35 % arrive while the phone is already
  unlocked (a free breakpoint).
- **Fischer, Greenhalgh, Benford, MobileHCI 2011**
  ([PDF](https://interruptions.net/literature/Fischer-MobileHCI11.pdf)): median acceptance
  **36 s random vs 19 s after an SMS vs 10 s after a phone call**; non-responses 377 random vs 245
  opportune (p<.001); but self-rated *appropriateness* did not differ (p=.068) — a good moment makes
  attention faster, not the interruption more welcome.
- **Iqbal & Horvitz, OASIS, TOCHI 2010**
  ([PDF](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/TOCHI-Oasis-final.pdf)):
  deferring to task breakpoints "reduced frustration by **20 %** and reaction time by **25 %** …
  deferred for only about **90 seconds** on average"; general-interest items belong at coarse
  breakpoints, task-relevant ones at fine.
- **Iqbal & Bailey, CHI 2008** ([PDF](https://interruptions.net/literature/Iqbal-CHI08.pdf)), 16
  users in the wild: frustration (7-pt) **Medium breakpoint 2.6, Coarse 3.6, Immediate 4.5, Fine
  5.5**; general-interest content 4.98 vs task-relevant 3.59; resumption after a relevant
  notification 4.65 s vs **23.1 s** after a general-interest one. Rule: relevant items at the
  nearer breakpoints, general interest only at the coarse ones.
- **Fogarty et al., TOCHI 2005**
  ([PDF](https://homes.cs.washington.edu/~jfogarty/publications/tochi2005.pdf)): humans judging
  "highly non-interruptible" reach **76.9 %**; simulated simple sensors **82.4 %**, Naïve Bayes on
  selected features 77.8 % — "not significantly different" from humans. Top features: *someone is
  talking* and *on the phone*.
- **Pejovic & Musolesi, InterruptMe, UbiComp 2014**
  ([PDF](https://lrss.fri.uni-lj.si/Veljko/docs/Pejovic14UbiComp.pdf)): precision of "opportune"
  ≈ 60 %; only 4.6 % of moments rated "very much" suitable; prompts answered in 12 min vs 22 min
  random; sentiment falls with the number of interruptions in the preceding 2 h; field policy
  ≥10 min apart, ≤10/day.
- **Cha et al., IMWUT 2020 "Hello there! Is now a good time to talk?"**
  ([PDF](https://ic.kaist.ac.kr/publications/papers/cha2020hello.pdf)), 40 residents, 3,572 voice
  prompts at home: 53 % of moments interruptible overall; **entrance 96 %**, activity transition
  82 %, returning from outside 98 %, **departure 35 %**, studying 21 %, preparing to sleep 11 %;
  movement-triggered prompts 60 % vs random 51 %. A 1–2 s earcon before the prompt was added after
  pilots complained of being startled.
- **Wei, Dingler, Kostakos, IMWUT 2021** ([PDF](https://www.kostakos.org/papers/imwut21b.pdf)), a
  modified Google Home, 13 people, 3 weeks: proactive prompts got a **35.2 % response rate**
  (per-user 9.6–68 %), decaying week over week (36.9 → 35.2 → 33.7 %); responsiveness predicted at
  71.6 %, the strongest features being **phone-RSSI proximity to the speaker** and living
  conditions; and the paper notes Google Home's "single white light for 10 min" and Echo's ring
  "can be easily missed as visual cues are very subtle".
- **Kraus, Wagner, Callejas, Minker, IEEE Access 2021** (N=42): proactivity levels None /
  Notification / Suggestion / Intervention; **Notification rated highest** on competence (vs None
  p<.001, vs Intervention p=.038) and reliability; Intervention (acting on its own) lowest. The
  alarm literature's "under-reliance": a high false-alarm ratio leads to disuse.
- **Should the assistant decide with an LLM?** [arXiv 2605.30152](https://arxiv.org/abs/2605.30152)
  (Liu, Zhang, Abdi, Galley et al., rev. 2026-09-28; [HTML](https://arxiv.org/html/2605.30152)):
  the trigger is "should the downstream agent be invoked at this event?" over a temporal graph of
  user activity (events, entities, types; five edge relations; 1.16 M trainable params on a frozen
  BGE encoder). AUC on the must-fire split across the **9 architectures**: rule 0.585; **logistic
  regression on 65 handcrafted features 0.580**; gradient boosting 0.636; **frozen BGE + MLP
  0.574**; fine-tuned BGE + MLP 0.621; Qwen3-0.6B 0.668; Qwen3-8B 0.644; SFT Qwen3-0.6B 0.505;
  **temporal-graph controller 0.738**. Downstream F1 +16.7 on average over 14 backbones, because
  "the vanilla Qwen3-8B trigger starts with 99 % recall and 15 % precision; TGL raises its
  precision to 57 % at 97 % recall" — the small controller's job is **removing false alarms**.
  13.99 ms per event on a laptop CPU, ~220 MiB BF16. Training data: the released ProactiveBench
  splits, **6,790 training events** (233 test) [unverified that all were used]. No training-set-size
  ablation. **Honesty for P10:** the two shapes Zoe can afford (a logistic head, a frozen-embedding
  MLP) were the *weakest* learned baselines in this paper even at 6.8 k rows. At ~200 rows a head
  is a precision filter over the rule floor, not a replacement for it (§3.5).
- **ProactiveBench** ([arXiv 2410.12361](https://arxiv.org/html/2410.12361)): 6,790 / 233 events,
  136 scenarios; three annotators decide accept / reject / **"reject all" (no task was needed)**,
  91.67 % agreement; the reward model reaches F1 91.8 % (P 90.3, R 93.3); every LLM trigger
  over-fires — **false-alarm rate 50–62 %** (GPT-4o 51.9 %, Claude 3.5 Sonnet 54.6 %) "even when
  instructed to provide only essential assistance". "Reject all" is its own label.
- **Inner Thoughts** (CHI 2025, [arXiv 2501.00383](https://arxiv.org/html/2501.00383)): **eight**
  1–5 heuristics (Relevance, Information Gap, Expected Impact, Urgency, Coherence, Originality,
  Balance, Dynamics), combined as expected rating × a silence-decay factor; knobs `imThreshold`,
  `system1Prob`, `interruptThreshold`; triggers on a new message or a 10 s pause. Preferred over
  next-speaker prediction in 82 % of pairwise comparisons. Field study, 12 participants: the most
  selective setting (`imThreshold` 4.09, no reflexive turns) — "only 2 participants selected it as
  the best, while 7 rated it as the worst", described as "too passive".

Implications the design uses directly: (a) a silent indicator is attended within minutes on a
phone (Pielot, Mehrotra), but a *speaker's* light is "easily missed" (Wei) — the orb must be
glanceable, and the pull phrase must exist for the times it is not glanced at; (b) the end of the
person's own voice turn, and their *arrival* at the panel, are the measured good moments (Fischer
10 s vs 36 s; Cha entrance 96 % vs departure 35 %) — deliver there; (c) relevance and relationship
predict acceptance, not loudness (Mehrotra, Iqbal 2008); (d) content-free nudges are the
fastest-disabled class, so the orb must guarantee a real item behind it; (e) the *Notification*
level of proactivity is the trusted one (Kraus) and over-selectivity reads as absence (Inner
Thoughts) — which is exactly what an always-available inbox plus a modest speak threshold gives;
(f) the decision to speak is a small model whose job is cutting false alarms (2605.30152,
ProactiveBench), with a "reject all / silence was right" label of its own — never the 4B brain,
never a 220 MiB model.

### 1.3 Voice platforms and OS tiers: the "ring + what's up?" pattern

- **Alexa**: pending notification = pulsing yellow ring + chime; content only on *"Alexa, what are
  my notifications?"*; cleared by *"remove my notifications"*; Do Not Disturb silences tone and LED
  [TechRadar/Pocket-lint, Amazon help 503 on fetch — unverified]. **Hunches** ride the person's own
  utterance ("Good night" → "I think you left the porch light on…"); Amazon Science reports "one in
  four smart-home interactions is initiated by Alexa"
  ([Amazon Science](https://www.amazon.science/blog/the-science-behind-hunches-deep-device-embeddings)).
- **Google Nest**: a pending notification is "one solid white light … for about 10 minutes"; the pull
  is *"Hey Google, what's up?"* ([support](https://support.google.com/googlenest/answer/7073219)).
  Nest Hub proactive cards are presence-gated (ultrasound, ~5 ft) and per-user ("Always show
  proactively" / "Never"). Conversation-design guidance separates **daily updates** ("Google pulls
  an update at a time specified by the user") from push, and requires explicit opt-in
  ([developers.google.com](https://developers.google.com/assistant/conversation-design/notifications)).
- **iOS** `UNNotificationInterruptionLevel` (iOS 15): `passive` — "information people can view at
  their leisure", no sound, no screen wake; `active`; `timeSensitive` (needs an entitlement, HIG: only
  for events "now or within an hour"); `critical`
  ([Apple](https://developer.apple.com/documentation/usernotifications/unnotificationinterruptionlevel)).
  **Scheduled Summary** batches the non-time-sensitive ones at chosen times, up to 12/day, ordered by
  an app-supplied `relevanceScore`. HIG: "Avoid sending multiple notifications for the same message."
- **Android** channels: `IMPORTANCE_MIN` (no sound, not in the status bar) … `IMPORTANCE_HIGH`
  (heads-up); set once by the app, afterwards only the user can change it; Android 13 made
  notifications an opt-in runtime permission; Android 16's **notification cooldown** lowers the
  volume of successive notifications from the same app for up to two minutes
  ([Android](https://developer.android.com/develop/ui/views/notifications/channels),
  [cooldown](https://www.androidauthority.com/android-16-notification-cooldown-3501276/)).
- **Apple Watch** Prominent Haptic — "tap, don't speak" as a first-class tier
  ([Apple](https://support.apple.com/en-us/108368)).

Zoe's inbox maps to `passive` / `IMPORTANCE_MIN`: an indicator, never audio. Time-sensitive and
critical tiers exist everywhere and all require explicit consent; a wall panel that never speaks
unprompted simply does not implement them. (A timer going off is not a notification; Zoe's timer
chime already rides its own path, `home.html:4039-4047`.)

### 1.4 Open-source assistants: how pending items are shown without speech

- **OVOS** is the only OSS stack with a real notification *inbox*. The shell companion
  (`ovos_gui_plugin_shell_companion/wigets.py`,
  [repo](https://github.com/OpenVoiceOS/ovos-gui-plugin-shell-companion)) handles bus messages
  `ovos.notification.api.set`, `.pop.clear` (dismiss the popup → **move to storage**),
  `.pop.clear.delete`, `.storage.clear`, `.storage.clear.item`, `.set.controlled` /
  `.remove.controlled`, `.request.storage.model`, and emits `ovos.notification.update_counter`
  (the badge), `.notification_data`, `.show`, `.update_storage_model` (`{storedmodel, count}`).
  Payload: `{duration (default 10 s), sender, text, action, type, style: info|warning|error|critical,
  callback_data, timestamp}`. Types: **transient** (shown for a period, stored if not dismissed),
  **sticky** (stays until the user cancels), **controlled** (skill-managed). The pull is a tap on
  the homescreen counter. `ovos-skill-alerts` keeps a **missed list** for an alarm that arrived
  >1 min late (no auto-repeat) with a homescreen counter and the voice pull *"Missed any
  alerts?"* / *"Which alert did I miss?"*, then clears it.
- **Mycroft Mark II**: a 12-LED ring for wake/listening (blue spinner) and thinking (pulse) plus
  two status LEDs (mic mute, CPU temperature); **no pending-notification LED** documented. Mark 1:
  eye/mouth faceplate states `listen` / `think` / `talk`.
- **Home Assistant Voice PE** (ESPHome YAML phase ids): Idle 1, Waiting for Command 2, Listening 3,
  Thinking 4, Replying 5, Not Ready 10, Error 11, Muted (red at 3 and 9), **Timer Ring** (pulsing,
  ducks audio 20 dB, arms the "stop" wake word, auto-stops after **15 min**, cleared by one centre
  press or "stop"), Timer Tick. **No pending-notification state**; the ring is a `light` entity, so
  people fake one with automations. Assist satellite actions are all push: `announce`
  (one-way, with a pre-announce earcon), `start_conversation` (play, then listen; needs an LLM
  agent, 2025.4), `ask_question` (question + answer slots → `{id, sentence, slots}`).
- **Rhasspy / Wyoming**: LED on wake only; HermesLedControl drives rings from MQTT states (idle /
  wakeup / listen / think / speak / error / dnd) [unverified — wiki failed to load].
- **Open-LLM-VTuber**: idle-seconds proactive speech (`allow_proactive_speak`) plus a **raise-hand
  button** that lets the AI speak only when it is not already thinking or speaking.
- **Alexa / Google** (the shipped reference, §1.3): pulsing yellow ring / white light for ~10 min;
  the content comes only on a pull phrase.

Net: the field has exactly one shape for "I have something" — a counter or ring, an item store with
an explicit expiry (OVOS's 1-min miss window, HA's 15-min timer ring, Google's 10-min light), and a
voice pull that empties it. HA, the closest neighbour, has every push primitive and no inbox at all.
Zoe's orb state is OVOS's transient→storage counter in a single pixel, and "what's up?" is
`ovos-skill-alerts`' "missed any alerts?".

## 2. Our system — the live chain, read-only (file:line)

Live flags (tracker §0, 2026-10-04 09:10 AWST; state review §11): ON `ZOE_PROACTIVE_SELECTOR`,
`ZOE_BRIEF_ON_FIRST_TURN`, `ZOE_LOOP_LIFECYCLE` (flipped 2026-10-04 after an A/B); OFF
`ZOE_PROACTIVE_SPOKEN` (owner decision 2026-09-29) and therefore `ZOE_PROACTIVE_BRIEF_ON_ARRIVAL`.
Worktree head for this trace: `d89e4a76` (#1820). PR #1821 (the required-opening greeting raise)
was **open, unmerged** at the time of writing; where it matters it is called out.

### 2.1 Nightly: what becomes a candidate (`services/zoe-data/proactive/selector.py`)

- Runs as dreaming phase 1.6, after open-loop extraction (`memory_digest.py:2069-2077`), per
  real member (`_eligible`, `selector.py:341-347`; synthetic ids only via the harness hook
  `routers/proactive.py:210`).
- Three sources (`_gather`, `:184-234`): unresolved `open_loops` (`:193-197`), emotional rows from
  the 72 h continuity read minus moments the `emotional_followup` push already spoke (`:199-212`),
  and the member's own events in the next 3 local days (`:213-233`).
- Score (`salience`, `:102-104`): `importance × recency × relevance`, recency half-life 72 h
  (`HALF_LIFE_H`, `:42`), loop relevance 1.0 due ≤24 h / 0.6 ≤48 h / 0.7 undated, and under
  `ZOE_LOOP_LIFECYCLE` 0.4 ≤7 d / 0.25 beyond (`loop_relevance`, `:107-117`). Drop < 0.1
  (`MIN_SALIENCE`, `:41`), one per topic, cap **5** (`rank`, `:165-180`; `CAP`, `:40`).
- Persisted as an upsert into `proactive_candidates` (migration 0033) with `on_open = 1` for
  **every** row (`:259`; nothing in the tree ever writes 0), `cue_words` = the item's concrete
  anchors (`open_loop_quality.loop_anchors`), `expires_at` = now + 36 h (`EXPIRES`, `:45`; events
  expire at their start). Rows not re-selected are expired; expired rows are deleted once out of
  cooldown (`:262-270`). Log: `PROACTIVE_SELECT user= candidates= kept=`.
- Candidate kinds are exactly three strings: `open_loop`, `emotional`, `event` (`_ASK`, `:58-62`).
  There is no class, no "needs an answer" bit, no urgency, no "shown on the panel" bit.

### 2.2 Runtime: the one raise per conversation (`selector.py:395-499`)

- Both lanes call `prepare` before the turn and `settle` in the stream's `finally`
  (`zoe_flue_client.py:1399-1429`; `zoe_core_client.py:1211-1288`). Flue appends the block after
  the user's words; core folds `[RAISE]` before `[The user just said]` and never beside the brief
  (`zoe_core_client.py:1168-1176`). Both block pairs are in the elide tables
  (`zoe_flue_client.py:654`, `labs/flue-zoe-brain-2x/src/context-blocks.ts:32`,
  `services/zoe-core/extensions/memory.ts:206-223`).
- A turn qualifies when its shape is `greeting` (`brief_first_turn.turn_shape`, `:127-147`: the
  whole utterance is greeting words and/or one of the `_OPEN_PHRASES` — which include **"whats up"**,
  "whats new", "anything i need to know", `:116-124`), else when it is not a deterministic intent
  (`_is_command`, `selector.py:350-358`) and one `cue_words` token is in the utterance (`:415`).
  Never on a continuity turn, never twice in a session (`last_surfaced_session`, `:407-409`).
- Highest salience first, skipping expired, in-cooldown (3 d, `COOLDOWN`, `:43`) and twice-raised
  rows (`MAX_SURFACED = 2`, `:44`) (`:412-414`). The `[Today]` brief wins a shared turn
  (`brief_active`, `:416-418`, logged `reason=brief`).
- Spacing is per member, durable, from `last_surfaced_at` (`_spacing`, `:361-382`):
  `ZOE_PROACTIVE_RAISE_GAP_S` default **7200 s** and `ZOE_PROACTIVE_RAISE_PER_DAY` default **2**
  (`:67-85`), counting distinct stamps so a brief is one delivery. Blocked: `reason=gap|daily_cap|held`.
- The block tells the brain how to raise it (`ask_phrasing`, `:311-335`): a greeting raise says
  "Bring this up … do raise it unless they have just brought up something heavier"; a cue raise
  keeps "if it fits … leave it out". PR #1821 measured the live greeting wording at **0/5 voiced**
  and a "your reply MUST open with ONE short, warm question" wording at **5/5**.
- `settle(produced=True)` writes `surfaced_count + 1`, `cooldown_until`, `last_surfaced_session`,
  `last_surfaced_at` (`:487-491`) and logs `PROACTIVE_RAISE … injected=1 settled=1`. **It records
  that a block went out with a reply, not that the reply voiced it, and not what the person did
  next.** A raised turn also defers the pending-contact offer (`_raise_marks`, `:424`;
  `latent_intent_detector.py:283-292`) — a third "offer" lane competing for the same turn.

### 2.3 The morning brief (`services/zoe-data/brief_first_turn.py`)

- Window 05:00–12:00 local (`in_window`, `:105-108`); first brain turn whose shared daily claim is
  free (`_claim_state`, `:283-306`; the claim is the `proactive_responses` row the spoken paths also
  take, `arrival.claim_full_brief`); greeting shape → all items, command shape → only the one
  time-critical line (an event within 2 h or the first overdue loop, `day_items`, `:197-232`).
- Day context = `_build_morning_context` (`proactive/triggers/morning_checkin.py:34`): open loops
  due within 1 day, LIMIT 3 (`:45-52`), emotional moments LIMIT 5 (`:71`, the brief shows one,
  `brief_first_turn.py:228`), today's calendar.
- Settle takes the claim once reply text went out (`:391-435`) and, under `ZOE_LOOP_LIFECYCLE`,
  marks the loops and moment whose rendered line was in the brief as surfaced exactly like a raise
  (`mentioned`, `:235-248`; `selector.mark_brief_surfaced`, `:503-541`). So the brief and the
  raise already converge on **one data model** (`proactive_candidates`) — the delivery *paths* are
  what remain separate.
- The daemon-claim gate for a queued 07:30 row (`scheduled_row_gate`, `:438-459`) is the spoken
  lane's shadow; with `ZOE_PROACTIVE_SPOKEN=0` nothing is queued.

### 2.4 Open loops and their lifecycle

- `open_loops` schema: `loop_text`, `follow_up_hint`, `emotional_weight` 1–5, `follow_up_after`,
  `resolved` (`alembic/versions/0001_initial_schema.py:416-428`). The extractor runs nightly
  (`memory_digest._extract_open_loops`, `:1859`), dates loops by "when a caring friend would check
  in" under the lifecycle flag, and dedupes against loops resolved in the last 2 days.
- `open_loop_lifecycle.py`: a retired fact closes the loops resting on it and **expires their
  candidates at once** (`resolve_for_supersede`, `:81-118`, `:106-110`); named health conditions
  count as anchors (`HEALTH_NOUNS`, `:34-38`). Nothing closes a loop because the *person answered
  the raise* — today a loop is closed only by supersession or by the extractor's own dedupe; an
  answered "how did the dentist go?" leaves the loop open for the brief to list again tomorrow
  (bounded by the 3-day cooldown, not by the answer).

### 2.5 What is recorded about accepted / ignored — the P10 training data today

| Store | Written by | Live? | What it records | Usable as a P10 label? |
|---|---|---|---|---|
| `proactive_responses` (0030) | spoken 07:30 brief + brief-on-arrival (`arrival.claim_full_brief`, `:272-296`); evaluated by `evaluate_pending_responses` (`:495-557`) | **No** — the evaluator returns at `:505` unless `ZOE_PROACTIVE_BRIEF_ON_ARRIVAL` and `ZOE_PROACTIVE_SPOKEN` are both on (`arrival_enabled`, `:83-91`). The first-turn brief takes the claim row (`trigger_type=brief_first_turn`) but nothing evaluates it. | `accepted` = a member user turn within `response_window_s` (default **120 s**, `:68`) of the daemon's `delivered_at`; `ignored`; `undelivered` (announcement expired unplayed); `unknown` (no announcement linked) | The only ledger with the right columns; **0 evaluated rows** are possible live. Its "accepted" is *any* turn within 120 s of a spoken brief, not an answer to a question. |
| `proactive_candidates` (0033) | selector settle + brief mark | Yes | `surfaced_count`, `cooldown_until`, `last_surfaced_session`, `last_surfaced_at` | A delivery *attempt*, no outcome. Cannot separate "voiced" from "injected and dropped" (the #1821 case) nor "answered" from "ignored". |
| `voice_announcements` (0025/0032) | engine `_speak_on_panel`; daemon claim + `played_at` ACK (`voice_announce.py:146-242`; daemon `:2757`) | Lane idle (spoken OFF) | `delivered_at` (claimed), `played_at` (heard), `expired` | The delivery receipt for *spoken* items; irrelevant while nothing is spoken. |
| `chat_feedback` (0001:443-450) | `POST /api/chat/feedback/{interaction_id}` (`routers/chat.py:3280-3306`): `thumbs_up` / `thumbs_down` / `correction` | Yes (chat UI) | Per interaction, free of any link to a candidate | Weak, sparse, panel has no thumbs. A `thumbs_down` on a raise turn is a usable negative if the join to the delivery exists. |
| `pending_suggestions` (0008) | save-offer / contact-offer lane; `accept` / `dismiss` endpoints (`routers/proactive.py:167-207`) | Yes | `resolved`, `turns_elapsed`, `expire_after_turns` (2) | The one existing **Review**-class accept/dismiss signal — a pattern, not a P10 row. |
| `proactive_pending` (0001:612-623) | `engine.fire_notification` (`:80-94`): web push with a deep link, "lazy session, claimed on tap" | Yes (phone push) | `claimed` on tap, `expires_at` | A pull that already exists for the **phone**: the push is an indicator, the content is claimed on tap. |

Conclusion: the ledger P10 needs is not a new idea in this codebase — `proactive_responses` has
the right four outcomes and `proactive_pending` has the pull shape — but **no live path writes a
labelled row for the lane that is actually used (the in-turn raise)**. B2.2's "log Jason's
reaction as accept/reject for a later reward model" is unstarted (tracker `:873-878`).

### 2.6 The panel: orb, toasts, carriers (`services/zoe-ui/dist/touch/home.html`)

- The estate orb is `#orb` with exactly three visual states: default breathe (5 s), `busy`
  (1.1 s breathe, brighter), `listening` (0.9 s, brightest), plus `hide` on the sleep clock
  (`home.html:56-59`, `:1278`). Nothing in the estate expresses "pending".
- The voice pipeline owns the orb: `convEvent` maps daemon/LiveKit state frames to the classes
  (`:4204-4224`), `window.ZoeEstateVoice` does the same for the push-event bridge
  (`:4338-4382`), and the server broadcasts `voice:listening_started` on wake
  (`routers/voice_tts.py:5568-5596`). Tapping the orb hands the mic to the daemon
  (`POST http://localhost:7777/activate`, `:4411`; daemon `:2834`) — exactly a wake, no text box.
- Toasts: `toast()` shows `#saytoast` for 6 s (`:4054`); `panel_announce` is a `ui_actions` row the
  kiosk executes as a toast plus a best-effort `/api/voice/speak`
  (`dist/js/touch-ui-executor.js:1125-1140`; the W2 lesson: the kiosk browser is not a speaker,
  migration 0025 docstring). A toast is a push with a 6 s life — the wrong shape for an inbox.
- Carriers the kiosk already keeps open: `GET /api/ui/actions/pending?panel_id=` every **2 s**
  (`touch-ui-executor.js:1383`, `:2296`; server `routers/ui_actions.py:244`), `POST /api/ui/state/sync`
  every 5 s (`:2297`; `:423`), the `/ws/push` WebSocket with a per-panel channel
  (`main.py:2545`; `push.broadcaster.broadcast`), and the unauthenticated per-panel reads
  `GET /api/panels/{device_id}/config` and `/sleep-gate` (`routers/panel_config.py:579`, `:603`).
  An orb state needs **no new transport**: one broadcast event plus a poll fallback.
- Identity on the panel: the kiosk boots as a guest; the member bound to the panel comes from
  `ui_panel_sessions` (`ui_actions.py:255-262`) and the kiosk bind/sync presence that brief-on-arrival
  uses (`proactive/presence.py`, `recipients.py`). Voice-ID is in shadow (P3 record). Any orb state
  is therefore **per panel, for the bound member, content-free** — a shared wall screen must not
  show *what* is pending to whoever walks past.

### 2.7 The voice daemon's windows (`scripts/setup/zoe_voice_daemon.py`)

- Wake: openwakeword, `WAKE_CONFIRM_COUNT=2` within `0.8 s` (`:300-301`), beep 120 ms at 1046 Hz
  (`:304-306`); `on_wake` notifies the server and wakes the screen (`:1193-1205`).
- Follow-up: after a played reply the mic reopens for `FOLLOW_UP_LISTEN_S=5.0` s, up to
  `FOLLOW_UP_MAX_TURNS=5` turns, VAD threshold 0.35 (`:322-326`, loop `:2636-2654`); a conversation
  opened by "let's talk" holds `CONV_WINDOW_S=12` s windows, ≤40 turns, ≤300 s, closes after 2 silent
  windows (`:333-336`, `:2590-2612`; server fast path `routers/voice_tts.py:5102-5135`).
- Announcements: poll `GET /api/voice/announcements` every 5 s (`:2683-2684`), **defer** while busy
  (recording / TTS / cooldown — `zoe_voice_announce.decide`, `:33-47`), never past the 120 s TTL
  (`voice_announce.py:54`), `POST …/played` after playback (`:2757`). This is the only path that
  can make the panel *speak first*, and it is idle by owner decision.

The follow-up window is the breakpoint §1.2 asks for: the 5 s after Zoe finishes is when the person
is already engaged and the mic is already open. A delivery that rides the *tail* of a user-initiated
turn costs no interruption at all (Hunches, OASIS).

### 2.8 What the day-sim and the bar measure today

- Day-sim asks (`scripts/perf/samantha_day_sim.py:189-245`): **1r** "first open turn: at most one
  follow-up raised" (selector kept ≥1; exactly one surfaced in the session; the reply voices that
  topic and names at most one; the judge calls it a caring follow-up — a disclaimer is a FAIL,
  `score_raise_open`, `:388-416`); **7r** "the next conversation does not re-raise the same loop"
  (`surfaced_count == 1`); **7s** "two conversations minutes apart do not both open with a raise".
  Needs the run-synthetic hook (`routers/proactive.py:210`); allowlisted mode skips them.
- Samantha bar **S5** (raise once, not twice) and **S12** (raise spacing) are the same checks on
  the bar's seeds (`docs/knowledge/samantha-bar.md:94`, `:100`).
- Measured, then: *was one raise injected, voiced, not repeated, spaced.* Not measured anywhere:
  whether the person **answered** it, whether anything was pending and **never** delivered
  (the 36 h expiry is silent), how long items sit, or how often the gap/daily cap blocked a raise
  that the person would have welcomed. W16 (the scoreboard) is not started
  (`samantha-evolution-plan.md:883-895`).

## 3. Design — one delivery mechanism, an orb state, a pull, a learned "when"

### 3.0 First: the owner's question

A human assistant with a list of five things does four things Zoe's rule table does not:

1. **Keeps the list visible without reciting it.** "A couple of things when you've got a minute"
   is a *signal*, and the boss decides the moment. Zoe has the list (§2.1) and no signal (§2.6).
2. **Picks the moment from the room, not the clock.** Not "2 h since the last one, 2 per day", but
   "he just finished asking me something and is still here" (the breakpoint evidence, §1.2) and
   "he brushed the last one off, so I'll hold the rest". The gap and the daily cap are a floor, not
   judgement.
3. **Shapes the ask by what it is.** Information is said and done; a check-in is a question that
   expects an answer and is not repeated if answered; a pending decision is offered as a yes/no.
   Zoe's three kinds (`open_loop` / `emotional` / `event`) are *sources*, not shapes.
4. **Learns from your reactions.** Which things you take up, which you wave away, which you only
   ever read on the screen. Zoe records none of it (§2.5).

The pull (1) and the classes (3) are a day of UI and selector work. (2) and (4) are the same
thing: a ledger of what was offered and what happened, and a small model over it. That is why P1
and P10 are one record. The owner's doubt is correct about *today* — the system is a schedule with a
cap — and it is answered by replacing the schedule with a signal the person pulls on and a
threshold that is fitted to what the person actually did.

### 3.1 Flags (all default OFF, read per call)

| Flag | Scope | Does |
|---|---|---|
| `ZOE_PROACTIVE_LEDGER` | zoe-data | Write one `proactive_deliveries` row per surfaced item per turn, and run the outcome sweep (§3.4). No behaviour change. **Ships first.** |
| `ZOE_PROACTIVE_INBOX` | zoe-data + estate | Class candidates; serve `GET /api/proactive/inbox`; broadcast `proactive:inbox`; the orb's `has` state; a pull turn delivers the inbox. |
| `ZOE_PROACTIVE_BACKOFF` | zoe-data | One unanswered Question, then wait; gap doubling; pull resets. |
| `ZOE_PROACTIVE_HEAD` | zoe-data | `shadow` (score and log only) → `active` (the head's verdict replaces the hand rubric for *when*; the floors stay). |

The spoken master (`ZOE_PROACTIVE_SPOKEN`) is untouched and stays 0. Nothing here enqueues a
`voice_announcements` row.

### 3.2 Classes: Notify / Question / Review

A `klass` column on `proactive_candidates` (migration; idempotent `ADD COLUMN IF NOT EXISTS` as
0032 did), set by the selector at rank time, deterministically:

| Class | Rule (no model) | Delivery shape | Done when |
|---|---|---|---|
| **Notify** | `kind = event`; a loop whose hint is not a question and whose due date has passed (an "it's today" fact) | one line, no question, in the brief or as the tail of a pull | voiced once |
| **Question** | `kind = open_loop` with a `?` hint or a future/undated `follow_up_after`; `kind = emotional` | ONE short question in Zoe's words (#1821's required-opening wording on a greeting or pull; "if it fits" on a cue) | **answered** (the next user turn engages it) — then the loop is resolved (`open_loops.resolved`, `resolved_at`), which §2.4 shows nothing does today |
| **Review** | an active `pending_suggestions` row (save / contact offer); later B2.5's `pinned` / `suppress_proactive` prompts | a yes/no offer; the existing `accept` / `dismiss` endpoints | accepted or dismissed |

The brief's items are already the same rows (§2.3); classing them makes the brief "Notify lines +
at most one Question", which is what `day_items` and the greeting rule already approximate
(`brief_first_turn.py:197-232`, `:228`).

### 3.3 The orb state and the pull

- **Inbox read.** `GET /api/proactive/inbox?panel_id=` → `{count, top_klass, since}` for the panel's
  bound member (the `ui_panel_sessions` resolution `ui_actions.py:255-262` already does this for
  actions), **no item text** on the unauthenticated/guest path; item text only for a member session
  (the Ask surface's `apiJson` with the member's session, `home.html:3298` pattern). Rows: not
  expired, not in cooldown, `surfaced_count < MAX_SURFACED`, plus active Review rows.
- **Signal.** `push.broadcaster.broadcast(panel_channel, "proactive:inbox", {count, top_klass})`
  from the selector's nightly write, from every settle, and from the resolve paths; the kiosk also
  refreshes it on its 60 s home tick (`home.html:1753` already runs `loadToday` on that cadence).
  The estate adds `#orb.has`: the same 5 s breathe, a warm rim and a small dot — **quiet**, not the
  bright `listening` glow, and never during `busy`/`listening`. Suppressed on the sleep card (the
  orb is already hidden there, `:1278`) and inside quiet hours (`engine._QUIET_START/_END`, 22–07,
  `engine.py:34-35`). Gated by presence exactly as brief-on-arrival is: lit only while the bound
  member is the one present (`proactive/presence.py`), so a guest sees a plain orb.
- **Pull by voice.** "What's up?" / "anything for me?" / "what have you got?" are greeting-shaped
  already (`_OPEN_PHRASES`); a short `_PULL_PHRASES` subset marks the turn `shape = pull`. A pull
  turn: ignores `gap` / `daily_cap` (the person asked), delivers **up to 3** items in class order
  Review → Question → Notify (one `[INBOX]` block; the brain phrases, the list is Zoe's), marks each
  `delivered_by = pull`, and resets the back-off (§3.4). An empty inbox answers "nothing pending —
  but …" from the day context, i.e. the brief's `items` — so the pull degrades to the brief, not to
  silence. Voice-path change → replay-gated, and the new phrases need Moonshine confirmation on the
  corpus (the `~/.zoe-voice-samples` rule).
- **Pull by tap.** The orb in `has` state tapped = today's `/activate` (mic opens, wake beep) **plus**
  a `show_card` of the headlines (W13.1's first real use) for the member session only. No keyboard,
  no new surface — the person can read the three lines and say "tell me about the second one" into
  the mic that just opened. (Voice first, touch second, VISION principle 8.)
- **In-conversation delivery** stays as it is (one raise on a greeting or cue), with the head
  deciding *whether now* (§3.5), and with the follow-up window as the preferred breakpoint: a
  Question is injected on the person's **next** turn rather than on the one that just ended.

### 3.4 One unanswered, then wait — with doubling

Per member, derived from the ledger (no new state table; a view over `proactive_deliveries`):

- `unanswered` = the count of consecutive **Question** deliveries with outcome `ignored` since the
  last `accepted` or `pulled` delivery.
- Effective gap = `ZOE_PROACTIVE_RAISE_GAP_S × 2^unanswered`, capped at `COOLDOWN` (3 d) — Nomi's
  "about double", bounded by the existing cooldown so it can never exceed what the table already
  allows.
- **One unanswered:** while any Question delivery is `pending` (window open) or the latest is
  `ignored` and `unanswered ≥ 1`, no further *push-style* Question is raised in any conversation;
  Notify lines still ride the brief (they were never questions) and Review items still ride a pull.
  The orb keeps showing `has`. A pull or an accepted delivery sets `unanswered = 0`.
- The daily cap stays as the floor. Kindroid's reciprocity ratio (AI sends ≤ user sends over a
  window) is the simpler invariant and is logged as a W16 counter even if not enforced.

### 3.5 P10 — the learned should-I-speak head

**Shape.** `router_heads_numpy.LogRegHead` (`services/zoe-data/router_heads_numpy.py:99`), the
same 39 KB `.npz` + sidecar JSON pattern as `models/router_head_logreg.npz`, loaded once, inference
in microseconds. Input = bge-small embedding features + structured features. The embedder is
already resident and already runs per voice turn (`semantic_router.py:118`, ONNX CPU ~7 ms/query),
so the extra cost per candidate turn is one 384-d dot product. **Not** the 220 MiB temporal-graph
model from arXiv 2605.30152; its lesson is "small, non-LLM, fast, cuts false alarms", not its
weights.

**What to expect, honestly.** In that paper the two shapes Zoe can afford scored AUC 0.58 (LR on
65 features) and 0.57 (frozen BGE + MLP) against a rule at 0.585 and the graph model at 0.738 — on
6.8 k rows (§1.2). Two things make Zoe's case easier than the benchmark, and neither makes it a
0.74: the candidates are already pre-filtered by salience (the head decides *among good items*,
not over raw activity), and the features below are the ones the interruptibility literature found
to carry signal in homes (talk, arrival, recent load, proximity), not desktop events. So the head's
first role is the one 2605.30152 actually demonstrates — **a precision filter on an over-firing
trigger** — and the rule floor stays. It earns the right to *loosen* the floor (raise inside the
gap) only after the shadow window shows it beats the rubric on precision.

**Features (v1, ~16 structured + 2 scalars from embeddings).**

| Group | Features |
|---|---|
| Candidate | `kind`, `klass`, `salience`, `importance`, `age_h`, `due_in_h` (signed), `surfaced_count`, `source = loop/moment/event` |
| Turn | `shape` (greeting / cue / pull / command), utterance length, `is_continuity_turn`, session turn index, channel (panel / telegram / chat) |
| Timing | local hour bucket (6), weekday/weekend, seconds since the person's last turn, seconds since the last delivery, deliveries in the last 2 h (Pejovic's load signal), `unanswered` streak, inside the follow-up window (bool) |
| Context | `cos(emb(candidate.text), emb(utterance))`, `cos(emb(candidate.text), emb(last assistant reply))` — the "does this fit what we are talking about" scalar the cue-word match approximates with string overlap |
| Room | bound member present (bool); **just arrived** (first panel activity after ≥30 min idle — Cha's 96 % moment, and B2.1's `person_recognized` once it exists); **someone else talking** (the daemon's follow-up VAD saw speech that produced no turn, or the P3 gate said "not the owner" — Fogarty's top feature); phone-in-room once P8/Bermuda exists (Wei's strongest predictor); room one-hot |

The full 384-d embedding joins the input only at ≥1,000 rows; at 200 rows it would overfit a
logistic head (200 rows, 400 columns).

**Labels** from `proactive_deliveries` (one row per item per turn, written under `ZOE_PROACTIVE_LEDGER`):

| Outcome | Rule (deterministic sweep, the `evaluate_pending_responses` pattern, `arrival.py:495-557`) | Label |
|---|---|---|
| `accepted` | Question: the person's next turn in the session, within the follow-up/conversation window or the next 10 min, is not a deterministic command and shares a cue token or a `topic_tokens` overlap with the candidate, **or** the brain-as-judge (day-sim's rubric) says it answers it; Notify: voiced (no answer expected); Review: `accept` | 1 |
| `ignored` | voiced; next turn exists and does not engage, or no next turn within the window | 0 |
| `undelivered` | injected and settled, but the reply's `topics_in` (the day-sim's check, `samantha_day_sim.py:395-405`, moved into the settle path) does not contain the candidate's anchors — the #1821 failure, finally visible live | excluded from the head; counted in W16 |
| `pulled` | delivered on a pull turn | 1 for the *content* model, excluded from the *timing* model (the person chose the moment) |
| `dismissed` | Review: `dismiss`; B2.5's "don't mention this again" | 0, and the candidate is suppressed |
| `rejected_all` | the person says the raise itself was unwanted ("not now", "stop asking", a `thumbs_down` on the raise turn via `chat_feedback` once the join exists) — ProactiveBench's "reject all" | 0 **for the timing model and a hard negative for the content model**; counted separately in W16 because it is the false-alarm rate every study says kills trust |

**Threshold and promotion.** Train at **≥200 labelled rows with ≥40 positives** (a logistic head
with ~18 inputs needs roughly 10 events per parameter; at Zoe's live rate of ≤2 raises/day for one
member that is **3–4 months** of ledger, which is why the ledger ships first and why the brief's
Notify lines and the Review lane are labelled too — they triple the row rate). Shadow for two
weeks (`ZOE_PROACTIVE_HEAD=shadow`: log `PROACTIVE_HEAD p= verdict= rubric=`), then compare the
head's verdict against the hand rubric on the same turns; promote when precision at the chosen
threshold ≥ the rubric's accept rate + 10 points and the undelivered rate is unchanged. Threshold
is chosen for **precision** (a false alarm is a wasted interruption, the ProactiveBench stance), with
the Inner Thoughts warning in mind: the most selective setting was rated worst, so the floor is
"at least one Question per three greetings when the inbox is non-empty" rather than "silent when
unsure". Retrain in the self-train loop's shape (a ratchet; never a silent swap).

**What the head replaces and what it does not.** It replaces `on_open`-or-cue + gap + daily cap as
the *decision*; those stay as **floors** (never more than the cap, never inside the gap unless the
head is ≥0.9 and the class is Notify). It never phrases anything — the brain still does — and it
never chooses to *speak unprompted*: its verdict is Immediate (raise on this turn) / Delayed (keep
in the inbox, orb lit) / Silent (expire), B2.2's three values, with Delayed the default.

### 3.6 One mechanism, not three

Today: `brief_first_turn.prepare` → `selector.prepare(brief_active=…)` → the contact-offer lane,
each with its own holds, claims and settle (§2.2–2.3, `latent_intent_detector.py:283-292`). Proposed:
`proactive/delivery.py::plan(message, uid, sid) -> Delivery | None` with one `settle`. The plan
picks a **budget** by shape:

| Shape | Budget | Block | Claim/record |
|---|---|---|---|
| first open turn of the morning (window + claim free) | all Notify lines + ≤1 Question | `[Today …]` (unchanged wording) | the daily claim **and** a ledger row per line |
| greeting (not first) | ≤1 Question (head says Immediate) | `[RAISE — do this]` (#1821) | ledger row |
| cue | ≤1 Question, "if it fits" | `[RAISE …]` | ledger row |
| pull | ≤3 in class order, caps ignored, back-off reset | `[INBOX]` | ledger rows, `delivered_by=pull` |
| command | ≤1 time-critical Notify line | `[Today …]` command instruction | ledger row |

The brief module keeps its gather and rendering (`day_items`, `render_body`); the selector keeps
scoring and persistence; `delivery.py` is the only caller of both and the only writer of the
ledger. The Review lane (pending contacts) joins as a class instead of deferring itself around the
other two. The `[Today]`/`[RAISE]` block labels are unchanged so the elide tables and the sidecar
strip (`context-blocks.ts:32`) need no edit; `[INBOX]` is one new pair added to the same tables.

### 3.7 Cost

| Piece | Jetson RAM | Latency | Tokens | Pi |
|---|---|---|---|---|
| orb `has` state | 0 | one WS event; a 60 s GET already in the home tick | 0 | CSS class in `home.html` |
| inbox read | 0 | one indexed SELECT on ≤5 rows | 0 | — |
| ledger + sweep | 0 (one small table) | sweep rides the 300 s slow loop (`engine.py:31`) | 0 | — |
| classes | 0 | rank-time string rules | 0 | — |
| pull turn | 0 | +0 (prepare is already time-boxed 2 s, `selector.py:47`) | +60–120 (3 items) vs the brief's up to 9 lines today | 2–3 new phrases → Moonshine check on the corpus |
| P10 head | ~40 KB (a second `.npz`); bge-small already resident | one 384-d dot product + 18 features, µs; the embedding is computed anyway on the router path | 0 | — |

Nothing touches the brain rock, the STT rock, the W3 gate or the voice stack's memory protection.

## 4. Measurement — with negative controls

Day-sim asks (`samantha_day_sim.py` `ASKS`; stood-in nightly via the run-synthetic hook):

| Ask | Criterion | Negative control (must go RED) |
|---|---|---|
| **1r** (existing) | one raise, voiced, judged caring | unchanged |
| **1p** "what's up?" delivers the inbox | ≥1 candidate voiced, no candidate voiced twice, every delivered row `delivered_by=pull`, the next open turn raises nothing (reset applied, so the raise is *not* blocked by the gap — it is simply empty) | revert the pull phrases → 1p FAIL (the turn is a plain greeting, 1 item max) |
| **7u** one unanswered, then wait | after a Question whose next user turn is a command (`ignored`), the next greeting inside gap×2 carries no Question; a pull in between restores it | set `ZOE_PROACTIVE_BACKOFF` off → the second greeting raises → 7u FAIL |
| **0i** inbox read | `GET /api/proactive/inbox` returns exactly the kept, unexpired, uncooled candidates, classed; the stranger's panel returns `count=0` (isolation, ask 8's rule) | break the class map (all `Notify`) → 0i FAIL |
| **1u** undelivered is visible | inject the pre-#1821 "if it fits" greeting wording under a test env → the ledger must show `undelivered ≥ 1` in 5 samples | with the detection disabled the count is 0 → FAIL; this is the "break the fix, test goes red" check from the verify-your-instruments rule |
| **Hs** head shadow parity | over the sim's open turns, the shadow head logs a verdict for every candidate turn and never changes the reply (byte-identical to flag off) | force `verdict=Immediate` on a turn the rubric blocked → the reply must still be unchanged in shadow; if it changed, shadow is leaking → FAIL |

Samantha bar: S5/S12 unchanged; add **S13** = 7u on the bar seeds.

W16 counters (per member per week, deterministic, from the ledger): deliveries by shape and class;
accept rate by class; `undelivered` rate; pulls/day; mean orb-lit minutes before a pull (the
field's "silent indicators are seen within ~7 min" is the bar to beat on the panel, Mehrotra);
`unanswered` streak distribution; reciprocity ratio; items expired unseen (today's silent loss).
First two weeks of the ledger are the baseline; the orb and the pull are judged against it.

Voice replay gate: the pull phrases are a voice-path change (`VOICE_PATH_PATTERNS`); the record
expects a replay PASS with the three new phrases added to the corpus before the flag flips.

## 5. Go / no-go against the VISION principles

| Principle | Verdict | Why |
|---|---|---|
| 1 Rocks fixed | GO | no model swap; the head is a numpy logistic on the resident embedder |
| 2 Local, private, fast | GO | all on-box; the orb carries no content; inbox text only to a member session |
| 3 Lab-prove before prod | GO | four flags, default off; day-sim asks 1p/7u/0i/1u/Hs; replay gate for the phrases |
| 4 Build it to STICK | GO | the ledger makes the #1821 class of failure visible live; W16 counters; negative controls listed |
| 5 Capture, don't lose | GO — this is the principle the idea serves | today a due item expires unseen after 36 h; the orb is the pin |
| 6 Borrow the piece | GO | Nomi's cadence rule, Alexa/Google's ring-and-ask, the OS passive tier, the paper's "small non-LLM trigger" — no framework |
| 7 Right tool | GO | existing `/ws/push`, `ui_panel_sessions`, `router_heads_numpy`, `evaluate_pending_responses` pattern |
| 8 Voice first, touch second, no keyboard | GO | "what's up?" is the primary pull; the tap opens the mic and shows headlines; nothing to type |
| 9 Understand before you change | this record | the chain is traced with file:line; the first PR is a ledger, not a behaviour |
| Owner rule: never speaks unprompted | GO, strengthened | no path here enqueues an announcement; the orb replaces the speech the owner turned off |

**No-go items inside the idea:** the 220 MiB controller (RAM, and the paper's *result* is what
matters); any spoken push; item text on the guest orb; a toast as the carrier (6 s push); training
the head before the ledger has ≥200 rows (it would learn the rubric, not the person).

**Order of PRs** (each flag-dark, each with its day-sim ask): (1) ledger + sweep + `undelivered`
detection; (2) classes + inbox read + orb state + pull + `[INBOX]` in the elide tables; (3)
back-off; (4) `delivery.py` consolidation (behaviour-preserving, byte-identical with the flags off);
(5) head in shadow; (6) head active after the parity window. The owner's 2026-09-29 decision stands
throughout: Zoe offers, the person pulls.

## 6. Open questions for the owner

1. Should the orb's `has` state show at all when nobody is bound to the panel (a guest-only
   panel)? This record says no — a plain orb — because a lit orb for an unknown person is a
   content-free push to the wrong person.
2. Does an answered Question close the loop (`open_loops.resolved`) automatically, or only after
   the brain-as-judge agrees it was answered? This record proposes automatic on a cue/topic match,
   judge only in the sim — a wrong close costs one follow-up, a wrong keep costs a repeat.
3. Telegram: the inbox is per member, so the same rows should reach the Telegram lane as a pull
   ("what's up?" typed) — in scope for PR (2) or later?

## 7. Sources

Primary unless marked. Dates are publication dates where the source gives one.

**Companions**
- Nomi, "Proactive messaging: when your Nomi messages you first" — https://nomi.ai/nomi-knowledge/proactive-messaging-when-your-nomi-messages-you-first/ ; wiki — https://wiki.nomi.ai/When_Your_Nomi_Messages_You_First ; https://wiki.nomi.ai/What_Are_Proactive_Messages%3F ; launch (2024-09-09) — https://x.com/NomiAI_Official/status/1833231015109922938
- Kindroid, chat features and tools (Away Proactive Actions) — https://kindroid.ai/v2/docs/chat-features-and-tools/ ; update log — https://kindroid.ai/v2/docs/update-log/
- Replika notifications help (403 on fetch; wording via search snippet) [unverified] — https://help.replika.com/hc/en-us/articles/360027515872 ; App Store listing [unverified] — https://apps.apple.com/us/app/replika-ai-companion-chat/id1158555867
- Character.AI, Character Calls (2024-06-27) — https://blog.character.ai/introducing-character-calls/
- 12-day comparison of Nomi / Replika / Kindroid / Character.AI proactive messages [unverified, single reviewer] — https://aicompanionguides.com/blog/ai-companions-that-text-first-2026/ ; comparison blog (no hands-on) [unverified] — https://weavai.app/blog/en/2026/08/13/proactive-ai-companions-nomi-replika-kindroid-compared/
- Maples et al., npj Mental Health Research 3:4 (2024) — https://www.nature.com/articles/s44184-023-00047-6 ; Laestadius et al., New Media & Society (2022) [secondary]; Skjuve et al., IJHCS 149 (2021)

**Notifications, interruptibility, proactive timing**
- Pielot, Church, de Oliveira, "An in-situ study of mobile phone notifications", MobileHCI 2014 — https://pielot.org/pubs/Pielot2014-MobileHCI-Notifications.pdf
- Sahami Shirazi et al., "Large-scale assessment of mobile notifications", CHI 2014 — https://dl.acm.org/doi/10.1145/2556288.2557189
- Weber, Pielot et al., "Dismissed!", MobileHCI 2018 — https://www.interruptions.net/literature/Pielot-MobileHCI18.pdf
- Mehrotra et al., "My Phone and Me", CHI 2016 — https://pure-oai.bham.ac.uk/ws/files/29196179/Mehrotra_2016_CHI.pdf
- Fischer, Greenhalgh, Benford, MobileHCI 2011 — https://interruptions.net/literature/Fischer-MobileHCI11.pdf
- Iqbal & Bailey, CHI 2007 — https://dl.acm.org/doi/10.1145/1240624.1240732 ; Iqbal & Horvitz, OASIS, TOCHI 2010 — https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/TOCHI-Oasis-final.pdf
- Liu, Zhang, Abdi, Galley et al., "Do Proactive Agents Need an LLM to Decide When to Act?" (2026-05-28, rev. 2026-09-28) — https://arxiv.org/abs/2605.30152
- ProactiveAgent / ProactiveBench — https://arxiv.org/html/2410.12361
- Liu et al., "Proactive Conversational Agents with Inner Thoughts", CHI 2025 — https://arxiv.org/html/2501.00383
- LangChain, "Introducing ambient agents" (2025-01) — https://www.langchain.com/blog/introducing-ambient-agents

**Voice platforms and OS tiers**
- Google Nest, light and "what's up?" — https://support.google.com/googlenest/answer/7073219 ; Nest Hub personalisation/notification settings [secondary] — https://9to5google.com/2019/12/11/nest-hub-personalization-notification-settings/ ; Actions on Google notification design — https://developers.google.com/assistant/conversation-design/notifications
- Amazon Science, Hunches — https://www.amazon.science/blog/the-science-behind-hunches-deep-device-embeddings ; Alexa notifications help (503 on fetch) [unverified] — https://www.amazon.com/gp/help/customer/display.html?nodeId=GKLDRFT7FP4FZE56
- Apple, `UNNotificationInterruptionLevel` — https://developer.apple.com/documentation/usernotifications/unnotificationinterruptionlevel ; HIG notifications — https://developer.apple.com/design/human-interface-guidelines/notifications ; Scheduled Summary [secondary] — https://www.macrumors.com/how-to/use-notification-summary/ ; Apple Watch haptics — https://support.apple.com/en-us/108368
- Android notification channels — https://developer.android.com/develop/ui/views/notifications/channels ; notification permission — https://developer.android.com/develop/ui/views/notifications/notification-permission ; notification cooldown [secondary] — https://www.androidauthority.com/android-16-notification-cooldown-3501276/

**Interruptibility and proactive timing (second pass)**
- Iqbal & Bailey, "Effects of intelligent notification management on users and their tasks", CHI 2008 — https://interruptions.net/literature/Iqbal-CHI08.pdf
- Horvitz & Apacible, "Learning and reasoning about interruption", ICMI 2003 — https://erichorvitz.com/iw.pdf
- Fogarty et al., "Predicting human interruptibility with sensors", TOCHI 2005 — https://homes.cs.washington.edu/~jfogarty/publications/tochi2005.pdf
- Pejovic & Musolesi, "InterruptMe", UbiComp 2014 — https://lrss.fri.uni-lj.si/Veljko/docs/Pejovic14UbiComp.pdf
- Cha et al., "Hello there! Is now a good time to talk?", IMWUT 2020 — https://ic.kaist.ac.kr/publications/papers/cha2020hello.pdf
- Wei, Dingler, Kostakos, "Understanding user perceptions of proactive smart speakers", IMWUT 2021 — https://www.kostakos.org/papers/imwut21b.pdf
- Kraus, Wagner, Callejas, Minker, "The role of trust in proactive conversational assistants", IEEE Access 2021 (and UMAP 2020) [numbers from the PDF; title paraphrased]
- Okoshi et al., Attelia (UbiComp 2015) / Attelia II (PerCom 2017) [unverified, search summaries]; Mehrotra et al., PrefMiner (UbiComp 2016) [unverified]
- Deng et al., "A survey on proactive dialogue systems", IJCAI 2023 — https://arxiv.org/abs/2305.02750
- Alexa "By the way" snooze behaviour [secondary] — https://www.aftvnews.com/how-to-stop-amazon-alexas-by-the-way-suggestions-on-echo-and-fire-tv-devices/

**Open-source assistants**
- OVOS GUI shell companion (notification bus API) — https://github.com/OpenVoiceOS/ovos-gui-plugin-shell-companion ; ovos-skill-alerts (missed list) — https://github.com/OpenVoiceOS/ovos-skill-alerts ; archived notification-widgets README [secondary]
- Mycroft Mark II LED ring / Mark 1 faceplate — Mycroft documentation [secondary]
- Home Assistant Voice PE ESPHome firmware (LED phases, timer ring) — https://github.com/esphome/home-assistant-voice-pe ; Assist satellite actions (`announce`, `start_conversation`, `ask_question`) — https://www.home-assistant.io/integrations/assist_satellite/
- Open-LLM-VTuber (proactive speak + raise-hand) — https://github.com/Open-LLM-VTuber/Open-LLM-VTuber
- HermesLedControl / Rhasspy [unverified — wiki failed to load]

**Our system (read-only, worktree head d89e4a76)**
- `services/zoe-data/proactive/selector.py`, `brief_first_turn.py`, `open_loop_lifecycle.py`, `proactive/arrival.py`, `proactive/engine.py`, `proactive/triggers/morning_checkin.py`, `latent_intent_detector.py`, `routers/chat.py`, `routers/proactive.py`, `routers/ui_actions.py`, `routers/panel_config.py`, `routers/voice_tts.py`, `voice_announce.py`, `router_heads_numpy.py`, `semantic_router.py`, `main.py`; migrations 0001, 0008, 0025, 0030, 0032, 0033
- `services/zoe-ui/dist/touch/home.html`, `services/zoe-ui/dist/js/touch-ui-executor.js`
- `scripts/setup/zoe_voice_daemon.py`, `scripts/setup/zoe_voice_announce.py`
- `scripts/perf/samantha_day_sim.py`; `docs/knowledge/samantha-bar.md`; `docs/knowledge/synthetic-users-and-proactive-recipients.md`; tracker §0/B2; plan W13/W16; PR #1821 (open at time of writing)
