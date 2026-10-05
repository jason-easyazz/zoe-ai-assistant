---
type: Reference
title: Memory authority - who said it decides who may change it
description: Every memory row carries a ranked provenance class; a write below the user classes can never supersede, archive or contradict a row that outranks it - it becomes a disputed candidate. The class, the allow-list, the choke point, the anchoring rule, the 14 retire-capable paths (covered vs follow-up), the rollout flag, the backfill, and the tests with their negative controls.
tags: [memory, authority, provenance, incident, flags, mempalace, people-graph]
timestamp: 2026-10-05T14:00:00Z
---

# Memory authority

Directive (owner, 2026-10-05): *"The memory needs to be flawless ... Samantha doesn't have
dementia, neither can Zoe."* Rule: **model inference must never supersede or contradict a fact the
user stated themselves.** Audit that found every writer: `docs/research/memory-fidelity-audit-2026-10-05.md`
(PR #1867) - this change is its P1.1 and P1.2 (P1.3 and P2 are follow-ups, below). The identity half
(a nightly-digest pass wrote the wrong name) is PR #1866 and is kept as the special case.

## The incident class

The nightly digest read a day transcript holding a speech-to-text fragment that named a third person,
the extractor asserted a fact about the owner from it, and its contradiction pass called
`MemoryService.review(edit)`. That **superseded a row the owner had stated** and **copied the old row's
`source` / `session_id` / `user_turn_id` / `source_excerpt` onto the model's text** - so the polluted row
looked like a regex write from a real conversation. No path checked who wrote the row it retired.

## The classes (`memory_authority.py`)

| Rank | Class | Which writers (allow-list; anything not listed is rank 0) |
|---|---|---|
| 6 | `operator` | `operator`, `operator-cleanup`, `identity_audit`, `admin`, `system`, `samantha_live_cleanup` |
| 5 | `user_confirmed` | the review UI (`review_ui`), the account acting on its own rows (`actor == user_id`: REST edits, intent handlers, MCP), the user approving a candidate |
| 4 | `user_stated` | the person's words typed or dictated (`voice_fact`, `proposal`/`manual` - the proposals route passes `origin="proposal"` whatever label the client sent, `conversation_correction`, `chat`, `skybridge_action`, `note_*`, `journal_*`, `person_created/updated`) and deterministic extractors over the user's turn (`chat_regex`, `chat_regex_fallback`, `voice_regex`, `conversation`, `voice`) - a DIRECT statement |
| 3 | `user_stated_derived` | a MODEL writer's fact that the user's OWN turn supports (below) - the person's words, paraphrased by a model |
| 2 | `user_unverified` | a voice-lane SELF-fact whose speaker the speaker-id did not confirm (`speaker_verified=False`; the voice-lane writers `voice_fact`, `voice_regex`, `voice`, `voice_turn_digest` only). **Wired (P1.3, see "The speaker verdict" below)**: a panel with the speaker gate ON reports it per turn; a panel with the gate off or in W5 shadow mode reports nothing (`None`) and **every panel write there is still a direct user class** - a guest, or another member, speaking to a panel bound to the owner can overwrite the owner's rows (not operator rows) until the gate is switched on |
| 1 | `model_from_turn` | `turn_digest`, `voice_turn_digest`, `person_extractor_llm`, `brain_tool`, `mcp` (every MCP review / forget call passes `origin="mcp"`; the account is only the acting member), `zoe_agent`, `decay_sweep` - when the turn does not support the fact |
| 0 | `model_from_transcript` | `digest`, `idle_consolidation` (unsupported), `consolidation`, `synthesis`, `music_digest`, the emotional pass, `profile-analysis`, `hindsight_retain_candidate`, **any unknown writer** |

Each row also gets `authority` (the owner's three-way view: `user_stated` | `user_confirmed` |
`inferred`; a derived row shows as `user_stated`), `authority_class`, `authority_basis` (why), `origin` (the real writer - finer than the lane
`source`; `person_extractor_llm` writes under `source="conversation"`), `turn_ref`, and `model` for LLM
writers. Stamped in **every** mode.

## The rule - one choke point (`MemoryService`)

* **May a write override a row?** Yes if its class is a **DIRECT user class (rank >= 4)** - a later
  statement of the person's always wins, even over a row they once approved (the one place the rule departs
  from the audit's literal `rank(writer) >= rank(target)`, so a typed correction can replace a
  `user_confirmed` row) - or if `rank(writer) >= rank(row)` (same class = newer wins; a higher class always).
  A **model's paraphrase of the user (`user_stated_derived`, rank 3) never overrides a direct statement**:
  both would otherwise read "user_stated", the newer would win, and a mis-paraphrase of an older sentence
  in a day's transcript could overwrite what the person said later. It does beat model classes and other
  derived rows. Cost, stated plainly: a change the person made that ONLY the turn digest captured (no
  regex/typed write) parks as a candidate instead of superseding at once - and is then ASKED about (next
  section) - so re-run the bar's S2 (Dunedin -> Hobart) / S10 before relying on it. A spoken sentence
  never undoes an OPERATOR row (it takes the review UI or an operator). Below that it is held back.
