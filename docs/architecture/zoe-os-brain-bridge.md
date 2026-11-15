---
type: architecture
title: Zoe OS and the brain - the brain proposes, the kernel disposes
date: 2026-11-15
status: lab proof (labs/zoe-os/zoe_os/brain/). Nothing here is wired to production; no flag exists; the live sidecar is untouched.
prototype: labs/zoe-os/zoe_os/brain/ (adapter, kernel-side drivers, scripted and recorded drivers), labs/zoe-os/tests/test_brain_bridge.py, labs/zoe-os/bench_brain.py
related: zoe-os.md, zoe-flue-integration.md, ../CANONICAL.md
---

# The brain proposes, the kernel disposes

Plain words first. Today Zoe's brain (the Pi agent that Flue runs, on the local model) has tools that write
straight into the stores: add to a list, set a reminder. Nothing in between records why, can answer "why did you
do that?", can undo it, or removes the effect when a person is forgotten. [`zoe-os.md`](zoe-os.md) describes a
kernel that does those things for every feature. This page is about the one seam that matters most: the brain.

The rule being proved: **the brain never touches a store. It proposes an action; the kernel decides, performs,
records a receipt, and can explain, undo and forget it.**

## Where Pi and Flue sit

    voice surface --(records the utterance, names the speaker)--> kernel
         |                                                          ^   decides: manifest, the person's grant,
         v                                                          |   the cited cause, the arguments;
    turn = (member, utterance)                                      |   performs; read-back; receipt
         |                                                          |
    Pi agent, run by Flue --tool call--> bridge adapter --act--> Unix socket
         ^                                     |
         +----- one sentence from the receipt -+

* **Pi and Flue** stay what they are: the model, the tool-calling loop, the streaming. Only the tools change.
* **The bridge adapter** (`zoe_os/brain/bridge.py`) is the only thing the brain process holds besides its own
  credential. It has no database path, no driver, no store, no member identity of its own.
* **The kernel** is the only writer. Its drivers (`zoe_os/brain/drivers.py`) live in the kernel process.
* **Who the turn is for** comes from the trusted voice surface as a `Turn` (member, utterance number). The model
  never writes either, so a tool has no `member` argument to be talked into.

## What the bridge does

1. **Derives the tool list** from the kernel (`describe`: the actions the brain's manifest names that declared
   their parameters). Nothing is hand-written on the brain side. Parameters marked `from: turn` are hidden from
   the model and filled from the turn. An action that needs a person's own yes is not offered (the brain cannot
   give it).
2. **Turns a tool call into one `act`** over the socket. The kernel answers with a receipt or a refusal.
3. **Gives the model one sentence built by code** (done and confirmed / tried and could not confirm / refused /
   invalid / kernel unreachable / unknown), never the receipt, never a store row.

## What was proven, and with which driver

Driver key: **scripted** = a deterministic stand-in that emits the calls a real agent would; **recorded** = the
sidecar's own stream format replayed; **real** = a real Pi agent on the live model.

| Claim | Driver | Test |
|---|---|---|
| tool schemas come from the kernel registry; an unregistered or manifest-excluded action is not a tool | scripted | `test_tools_come_from_the_kernels_registry...` |
| propose -> act -> receipt -> "why" from the receipt -> undo (once only) for lists, reminders and alarms | direct proposals | `test_propose_act_receipt_explain_undo` (x3) |
| forgetting the member removes the event, the receipt, the effect and the bytes; others' rows stay | direct proposals | `test_forgetting_the_member...` (x3) |
| the loop runs from utterances | scripted | `test_a_scripted_agent_runs_the_whole_loop` |
| the sidecar's `__TOOL__` args frames replay into the same actions; the old tool result is never shown to the agent | recorded (a hand-built stream in the real format; **not captured from the live model**) | `test_a_recorded_turn_replays...` |
| a member without the grant is refused and the agent can say so honestly | scripted | `test_a_member_may_not_set_alarms...` |
| a prompt-injected call for another member changes nothing, at three layers (schema, kernel argument check, kernel cause rule) | scripted | `test_a_prompt_injected_call...` |
| malformed proposals change nothing, with the adapter's check on or off | scripted | `test_a_malformed_proposal...` |
| the kernel being down: "I could not do that, nothing changed"; lost mid-call: "could not confirm"; killed between write and receipt: `uncertain` after restart, never done, not undoable | scripted | `test_kernel_down...`, `..._lost_on_an_open_connection...`, `..._killed_between...` |
| the brain process is handed a socket and a credential and holds no kernel, server, enforcer or store code and no open file | scripted, child process | `test_the_brain_process_is_handed...` |

