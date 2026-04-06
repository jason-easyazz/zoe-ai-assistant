---
name: proactive-agent
description: "Schedule proactive push notifications and one-shot nudges for users. Use when the user asks for a future reminder, notification, alert, or nudge like 'remind me at 3pm' or 'notify me tomorrow morning'."
version: 2.0.0
author: zoe-team
api_only: true
priority: 3
triggers:
  - "remind me"
  - "notify me"
  - "send me a message"
  - "alert me"
  - "every day"
  - "every morning"
  - "every evening"
  - "every hour"
  - "every week"
  - "check daily"
  - "schedule"
  - "recurring"
  - "automate"
  - "nudge"
  - "remind me every"
  - "set up a timer"
  - "periodic"
  - "cron"
allowed_endpoints:
  - "POST /api/proactive/schedule"
  - "GET /api/proactive/schedule"
  - "DELETE /api/proactive/schedule/*"
  - "POST /api/proactive/pending/*"
tags:
  - scheduling
  - automation
  - proactive
  - notifications
---
# Proactive Agent

## Overview
Zoe can proactively reach out to users via push notifications.  Notifications
deep-link back into a chat session so the conversation continues naturally.

## Endpoints

### Schedule a one-shot nudge
```
POST /api/proactive/schedule
{
  "message": "Time to do your stretches!",
  "send_at": "2026-05-04T14:00:00Z",
  "user_id": "family-admin"   // optional — admin only
}
→ {"id": "<scheduled_id>", "user_id": "...", "send_at": "..."}
```

### List pending scheduled nudges
```
GET /api/proactive/schedule
→ [{"id": ..., "message": ..., "send_at": ..., "fired": 0, ...}]
```

### Cancel a scheduled nudge
```
DELETE /api/proactive/schedule/<scheduled_id>
→ {"cancelled": true}
```

### Claim a pending notification (called by UI on tap)
```
POST /api/proactive/pending/<pending_id>
→ {"session_id": "<new_session_id>", "message": "..."}
```

## Zoe Agent MCP Tool
Use the `proactive_schedule` MCP tool when the user asks to be notified at a
specific future time.  Example:
```json
{
  "name": "proactive_schedule",
  "arguments": {
    "message": "Time for your 3pm stand-up!",
    "send_at": "2026-05-04T05:00:00Z"
  }
}
```

## When to Use
- User says "remind me at 3pm to call the doctor"
- User says "notify me in 2 hours"
- User says "send me a message tomorrow morning at 8am"
- Any explicit future-notification request

## Workflow

1. Parse the requested reminder message and target time.
2. Ask a clarifying question if the time, date, timezone, or recipient is ambiguous.
3. Use `proactive_schedule` or `POST /api/proactive/schedule` to create the nudge.
4. Confirm the scheduled message and when it will fire.
5. Use the list or cancel endpoints when the user asks to review or remove pending nudges.

## Example

**User:** "Remind me tomorrow at 8am to put the bins out"

**Steps:**
- Parse the message as "put the bins out".
- Resolve "tomorrow at 8am" to an ISO timestamp in the user's timezone.
- Call `proactive_schedule` with the message and `send_at`.
- Respond with a short confirmation including the scheduled time.

## Error Handling

- **Ambiguous time**: Ask for a specific time before scheduling.
- **Past time**: Ask for a future time instead of silently adjusting.
- **Duplicate request**: If a very similar pending nudge exists, mention it before creating another.
- **API failure**: Tell the user the notification could not be scheduled and suggest retrying.

## Implementation Notes
- APScheduler persists jobs in SQLite so they survive restarts.
- Quiet hours (22:00–07:00 server local) suppress non-forced notifications.
- Tapping a push notification creates a fresh chat session pre-seeded with
  the notification message, enabling a natural follow-up conversation.