* **Held back = a `disputed` candidate.** `ingest` stores the fact with `status=disputed`,
  `contradicts_id=<row>`, `authority_blocked=true`; `review(edit)` leaves the same candidate and returns
  `None` (every caller already treats `None` as "supersede refused"). `disputed` is a blocked-read status,
  so a candidate is never recalled. Log (labels only, never text):
  `AUTHORITY_BLOCKED writer=<name> kind=<attr> action=<ingest|edit|rewrite|archive|supersede|approve>`.
  `kind` is a closed vocabulary (`name birthday age home work relationship pet health preference other`).
* **Where:** `ingest` (a contradicting fact), `review` edit / archive / reject / approve (an automatic
  actor cannot approve a candidate raised against a row), `supersede_by` (the SUCCESSOR's class decides:
  the implicit-supersede passes), `archive_duplicate` (the weekly merge). `sweep_soft_archive` goes through
  `review(archive)` so the decay sweep (`decay_sweep`, rank 1) can no longer archive a never-recalled row
  the user said.
* **Contradiction** = same subject + same attribute + a different value (`memory_authority.conflict_kind`
  on the shared reconciler's attribute matcher and `memory_supersede`'s subject/home/change-cue matchers),
  or a stated END of it ("no longer works at X"). Pure restatements by a lower class are refused quietly
  (`action=rewrite`, no candidate).
* **A supersede records the NEW writer.** `review(edit)` no longer copies `source`, `session_id`,
  `user_turn_id`, or - for a writer below the user classes - `source_excerpt`; it stamps the editor
  (`source` is the editor's lane label when on the allow-list, else `review_ui`; `origin` is always the
  exact actor). Callers pass `session_id` / `turn_ref` of the new write.
* **The user resolves it.** A person approving a candidate (`review(approve)` by a user class) makes it
  `user_confirmed` and **retires the row it disputed**; rejecting it keeps the row and the candidate is
  not resurrected by the next extraction.

## A held-back write is asked about, not lost (`memory_disputes.py`)

* `GET /api/memories/review` lists `disputed` candidates (`dispute: true`) beside the text they dispute
  (`contradicts_text`). A held-back `review` answers 409, not a crash (same for MCP).
* `run_turn_digest` turns the candidate it just produced - and, on later turns, the one whose topic the
  message touches - into ONE offer through `pending_suggestions` (`action_type=memory_dispute`):
  "Earlier you told me X and I've just heard Y - which is right?". **Yes** (`execute_suggestion`)
  approves the candidate (-> `user_confirmed`, retires the row it disputed); a dismissal rejects it.
* `expire_stale` (weekly pass) resolves an unanswered dispute after **30 days** to a logged
  `STALE_DISPUTE` (`status=rejected` + review note); the disputed row keeps standing; nothing is deleted.
