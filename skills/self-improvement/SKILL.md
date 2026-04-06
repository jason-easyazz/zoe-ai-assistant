---
name: self-improvement
description: "Stores user corrections and preferences as learnings to improve future responses. Use when the user corrects a mistake, states a preference, or says 'remember that', 'from now on', or 'I prefer'."
version: 1.0.0
author: zoe-team
api_only: true
priority: 1
triggers:
  - "remember that"
  - "don't forget"
  - "i prefer"
  - "from now on"
  - "always remember"
  - "never forget"
  - "i told you"
  - "don't do that again"
  - "i like it when"
  - "stop doing"
allowed_endpoints:
  - "GET /api/learnings"
  - "POST /api/learnings/*/confirm"
  - "POST /api/learnings/*/dismiss"
tags:
  - meta
  - self-improvement
---
# Self-Improvement

## When to Use

Runs in the background after every conversation. Activates explicitly when the user corrects Zoe, states a preference, or uses trigger phrases like "remember that", "from now on", "I prefer", or "don't do that again".

## Workflow

1. **Detect correction signal** — scan the user's message for corrections ("actually, I meant…"), preference statements ("I prefer…"), or explicit triggers ("remember that…")
2. **Extract the learning** — identify what was wrong and what the correct behavior is, structured as a key-value pair:
   - Category: `tone`, `formatting`, `factual`, `preference`, `workflow`
   - Before: what Zoe did wrong
   - After: what the user wants instead
3. **Store via API** — `GET /api/learnings` to check for duplicates, then the learning is persisted with user ownership
4. **Confirm storage** — respond with a brief acknowledgment (e.g., "Got it, I'll remember that for next time")
5. **Apply in future** — before generating responses, retrieve relevant learnings from `GET /api/learnings` and adjust behavior accordingly

## Example

**User:** "Actually, I prefer bullet points over paragraphs when you summarize things."

**Steps:**
- Detect preference signal ("I prefer")
- Extract: category=`formatting`, before=`paragraph summaries`, after=`bullet point summaries`
- Store the learning
- Respond: "Noted — I'll use bullet points for summaries from now on."

## Security

- Only trusted sources can create learnings (Trust Gate integration)
- Owner corrections auto-confirm via `POST /api/learnings/{id}/confirm`
- Trusted contact corrections require user review before confirming
- Learnings expire after 30 days without confirmation — dismiss stale ones via `POST /api/learnings/{id}/dismiss`
