# Parity re-run fix packet — 2026-07-07

Cold-start-executable packet from the clean (confound-free) parity quality
re-run: flue live brain vs dormant prod core, both as fresh empty-store test
users, 46-prompt reconstruction of the 2026-07-03 corpus bias. Each item below
is one small PR. Scoring record: interim numbers in this file; the full-46
clean comparison lands in RESULTS.md once the flue re-run completes (the first
flue pass lost rows 31–46 to deploy-triggered zoe-data restarts mid-corpus).

## Re-scored headline (2026-07-07, 30 comparable pairs)

- Raw: flue 27/30 (90%) vs core 29/30 (96.7%).
- **But 2 of flue's 3 failures were NOT the brain**: zoe-data's fast-path
  research classifier intercepted the prompts before Seam-A (item 1 below).
  The core side was driven brain-direct and never crossed that code, so the
  raw comparison is pipeline-vs-brain, not brain-vs-brain, on those rows.
- **Brain-attributable: flue 29/30 (96.7%) = parity with core (29/30).**
  Flue's one real failure is a persona leak (item 2). Core's failure class is
  different: memory-honesty waffle — "deny-then-cite" ("you haven't told me
  yet! We talked about how much you love pizza") plus flat temporal-recall
  denials over its full 46 (43/46 = 93.5%).
- Latency withheld: the flue pass ran under merge-train restarts. The
  2026-07-03 clean latency verdict (flue ~2× faster) stands.

## Item 1 — zoe-data fast path: research classifier hijacks conversation (LIVE BUG, do first)

`services/zoe-data/research_evidence.py:93 classify_query` returns
`"research"` on **bare substrings** — `weekend`, `best`, `price`, `recipe`,
`events`, `deal`, `compare`, `cheapest`, `find me`, `flight`, `bottle shop` —
and `routers/chat.py:2110` then stalls the turn with
`_research_followup_prompt` ("Before I start research, I need a bit more
detail: What location… What budget…"). Observed live: "I enjoy hiking on
weekends" (a preference statement) and "What do I do on weekends?" (a memory
recall question) both hijacked; the brain never saw them. Any user saying
"best", "recipe", or "weekend" conversationally hits this today.

**Fix shape:** require a request/imperative frame, not a bare marker. A
message should classify `research` only when a research-verb frame is present
(`find (me)?|search|look up|compare|research|what's the (cheapest|best)|
where can i (buy|get)` …) — and never when it is (a) a first-person statement
(`^i .*(enjoy|love|like|went|had|am|'m)\b` without a request verb) or (b) a
recall question about the user themself (`what do i|did i|when did i|where do
i`). Keep true positives: "find me the cheapest flight to Bali", "compare NBN
plans", "best pizza near Geraldton".

**Tests (same file's test module or a new
`services/zoe-data/tests/test_research_classifier.py`, mark `ci_safe` if the
import is slim — `research_evidence.py` is stdlib+re):
"I enjoy hiking on weekends" → general; "What do I do on weekends?" → general;
"find me the cheapest flight to Bali" → research; "best bottle shop deals this
weekend" → research; "what is the capital of France" → simple_factual.**

**Gate:** `routers/chat.py` is the live chat/voice path → replay-gate
(`scripts/maintenance/voice_regression_probe.py` under
`flock /tmp/zoe-voice-harness.lock`) before merge. Watch the deploy-restart
treadmill: every merge to main restarts zoe-data (deploy.yml) — do not run
measurements mid-merge-train.

## Item 2 — flue brain: persona leak on identity questions

"What's your name again?" → *"My name is Gemma 4. I'm a large language model
developed by Google DeepMind."* One instance in 30, but it is the
worst-in-class failure for a companion. The soul (verbatim SOUL.md) plus the
doctrines in `labs/flue-zoe-brain/src/agents/zoe.ts` (ACTIVATOR_DOCTRINE,
VOICE_DELIVERY_DOCTRINE, IN_SESSION_CONTEXT_DOCTRINE) never state the
identity rule imperatively.

**Fix shape:** add a short IDENTITY_DOCTRINE block in `src/agents/zoe.ts`
following the existing exported-doctrine pattern (append after the soul, keep
it 2–3 lines for the 4B model): you are Zoe, always; never identify as Gemma,
a Google/DeepMind model, an LLM, or "an AI assistant" — if asked what you
are/your name, answer as Zoe. Export it and extend the existing doctrine
assembly + the offline unit tests (`test/tool_disclosure.test.ts` pattern —
assert the block is present in the assembled instructions; add a
`test/identity_doctrine.test.ts` asserting assembly order and content).

**Gate:** sidecar unit tests + re-ask the greetings block of the parity corpus
(3 prompts) through the live seam; no replay gate needed unless latency-path
files change, but rebuild + `systemctl --user restart flue-zoe-brain.service`
is required for it to go live (operator).

## Item 3 — (core-side, informational) memory-honesty waffle

Not scheduled — core is the dormant fallback. Recorded so the failure class
is not forgotten if core is ever revived: deny-then-cite contradictions and
flat "I'm an AI" temporal-recall denials. The flue-side equivalent was already
fixed by IN_SESSION_CONTEXT_DOCTRINE (#988); core has no such rebalance.

## Harness note for the next runner

Drive BOTH sides through the same surface. This run drove flue through
`/api/chat` (whole pipeline) but core brain-direct (`run_zoe_core`) — that
asymmetry is exactly what exposed item 1, but for a brain-vs-brain score
either drive both through Seam-A equivalents or subtract fast-path
interceptions explicitly, as done here.
