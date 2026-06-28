# Flue brain — parity & latency results (2026-06-28, lab)

Ran `parity/parity_check.py` against the live **current prod brain** (zoe-data
`/api/chat`, the Pi-CLI brain) and the **Flue brain sidecar** (this spike) on a
representative prompt set. This is the cutover **gate**: it shows how close the
Flue brain is, and what's left before any production cutover.

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
   tool/memory rows reach parity.
2. **Voice-corpus replay** (`tests/replay_samples.py` / `~/.zoe-voice-samples`)
   said-vs-did parity, end-to-end through the voice path.
3. **#735 latency probe** no-regression under the Flue brain.
4. **Operator sign-off**, reversible flip (`_USE_ZOE_CORE`), Pi-CLI brain kept as
   fallback until soaked.

Per `docs/architecture/zoe-flue-integration.md` §4–§6.
