---
type: research
title: "Deep reads of comparable projects, 2026-11-29: what Zoe takes from Hermes, OpenClaw, Leon and the voice and memory stacks"
date: 2026-11-29
status: research-only. Fifteen read-only deep reads of third-party projects, each given a slice at the depth the nod study had. Third-party code was read, never installed, built, imported or run. Every fault claimed in Zoe's own code was checked against a clean checkout of main; other "Zoe today" notes come from the reports and were spot-checked only. Nothing in Zoe was built, flipped or deployed. The order of work lives in ROADMAP.md, not here.
description: The second research record after the nod study and the similar-projects survey. Mechanisms worth taking, grouped by theme (approvals and the record of what was done; delivery and restart honesty; scheduled work and night jobs; skills and admission; sessions and several people; memory over time; wake, first turn and turn-taking; testing and evals), each described in our own words with its source file and Zoe's smallest first version; the faults these reads exposed in Zoe, each checked against the code; what not to copy; how the projects' wording works, and their thresholds.
evidence: fifteen deep-read reports and their reading ledgers (fleet run 2026-11-28, kept outside the repo); third-party files cited by path at the clone heads named below (the text is described, not reproduced); Zoe files cited inline as they stand on main at 72685151d.
---

# Deep reads of comparable projects (2026-11-29)

> **Status:** nothing here is built. The order lives in [`../ROADMAP.md`](../ROADMAP.md) (Later, "From the deep reads (2026-11-29)");
> this record is the evidence behind those items. It builds on the [nod deep dive](nod-deep-dive-2026-11-28.md) (its final build
> order, items 1 to 10, is the numbering used below) and the [similar-projects survey](similar-projects-2026-11-28.md).

## What was read and how

The survey gave each project one reader and a handful of files. These reads gave each project a slice and read its core in full.

| reader | third-party project (licence) | slice |
|---|---|---|
| hermes-agent | Hermes Agent (third-party, MIT), clone head 3ef8bf4f | the turn loop, prompt assembly, background review, skill curator, verification, secrets |
| hermes-jobs | Hermes Agent | cron scheduler, missed runs, incidents, delivery queue, suggestions, persistence, evals |
| hermes-gateway-tools | Hermes Agent | gateway sessions, pairing, delivery, restart, tool registry and search, wake word, skills, doctor |
| hermes-rest | Hermes Agent | approvals, live-model evals, memory, session search, compaction prompts, injection scan |
| openclaw-concepts | OpenClaw (third-party, MIT), clone head afc98065f | concept docs plus standing intents, dreaming, provenance, typed decisions, user model |
| openclaw-agents | OpenClaw | one turn: prompt order, empty-turn recovery, loop breakers, queue, stall recovery, compaction |
| openclaw-gateway | OpenClaw | session keys and resets, inbound dedupe, durable input, stop, restart recovery, ingress gates |
| openclaw-cron-hooks | OpenClaw | scheduling, catch-up, interrupted runs, delivery, failure repair, heartbeat |
| openclaw-security | OpenClaw | approvals, secrets, audit, untrusted content, roles, their own static-analysis rules |
| openclaw-skills-plugins | OpenClaw | skill prompt budget, gating, agent-written skills, plugin manifests, tool policy |
| openclaw-qa | OpenClaw | 615 QA scenario files mapped, about 110 read; changelog and troubleshooting |
| leon | Leon (third-party, MIT), clone head 3f53454 | local inference scheduler, Pulse, self-model, controlled route, skill memory, voice |
| voice-stacks | OHF voice-support, Home Assistant Assist core, hassil, speech-to-phrase 1.4.3 (third-party, Apache-2.0); OpenClaw talk | wake verifier, endpointer, pipeline events, template matching |
| turn-taking | Pipecat, Smart Turn, TEN, Unmute, OVOS dinkum, linux-voice-assistant, wyoming-satellite (third-party) | end of turn, interruption, history after a cut, follow-up, idle |
| memory-time | Graphiti, Letta, mem0, LangMem, MemOS (third-party) | how facts are dated, retired, merged and forgotten |

Method, per reader: map the slice, read the core files and their prompts and defaults in full, read the test names for those files
(the team's own list of what once broke), and keep a ledger of every file opened. The clones were treated as untrusted data: read only,
no instruction in them followed, no interpreter run with a clone as its working directory. The Hermes, OpenClaw and Leon clones are
shallow (one commit), so lessons come from issue numbers cited in code comments, not from history. The Letta clone was a stub; its
archived prompts were read from the public repository. Coverage was partial where stated: about 30 OpenClaw concept pages and most
transport code were skipped, with reasons in the ledgers.

**How third-party text is handled here.** Prompts, user-facing strings and docs are described in our own words, with the file path to
read for the original. A handful of very short phrases (each under fifteen words, attributed) are quoted where the exact words are the
point; they are listed at the end of the record.

**Summary.** The biggest finds are not new capabilities but honesty at the edges: a call that may have run is marked unknown and never
re-sent; an empty reply after a write is summarised from the record instead of asking the person to repeat themselves; a late reminder
says it is late; a saved reply holds only what was heard. Four faults of that class are confirmed in Zoe's code (see the table). Where
Zoe is already ahead (kernel tables instead of a model's verdict, per-member memory authority, a durable forget ledger, per-speaker
identity), these projects confirm it: two of them state in their own docs that separating their users is not a security boundary.

## 1. Approvals and the record of what was done

- **A call is saved before it runs; an unanswered side-effecting call is unknown on resume** (Hermes, `agent/turn_tool_round.py`
  `run_tool_round`, `agent/replay_cleanup.py`). The tool-call row is written first, and if that write fails no tool runs. On resume,
  interrupted calls on a closed read-only list (`NO_EFFECT_TOOL_NAMES`; any unknown tool counts as side-effecting) are dropped, and every
  other one gets a synthetic result telling the model the call may already have run, its effect is unknown, and it must inspect the
  current state before trying again. It exists because a dangling restart call was re-issued on every resume, an endless reboot loop.
  *Zoe first version:* when a brain or agent turn dies, classify each unanswered home, list or message call against a closed read-only
  list; anything else becomes "may have run, check state first" plus a kernel `uncertain` mark. **Under item 1.**
- **Fixed floors first, a person last, and a model may only escalate** (Hermes, `tools/approval_floors.py`, `approval_smart.py`;
  OpenClaw, `src/agents/exec-auto-reviewer.ts`). Fixed deny lists run before any standing allow. A guard model answers with one word at
  temperature 0 with thinking off; an empty reply, an unknown word, a timeout or an error all turn into a question for a person, logged
  loudly. Its prompt tells the guard that the command text is untrusted and that any instruction inside it must be ignored. In OpenClaw,
  three reviewer denials in a row escalate to a human. *Zoe:* the kernel already decides from tables; take only the rule that a judge can
  narrow a card but never approve it, and that every judge failure becomes a person's question. **Under item 1.**
- **Card semantics** (Hermes, `website/docs/user-guide/security.md`, `approval_human_wait.py`). An unanswered card expires as
  "not answered" rather than as a denial, so asking again carries no penalty; an expired card is never reopened; only the requester or an
  admin may answer, and anyone else is told by name who can; a card that cannot show the whole action offers only Deny; time spent
  waiting for a person is excluded from the tool deadline. *Zoe:* these five rules as the kernel record's card states. **Under item 1.**
- **Arguments frozen at approval; a denial is final and the model is told so** (OpenClaw,
  `agent-tools.before-tool-call.approval.ts`). Arguments that change after approval are refused; a once-only approval used
  twice is rejected; a late approval cannot restart a closed turn. A denial comes back as an ordinary tool result that says the call did
  not run, that the decision is final, and that the model must neither retry nor ask again, only explain that a new request would raise a
  new card. *Zoe:* a declined card returns the same kind of result to the brain. **Under item 1.**
- **Standing grants bound to an exact job definition** (OpenClaw, `docs/tools/exec-approvals.md`). An always-allow on a scheduled job
  is tied to its definition, command, folder and environment; any edit retires it, even one that later restores the original; a ledger
  lists every grant with its use count and a Revoke action. *Zoe:* a routine's grant is tied to a hash of its definition; an edit means
  re-approval; History lists grants with Revoke. **Under item 5.**
- **Typed scope lines on a card, written by the door, never inferred** (OpenClaw, approval scope summaries). Three kinds: a message
  send (recipients, internal or external), a payment (amount, currency, payee) and an external post (destination). The docs are explicit
  that these facts come from the owner's side, are never guessed from message text, and grant nothing by themselves. *Zoe:* the kernel
  door writes these lines; the brain never does. **Under items 1 and 6.**
- **One closed refusal table: what, why, one next step** (OpenClaw, `src/gateway/operator-refusal.ts`). About 15 causes, each rendered
  as a sentence naming the action not taken, the reason and one next step; the model is handed the same sentence and told not to retry,
  so that it cannot make up a different reason. *Zoe:* add a next-step column to the kernel's refusal table
  (`labs/zoe-os/zoe_os/brain/bridge.py` `SAY`). **Under items 3 and 5.**
- **Narrowing access stops that person's pending work; widening never widens accepted work** (OpenClaw, gateway roles and
  `docs/concepts/multi-user.md`). When someone's role is reduced, the runs they started stop and they are told why; restoring the role
  does not bring the old authority back; queued and saved work always runs as the person who asked for it, never as the session owner or
  the scheduler, and is rechecked when it starts. *Zoe:* when a permission cell narrows, cancel that profile's pending confirmations and
  queued or routine actions; each routine run asks the kernel with `started_by`'s current cells. **Under items 1 and 5.**
