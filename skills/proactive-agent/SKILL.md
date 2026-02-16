---
name: proactive-agent
description: Schedule recurring tasks, reminders, and proactive checks
version: 1.0.0
author: zoe-team
api_only: true
priority: 3
triggers:
  - "every day"
  - "every morning"
  - "every evening"
  - "every hour"
  - "every week"
  - "check daily"
  - "schedule"
  - "recurring"
  - "automate"
allowed_endpoints:
  - "GET /api/scheduler/jobs"
  - "POST /api/scheduler/jobs"
  - "DELETE /api/scheduler/jobs/*"
tags:
  - scheduling
  - automation
  - proactive
---
# Proactive Agent

## When to Use
User wants to set up recurring tasks, automated checks, or periodic reminders.

## Examples
- "Check my email every morning at 8am"
- "Give me a weather update every day at 7am"
- "Check if the garage door is open every night at 10pm"

## How to Handle
1. Parse the schedule (time, frequency)
2. Create a scheduled job via the API
3. Confirm the schedule to the user

## Rate Limits
Each integration has rate limits to prevent excessive API calls.
Default limits are generous but configurable per user.
