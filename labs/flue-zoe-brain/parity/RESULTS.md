# Flue brain — parity & latency results (lab)

Ran `parity/parity_check.py` against the live **current prod brain** (zoe-data
`/api/chat`, the Pi-CLI brain) and the **Flue brain sidecar** (this spike) on a
representative prompt set. This is the cutover **gate**: it shows how close the
Flue brain is, and what's left before any production cutover.

---

## Tool-BREADTH gate (2026-07-07, lab) — 4/6 tool families work, 3 misroute bugs found

`parity/tool_breadth_gate.py` closes the breadth gap: the older gates
(`parity_check.py` / `hard_gate.py` / `tool_reliability.py`) only exercise ~6 of
the brain's ~19 registered Zoe tools end-to-end. This gate drives the LIVE prod
path (`/api/chat` on :8000, routed through the Flue brain since 2026-07-03) as
the authed `parity-gate-user`, with PER-RUN nonce'd per-turn session ids, and
verifies **said-vs-did against Postgres ground truth** — a reply that CLAIMS a
write while the DB shows nothing is a FAIL. Read tools are anchored to a
DB-proven write (list_reminders/note_search/people_search must surface the
nonce), and `get_weather` (no DB side-effect) is verified by response sanity.
Every nonce'd row (incl. misroute leaks into `list_items`) is soft-deleted at
the end.

Result (two consecutive runs, stable): **PASS reminders (add+list), timers
(fail-closed honesty), weather (named location)**; **FAIL journal, notes, people**
— all three write tools MISROUTE and lie about it:

| Tool family | Verdict | Ground-truth finding |
|---|---|---|
| reminders add + list | PASS | reminder lands in `reminders`; list surfaces it |
| set_timer | PASS | fail-closed — never fabricates a timer the backend can't schedule |
| get_weather (named loc) | PASS | plausible weather reply, no stall/fabrication |
| journal create | **FAIL** | "Added … **to your shopping list**" — misrouted to `list_add`; nothing in `journal_entries` |
| note create | **FAIL** | "I'll remember …" — misrouted to `remember_fact`; nothing in `notes` |
| note_search (read) | **FAIL** | hits the **research-trap STALL** ("what budget/location…") — the regression `hard_gate` guards elsewhere |
| people create | **FAIL** | "Added … **to your shopping list**" — misrouted to `list_add`; nothing in `people` |

The misrouted journal/people writes were confirmed to land as nonce'd
`list_items` rows (the shopping list), which the gate now cleans up. Each FAIL is
a said-vs-did honesty defect (the brain claims success for a tool it never
called) and should become its own fix ticket:
1. journal_create misroutes to list_add;
2. note_create misroutes to remember_fact;
3. note_search triggers the research-stall instead of searching;
4. people_create misroutes to list_add.

Run: `python3 labs/flue-zoe-brain/parity/tool_breadth_gate.py` (full) or `--dry`
(2-tool smoke, for a merge-train window). Latest results:
`parity/tool_breadth_gate_results.json`.

---

## Phase 3, increment 1 — REAL tools wired (2026-06-28, lab) — the headline finding

The gap from the increment-1 (persona-only) run below was **no tools**. This
increment wires three real Flue `defineTool` tools onto the Zoe agent, each
calling **zoe-data's existing internal capability endpoints** over HTTP (the same
seam the prod Pi brain uses):

| Tool | Endpoint | Maps to prod |
|---|---|---|
| `get_time` | (local clock, no network) | `abilities/info.ts` time/date |
| `recall_memory` | `GET /api/memories/for-prompt?user_id&message` | `extensions/memory.ts` |
| `shopping_list_add` | `POST /api/system/intent-dispatch` `{intent:"list_add"}` | `abilities/_dispatch.ts` |

Identity is bound in **trusted code** — the acting `user_id` comes from
`ZOE_BRAIN_USER_ID` (env), never from the model's tool arguments (tool args are
model-chosen, not an auth boundary). User-scoped tools **fail closed** when no
user is configured. `shopping_list_add` is gated behind `ZOE_BRAIN_ALLOW_WRITES`
(default **OFF → dry-run**), so a parity run never mutates the real list.

### Does Gemma reliably call the tools? — **YES, in this run.**

This was the open question for the cutover decision. On the parity prompt set and
direct smoke tests, **Gemma E4B called every tool it should have**, first try:

| Row | Flue reply | Tool actually fired? |
|---|---|---|
| `tool:time` "What's the time right now?" | "It's 10:33 pm on Sunday, June 28, 2026." | ✅ `get_time` (prod gave "10:33 PM" — **parity**) |
| `tool:list` "Add milk to my shopping list." | "Okay, I've added milk to your shopping list." | ✅ `shopping_list_add` (dry-run) — **parity with prod's phrasing** |
| smoke: "What do you remember about my family?" | Returned Neil / Janice / Karen / Julie / NCIS — **real stored facts** | ✅ `recall_memory` hit `/api/memories/for-prompt` for user `jason` |