- **A metadata-only audit that says what it cannot prove** (OpenClaw, `docs/gateway/audit.md`). Rows for run and action start and
  finish, no message bodies or arguments, keyed pseudonyms, 30 days. The docs warn that a missing row proves nothing, and that a recorded
  approval does not show the action ran. *Zoe:* "What Zoe did" says when its history may be incomplete and never treats an approval as a
  completion. **Under item 5.**
- **Guest grants expire by default** (OpenClaw, `extensions/visitor-access`). 14 days; a permanent grant must be asked for explicitly
  on each invite. *Zoe:* guest cells carry `expires`. **Under item 5.**

## 2. Delivery and restart honesty

- **An empty reply after a write gets one tool-free summary** (OpenClaw,
  `embedded-agent-runner/run/incomplete-turn-recovery.ts`, `settled-turn-finalization.ts`). When every call has a recorded
  result and nothing was delivered, exactly one more model call runs with tools switched off. Its instruction is to summarise the
  recorded results, not to repeat or restart anything, and to admit any tool that failed rather than claim success. If that call is
  empty too, the person gets one fixed notice inviting them to ask again, plus a warning that some actions may already have run. Retries
  are skipped entirely once a side effect is possible. *Zoe first version:* when the turn holds a write receipt (`write_receipts.py`),
  say what was done from the receipt ("Added X to Y") instead of the brain-unreachable sentence; failing that, say the action happened
  but the reply was lost and ask the person to check before repeating. **Under item 3.**
- **Identical-call breaker** (OpenClaw, `packages/agent-core/src/tool-repeat-guard.ts`; Hermes, `tool_guardrails.py`). A call
  is keyed on its name plus its arguments in any key order. OpenClaw lets the third identical call run with a note asking the model to
  change approach, and ends the turn at the twentieth; Hermes warns at 2 and blocks at 5, with hard stops on unattended runs only. New
  user input resets the count; tools that poll by design declare themselves repeatable. *Zoe:* sized for a voice turn, a note at the 2nd
  identical call and a stop at the 3rd. **Under item 3.**
- **A repetition guard before speech** (Hermes, `repetition_guard.py`; OpenClaw adapts it). A 60-character window repeating over
  half of a long fragment aborts the reply, and a placeholder is stored instead of the loop, because feeding the looped text back would
  start the loop again. *Zoe:* the same check before TTS. **Under item 3.**
- **Lost sends are ambiguous and are not resent** (Hermes, `telegram_send_errors.py`, `delivery_ledger.py`; OpenClaw,
  `docs/concepts/retry.md`). Send failures fall into closed classes (refused, rate-limited, never sent, server error, lost), and the two
  that might have reached the person are never resent in another form. A rate-limit window is written to disk so it survives a restart. A
  partial send keeps the ids that landed and is no longer retryable. Any redelivery that might repeat a message carries a visible marker
  saying so, and OpenClaw tells the person once that it could not confirm delivery and will not resend on its own. *Zoe:* see the
  Telegram fault below: the bot returns a distinct status for lost sends and zoe-data maps it to `uncertain`; home actions are never
  redelivered. **Under item 1.**
- **Durable accepted input, and one resume input after a restart** (OpenClaw, `docs/concepts/queue.md`,
  `docs/gateway/restart-recovery.md`; Hermes, `session_admissions` in `session-storage.md`). Input is saved before it is
  acknowledged. After a restart each interrupted session gets one hidden input telling the model to carry on from its saved results
  without replaying the request and to report, not retry, anything whose effect is unknown; a shell command with no saved result pauses
  the turn instead. Recovery runs under the original requester, rechecked, and never under an administrator in their place; a deploy
  token stops work from an older deployment resuming. In Hermes a stored-but-unsent reply is delivered once with a recovery notice, an
  input that was running when its owner died becomes unknown and is never replayed, and a session active across 3 restarts is suspended.
  *Zoe:* a kernel `accepted_inputs` row (who, surface, text hash, state) before the brain call; at boot each unfinished row is either
  resumed without replay or answered with a short note that Zoe restarted before the request ran; home actions become `unknown`. **Under item 1.**
- **Inbound dedupe by claim, commit and release** (OpenClaw, `src/auto-reply/reply/inbound-dedupe.ts`). Keyed on agent, route and
  message id for 20 minutes; marked done only when the turn is accepted, and released on an early failure so redelivery can retry.
  *Zoe:* keyed on the panel utterance id or the Telegram update id. **Under item 1.**
- **Stop is an exact whole message, with a cutoff** (OpenClaw, `src/auto-reply/reply/abort-trigger-text.ts`, `abort-cutoff.ts`). A
  fixed set of stop phrases, matched against the whole normalised message ("wait" deliberately excluded); messages from before the stop
  that arrive late are treated as stale. **Under item 9; the voice form is in section 7.**