* Short elliptical answers ("no, Perth now", "it's Alex") are read with the assistant question they answer
  (`prompt_text` - context only, never evidence; `memory_digest.prev_assistant_question`, personal-attribute
  questions only). First-person present / change shapes ("I'm 41 now", "I started at Acme", "I'm in Perth
  now") name their attribute without its noun and are recognised.
* The brain's `remember_fact` (`brain_tool`) carries the user's latest turn as `anchor_text`
  (`memory_digest.latest_user_turn`); when that turn is an explicit "remember / note that ..." that
  supports the fact the person is dictating through the brain (`origin=explicit_teach`, a direct
  statement), so "remember that I live in Perth now" changes the home; otherwise the paraphrase stays
  rank 1 and the reply says it is waiting to be confirmed.

## How a model writer earns `user_stated_derived` (`supports`)

The caller passes the user's OWN turn text as `anchor_text` (user turns only - never assistant text;
defaults to `source_excerpt`). The fact is supported when ONE user sentence (or two adjacent ones of the
same turn - never across turns) holds all its value tokens (names, numbers, places), the attribute cue it
names (name / where they live / work / age / birthday / a liking / an allergy, with synonyms), most of its
other words, and - for a fact about the user - the user speaking in the first person. So `uh Casey is
coming over, I think` does not support `User's name is Casey` (no name cue), `Casey moved to Perth` does
not support `User lives in Perth` (no first person), and `my dog is Teddy` / `uh Rex is coming` on two
turns does not support `User's dog is Rex`. Dates are normalised day-first before matching. The window must also be the user's own AFFIRMATIVE
statement: a question, a wish / hypothetical, a negation the fact does not share, `used to` the fact does not
share, and a possessive of ANOTHER PERSON ("my sister lives in Perth", "my wife's birthday") are all
rejected unless the fact itself names that relation.

P1.2 (digest): `_EXTRACTION_PROMPT` now asks for `{"fact","type","quote"}` - the user's own words, verbatim.
`memory_digest.fact_anchor` uses the quote only if it is a verbatim span of the user turns (a hallucinated
span leaves the fact unanchored), and falls back to the whole user transcript only when the model returned
no `quote` field. Idle consolidation now sends **user turns only** to the extractor (it fed the assistant's
lines too) and anchors the same way. The weekly merge **archives identical duplicates** (keeper = strongest
class, then confidence) instead of rewriting the weaker row - `archive_duplicate` - and leaves a merely
similar row alone.

A third-person name that the identity wall drops (`User's name is <X>`) and that merely appears in the
user's text becomes a **pending PERSON candidate** ("<X> was mentioned in conversation ...",
`person_pending`), never a user attribute. If the user did say it is their name, nothing is created.

## The 14 retire-capable paths (audit section 2.2)

| Path | Covered here? | How |
|---|---|---|
| W1 regex extractor | yes (user class; may override) | `chat_regex` etc. = `user_stated` |
| W2 turn digest | yes | `model_from_turn`, `user_stated` only if the turn supports it |
| W3-edge relationship-graph supersede | yes | migration 0037 stamps edges (`authority`, `origin`); an inferred writer cannot close a user-stated or unstamped (legacy) edge - it leaves a candidate (`contradicts_id=edge:<id>`) |
| W4 LLM person extractor | yes | own `origin=person_extractor_llm` (was indistinguishable from the regex half); a blocked fact is not written to the structured people tables either |
| W6a nightly contradiction judge | yes | `review(edit)` with the verbatim quote as anchor |
| W6b nightly reconcile update | yes | same call |
| W8 idle consolidation | yes | user turns only + anchor |
| W9 weekly merge | yes | `archive_duplicate`, no rewrite |
| W10 weekly contradiction judge | yes | `consolidation` = rank 0 |
| W11 write-time implicit supersede | yes | `supersede_by`, successor's class decides |
| W12 nightly implicit-conflict pass | yes | same |
| W16 decay archive | yes | `decay_sweep` = rank 1 |
| W20 voice teach | yes (user class) | `voice_fact` |
| W22 MCP `memory_review` / `memory_forget` as the user | yes | `mcp_server` passes `origin="mcp"` (rank 1) on every review and forget call; the account stays `actor` (audit). An MCP edit, archive, reject or candidate-approval cannot touch a row the person said; the tool answers "held back" instead of crashing |
| `person_merge.merge_person` (closes / re-points people rows + edges) | yes | a USER/admin action enforced at the entry point: a named writer below the user classes is refused (`AUTHORITY_BLOCKED ... action=merge`); the REST endpoint passes `actor=<account>`; nothing automatic calls it |

### The speaker verdict (P1.3): voice turn -> `speaker_verified` -> every write the turn causes