The increment-1 "expected gap" rows (time/list) are **now closed.** The Flue brain
*does* what prod does, in Zoe's voice, and still **faster** (median **~3.0 s** vs
prod **~8.3 s** this run).

### Honest caveats (these matter for the cutover decision)

- **Single run, n=1 per prompt.** Tool-calling reliability on a 4B local model is
  the whole risk; one clean pass is encouraging but **not** a reliability proof.
  Before cutover this needs a **repeated / fuzzed** tool-call harness (paraphrases,
  ambiguous phrasings, negative cases) to measure call-rate and false-fire-rate.
  Prod mitigates small-model tool drowning with **progressive disclosure**
  (`abilities.ts` `setActiveTools`); the Flue agent here exposes **all 3 tools every
  turn** — fine at 3, but won't scale to prod's ~56 abilities without the same
  relevance gating. That gate is **not yet ported.**
- **`shopping_list_add` dry-run masks a real-write unknown.** With writes OFF the
  tool returns a dry-run string, but **Gemma paraphrased it as success** ("I've
  added milk") rather than relaying the "(dry-run)" caveat. Verified against
  zoe-data that the list was **NOT** mutated — the gate held — but it means the
  model's *spoken* result can diverge from the *actual* effect. A live-write parity
  run (writes ON, a throwaway item, then cleanup) is still owed before trusting the
  write path end-to-end.
- **`tool:memory` row ("What did I just ask you about?") is still harness-limited,
  not a tool result.** That prompt is a *within-session context* question, not a
  recall query, so neither brain hit memory; both answered conversationally. The
  *recall* path is proven by the family-facts smoke test above, not by this row.
  A proper memory parity row needs a single multi-turn session.
- Only **time / list / memory** are wired — calendar, reminders, timers, notes,
  people, etc. (all reachable via the same `intent-dispatch` allowlist) are **not**
  yet ported.

**Bottom line for cutover:** the architecture works — Gemma calls real tools that
reuse prod's exact backend, and the time/list gap closes. The remaining risk is
**reliability at scale** (more tools + progressive disclosure + a repeated tool-call
benchmark) and the **write-path verification**, not feasibility.

---

## Increment 1 (persona-only) — original baseline run

## Verdict

| Dimension | Result |
|---|---|
| **Persona / conversation** | ✅ **Parity.** Flue brain answers in Zoe's voice — warm, curious, present — matching the prod brain (arguably cleaner/more concise). |
| **Knowledge / reasoning** | ✅ **Parity.** Both correct ("Paris"; "2 apples"). Flue brain is more direct; the prod brain rambled (its session had accumulated a fictional "Alice" persona). |
| **Tools (time / list)** | ⚠️ **Expected gap.** Flue brain (increment 1, persona-only) has no tools — it **honestly declined** ("where would you like me to add milk?") rather than fabricating. Prod did them via Zoe's abilities. **This is the Phase-3 work.** |
| **Memory** | ⚠️ **Not yet (and harness-limited).** Flue brain has no MemPalace; also the harness used a fresh session per prompt, so even within-run context wasn't exercised — retest with one session + the memory tool in Phase 3. |
| **Latency** | 🚀 **Flue brain is FASTER.** Median **~4.0 s** vs prod **~14.5 s** in this run. |

## Latency detail

```
PROD (/api/chat): median=14523ms  max=22232ms  n=8
FLUE brain      : median= 3952ms  max=12182ms  n=8
```

**Honest caveat on the numbers:** the harness used a *fresh session per prompt*,
so every prod call paid the **cold `pi --mode rpc` subprocess-spawn** penalty
(worst case). A warm prod session is faster (~5–8 s per earlier measurements).
But that's exactly the point: the Flue brain is an **in-process Pi `Agent`** with
**no per-turn subprocess spawn, no LRU worker pool, no RPC-stream parsing** — so
it has no cold penalty and is competitive-to-faster even against warm prod. This
directly serves the "optimised to be quick on Zoe" goal and validates the
architecture's core bet (collapse the process boundary onto in-process Pi).

## Gate for cutover (do NOT cut over until all green)

1. **Phase 3 — wire tools/memory** (time, lists, calendar, abilities, MemPalace)
   into the Flue brain via `defineTool`/MCP, then re-run this harness until the
   tool/memory rows reach parity. **(increment 1 of tools DONE — time/list/memory
   via `defineTool` → zoe-data endpoints; time/list rows now at parity. Still owed:
   the remaining abilities, progressive-disclosure relevance gating, a repeated
   tool-call reliability benchmark, and a live-write parity run — see the Phase-3
   section at the top.)**
2. **Voice-corpus replay** (`tests/replay_samples.py` / `~/.zoe-voice-samples`)
   said-vs-did parity, end-to-end through the voice path.
3. **#735 latency probe** no-regression under the Flue brain.
4. **Operator sign-off**, reversible flip (`_USE_ZOE_CORE`), Pi-CLI brain kept as
   fallback until soaked.

Per `docs/architecture/zoe-flue-integration.md` §4–§6.