- **Restart when idle, with a loop breaker** (Hermes, `restart_when_idle.py`, `restart_loop_guard.py`). A restart
  waits for the first idle moment and names what is still running; after 3 chained boots auto-resume is skipped; a distinct exit code
  marks a fatal configuration so the supervisor stops trying. OpenClaw holds its channels after 3 unclean boots in 5 minutes. *Zoe:*
  deploys wait until no voice turn, reminder fire or night job is in flight. **Under item 10.**
- **A boot label for how the last run ended** (Hermes, `monitoring/memory_health.py` `previous_exit_class`). A closed list ordered
  worst first, from out-of-memory kills through watchdog kills to an unclean stop or none. **Under item 10.**

## 3. Scheduled work and night jobs

- **Gates in code before any model call** (Hermes, `cron/scheduler.py` `_prepare_job_prompt`, `cron/monitor.py`,
  `scheduler_prompt.py`). In order: a corrupt config refuses; a script-only job never wakes the model; an empty job pauses itself; a
  monitor gate hashes the cheap source's output and stays silent when it is unchanged (saving the new hash before the model runs, and
  treating a source failure as an error rather than a change); a wake gate lets a pre-run script veto the model with one exact JSON line.
  OpenClaw's heartbeat skips the model when its notes are empty. *Zoe:* every scheduled job on the night lane declares a code
  pre-check; unchanged means no brain call (`night_mind` already treats a quiet day as zero calls). **Under item 4.**
- **Silence is an explicit marker; failures always report** (Hermes, `cron/scheduler.py` `_is_cron_silence_response`; OpenClaw,
  the cron-jobs docs page). A silence marker counts only as the whole reply or its own first or last line, and a failed job reports
  whatever the model said. The scheduled-job prompt forbids the model from creating new scheduled jobs because the task text mentions a
  schedule. OpenClaw's contract for unattended runs says nobody is there to answer, so the reply must be the result itself, unchanged
  state and known blockers stay quiet, and prose is never graded as a failure. *Zoe:* the same rules in every night-lane and proactive
  prompt. **Under item 4.**
- **Missed occurrences are accounted for, not replayed** (Hermes, `cron/missed_runs.py`, `extra_fire.py`; OpenClaw,
  `store/scheduler-actor.worker.ts` `boundedCatchup`). Hermes moves the next run time forward before dispatch. Inside a grace
  window (half the period, clamped to 120 s to 2 h) a job fires late; past it the backlog collapses into one catch-up and a single
  "missed N times" row; one-shots more than 120 s overdue are retired, not fired. A manual "run now" is an extra fire that does not use
  up the real slot (the bug it fixed: test-firing a half-hour reminder completed it, so the real one never came). OpenClaw delays model
  work at boot by 2 minutes and runs at most 5 catch-ups, 5 s apart. *Zoe:* a reminder found past its grace is spoken as late (with the
  time it was due), not as if on time, and gets a missed row in "What Zoe did"; boot catch-ups wait behind the voice brain's warm-up. Zoe
  keeps her retry for reminders: for a reminder, a repeat is better than a loss. **New (scheduled work); the boot throttle under item 4.**
- **Incidents: alert once per cause, re-arm on success** (Hermes, `incidents.py`; OpenClaw, its cron failure alerts). Failures are
  keyed by job and a hash of the normalised error, with durations masked out. The first failure alerts; repeats wait 6 hours in Hermes
  (OpenClaw: the same cause at most once a day, a new cause after an hour); a success closes the incident silently; an acknowledgement
  silences that cause only; 3 failures in a row add a nudge to fix, pause or remove the job. Quick re-runs at 5, 15 and 30 minutes happen
  only when the failure was a network error and no model call had been made, so a re-run cannot double a side effect. **Under item 10.**
- **Failure repair goes to the person who made the job** (OpenClaw, `service/failure-repair-brief.ts`). Instead of a general
  alert, a system turn goes into the conversation that created the job, telling the model it is the user's only notice. It should stay
  silent on transient causes, fix what it can and report in one line without claiming the job is fixed (a later run must prove that), and
  otherwise explain the problem plainly without pasting the raw error. *Zoe:* a household row in plain words that names the routine, the
  cause and when it will try again, sent to the routine's owner rather than to everyone. **Under items 5 and 10.**
- **Delivery to a person never guesses** (OpenClaw, `delivery.md`). An owner target resolves only to a positively
  identified owner and never guesses when that is ambiguous; a missing route still runs the job and records it as undelivered; a one-shot
  is deleted only after delivery is confirmed. *Zoe:* a member-targeted result goes only to that member's phone or verified panel
  session, never to whichever panel was used last, and a reminder is done only when a device acknowledged it (`proactive/recipients.py`).
  **Under item 1.**
- **One foreground slot for the local model** (Leon, `server/src/core/llm-manager/local-inference-scheduler.ts`). The owner's turn holds
  a foreground counter for its whole length, including the tool runs between model calls; background waiters get nothing while it is
  held, even when the slot is idle; the timeout starts at the grant, not on enqueue; a background retry releases the slot and queues
  again. The acceptance test starts a background planning call inside a foreground turn and checks it finishes only after the answer is
  delivered. Hermes merges queued reviews per session, a newer snapshot replacing the older. *Zoe:* the survey's item 3, now with the
  whole-turn reservation, the grant-time timeout and that scenario as its test; `brain_slot_guard.py` already defers and pre-empts.
  **Under item 4.**