The Pi daemon (`scripts/setup/zoe_voice_daemon.py`) adds ONE optional field to `/api/voice/turn` and
`/api/voice/turn_stream`: `speaker: {"verified": null|false, "member": <id>|null, "score": <float>|null}`
(beside the legacy flat `voice_user_id` / `voice_score`). zoe-data (`routers/voice_tts.py::_speaker_verdict`)
turns it into `speaker_verified`, **decided by the server, never by the panel**: `True` only when the claim
passes the server's own gate (`ZOE_SPEAKER_ID_THRESHOLD` + the member's current consent) - a `verified: true`
on the wire is ignored; `False` when the gate ran and did not confirm (a scored claim the server refused, a
revoked consent, or the daemon's `verified: false` = "scored, nobody matched"); `None` (today's behaviour,
byte for byte) when there is no `speaker` block - the gate is off, in W5 shadow mode (the default: scored and
logged, never attached), errored, or the caller is not a device token. The verdict rides `_run_voice_memory_passes`
-> `extract_and_ingest` (`voice_regex`: ingest + the correction `review(edit)`) and `run_turn_digest`
(`voice_turn_digest`), and `fast_tiers` -> `expert_dispatch.store_fact` (`voice_fact`); only a verdict is passed on
(no verdict = the exact call the lane always made). Classes: verified -> `user_stated`; unverified self-fact ->
`user_unverified` (rank 2: stored, never supersedes the owner, held back as a `disputed` candidate when it
contradicts a row outranking it); a later verified statement supersedes it (rank 4 >= 2); no verdict -> unchanged.
Third-person facts (the two person extractors) are not self-assertions and never change class. The recall packet
labels an unverified row `(someone at the panel said this; speaker not confirmed)` and never quotes it as "you
said". **Known residual**: the brain's `memory_store` tool (`brain_tool`, `origin="explicit_teach"` on a "remember
that ..." turn) crosses the Flue sidecar process boundary and does not carry the verdict - an unconfirmed voice
that says "remember that I live in X" is still a direct statement there. Pinned by
`services/zoe-data/tests/test_voice_speaker_verdict.py`, `tests/unit/test_voice_daemon_speaker_verdict.py` and
ZMB cells `A6.panel_unverified.*` / `A7.panel_unverified_kept.*` (control `speaker`). Enabling the gate
(`SPEAKER_ID_ENABLED=true`, `SPEAKER_ID_SHADOW=false`) with nobody enrolled makes EVERY panel self-fact
`user_unverified` - enrol first; the shadow-week numbers (docs/research/speaker-gate-step1-results-2026-10-05.md)
say the gate is not yet fit to enforce.

