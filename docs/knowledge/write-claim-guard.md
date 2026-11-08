---
type: Reference
title: The write-claim guard - Zoe never says "I've added it" unless a row landed
description: Why a spoken "done" is checked against receipts the writers record; where the guard sits (brain_dispatch, every brain turn), how a claim is recognised (per-language lexicon DATA), what counts as a write, the fast-path parser fixes that came with it, and what is still open. Flag ZOE_WRITE_CLAIM_GUARD = enforce | shadow | off, default enforce.
tags: [trust, voice-path, lists, calendar, skybridge, zoe-data]
timestamp: 2026-10-13T00:00:00Z
---

# The write-claim guard (`write_claim_guard.py`, `write_receipts.py`)

## The failure (live, 2026-10-12, AWST, the owner's own words to the panel)

| time | said | what Zoe did |
|---|---|---|
| 18:34:57 | a complaint ("I was asking you to set meetings and add things to the shopping list") | skybridge VETOED it (`router_disagrees`), then the legacy `intent_router` list path took the whole sentence as an item and answered "I've added i don't know where you got that from… Check the panel and tap Done" - over a form the (deaf) panel never showed. Nothing was saved. |
| 18:35:18 | "Can you add noodles? Noodles. To the shopping list." | saved, as `noodles? Noodles` |
| 18:35:47 | "Work." (the answer to "…a meeting tomorrow at 9 a.m. for work") | the brain said "I've added a work meeting to your calendar for tomorrow at 9 a.m." - **no row, one model round (`rounds=1`), no tool call**. |
| 18:36:03 | "Can you add fix zoe to my to-do list." | skybridge parsed a list SHOW ("Lists has 2 items."); nothing added. |
| 18:42:38 | "Can we add a doctor's appointment on Friday at twelve o'clock?" | two failed NLU calls (6.8 s), then the brain asked for a title the words had given; the next turn lost the context; no event. |
| 18:43:04 | mis-heard "Keynes pollutants in my calendar are at 12 o'clock" | `SKYBRIDGE_GATE … calendar show … router_unsure`, router confidence 0.39, ALLOWED -> "You have 0 events today." |

Root causes, in order of weight:

1. **A claim was never checked against a write.** Nothing between the 4B brain's text and the speaker asked "did a row
   land?". The Flue sidecar's prompt already says "NEVER claim … anything a tool didn't confirm" (`agents/zoe.ts`); a 4B
   model does not obey it reliably. The tools exist (`add_calendar_event`, `add_to_list`, `shopping_list_add`,
   `add_reminder`), `ZOE_BRAIN_ALLOW_WRITES=true` live, and they were *offered* on that turn (`tools=872` tokens, the
   calendar group disclosed and session-sticky) - the model simply answered in prose.
2. **The legacy list panel path claimed a write it had not made** (`routers/voice_tts.py`, quick `list_add`): the reply
   said "I've added X" while the item waited behind a Done button, and the path ran with no INTENT_GATE so a turn the
   router had sent to memory was still claimed.
3. **A loose pattern stored a sentence as an item** (`intent_router`'s "STT-garbled leading add" matcher:
   `^(?:i'd|and|at|it|hey|a)?\s*(?:go to )?(.+?) to my <list> list`), plus no item hygiene (stutter, punctuation, filler).
4. **Parser gaps**: `to-do` / `to do` were not list names in the Skybridge add regex; "put X on my list" was not an add;
   `_calendar_create_from_text` accepted digit times only, ignored weekdays (`_calendar_date_from_text` had none) and kept
   "on Friday" inside the title; a loose list/calendar SHOW fallback claimed any unparsed add.
5. **The router_unsure allowance had no floor** for personal-data writes.

## The guard

`brain_dispatch.brain_streaming` / `brain_oneshot` is the one place every brain turn ends (voice stream and non-stream,
chat, Telegram, LiveKit). `_claim_guarded` wraps the lane stream (`write_claim_guard.guard_stream`); the one-shot form is
`guard_text`. The replay harness (`replay_isolation`) is never guarded: its writes are deliberately not committed.