- **A proactive pipeline where most ticks are quiet** (Leon, `pulse-manager.ts`). A planner picks at most one lead and
  is told that quiet is the normal outcome; read-only research follows under a 3-minute deadline; a separate review sees only the tool
  evidence (the research agent's own answer counts for nothing) and decides silent, save or notify; no successful tool call means silence
  before any review; a unique index stops the same finding being announced twice. A matter closes when the owner settles it in chat,
  checked by a quote that must appear literally in the owner's words. Skipped ticks are recorded with a reason. *Zoe:* on the night lane,
  not the voice brain. **Under item 4; the quote-checked close is new.**
- **A decline ladder** (Leon, `pulse-manager.ts`). 1, then 7, then 30 days after successive declines, with related offers suppressed
  too. Zoe today: a flat 24 hours (`anticipation.py` `COOLDOWN`). *Zoe:* keyed on member, kind and subject from fixed tables, not model
  text; a "Not now" tap on the panel is a decline with no classifier. Hermes adds a permanent dismissed latch per offer and a cap of 5
  pending suggestions (`suggestions.py`). **Under item 9** (the survey's item 9 already named the ladder).
- **Promote a repeat into a routine with a visible test** (OpenClaw, `how-it-works.md`). The confirmation reads back
  the schedule in plain words rather than as a cron expression, the job is run once straight away, and it is removed if that run fails.
  **Under item 9.**

## 4. Skills and admission

- **Re-consent when a manifest widens** (OpenClaw, `manage-plugins.md`, `src/plugins/tools.ts`). Consent is a hash of the
  declared capability surface: an update that declares more needs fresh consent, the same or less reuses it, and declining keeps the old
  version running; a tool the manifest does not declare is refused, and so is a name clash. Zoe today: `GrantStore.install` replaces the
  manifest silently (fault below). *Zoe:* store a manifest hash at install; a wider ceiling waits for an adult's yes; record it as a
  kernel event. **Under item 8.**
- **Declared equals registered, checked at load** (Hermes, `tools/registry.py`, `skills/AGENTS.md`). Plus a test that each skill's
  description is one sentence of at most 60 characters, since Zoe's kernel tables and router labels are generated from it. **Under item 8.**
- **Anything Zoe writes about her own skills is a draft** (OpenClaw, `extensions/skill-workshop`; Hermes, `curator.py`). OpenClaw's
  default lets the agent rewrite its own skills unasked, and those skills skip the allowlists; do not copy that. Take its draft mode
  instead: at most 50 drafts waiting, an adult applies one on the phone, the previous version is kept and Undo works. Its review prompt
  works because it treats the conversation as evidence rather than instructions, expects most reviews to change nothing, and allows at
  most one change. Hermes's curator runs without a model: stale at 14 days, archived at 30, pinned and scheduled-job skills skipped,
  nothing ever deleted. **Under item 8.**
- **Install policy fails closed** (OpenClaw, `security.installPolicy`; Hermes,
  `catalog-submission.md`). A timeout, an invalid reply or a missing reason blocks; a warning can be overridden only by typing the
  plugin's name, and automatic installs never approve their own warnings. Hermes's catalogue demands an exact commit pin, no
  self-updating code, and that anything waiting for a person fails cleanly when run unattended. **Under item 8.**
- **A hashed copy of the rules with a drift report** (OpenClaw, `extensions/policy`). An approved policy file is compared with the live
  configuration, and drift becomes a doctor finding with a fix hint. *Zoe:* the live permission cells against an adult-approved hashed
  copy, plus a "why can't I" answer that names the deciding cell. **Under item 5.**
- **A narrower cell intersects, never replaces** (OpenClaw, `config/tools-by-sender.ts`, as a warning). There a specific entry
  replaces the wildcard, so an empty entry for one person means unrestricted, and display-name keys still match. *Zoe:* a narrower cell
  always intersects with the defaults; identity keys are typed; a name is never identity. **Under item 5.**
- **A plain doctor** (Hermes, `hermes_cli/doctor.py`). Ordered sections; each finding is fixable automatically, needs a person, or was
  fixed; a doctor run never refreshes a token, and real network probes run only on request. *Zoe:* a "Check Zoe" panel page, one sentence
  and one button per row, fixable versus needs an adult. **Under item 10.**

## 5. Sessions and several people

Both big agents are single-user underneath. Hermes says so in one line ("Hermes is single-user", `owner_identity.py`).
OpenClaw's multi-user page calls session ownership and presence "usability features, not security boundaries"
(`docs/concepts/multi-user.md`), and describes its per-person profile file as "prompt selection, not filesystem secrecy"
(`docs/concepts/user-model.md`). Its default puts everyone's direct messages in one session, and with collaborative steering on, one
person's message can act with another person's permissions. What follows is what to take despite that.

- **Mixed senders share no authority** (OpenClaw, `auto-reply/reply/queue/drain.ts` `resolveCollectedRun`). When a batch holds
  messages from more than one person, or from someone unverified, no sender's authority carries into the run. A queued request is claimed
  only when route and person match, and a stalled turn's recovery never jumps another person's earlier request. *Zoe:* two speakers
  merged into one turn run at the lower authority (guest level); follow-ups queue per speaker; older input runs first. **Under item 1**
  (`started_by`).
- **Sessions keyed on who is speaking, kept alive only by real input** (OpenClaw, `src/routing/session-key.ts`,
  `docs/concepts/session.md`). Separate times for session start, last real interaction and bookkeeping; only committed user input
  extends idle time, so heartbeats and scheduled events do not keep a session open; a reset drops stale queued notices. Zoe today: one
  session per panel (fault below). *Zoe:* key on panel plus verified member, with guest or unknown as its own key. **New.**
- **A busy session queues, it does not refuse** (OpenClaw, `docs/concepts/queue.md`). Zoe's chat lock refuses a second request after
  5 s (`routers/chat.py` `locked_chat_stream`). *Zoe:* queue the follow-up under its own sender. **Under item 9.**
- **Strangers get one rate-limited reply and create no state** (Hermes, `gateway/pairing.py`). A decline goes out once per sender per
  24 hours, and the stamp is written before sending so that a failed send cannot turn into a stream of declines; the owner gets one notice
  per sender. Zoe's own linking (a signed token minted in an authenticated session, shown as a QR) is better than Hermes pairing; keep it
  and rate-limit the reply (fault below). In OpenClaw a stranger cannot change where the owner's replies go, and a paired device never
  gains wider access without a new request. **New, beside item 5.**
- **Every side door runs the same identity check** (OpenClaw, `security/opengrep/precise.yml`). 145 blocking rules built from 143 of
  their own published advisories. The classes that matter here: identity matched on mutable display names; side doors (reactions,
  buttons, voice ingress, system events) skipping the sender check; untrusted text placed in the trusted prompt; an empty approver list
  read as "anyone"; queued work delivered without rechecking current policy. *Zoe:* one rule per fixed Zoe bug, blocking in `validate`,
  starting with "every door and side door calls the kernel". **Idea** (IDEAS.md).
- **A message to a phone is written into that person's conversation** (Hermes, `mirror.py`). It is stored in the user role,
  because a copy in the assistant role would be replayed later as if it were a real turn; a "yes" reply then binds to it. **Under item 1.**
- **Group chatter never wakes the assistant** (OpenClaw, `docs/channels/ambient-room-events.md`, as a warning). Using unaddressed room
  speech as context would turn a kitchen microphone into passive listening; Zoe's wake word is the right gate. Not taken.

## 6. Memory over time

- **Date facts by when they were said, and retire them by when they became true** (Graphiti, `utils/maintenance/edge_operations.py`;
  dating prompt in `prompts/extract_edges.py`). Duplicates are found first, then the new fact is dated, then old facts are invalidated:
  (a) a new fact that already carries an end date is history on arrival; (b) if a contradicted older fact actually became true later than
  the new one, the new one is retired instead; (c) an old fact is retired only if it became true earlier and the two periods overlap. Its
  own gap: when either fact is undated nothing happens, silently. The dating prompt forbids invented dates and resolves relative times
  against the source episode's own time, not the ingest time; mem0's extraction prompt (`configs/prompts.py`) says the same about
  the observation date. *Zoe:* carry a `said_at` (the message or recording time) into ingest and pass it as `now` to `parse_validity`;
  one pure helper for all three retire paths (`supersede_for_turn`, `conflict_pairs`, `retire_by_claims`); test cases "said on day 1,
  ingested on day 3 must not retire what was said on day 2" and the undated pair. **Extends the survey's item 2.**
- **Background memory jobs only add** (Hermes, `tools/memory_tool.py` `_background_delete_gate`). An unattended job may only add; a
  change or removal is staged for approval together with the full entry it targets, and refused if that entry changed in the meantime.
  Whole-entry replace was removed after models used it as find-and-replace and wiped the other facts in an entry. *Zoe:* any edit or
  removal a night or self-review job proposes becomes a pending card for a verified adult, pinned to the row version it was reviewed
  against. **Extends the survey's item 6.**
- **Grounding, and a list of what not to capture** (Hermes, `background_review.py`, `prompt_builder.py`; OpenClaw,
  `experience-review-prompt.ts` in the skill workshop). The review prompt allows only facts the conversation can point to;
  forbids guessing traits, mood or preferences from topic or tone; keeps a one-off request from becoming a standing preference unless the
  person marked it as general; and says to leave a fact out when unsure. Its do-not-capture list keeps out failures that depend on the
  environment, transient errors, unresolved attempts and, above all, negative claims about tools, which otherwise harden into refusals the
  agent repeats long after the cause is fixed. Memory is written as plain statements of fact, never as orders to itself, because an order
  gets re-read as an instruction. *Zoe:* the same clauses in `person_extractor_llm.py` and the struggle and digest prompts. **Extends the
  survey's item 6.**
- **Finished actions recorded as finished, uncertain claims marked unverified** (Hermes, `context_compressor_prompt_rules.py`;
  Letta, sleep-time prompts). The summariser rewrites a to-do that was carried out as a dated, completed action, so it is never left
  sounding unfinished; because summaries lose hedging words first, each uncertain claim is kept under an explicit unverified label with its
  basis. Letta's prompt avoids relative words like "today" in stored memory, since the memory outlives the day. *Zoe:* both rules in
  `memory_digest.py` and the turn-digest prompts, so a finished errand is not re-mined as an open task. **Extends the survey's item 6.**
- **Turn taint, and reject rather than redact** (OpenClaw, `session-provenance.ts`, and its memory redaction and provenance
  QA scenarios). A network tool result taints the rest of the turn for memory; a night candidate that already contains a raw secret is
  rejected, not redacted into long-term memory; restricted sessions are left out of the night run. Zoe already refuses writes for a
  tainted turn in the brain fence; extend it to every memory writer and to the night review. **Extends the survey's item 6.**
- **Night consolidation as checked operations, not prose** (OpenClaw, `dreaming-consolidation.ts` in memory-core). The
  model returns only one operation per candidate (added, merged or superseded, naming the prior entries) and never writes memory text;
  the host writes the sourced entry. Code refuses a plan that removes more than 25% of entries, keeps the previous file and re-checks its
  hash before swapping it in. Promotion needs a score of 0.75, 3 recalls and 3 distinct queries. **Idea** (IDEAS.md), for the 12B night lane.
- **Forget by supporting provenance** (Graphiti, `graphiti.py` `remove_episode`). A fact goes when its last supporting episode is
  removed, and summaries are rebuilt from what remains. Do not copy its fallback, which keeps the old summary when a rebuild fails, so
  forgotten text survives: Zoe blanks or blocks the summary until a rebuild succeeds. **Forget-reach work**
  ([forget reach](../knowledge/forget-reach.md)).
- **Person merges are proposals** (Graphiti, `dedup_helpers.py`). Its automatic merge on an exact single name match is
  wrong for a household (two people can share a name, and a family word means a different person to each member). Take the
  possessor-qualified extraction (a relative is stored as "the speaker's" relative, keyed to the speaker's member id), the fuzzy gate
  (Jaccard 0.9, name entropy 1.5) for suggestions only, and "unsure means a new entity". **Idea.**
- **A watermark per member, advanced only on success** (Letta, `groups/sleeptime_multi_agent_v4.py`, as a warning: Letta advances it
  before the run and swallows failures, so a failed run loses messages). **Idea.**

## 7. Wake, first turn and turn-taking

The turn-taking reader found Zoe further along than most of these projects (clean and hesitation tails, speculative turn start, a
barge-in grace period, conversation mode with enders, Smart Turn measured offline and judged not yet). What follows is the delta.

- **Save only what was played** (Pipecat, `llm_response_universal.py`; Unmute). Pipecat writes
  assistant text into the history as its audio plays, and on an interruption it closes the message behind what was already played, on the
  reasoning that "the user already heard those words". Unmute marks a cut reply and strips the mark before the next model call so the
  model does not repeat it. Zoe today saves every sentence produced, played or not (fault below). *Zoe:* the panel daemon sends its
  played-sentence count with the barge commit (it already computes it); the server trims the saved reply and adds a note of where it was
  cut. **New.**
- **A stop word during playback ends the reply, it does not start a turn** (TEN turn-detection test set, whose "wait" class is really
  "be quiet", not "hang on"; linux-voice-assistant `stop_word.py`). *Zoe:* stop-class words said during playback end the reply silently
  with no follow-up window; log the barge kind (stop, words, noise). Do not buy a separate stop-word model until the mic's behaviour
  during playback is measured: the speakerphone suppresses its own mic then. **New.**
- **Backchannels are not interruptions** (Pipecat, user backchannel frames). A short acknowledgement keeps the reply going and stays out
  of the model's context. *Zoe:* once duck-decide-resume is on, a committed barge whose transcript is only a filler resumes. **Idea.**
- **A wake verdict before the turn, and a refusal starts no cooldown** (OHF voice-support, `voice/kaldi/verify.py`, `wake.yaml`; Home
  Assistant, `assist_pipeline` default pipeline). A small Kaldi decode over at most 1.4 s compares the wake phrase with background speech,
  the phrase minus its last word and a curated list of confusables, and accepts only when three per-frame ratios clear fixed thresholds;
  every threshold and every removed confusable sits next to the measurement that moved it. It fails open. Home Assistant starts its
  duplicate-wake cooldown only after an accepted verdict, never on a refused one. *Zoe:* an energy gate on the pre-roll first, the
  VAD-threshold arm second, a verifier third; a refusal is silent, logged, keeps its clip and does not start the wake cooldown.
  **Extends the survey's items 1 and 5.**
- **Wake listener health** (Hermes, `tools/wake_word.py`). A peak of 10 or less for 10 s sets a "microphone seems silent" state with a
  named fix; a failed stream reopens at 0.5, 1 and 2 s, then gives up; the listener will not arm unless speech recognition and TTS are
  both ready, so it never hears someone and then does nothing; the wake engine is reset on every start. Zoe already confirms twice and
  resets the wake model. *Zoe:* the dead-mic state and the arming check, shown on the panel. **New.**
- **Matching order and area scope before any model** (Home Assistant, `conversation/default_agent.py`,
  `gazetteer.py`; hassil). Sentence triggers first, then a strict template match against exposed devices, then unexposed devices only to
  give a precise error, then unknown names so the reply can say no such device exists, then a gazetteer over areas and aliases that acts
  only when it can also say what it did (acting silently is judged worse than a clear error); the panel's own area wins a name tie. HA
  abandoned fuzzy n-gram matching. *Zoe:* fold the area scope, the act-only-if-you-can-answer rule and the precise "no X in this room"
  errors into `stt_snap` and `fast_tiers`. **New; the error wording under item 3.**
- **A constrained second recognizer for unclaimed wake turns** (speech-to-phrase 1.4.3, `train_kaldi.py`, `transcribe_kaldi.py`). A
  trigram model built from the command templates and the home's own names rescores the 3 best hypotheses, allowing at most 2 dropped
  words, and otherwise returns nothing. It is a fallback beside the fixed speech model, never a replacement, and needs an owner decision
  and a RAM measurement. **Idea.**
- **One event list per turn with error codes** (Home Assistant, `assist_pipeline`). Timestamped events for wake, speech start and end on
  the audio clock, transcript, intent, TTS and errors with a code; the last 10 runs kept; optional per-stage audio. When every target is
  in the panel's own room, a chime replaces the spoken reply. **Under item 9.**
- **Train the end-of-turn model on Zoe's own turns** (Smart Turn, `train.py` and its contribution guide). Zoe's logs already hold
  labels: a speculative verdict that turned into a continuation is "incomplete", a commit is "complete", a re-speak within seconds of a
  close is a false close. Fine-tune offline at night and judge it by the existing offline evaluation's pass and kill rule. **Idea.**
- **Ask before opening the follow-up window?** Home Assistant and linux-voice-assistant open the mic only when the reply asked something;
  Zoe opens it after every reply and relies on the speaker gate. A shadow count of window turns after replies that asked nothing would
  show whether it earns its exposure. **Idea.**
- **A reassurance is a placeholder, never a result** (Pipecat, `wait_reassurance/llm_extension.py`). The filler said while work runs may
  only say the work is still going: no findings, no claim of completion, no questions, offers or promises, and an explicit "say nothing"
  answer is allowed. Any change to that prompt must be committed together with replay numbers. **Under item 9.**

## 8. Testing and evals

- **A live-model lane over the same scenarios** (Hermes, `evals/core_tool_deferral/`). Real agents, 14 tasks × 3 runs × 3 model
  sizes, graded by code with partial credit. The rules: a run that errors scores zero and stays in the denominator; infrastructure
  failures are not scored at all; when the model asks back, a scripted reply answers (a first run without this was thrown away); contested
  cells grow from 3 to 6 runs; a one-run difference is treated as noise until the transcript is read; small and mid-sized models are the
  ones that show the signal. The ambiguity trap scores asking at 0.4, the right result at 0.6, and harm at zero overall. *Zoe:* the
  scenario harness stays deterministic for CI; add a nightly or manual lane that runs the same scenarios against the real voice brain
  under these rules, where turning off the wrong light or deleting the wrong list scores zero. **Under item 2.**
- **Fixture-as-contract scenarios, a marker and a decoy, a request cursor** (OpenClaw, `qa/scenarios/`, `extensions/qa-lab`). A request
  file and an evidence file define what an honest completion looks like; every pass needs an exact marker; every denial test plants a
  decoy that must never appear; a cursor taken before the turn lets a test check which tool the model planned after it, and that the next
  request carries the earlier result; one retry for infrastructure errors, none for model errors; a skipped scenario must be logged as a
  known gap; persona scenarios only capture transcripts for a later blind judge. **Under item 2.**
- **Scenario ideas the nod catalogue lacked** (OpenClaw personal and runtime packs, in `qa/scenarios/`), in household form: Zoe may not
  say a message was sent until its record exists, checked against a fixed list of forbidden claims; a declined card's hidden content never
  leaks; a partial failure is reported with what worked, what did not and a promise not to retry unasked; a bare "yes" after Zoe offers
  to add something must produce a real row; one member's private recall never surfaces at the shared panel; a permission change applies
  on the very next utterance; a phone that resubmits one utterance gets one row; a deploy onto a copy of the real store keeps forgotten
  facts forgotten. **Under item 2.**
- **A recall exam for summaries** (Hermes, `evals/compaction/`). Questions are generated from the part of the conversation that will be
  summarised away; the answering model sees only what is left and must say plainly when the answer is not there; answers are scored
  2/1/0 against a full-transcript ceiling and a closed-book floor. A mechanical list of exact identifiers copied beside the summary raised
  exact-fact recall from 23% to 60% (cloud-judged, so the method transfers, not the numbers). *Zoe:* for `conversation_notes.py` under
  the 8k slot, with an anchor list of names, times and list items copied verbatim. **Under item 2.**
- **Crash tests that prove the crash** (Hermes, `tests/conformance/persistence/README.md`). A real kill of a child process that the test
  first proves was alive; a crash between sending and recording must not deliver twice. **Under item 2.**
- **Prompt snapshot test** (Hermes, `tests/ci/test_prompt_surface.py`). Any prompt or tool change fails without a committed render.
  *Zoe:* a golden render of the voice brain's prompt in CI. **Under item 3.**
- **Promote a guard only after a frozen replay** (Hermes, its contributing guide). A heuristic rule only warns until a replay over a frozen
  set of past changes shows it is precise enough to block. It is Zoe's shadow-then-enforce rule, stated cleanly.

## Faults found in Zoe by these reads

Each was checked in a clean checkout of main (72685151d) by reading the named file. "Confirmed in code" means the code says it; none
was reproduced live. Where a report's description was wrong, the correction is in the row.

| fault (by class) | where | status | suggested fix |
|---|---|---|---|
| A Telegram send that times out after it left is classed "retry", so a retry can deliver twice | `labs/flue-zoe-telegram-2x/src/notify.ts` turns every send exception into 502 "telegram refused the message"; `services/zoe-data/telegram_notify.py` maps any non-400/403 status to `retry` | confirmed in code | the bot returns a distinct status for lost or timed-out sends and zoe-data maps it to `uncertain`; a visible "possible repeat" marker on chat and reminder redelivery; never redeliver a home action |
| An unlinked sender gets the linking reply on every message, with no rate limit | `labs/flue-zoe-telegram-2x/src/handler.ts` `handleIncoming`, `voice.ts` | confirmed in code | once per sender per 24 h, stamped before sending, no other state; one panel notice |
| A brain error on Telegram is only logged; the person gets no reply | `labs/flue-zoe-telegram-2x/src/app.ts` (catch logs to the console), `brain.ts` `askZoeAs` throws on a non-2xx | confirmed in code | one fixed notice, sent once, saying no answer came back and inviting the person to ask again |
| An empty brain reply is spoken as "trouble reaching my brain… try that again", even when the turn's write happened | `services/zoe-data/zoe_flue_client.py` (empty 200 → `_FALLBACK_TEXT`); `voice_phrases.json` `brain_unreachable` | confirmed in code (that path does not consult write receipts; callers upstream not traced) | when the turn holds a write receipt, report the write from the receipt; never "try again" |
| No identical-call breaker on the live brain lane | `services/zoe-data/plan_loop.py` has `repeated_step`, but no runtime module imports it; no repeat guard in `labs/flue-zoe-brain-2x/src` | confirmed by search | a note at the 2nd identical call, a stop at the 3rd |
| After a barge-in, the saved reply includes sentences that were never played | `services/zoe-data/routers/voice_tts.py` `_emit_sentence` appends before synthesis and the stream's end saves all of it as heard; `scripts/setup/zoe_voice_daemon.py` computes the played prefix (`_PlayoutLedger.heard`) but never sends it | confirmed in code; not reproduced. Correction: the daemon's `heard_ms` is the reply's played time, not the user's speech | send the played-sentence count with the barge commit; trim; add an "(interrupted after …)" note |
| "stop" said over a one-shot reply becomes a new turn | `services/zoe-data/conversation_opener.py` (enders honoured only in conversation mode, by design) | confirmed in code | stop-class words during playback end the reply silently and open no window |
| One voice session per panel, not per speaker, so a second member continues the first one's history | `routers/voice_tts.py` `_get_or_create_voice_session` (keyed on `panel_id`, 5-minute idle) | keying confirmed in code; "an echo or system turn refreshes the idle clock" not confirmed | key on panel plus verified member, guest or unknown separate; extend idle only on a committed human utterance |
| A second request to a busy session is refused with "Please wait." | `routers/chat.py` `locked_chat_stream` | lock and wording confirmed; that the Telegram bot relays it as the answer not confirmed | queue under its own sender, older first |
| A missed one-shot reminder fires at once however late, with no "late" wording and no record of the gap | `services/zoe-data/proactive/engine.py` `_recover_missed_reminder` → `_ensure_reminder_job(catch_up=True)`; `proactive/scheduler.py` `misfire_grace_time` 300 | confirmed in code (alarms already skip past `MISSED_GRACE_S` = 600 in `alarms.py`; reminders do not) | past its grace, say when it was due and record a missed row; a manual test fire is an extra fire |
| No record of accepted input or in-flight turns; a restart mid-turn loses the turn silently | `services/zoe-data` (no in-flight or accepted-input marker found; `reply_ledger.py` is provenance only) | confirmed by search | an `accepted_inputs` row before the brain call; at boot, resume without replay or a note that Zoe restarted before the request ran; home actions `unknown` |
| Stated times ("yesterday") are resolved against the capture time, so a late re-pass or digest dates them wrongly | `services/zoe-data/memory_service.py` `parse_validity(..., now=_now_dt)`; `_now_dt` is the current time unless a restore passes `captured_at` (only restores and `research_evidence.py` do) | confirmed in code | carry `said_at` from the source message or recording and use it as `now` |
| Write-time supersede retires a matching older row with no date comparison | `services/zoe-data/memory_supersede.py` `supersede_for_turn`; the nightly `conflict_pairs` sorts by `added_at` | confirmed in code (the nightly half was in the survey; the write-time half is new) | one date-ordered retire helper for all retire paths, with the undated-pair case |
| Re-installing a skill manifest replaces it silently: no hash, no adult check, a wider ceiling goes through | `labs/zoe-os/zoe_os/capability.py` `GrantStore.install` | confirmed in code | hash at install; a wider ceiling waits for an adult's yes; a kernel event |
| A kernel refusal says why but gives no next step | `labs/zoe-os/zoe_os/brain/bridge.py` `SAY` | confirmed in code | a next-step column; "do not retry" passed to the brain |
| Named guests have no expiry | `services/zoe-data/named_guests.py` (only enrolment tokens in `named_guest_clips.py` expire) | confirmed by search | guest cells carry `expires`; "forever" must be explicit |
| The wake listener has no dead-mic state and does not refuse to arm when speech recognition or TTS is down | `scripts/setup/zoe_voice_daemon.py` | confirmed by search (an equivalent health check elsewhere not ruled out) | near-silent input for 10 s sets a panel status with a named fix; bounded reopen; arm only when both are ready |
| A decline holds an offer back for a flat 24 hours | `services/zoe-data/anticipation.py` `COOLDOWN` | confirmed in code | the 1/7/30-day ladder keyed on member, kind and subject; a never-re-offer latch |
| The fence around untrusted content (forwarded messages, transcripts) has a hardening gap in how it normalises text | the brain and Telegram fences | partly confirmed; the report overstated it. Detail is kept off the public record | fix in a security-reviewed change |
| The person extractor's prompt has no "a one-off is not a standing preference" rule | `services/zoe-data/person_extractor_llm.py` | confirmed by search | add the grounding and do-not-capture clauses |
| Night jobs have no alert-once-per-cause incident record | `services/zoe-data/night_mind.py`, `omi_repass.py` | not confirmed: the pendant re-pass now names a missing voter and degrades the night (#2409); no per-cause alert dedup was found | (job, error signature) incidents, alert once, re-arm on success |
| A staged memory change is not pinned to the row version it was reviewed against | `services/zoe-data/struggle_proposals.py`, `evolution_queue.py` | not confirmed (no row-version pin found; whether these ever edit a memory row was not traced) | pin and refuse on change, if they do |

Already recorded by the earlier records and seen again here: the brain call semaphore with no priority class
(`zoe_core_client.py`, `asyncio.Semaphore(ZOE_CORE_MAX_CONCURRENCY)`, default 2); the retry chime on a near-silent wake; the brain retry
that does not check whether a write happened.

## Do not copy

- **A model's verdict standing in for a person's yes** (Hermes's "smart" approval mode lets a side model auto-approve flagged
  commands). A judge may narrow a card, never approve it.
- **Collaborative steering and one shared session for everyone** (OpenClaw defaults). A child speaking into a parent's turn acts as the child.
- **Owner authority surviving network taint** (OpenClaw). Zoe's rule that taint blocks writes is stronger.
- **Review quotas that push for change** (Hermes's skill review prompt discourages answering that there is nothing to save, and its curator
  treats few archives as stopping too early). Nothing stays the default.
- **Treating recalled memory as always authoritative** (Hermes's compaction prefix) and **turning a classifier's paraphrase into
  owner-stated memory** (Leon stores it as explicit at 0.95 confidence). Member-stated outranks inferred.
- **Agent-written skills that bypass allowlists** (OpenClaw's default workshop mode), and **cutting the skill list alphabetically**.
- **A 60-second grace that keeps a failed tool advertised** (Hermes). For home devices that offers a dead device.
- **"Interrupted, retry once and reconcile" for home actions** (OpenClaw cron). Home actions stay unknown, never replayed.
- **"Unknown, never retried" for reminders** (Hermes delivery queue). For a reminder or medication prompt, a repeat beats a loss.
- **One-shots that fire late with no cut-off** (OpenClaw) and **heartbeats without repeat suppression**. Zoe's daily cap and cooldowns stay.
- **Mood from weather and day of week** (Leon persona). Zoe's structured persona layer is the better design.
- **Ambient room chatter as context, a 3 s VAD lockout after speech starts, muting the user while a tool runs, a nudge after 7 s of
  silence** (OpenClaw, Unmute, Pipecat).
- **Contradictions kept as links with both facts live, hard deletes, plaintext "deleted" history, two people's statements fused into
  one** (mem0, MemOS, LangMem); and Graphiti's keeping of the old summary when a rebuild fails.
- **A stock end-of-turn model judged on typed text** (TEN: a 7B model evaluated on typed sentences).
- **Fuzzy n-gram command matching** (hassil added it and later removed it).
- **The scale.** Hermes's scheduler is 218 KB and its gateway 270 KB; OpenClaw's turn is about 5,600 files. Take the rules, not the machinery.

## How their wording works, and the thresholds

**What makes the user-facing wording work** (read the originals at the paths given):
- **Say what did not happen, then what to do.** Hermes's failure notices name the action that was not run, state that nothing changed,
  and say what to check before trying again; a turn that may have run part-way says so instead of implying a clean failure. Its
  contributing guide requires every user-facing error to name the real cause and the fix. Source: Hermes `CONTRIBUTING.md`, the
  turn-loop notices around `agent/conversation_loop.py`.
- **Admit uncertainty about delivery once, and never resend on a guess.** OpenClaw tells the person a single time that it could not
  confirm a reply arrived and invites them to ask for anything missing; Hermes labels a recovered message as a possible repeat, and a
  split message as possibly partly delivered. Source: OpenClaw `src/channels/turn/pending-delivery-notice.ts`; Hermes `delivery_ledger.py`.
- **A refusal is three parts: the action not taken, the reason, one next step.** Paired with an explanation that the thing is turned
  off for this profile, instead of a claim that the capability does not exist. Source: OpenClaw `src/gateway/operator-refusal.ts`.
- **An approval names who may answer.** Anyone else is told whose decision it is and the card stays open. Source: Hermes
  `website/docs/user-guide/security.md`.
- **A scheduled-job failure says it did not run, that nothing was charged, when it will try again, and that the alert will not repeat.**
  Source: Hermes cron failure notices (`cron/`).
- **A dead microphone names the device and the fix.** Source: Hermes `tools/wake_word.py`.
- **Long output is moved to the screen with one spoken pointer**, and status lines while work runs come from a fixed catalogue that never
  splices in tool arguments or reasoning. Sources: OpenClaw `tts/speech-text.ts`; Hermes gateway status phrases.
- **Re-prompts after silence come in a short and a patient form.** Source: Pipecat's idle handling (`user_idle_controller.py`).

**What makes the model-facing wording work:**
- **After a side effect, forbid tools and forbid repeats in the same sentence**, and require any failed tool to be admitted; on restart,
  report unknown effects rather than retrying them. Source: OpenClaw `settled-turn-finalization.ts`, `docs/gateway/restart-recovery.md`.
- **Make a denial final and say why retrying is wrong**, including rephrasing the same command; a rejected tool means it did not run.
  Source: Hermes approval tools (`tools/approval_floors.py`, `approval_smart.py`).
- **Treat an "ask" as a cost to a person, not a softer no**, and treat a classifier's result as information, never permission. Source:
  OpenClaw `src/agents/exec-auto-reviewer.ts`, `decision-models.md`.
- **Let a change of subject end in-flight work**, and refuse to resume a task merely because the topic overlaps. Source: Hermes
  `agent/context_compressor.py`.
- **Separate evidence from instruction**: the reviewer of a proactive finding is told to treat all text as evidence and to give the
  agent's own answer no weight. Source: Leon `pulse-manager.ts`.
- **Contradiction needs an explicit falsifying statement**, never recency, dates or a new event about the same people; and a wrong
  extraction is worse than none. Sources: Graphiti `prompts/`; mem0 `configs/prompts.py`.
- **Complete grammar is not a complete turn**, and a garbled request may be a transcription error. Sources: Pipecat turn-marker prompt;
  Unmute system prompt.
- **Mark external content as data with no authority of its own**, without listing forbidden actions (OpenClaw found such lists made the
  model refuse legitimate requests). Source: OpenClaw `src/security/external-content.ts`.

**Thresholds** (third-party defaults, to be re-measured on Zoe's box before use):

| what | value | source |
|---|---|---|
| identical-call breaker | warn 2 / block 5 (Hermes); note 3 / stop 20 (OpenClaw); Zoe proposal 2 / 3 | `tool_guardrails.py`, `tool-repeat-guard.ts` |
| empty-reply recovery | 1 tool-free continuation; reasoning-only retry 2; none after a side effect | `incomplete-turn-recovery.ts` |
| missed-run grace | half the period, clamped 120 s to 2 h; one-shots retired past 120 s | `cron/missed_runs.py` |
| boot catch-up | model work deferred 2 min; at most 5 immediate, 5 s apart | `store/scheduler-actor.worker.ts` |
| incident alerts | repeat after 6 h (Hermes) or once a day per cause, new cause after 1 h (OpenClaw); streak nudge at 3 | `incidents.py`, OpenClaw cron alerts |
| network-only re-runs | 5, 15, 30 min, only with zero model calls | `unreachable_retry.py` |
| send retries | 3 attempts, 400 ms to 30 s with 10% jitter; redelivery backoff 30 s then 120 s | OpenClaw `docs/concepts/retry.md`, Hermes `delivery_ledger.py` |
| inbound dedupe | 20 min, 5,000 keys | `inbound-dedupe.ts` |
| stranger reply | once per sender per 24 h, stamped before sending | `gateway/pairing.py` |
| restart loop breaker | 3 chained boots; channels held after 3 unclean boots in 5 min | `restart_loop_guard.py`, OpenClaw restart recovery |
| decline ladder | 1 / 7 / 30 days; at most 5 pending offers | Leon `pulse-manager.ts`, Hermes `suggestions.py` |
| guest grant | 14 days; permanent only when asked | `extensions/visitor-access` |
| wake | 3 frames over 0.6, 2 s cooldown, silent mic at peak ≤ 10 for 10 s, reopen at 0.5 / 1 / 2 s | `tools/wake_word.py` |
| wake verifier | window 1.4 s (shorter windows let cut-off confusables through); ratio thresholds −0.85 / −0.35 / 0.10; verdict wait 2 s | `voice/kaldi/verify.py`, `wake.yaml` |
| endpointer | start 0.5, hold to 0.35; silence plus a 0.3 s offset; a cough pauses the count (0.1 s); no speech by 8 s; cap 15 s | `vad.py` |
| memory promotion | score 0.75, 3 recalls, 3 distinct queries; consolidation refused above 25% loss | OpenClaw dreaming (memory-core) |
| person-merge suggestion | Jaccard 0.9, name entropy 1.5, minimum name length 6 | Graphiti `dedup_helpers.py` |
| live-model evals | n=3, contested cells to 6; ask 0.4 / right 0.6 / harm 0 | Hermes `evals/` |

**Test ideas worth mirroring** (described in our own words; the originals are in each project's test suite next to the files named above):
- A batch of interrupted calls gets truthful wording per call, not one blanket message (Hermes turn loop).
- A delivery whose sender died mid-send is marked unknown and never retried (Hermes delivery queue).
- A dismissed suggestion is never shown again (Hermes suggestions).
- A failing monitor source is an error, never a "change" (Hermes cron monitor).
- A sender who is refused is never even transcribed (Hermes voice notes).
- A silent microphone stream is detected and the detector recovers (Hermes wake word).
- Removing one episode keeps a fact that another episode restated (Graphiti).
- A stop word is ignored while nothing is playing (linux-voice-assistant).
- A failed verifier decode is not counted as a refusal (OHF voice-support).
- An empty turn is not retried after a side effect; a stalled request never jumps ahead of another person's earlier one; a message
  accepted just before a downgrade never runs with the old access; a scheduled slot from before a schedule edit is never replayed (OpenClaw).

**Direct quotes in this record** (each under fifteen words, attributed, one per source file): "Hermes is single-user" (Hermes
`owner_identity.py`); "usability features, not security boundaries" (OpenClaw `docs/concepts/multi-user.md`); "prompt selection, not
filesystem secrecy" (OpenClaw `docs/concepts/user-model.md`); "the user already heard those words" (Pipecat `llm_response_universal.py`).
