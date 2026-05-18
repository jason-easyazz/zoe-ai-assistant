# Multica Issue Capture — Design Brief
*Created: May 18 2026*

## What We Want

Users should be able to mention a problem, mistake, or improvement naturally in
conversation — without knowing Multica exists — and Zoe should silently log it,
create a Multica issue, and acknowledge it casually. The user never needs to say
"add issue to Multica." They just say what they'd say to any assistant.

**Examples of what should work:**
- "You got that wrong"
- "That didn't work"
- "You keep messing up timers"
- "There's a problem with the weather card"
- "Fix your music controls — they're broken"
- "You should know that X doesn't work"
- "Make a note that the lights aren't responding"
- "That was wrong, you need to sort that out"
- "I keep having issues with reminders"

---

## What Already Exists

### Frustration tracker (chat.py line ~436)
`record_frustration_signal()` is called inline from `chat.py` when the
frustration heuristic fires (repeated failures, negative sentiment). It creates
a `user_frustration` evolution proposal which auto-syncs to a Multica issue.

**Problem:** It's passive — triggered by repeated patterns, not by the user
explicitly naming a problem. A one-off complaint gets missed.

### Evolution notice (nightly)
`run_evolution_notice()` creates proposals from `intent_miss` logs and agent
health data. These sync to Multica automatically.

**Problem:** Runs nightly. User feedback sits unlogged until then.

### Evolution proposals review (intent: `evolution_proposals_review`)
User can ask "show me my evolution proposals" and see pending items.

### What's missing
There is **no intent** that catches a user *explicitly naming a problem* in
natural language and immediately logs it to Multica. One-off complaints,
specific bug reports, and direct "fix this" feedback are lost unless the
frustration threshold happens to trigger.

---

## The Gap to Fill

### New intent: `user_issue_report`

**Regex patterns to catch:**
```
you got that wrong | you keep getting X wrong
that didn't work | that's not working
there's a problem with X | there's an issue with X
fix your X | X is broken | X doesn't work
you should know that X | make a note that X
I keep having issues with X | you need to fix X
that was wrong | that was incorrect | that was right but then...
you messed up X | you failed at X
```

**When triggered:**
1. Extract a short description of what's wrong (raw text or LLM-extracted)
2. Write to `evolution_proposals` table as type `user_issue_report`
3. Call `sync_evolution_proposal_to_multica()` immediately
4. Reply naturally: *"Got it, I've made a note. I'll review that."*  
   (Never mention Multica by name)

### Zoe Agent tool: `report_issue`

For cases that don't match regex but the LLM recognises as issue/complaint
feedback during a conversation, expose a `report_issue(description)` tool in
`zoe_agent.py`. This lets the model log issues mid-conversation without an
explicit phrase match.

**Tool behaviour:** Same as the intent — writes to `evolution_proposals`,
syncs to Multica, returns a short acknowledgement string.

---

## Files to Change

| File | Change |
|------|--------|
| `services/zoe-data/intent_router.py` | Add `_USER_ISSUE_RE` regex + `user_issue_report` intent; add `execute_intent` handler that calls `record_user_issue()` |
| `services/zoe-data/evolution_notice.py` | Add `record_user_issue(message, user_id)` function (similar to `record_frustration_signal` but type = `user_issue_report`, fires immediately, no repeat threshold) |
| `services/zoe-data/zoe_agent.py` | Add `report_issue` tool to the always-on tool list; implement handler that calls `record_user_issue()` |
| `services/zoe-data/guest_policy.py` | Add `user_issue_report` to `PUBLIC_HOUSEHOLD_INTENTS` so the touch panel can use it without login |
| `services/zoe-data/multica_client.py` | No changes needed — `sync_evolution_proposal_to_multica` already handles any proposal type |

---

## Multica Issue Label

When creating the Multica issue, tag it with label `user-feedback` (distinct
from `evolution-proposal` which is Zoe's self-generated improvement list).
This keeps the two streams visually separate in the board.

The `sync_evolution_proposal_to_multica()` function already accepts
`proposal_type` and maps it to labels — just ensure `user_issue_report` maps
to a `user-feedback` label in Multica. If the label doesn't exist, create it
on first use (the existing sync function already handles missing labels via
`get_or_create_label`).

---

## Acknowledgement Phrasing

Zoe should NOT say "I've added that to Multica" or "I've created an issue."
Instead use casual, natural language:

- "Got it, I've made a note of that."
- "Noted — I'll look into it."
- "Thanks for letting me know, I'll flag that for review."
- "I've logged that. I'll work on it."

Keep it short. No UI cards needed — this should feel like Zoe just heard you.

---

## Prompt to Start a New Chat

```
I want to add a "natural issue reporting" feature to Zoe — the AI assistant 
running at /home/zoe/assistant.

Please read the full design brief at:
  /home/zoe/assistant/docs/multica-issue-capture-brief.md

The goal: when a user naturally complains, reports a problem, or says something 
was wrong — without knowing anything about Multica — Zoe should silently log 
it as an evolution proposal and create a Multica issue automatically. The user 
never sees or hears the word "Multica."

Key files to read before starting:
- /home/zoe/assistant/services/zoe-data/intent_router.py  (where to add the intent regex + handler)
- /home/zoe/assistant/services/zoe-data/evolution_notice.py  (record_frustration_signal is the model to follow)
- /home/zoe/assistant/services/zoe-data/multica_client.py  (sync_evolution_proposal_to_multica — no changes needed)
- /home/zoe/assistant/services/zoe-data/zoe_agent.py  (where to add the report_issue tool)
- /home/zoe/assistant/services/zoe-data/guest_policy.py  (PUBLIC_HOUSEHOLD_INTENTS)

The brief has the full list of files to change, regex patterns, Multica label 
notes, and the acknowledgement phrasing Zoe should use.

Dive deep, validate everything in the codebase matches the brief, then plan 
and confirm before implementing.
```