* **Receipts** (`write_receipts.record(user_id, kind, row_id, source)`) are recorded by the writers themselves, after the
  row exists: `calendar_service.create_event_record` (calendar), `list_service.add_item_to_list` (list, including a replayed
  add that deduped), `reminder_service.create_reminder_record` (reminder), the Skybridge inline event / list inserts, the
  Skybridge timer store (timer), the note executor (note), and the mcporter fallback tail of `execute_intent` for
  `list_add` / `calendar_create` / `reminder_create` / `note_create` (that subprocess writes in ANOTHER process) - but ONLY
  when the result is a JSON object with no `error` and `status` `added` / `created` (`_mcporter_write_landed`): `handle_tool`
  catches a DB / validation error and returns `{"error": ...}` with `isError` false, so exit 0 proves nothing; an empty,
  non-JSON or status-less result records nothing and the reply says it could not confirm the save. A Flue
  brain tool call reaches those writers through `/api/system/intent-dispatch`, so a tool that succeeded leaves a receipt and
  one that failed ("I need a title…") does not. Per user, in process, 64 newest, 1 h.
* **Claims** are data (`lexicons_data/write_claims.json`, languages `en` and `es`): claim verbs (`added`, `scheduled`,
  `booked`; `put/set/saved/created/made` only with a target noun), target nouns per kind (list / calendar / reminder / timer /
  note), openers, `other_targets` (contacts, memory, lights, playlist… - not this guard's kinds), and blockers (negation,
  irrealis, future, "want me to"). Questions, offers, refusals and promises are never claims; a 2nd-person subject ("you
  added") is not. A claim verb counts in the active ("I've added") and the completed passive: `been` ("has been added") and
  `was` / `were` ("The appointment was added to your calendar") - the latter needs a target noun unless the verb is `added`,
  so "The restaurant was fully booked" claims nothing. Modifiers between the subject and the verb never hide a
  claim: the lexicon's `adverbs` / `modifiers` lists ("just", "definitely", "again") count anywhere, and any word ending in a
  `modifier_suffixes` entry ("-ly", es "-mente") counts once a first-person subject stands before it
  (`_subject_then_modifiers`: "I've definitely / happily added", "we have now quietly gone ahead and set") - so "Emily added the
  milk" and "I think Mum added eggs" stay someone else's write. Every claim clause of a unit counts (`find_claim`
  returns the union of their kinds, so one receipt cannot vouch for a second claim); a trailing tag ("…, okay?", "… - right?",
  "… — okay?", "…: okay?", es "…, ¿vale?" - `tag_questions`, after a comma, semicolon, colon, em / en dash or a SPACED hyphen; "to-do" is
  never a separator) questions only the tag (a leading discourse word - `discourse_openers`: okay / so / well / right / hmm - is skipped before asking whether the unit opens with an interrogative, so "Okay, have I added milk — right?" stays a genuine question; a "¿" inside the unit is one too), while a question that opens with an interrogative word or "¿" stays
  exempt; "you" excludes only an ACTIVE claim ("The milk you mentioned has been added" is still a claim); and a claim verb that
  opens a clause is a claim only as a completion ellipsis ("Added the milk.", "Added it.", or with a target noun and no copula),
  never an adjective ("Scheduled maintenance starts at 2 PM", "Added sugar is unhealthy" - `ellipsis_objects`, en only). "is scheduled for Friday" describes the calendar and is not a claim
  (it is how an honest read-back of an existing event sounds); only "is added" is (`state_claim` in the lexicon). A TRAILING temporal clause ("… after you asked me to", "… when / once / before …" -
  `clause_temporals`, per-language data) is a clause of its own: its leading "after" blocks only IT, so "I've added milk to your
  shopping list, after you asked me to" is still a claim and needs a receipt. A LEADING one ("When I've added it, I'll say so")
  still blocks its clause; a tail that FOLLOWS a claim and itself reports a completed first-person write ("… after I've scheduled the physio") has its
  leading temporal word stripped and needs its own receipt (the first claim's receipt does not vouch for it), while a tail after a promise
  or denial ("I'll tell you after I've added it") stays blocked; a temporal word followed by nothing or by another temporal ("once before") splits nothing; a temporal word that starts a CLAUSE (`temporal_clause_starts` data: a subject such as "you" / "I've", es "de que" / "lo") splits, while one that starts a time PHRASE ("after lunch", "before dinner") is neither a split nor a blocker, so "I've put the dentist after lunch in your calendar" keeps its target (the skipped words stay as placeholders so word positions, and with them the 12-word streaming bound, do not move); and a
  present-tense passive before one ("Milk is added once you tap Done") stays a description of a pending form, not a claim.
* **In-progress claims** (live 2026-11-04, typed chat: "Adding balloons, blue streamers, and goldfish crackers to your shopping
  list now." with no tool call and no write - the past-tense-only lexicon missed it, and the Oxford comma before "and goldfish"
  split the target noun off the verb's clause). A claim verb with `"form": "progressive"` ("adding", "setting", "putting",
  "saving", "scheduling"…; extra subjects `progressive_subjects`: "I'm", "we're", and "I am" / "we are" via `progressive_aux`;
  a bare sentence-opening one needs an `implied_cues` word - "now", "for you" - since "Adding milk to your list by tapping +" is
  an instruction) or a `promise_verbs` base form ("add", "put", "set"…) that counts ONLY after a `promise_subjects` word ("I'll",
  "I will": `promise_blockers` lets that "will" block only the other forms) AND with an `immediacy` word in its clause ("now",
  "right away"), and never with an "if" anywhere in the unit, says the write is happening in THIS turn. A unit with ANY
  in-progress clause waits for the end of the turn as a whole (each kind still needs its own receipt). The brain may say that
  just BEFORE its tool call, so such a claim (`Claim.in_progress`) is judged at the END of the turn: `guard_stream` holds that
  unit - and everything after it - until a receipt backs it (re-checked on every delta and sentinel, so a tool that lands
  releases it at once) or the turn ends; `guard_text` sees the whole reply anyway. An untargeted "I'll add it", "Shall I add it?",
  "I can add it if you like", "I was adding…", "Adding salt makes it better" stay non-claims. STANDING promises (QA
  2026-11-05) need no immediacy word - see "Gaps closed 2026-11-05" below. A comma before a joiner's first word splits
  the clause only when the whole joiner follows or a new subject follows it ("…, and I'm not sure about eggs"), so a list's last
  item keeps its verb's target.
* **Deferred writes**: the chat lane does not let the fast path write (`fast_tiers` `"chat": allow_writes False`), so
  `expert_dispatch` logs `EXPERT_DEFER_WRITE` and leaves the write to the brain. It also calls
  `write_receipts.note_deferred_write(kind)` (a context-bound marker, cleared at every dispatch, taken once, 60 s), and
  `brain_dispatch` passes the taken kind to the guard as `implied_kind`: an in-progress claim with no target noun of its own
  ("Sure, adding those for you now.") then claims that kind - only with an `implied_cues` word ("now", "for you") in its clause.
  So a deferred write that ends with no matching receipt never reaches the user as done; it is replaced by the honest line.
  (Re-asking the brain with the tool required was not chosen: a second 4B turn costs seconds and still cannot force the call.)
* **Verdict**: a claim naming kinds needs a receipt of every kind since the turn began (calendar and reminder back each
  other; a recap question - "did you add the milk?" - may stand on a write from the last 30 min). A claim naming no target
  ("I've added it") is backed by any receipt - and only a receipt. A tool's NAME is not evidence: the stream's `phase=start` /
  `phase=args` sentinels arrive before the tool has finished, so a tool that then failed (or an unrelated non-read tool)
  must not let "I've added it" through (review, #2004).
* **Action**: the claiming sentence is replaced - once per reply - by the language's honest line ("I didn't manage to add
  that; want me to try again?"), and `WRITE_CLAIM_BLOCKED action=replaced lane= user= lang= kinds= reason=no_receipt` is
  logged (kinds and counts only: the sentence is household text). `shadow` logs `action=shadow` and changes nothing.
* **Units and streaming**: a reply is judged in units - a sentence, or (once a sentence has run `LEAD_TOKENS` = 12 words)
  the part of it up to its next comma / semicolon / colon / dash. A claim verb must sit in the first 12 words of its unit. So
  the stream can decide from those 12 words alone whether a unit can ever become a claim: it holds a unit until then (or until
  it ends), and releases everything after at once, in its original deltas. Because the units and the verb bound are functions
  of the text, never of the cut, the streamed output is byte-identical to `guard_text` of the whole reply at every cut
  (`tests/test_write_claim_guard.py`: every single cut, char-by-char, word-by-word, and a seeded random property test).
  The cost: a sentence's first words reach a text-chat listener up to ~12 words later (about 0.35 s at the brain's decode
  rate); the voice path's first speakable unit is not delayed at all (it waits for a sentence end or ~60 characters anyway -
  simulated against `voice_tts._extract_first_unit`). Limit: a claim verb 13+ words into a unit with no comma / dash / full
  stop before it is not seen - in either mode. Tool sentinels pass through untouched.