Not in this PR (follow-ups): ~~**P1.3 speaker gate**~~ (done - "The speaker verdict" below); the `used to love <city>` cue false positive (P2.1); durable forgetting; REM/deep-sleep raw
upserts; a UI card for the dispute offer (it reaches the brain as an offer line like every other offer).
`_write_relationship` has one production caller (`process_text`, regex over the user's turn); it now passes
the caller's real class (`_edge_authority_for(source, text)`), so a model-sourced call cannot close a
user-stated edge.

## Affective records: the whole household, never guests (`ZOE_AFFECT_CONSENT_GATE`)

**Owner product decision, 2026-10-05:** Zoe keeps emotional context (`emotional_moment` rows and affect
metadata on facts) for EVERY household member INCLUDING children, and NEVER for guests; no stored consent
row is required. This overrides the "children never" rule in `docs/governance/emotional-safety-note.md`
(section 6 holds the decision) and the opt-in default PR #1868 introduced. Enforced at the same choke point
(`MemoryService._affect_allowed`, in `ingest` and `review(edit)`): an `emotional_moment` row is not
stored (`AFFECT_NOT_STORED`), and a feeling carried in an ordinary row's metadata (`affect` / `valence` /
`intensity`) is stripped (`AFFECT_STRIPPED` - the fact stays, the feeling does not; the sentence itself may
still name a feeling). The strip applies to `review(edit)` too: a feeling in the edit's `metadata`
(the turn digest's update path) is dropped when the gate refuses, and a feeling the superseded row carried
is not carried forward.

Modes (`memory_authority.affect_gate_mode`, per-call env read):

* `household` (**DEFAULT**, env unset): every member including minors; guests refused; an unknown or failed
  `member_modes` lookup refuses (closed). A person with no `member_modes` row is a member if they are a real
  account, i.e. not a guest sentinel (`user_filters.GUEST_USERS`: `guest`, `anonymous`, `voice-guest`,
  `voice-daemon`, empty - the kiosk guest resolves to user id `guest` / role `guest` in `auth.py` and
  `routers/panel_auth.py`).
* `members`: adult members only (a member flagged minor is refused), no stored consent, a failed lookup
  fails open with a warning. Explicit stricter option.
* `optin`: adult members with a stored persona mode (`member_modes` row = the consent record); fails
  closed. Explicit stricter option; an unrecognised value (a typo) also lands here, never on the default.
* `off` (`0` / `false` / `no` / `off`): no gate.

**Operator step after the household-default PR merges:** the live env pin `ZOE_AFFECT_CONSENT_GATE=members`
is removed so the default (`household`) applies and children's emotional context is kept.

## `ZOE_MEMORY_AUTHORITY` - `enforce` (default) | `shadow` | `off`

Read per call. `shadow` logs `AUTHORITY_WOULD_BLOCK` and changes nothing; `off` disables the wall.
Provenance stamping and the "an edit stamps the editor" fix are **unflagged** (they are pure correctness
and the wall depends on them). **Default `enforce`, argued:** the only thing the wall ever does is park a
MODEL's overwrite of something the user said as a `disputed` candidate - lossless (the text is kept),
reversible (approving it applies it) and invisible to recall - while a writer that has the user's turn earns
`user_stated` through `supports`, so what the user said is never refused. `shadow` default would leave the
incident class open for a week on a box whose only user is the owner. **Shipped `enforce` because** the findings that made it unsafe are fixed with tests: the anchor cannot be
satisfied by a relative's / negated / questioned sentence, the owner's reviewed rows are protected, the
idle-consolidation supersede is one operation, held-back writes are visible, asked about and expire logged,
and an explicit "remember ..." carries its turn. **Not measured:** the bar and the
replay gate were not run (the box is RAM-starved); the operator should run the Samantha bar (S2, S10,
S20-S22) and watch `AUTHORITY_BLOCKED` / `AUTHORITY_WOULD_BLOCK` for a night before relying on it, and flips
to `shadow` with one env line if a legitimate update is being parked. Voice-gate: none of the changed files
is in `voice_gate_check.VOICE_PATH_PATTERNS`.

## Existing rows (backfill)

The live code derives a class for unstamped rows (`legacy_class_basis`), so nothing needs migrating for
the wall to work. `scripts/maintenance/memory_authority_backfill.py --dry-run` reports the counts (class x
status, basis, writer, user, incident-shaped rows; never row text) and `--apply --i-have-reviewed
--i-stopped-zoe-data` persists the stamps (metadata only; idempotent). Rule: reviewed by a model -> model
class (the incident row: `source=chat_regex, reviewed_by=digest`); reviewed by a person -> `user_confirmed`;
source on the user allow-list -> `user_stated` (`review_ui` -> `user_confirmed`); a model writer that read
the user's turn -> `user_stated` only if the stored `source_excerpt` supports the text; anything else ->
rank 0. **Not run against the live palace.**

## Tests (all `ci_safe`, synthetic data, negative controls)

* `test_memory_authority.py` - provenance, store -> recall, the user's correction wins, an inferred
  contradiction cannot win (the digest incident replayed on a generic attribute), negation is
  `user_stated`, "I used to" keeps history, a third-person fragment creates no user fact (a PERSON
  candidate), isolation, anchoring table, the legacy rule, and the bar-shaped fixtures
  (`tests/fixtures/memory_authority_scenarios.json`, replayed at the store level; **not** wired into
  `scripts/perf/samantha_bar.py` - new scenario ids need a `--record-baseline` run on a healthy box).
* `test_memory_authority_matrix.py` - **writer x target-class** for every path above, with
  `ZOE_MEMORY_AUTHORITY=off` as the negative control on every cell (the S1 signature), shadow mode, the
  quote anchor, idle consolidation sending user turns only, the weekly merge.
* `test_memory_authority_people.py` - migration 0037, edge stamps, the edge wall + control.
* `test_memory_authority_review.py` - one group per finding of the #1868 review (anchor negatives table, owner-reviewed legacy rows, idle consolidation one-operation supersede, review-queue/offer/TTL for disputes, brain_tool explicit remember, user_unverified hook + exposure, admin approve, the P2 notes).
* `test_memory_authority_gates.py` - MCP cannot overwrite a user_stated row (with the old call shape as the
  control), person merge entry-point enforcement, the edge writer asks the rule, and the affect consent gate
  (guests, the household default incl. minors, `members` / `optin` as explicit options, fail-open/closed, off = control).
* `test_memory_authority_backfill.py` - the dry-run report (read-only, no text) and the apply.
* `test_identity_facts.py` - the digest-replay control now removes BOTH walls; removing only #1866's
  leaves the genuine row standing (defence in depth).