### Refusal matrix (every row also asserts the stores are unchanged)

| Brain proposes | Who stops it | What the agent is told |
|---|---|---|
| alarm for a member who did not grant it | kernel: the person's grant | "I am not permitted to do that for you. Nothing changed." |
| a call carrying `member`/`asked` | adapter schema; with the check off, kernel argument check | "the request was not valid. Nothing changed." |
| `on_behalf_of` another member (a compromised adapter), citing the speaker's utterance | kernel: the cited event must be about the person (new) | refused, recorded |
| an action outside the brain's manifest | kernel: the manifest is a ceiling | "not something I am able to do" |
| missing, wrong-typed, oversized arguments | kernel (new: declared `params`) | "the request was not valid. Nothing changed." |
| any call while the kernel is unreachable | adapter | "I cannot reach my action service. Nothing changed." |

## Latency of the hop

`labs/zoe-os/bench_brain.py`, 300 scripted actions (100 each of list / reminder / alarm), one kernel process on
the Jetson, read while the box carried its normal load (load average about 2.7). [lab]

| | p50 | p95 | p99 |
|---|---|---|---|
| `propose` end to end (adapter check, socket, authorise, intent, driver, read-back, receipt commit, reply) | 753 us | 950 us | 2.2 ms |
| the same driver call written straight to a store, in process | 1 us | 2 us | 6 us |
| the voice surface recording the utterance (every turn needs it already; not added by the bridge) | 354 us | 430 us | 1.3 ms |

So the hop costs roughly 0.75 ms at the median per action, dominated by the receipt's SQLite commit. A model
turn is seconds; this is not where speed is. It was measured against in-memory stores: a real store adds its own
write time to either design.

## What was NOT proven

* **A real model.** Whether Gemma 4 E4B, through Pi, calls these tools correctly and phrases the sentence it is
  given. Not run (see the next section). The scripted and recorded drivers prove the loop and the guards, not the model.
* **Recorded is not captured.** The stream fixture follows the sidecar's documented `__TOOL__` format
  (`labs/flue-zoe-brain-2x/src/streaming.ts`) but was written by hand.
* **The same-OS-user limit still stands.** A brain process under the kernel's own user can read the kernel's
  `/proc` entries (`test_HONEST_LIMIT_a_brain_running_as_the_kernels_user...` asserts it). The socket is not the
  whole boundary; file modes and a second OS user are. Shown elsewhere only in a root sandbox.
* **Real stores.** The three stores here are in-memory fakes in the kernel process. Production's stores are
  Postgres tables behind zoe-data intents; a kernel-side driver would call those, with the same read-back.
* **Consequential actions.** Only no-yes-needed actions are exposed. A yes-gated action needs the surface to
  record the person's yes first; the brain does not offer them.
* **Streaming and speed of speech.** The brain would now wait about a millisecond for each tool; the voice path's
  early-text behaviour was not exercised.

## Why the real turn was not run

The only way to run a real Pi turn against the already-running llama server is a second, lab-only sidecar on
another port. The live llama-server has `--parallel 1` (ONE slot, because draft-MTP is unsafe above one slot) and a
512 MB host prompt cache that every household prompt shares. A second agent has a different system prompt and a
different tool list, so each of its turns would take the slot, rotate the household's prefix out of the cache and
make the next household turn pay a full prefill. The repository's own harness refuses 07:00-22:00 local for this
reason; this was built at about 11:00 local. I could not show it is safe, so it was not run. `pi` is also not on
the box's PATH, so the `pi --mode rpc` route could not even be tried.

**What the landing session would need:**

1. A quiet window (no panel use for ten minutes, outside household hours, under `/tmp/zoe-voice-harness.lock`,
   not during a landing probe), using the harness's own guard (`scripts/maintenance/harness_quiet.py`).
