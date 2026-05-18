# Zoe Agent Improvement Brief
_Written: May 18, 2026_

---

## Audit: Zoe Agent

### What Zoe Agent Does

Zoe Agent is the on-device LLM brain loop running on the Jetson. It talks to a local Gemma model served via llama-server on port 8080 (OpenAI-compatible API).

**What it knows about the user:**
- Portrait: a synthesised "who is this person" summary loaded from the DB
- MemPalace: stored facts + semantic search for relevant memories
- Open loops: unfinished emotional/follow-up threads
- DB memory context: pre-loaded facts passed in from chat router

**Tools by category:**
- Memory: search/add/update stored facts
- Smart home: Home Assistant device control (lights, switches, scenes)
- Calendar & reminders: read/create events and reminders, schedule future notifications
- Lists: add/read shopping and personal lists
- Weather: current conditions and forecast
- Touch panel / UI: open Zoe pages on panel (weather, calendar, lists)
- System safe bash: whitelist of shell checks (disk, processes)
- Web search: DuckDuckGo-based live search
- Visual chat: maps, charts, button menus in the chat UI
- Escalation: OpenClaw for heavy/browser tasks; optional Hermes for deep reasoning

**Skill routing (`_select_skills` / `_build_tools`):**
For each chat message, keyword matching selects which tool groups to expose to the model — reducing prefill cost. Always-on tools: search, escalation, quick menus. If nothing matches, falls back to discovery. `FORCE_FULL_CONTEXT` override loads everything.

**Voice vs Chat:**
- Chat: long stable system prompt (ZOE_SELF.md embedded), skill-filtered tools, up to 5 tool rounds, history from DB
- Voice: shorter prompt, tighter timeouts, 2 tool rounds, fewer tokens, fast shortcut paths — VOICE PATH HAD NO HISTORY WINDOW (fixed in this session — now passes last 3 turns)

**KV cache warmup:** On startup, a dummy request warms the model's internal cache with the static system prompt so the first real query is fast.

**Escalation path:** Model calls `escalate_to_openclaw` → returns `__ESCALATE__:` marker → chat router switches to OpenClaw. Voice uses background escalation.

---

### Honest Weaknesses

1. Skill routing is keyword-based — brittle, unusual phrasing loads wrong tools
2. Two local inference paths with different servers: `GEMMA_SERVER_URL` (chat completions, used by zoe_agent + nlu_extractor) vs `LLAMA_SERVER_URL` (/completion endpoint, used by new Tier 0.5 classifier) — two things must be healthy
3. `memory_update` tool defined but NOT in any skill group or always-on list — model may never be able to call it in normal chat
4. Voice is intentionally shallow (2 tool steps) — multi-step voice tasks often can't complete without escalation
5. Memory semantic search skipped on many turns by keyword gate — saves compute but can miss softer recall questions
6. Web search depends on DuckDuckGo — can fail/thin out when blocked
7. Follow-up context (ConversationContext) is narrow — only volume/music coreference, not calendar/lists/reminders
8. `zoe_agent.py` is ~2700 lines — prompts, policies, tools, shortcuts, streaming all in one file

---

### Prioritised Improvements (impact order)

1. **Smarter skill routing** — use Tier 0.5 classifier or similar to choose tool groups, not just keywords. Fewer "didn't understand" moments. Medium-hard effort.

2. **Single documented inference stack** — consolidate `GEMMA_SERVER_URL` and `LLAMA_SERVER_URL` into one clear config or add a health panel showing both. Medium effort.

3. **Fix `memory_update` exposure** — add `memory_update` to the right skill group or always-on tools so "remember that I..." reliably uses it. Easy-medium effort.

4. **Richer follow-up context** — extend ConversationContext to resolve "add it", "same time", "the milk" for calendar, lists, reminders — not only volume. Medium effort.

5. **Voice: smarter fast path** — widen shortcuts for common home tasks, allow one extra tool step when needed, reduce unnecessary escalations. Medium effort (latency-sensitive).

6. **Reliable web answers** — pluggable search or clearer "couldn't verify live data" behaviour when DDG fails. Medium effort.

7. **Better escalation UX** — always show a clear status message + what OpenClaw is doing when escalation fires. Medium effort.

8. **Split `zoe_agent.py`** — move prompts, tool tables, shortcuts into separate modules. Hard (refactor risk), but unlocks safer improvement of everything above.

---

### What was already fixed in this session (May 18 2026)

- Added ConversationContext slot cache — coreference resolution for volume/music follow-ups ("make it 80" after volume context)
- Fixed voice history gap — voice LLM fallback now passes last 3 turns of history (was zero)
- Added Tier 0.5 LLM classifier — confidence-scored JSON classification for short missed utterances between regex and full agent
- Renamed all "Pi Agent" references to "Zoe Agent" across the entire codebase

---

## Prompt to start improvement plan in a new chat

```
I want to work on improving Zoe Agent — Zoe's on-device LLM brain loop running on a Jetson Orin NX.

Please read the full audit and improvement brief at `/home/zoe/assistant/docs/zoe-agent-improvement-brief.md` before we start.

Key files to understand:
- `/home/zoe/assistant/services/zoe-data/zoe_agent.py` — the main agent (~2700 lines)
- `/home/zoe/assistant/services/zoe-data/conversation_context.py` — new context cache (added May 18 2026)
- `/home/zoe/assistant/services/zoe-data/intent_classifier_llm.py` — new Tier 0.5 classifier (added May 18 2026)
- `/home/zoe/assistant/services/zoe-data/nlu_extractor.py` — slot extraction for create intents
- `/home/zoe/assistant/services/zoe-data/intent_router.py` — 45-intent regex cascade

The 8 prioritised improvements are in the brief. I'd like you to:
1. Dive deep into the codebase to validate these findings and check nothing has changed
2. Propose a sequenced plan starting with the highest-impact, lowest-risk items
3. Before building anything, confirm which improvement I want to tackle first

Do NOT start implementing yet — plan and confirm first.
```