* **Turn binding**: `write_receipts.begin()` mints a turn id bound to the calling context (`contextvars`); `record` stamps each
  receipt with the turn of the context it runs in; a verdict counts only its own turn's receipts and unstamped ones. A panel
  turn and a chat turn overlapping for one user therefore cannot vouch for each other's in-process write. A brain TOOL write
  (a separate `/api/system/intent-dispatch` request) is unstamped and counts for every turn of the user - the safe direction;
  closing it needs the turn id echoed on the dispatch call (ledgered).

## Gaps closed 2026-11-05 (hands-on QA round 2, typed chat)

Replies that claimed a write with no tool call and passed the guard: "I'll remind you to call the plumber at 2:30 pm
tomorrow.", "I'll add X, Y and Z to your to-do list.", "I've taken that lunch event off your calendar.", "…, got that noted.",
"I've removed the note about you being …", "I'll remember …". All are now claims, each against its OWN receipt kind - all
of it lexicon data (`write_claims.json`), no word list in code:

* **Standing promises** (`standing_promises`): a `promise_subjects` word ("I'll", "I will", now also "let me" / "let's") + a
  promise verb with NO immediacy word is an in-progress claim (judged at the end of the turn, like "I'll add it now"). Not
  when the unit holds a `promise_deferrals` word ("later", "as soon as") or an "if", opens with a temporal clause ("After I've
  added milk, I'll set a reminder"), or the promise is followed by a temporal clause ("… once you confirm"); and not at all in
  a turn whose user opened with a describe-question (`standing_skip_openers`: "what reminders have I got?" -> "I'll remind you
  at 2:30" describes). New promise verbs: `remind` (reminder), `remember` (memory), `note` (note), `forget` (forget), and the
  removals below.
* **Removals** (verbs with `"action": "remove"`: removed / deleted / cancelled / cleared / erased / wiped, `taken|took …
  off|out`, `crossed … off`, their -ing forms and base-form promises): a removal claims the REMOVAL kind of its target
  (`removal_kinds`: list -> `list_removed`, calendar -> `calendar_removed`, reminder -> `reminder_removed`, timer/alarm ->
  `timer_removed`, a note or a memory -> `forget`). A create receipt of the same target never vouches for a removal.
  `particles` must follow the verb in its clause, so "I've taken note of that" is no removal.
* **Memory / forget**: `memory` is now a target kind (it was an `other_target`, i.e. ignored); "forgotten that/it" and the
  `claim_phrases` "consider it forgotten" / "that's forgotten" claim `forget`; "got that noted" / "noted that down" /
  "consider it done" claim any write.
* **Receipts**: `write_receipts.KINDS` gained `memory`, `forget`, `list_removed`, `calendar_removed`, `reminder_removed`,
  `timer_removed` (note<->memory and calendar_removed<->reminder_removed back each other). Writers: the `memory_store` intent
  (the brain's `remember_fact` / `remember_emotional_moment`) records `memory`; `memory_forgotten.add` (every forget-shield
  entry) and `memory_retire` (a retired note) record `forget`; `_execute_list_remove_direct` (the brain's `list_remove`)
  records `list_removed`. The brain has NO calendar / reminder / alarm delete tool, so such a removal claim in a brain turn is
  always replaced - which is the truth.
* **Honest lines per kind** (`honest_by_kind`): a memory claim gets "I didn't manage to save that…", a forget "I haven't forgotten
  anything yet. Tell me what to forget, ..." (also what a blocked memory/note claim becomes on a turn that ASKED to forget -
  never "I didn't manage to save that", QA round 4), a removal "I didn't manage to remove that, so it's still there." - "want me
  to try again?" is not offered where no tool could do it.

## Device switches (2026-11-07) - receipt kind `device`

The owner's panel, a misheard quiet "turn on the light": the router guessed music, the brain had no tools offered, and it said
"I'll turn the lights on for you." - no tool call, the light stayed off. A switch was not a kind this guard owned ("lights" sat in
`other_targets`), so nothing stopped it.

* **Claims** (en data): `turned` / `switched` / `flicked` / `flipped` + an on/off particle, the progressive `turning` /
  `switching` / `flicking`, the promise `turn` / `switch` / `flick` / `flip` after "I'll" / "let me", and `claim_phrases` "done,
  the light's on" (and its off / plural forms). These verbs KEEP their own kind (the nouns around them never re-file it) and claim
  only with a device object in their clause (`kind_objects.device`: light, lamp, fan, heater, tv, it, them ...): "I turned it over
  in my mind" and "It turned out fine" are not claims; a state read-back ("The light's on.") is not either.
* **Receipts**: `smart_home_service.record_device_receipt` records one `device` receipt per entity the hub CONFIRMED
  (`controlled_entities`) - called from the Skybridge smart-home branch (voice fast path, panel taps) and from
  `intent_router._execute_smart_home_intent` (the brain's `home` tool through `/api/system/intent-dispatch`). A refused,
  offline or ambiguous switch records nothing. A device receipt never backs a WRITE claim, untargeted ones included
  (`write_receipts.NOT_A_WRITE`), and no write receipt backs a switch.
* **Honest line**: "I didn't manage to switch that, so nothing's changed. Want me to try again?"; `correction_kinds.device`
  feeds the session correction note. `authority_table.json` proves `smart_home` with the same receipt (`problem_loop`).
* Spanish has no device claim words yet (no native review). Pinned by `tests/test_garbled_light_first_turn.py` (`ci_safe`, the
  every-cut stream equality included).

## Session poisoning (2026-11-05) - `claim_correction.py`, `ZOE_WRITE_CLAIM_CORRECTION`

A replaced claim poisoned the rest of its session: after one timer request was answered with a claim and no tool call, every
later timer / alarm / reminder request in the session (and "yes, try again") came back with no tool call; a fresh session
worked. Mechanism: the Flue sidecar keeps its OWN per-session history of what the model GENERATED, so it held the model's
original claim - a write "done" with no tool call - which the 4B model copies on later write turns. The user saw the honest
line; the brain never did. (The honest line itself is in `chat_messages`, which reaches a brain only via
`flue_fastpath_context` rows or the core lane's `history`.)

Fix (flag-dark, `ZOE_WRITE_CLAIM_CORRECTION=1` to enable): when the guard REPLACES a claim (enforce mode, not shadow) it notes
the claimed kinds for the session (`brain_dispatch` passes `session_id`); the session's next Flue turn opens with one plain line
before the user's words (`correction_note` in the lexicon: "(Note: my previous reply said a timer or alarm was done, but
nothing was saved - no successful tool call backed it - and the user was told so. A change only happens through a successful
tool call; if no tool can make it, say so.)"). It is a fact, not a "tools fail" lesson, and not a delimited `[MEMORY CONTEXT`
block, so the sidecar's stale-block elision keeps it in history beside the claim it corrects. Once per replaced reply (kinds
merged), 30 min, 256 sessions, in process. `claim_correction.history_safe` swaps any honest line for the neutral
`history_marker` ("(nothing was saved in that reply)") in the `flue_fastpath_context` block and the chat lane's `history`.
Pinned by `tests/test_session_poison_claims.py` (a multi-turn test drives the real `brain_oneshot` -> guard -> Flue client
against a fake sidecar that copies its last write turn, as the live model did: flag on, turn 2 calls the tool; flag off, it
does not).

**Timer status reads are NOT tool-backed.** The brain has `set_timer` but no timer list/status tool (zoe-data has
`skybridge_service.active_timers_for`, no intent exposes it), so "how long's left on the pasta timer?" reaching the brain is
answered from its history ("still running for 12 minutes" - invented). Ledgered in `open-problems.md`.

## Parser and gate fixes that came with it

* `list_service.clean_item_text` / `is_plausible_item` - ONE cleaner (stutter, fillers, edge punctuation) and ONE refusal
  (a sentence, a vague noun, a pronoun/wh-word opener, > 12 words) used by both the Skybridge and the `intent_router` parsers.
  After a possessive, an auxiliary-looking word (`can`, `will`) is the NOUN of the item at the end of the phrase ("my spare can") or
  when it opens a known compound (`_NOUN_COMPOUNDS` data: "my tin can OPENER", "my brother's can opener", "our can OF paint", "my
  last will AND TESTAMENT"); "my phone can ring" / "my phone will not work" stay sentences. A new compound is a new row there. A LONE tail
  word that can be a verb or preposition ("label", "of") never exempts a predicate: those rows need a second token ("can of <noun>",
  "can label maker"), so "my printer can label packages" / "my printer can of course print" stay sentences.
* Skybridge `_list_add_from_text`: `add|put … to|on|in (the|my|our) <list>`, list names incl. `to-do`, `to do`, `todo`,
  `bucket`; the verb must open a COMMAND (`_command_shaped`: only lead-in words before it).
* Skybridge calendar create: `add|schedule|book|put|set up|create|make X (on <day>) at <time>` and "what/how about a
  <event noun> … at <time>"; spoken times ("twelve o'clock", "nine thirty am", "noon"); weekdays (next occurrence);
  default title from the noun ("Doctor's appointment"); a bare "at 9" is still not guessed. The read-back names the day and
  the time ("Added Doctor's appointment on Friday at 12 PM.").
* `intent_router._extract_simple_calendar_slots`: the legacy `calendar_create` fills title/day/time from the same parsers
  before the NLU filler (which had returned no tool call twice on the live sentence).
* `skybridge_router_gate`: a READ that contradicts a command-shaped add/put/create in the words is vetoed
  (`verb_contradiction`); an `router_unsure` verdict for a WRITE-like turn on a personal-data domain needs router confidence
  >= `ZOE_SKYBRIDGE_UNSURE_FLOOR_WRITE` (default 0.45; 0 = off) else `router_unsure_low_conf`. Plain reads keep the
  garbled-STT recovery.
* `voice_tts` quick `list_add` / `calendar_create`: behind the INTENT_GATE; when `push.PushBroadcaster.panel_live(panel_id)`
  is false the write is done NOW and the executor's true read-back is spoken, never "check the panel"; when the panel IS live
  the reply no longer claims the write ("Tap Done on the panel to save X to your list.").
* `push.PushBroadcaster.panel_live(panel_id, max_ack_age_s=None)` / `note_panel_ack(panel_id)`: a subscriber on the panel's
  own channel, and a fresh ack when acks are tracked - the hook the panel-liveness work feeds.

## Open (see `open-problems.md`, "False write claims")

The brain still sometimes skips an offered write tool - the guard makes that visible and honest, it does not make the model
call the tool. On the chat lane every add is deferred to the brain (`allow_writes` False); letting chat's fast path write, as
voice and Telegram do, is a separate decision. The change touches the voice path (`voice_tts.py`, `brain_dispatch.py`): it must be replay-gated before deploy.

## A tool call printed as text (2026-11-07, QA round 3b) - `src/text-tool-calls.ts`, `tool_text_guard.py`

The 4B brain sometimes PRINTS a call instead of making it: "chuck swim goggles on my to-do list" (a fresh session, the lists
group disclosed) came back as the reply text `add_to_list(item="swim goggles", list_type="tasks/to-do")`. llama-server returned
it as content, so there was no tool call, nothing was written and the user saw code. Two layers now:

* **The sidecar runs it** (`labs/flue-zoe-brain-2x/src/text-tool-calls.ts`, `ZOE_BRAIN_TEXT_TOOLCALL_RECOVERY`, default on;
  `off` restores the provider stream exactly). The provider stream holds the reply's lead only while it could still open
  `name(` (prose is released at its first non-identifier character). A reply that is exactly ONE call of a tool DISCLOSED to
  that round, with keyword (or one JSON object) literal arguments that fit the tool's schema, becomes a real tool call: the
  agent loop runs it with every normal gate (writes switch, replay isolation, untrusted turn) and the model answers from the
  result. Logged `TEXT_TOOLCALL_RECOVERED tool=<name>`. Anything else stays text.
* **zoe-data never shows the rest** (`tool_text_guard.py`, `ZOE_TOOL_TEXT_GUARD`, default on), in `brain_dispatch` beside this
  guard: a reply that opens with call syntax loses its call lines, and an empty remainder becomes "Sorry, that didn't go
  through on my end. Could you ask me that again?". Logged `TOOL_TEXT_BLOCKED tool=<name> replaced=<bool>`.

The list tools also stopped mapping an unrecognised `list_type` to `shopping` (the model sent "tasks/to-do" and "pharmacy
study list"; either would have landed on the HOUSEHOLD list): the sidecar passes the name through and zoe-data resolves it like
a spoken name (`intent_router._canonical_list_slots`): a to-do name is the member's own list, an unknown name is asked about.

## Contact delete (QA round 5, 2026-11-08) - receipt kind `contact_removed`

The brain said "I've deleted Tom from your contacts" after a `people` SEARCH call; the contact was still there. No tool can
delete a contact (contacts are soft-deleted only from the People page), so the claim can never have a receipt. `contact` is an
`other_targets` word (a contact ADD is another writer's claim and stays untracked), so the delete verb fell through the target
check. `write_claims.json` now has `removal_only_targets` (`contact` / `contacts`; es `contacto(s)`): a REMOVAL verb with one of
them claims `contact_removed`, which nothing records, so the claim is always replaced with the `honest_by_kind` line ("I can't
delete a contact yet, so they are still in your contacts. You can remove them from the People page."). An add, an offer
("want me to delete...") and a refusal are not claims. If a real contact-delete tool is ever added it must call
`write_receipts.record(user_id, "contact_removed", ...)` after the row is soft-deleted, and this line can go. Pinned by
`tests/test_qa5_memory_fixes.py`.