2. A tiny Flue app, in the lab, that registers ONLY tools generated from `BrainBridge.tools()` (a `defineTool`
   per schema; `run()` forwards to a small local bridge process over stdio or HTTP that calls `propose` with the
   turn it was started for, and returns `Outcome.say`). Provider and capped-completions code reused unchanged;
   a throwaway token and port; synthetic member; a handful of turns; no edit to `labs/flue-zoe-brain-2x/`.
3. Read the cache report (`scripts/maintenance/brain_cache_report.py`) before and after to put a number on what
   the run cost the household's cache, and run `scripts/perf/measure_cold_first_turn.py` after to confirm a
   household turn is warm again.
4. Capture that stream once with `parity/flue_wire.py`'s `ask` and commit it (synthetic content only) as the
   recorded fixture, so the recorded driver becomes genuinely recorded.

## Next three steps

1. **Capture one real recorded turn** (step 4 above) so the model's own tool-call frames, including its mistakes,
   become a regression fixture for the adapter. Cheapest, and it answers the model question without a live loop.
2. **First live candidate: lists.** `list_add` is already the most-used write tool (the Wave 1 daily driver), is
   reversible, needs no yes, has a trivial read-back (the row is there), and a wrong write is a shopping-list
   line. Run it behind a flag, shadow first: the brain still uses its old tool, the bridge receives the same
   call in parallel against a scratch store, and the two receipts are compared for a week before anything
   switches. Reminders second (time parsing is where a model slips); alarms and anything that talks to a person
   last.
3. **Decide the owner questions the lab cannot:** whether the kernel's database becomes the household's single
   record (today production has its own tables, and a kernel-side driver is one more writer to keep in step),
   how the kernel is supervised and restarted (a down kernel means the brain can only say it could not act), and
   whether the confused-deputy rule below should stay universal.

## Changes needed in the kernel (two, separate commits)

* **Declared parameters and `describe`.** `Dispatcher.register(doc=, params=)`; `act` checks the arguments
  before the intent is written; a ninth wire op `describe` returns the caller's own manifest actions that declared
  params. Additive: an action without `params` behaves exactly as before.
* **Cause rule.** An action for a person must cite an event about that person (or the kernel's own forget notice
  for them). This closes a confused-deputy gap that was real before the brain: a skill granted by two people could
  act for one on the strength of the other's event (`test_cause_binding.py`). It changed the premise of five
  existing negative controls (they now switch this rule off too, and one expected refusal reason moved earlier in
  the chain). That is a decision for the owner, not an implementation detail.

## Running the real turn (the guarded runner)

Built, dry-run green, not yet run for real. One-time build (done in daylight, copies the sidecar's `node_modules`
read-only, never touches the live unit): `bash labs/zoe-os/prep_real_turn.sh`.

The real run (a fixed set of five synthetic turns, 5-minute wall clock, 200-token cap per model call, own port 3591):

    bash labs/zoe-os/run_real_turn.sh --i-own-the-slot --owner-approved-daytime

Drop `--owner-approved-daytime` to keep the 22:00-01:50 window; the 01:50-04:55 night span is never overridable.
`--dry-run` swaps the model for a mock llama and is safe at any time. It refuses (exit 3, reasons on stderr; exit 2 if
the harness lock is held) unless: the flag; the window; the lock; no python voice probe process; no lander
voice-hold; speech active; no night job active; every llama slot idle; MemAvailable above `--mem-floor` (1500);
zoe-data's app log untouched for `--log-quiet-s` (60) seconds. On any exit the lab app's process group, bridge and
kernel are stopped and the lock released.

Output: a table per turn (expected tool, tool called, right tool, arguments valid, kernel outcome, model ms vs kernel
hop ms), the kernel checks (receipt, why, undo, forget removes effects and bytes), and the cost to the household
(the slot's held prompt size before, which is the upper bound its next turn may re-prefill, plus counter deltas and
the llama journal's re-prefill and eviction counts). The recorded fixture (tool frames verbatim, model text reduced
to length and hash) is written to `--out` (default `~/.zoe/agent-tools/real-turn-<time>/recorded_turns.json`).
Differences from the live sidecar, stated: stock pi-ai wire (no text-tool-call recovery, no prompt policy).
