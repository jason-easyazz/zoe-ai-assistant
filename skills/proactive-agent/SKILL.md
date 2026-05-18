---
name: proactive-agent
description: Schedule proactive push notifications and one-shot nudges for users
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

## Implementation Notes
- APScheduler persists jobs in SQLite so they survive restarts.
- Quiet hours (22:00–07:00 server local) suppress non-forced notifications.
- Tapping a push notification creates a fresh chat session pre-seeded with
  the notification message, enabling a natural follow-up conversation.
