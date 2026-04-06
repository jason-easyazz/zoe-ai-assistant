---
name: proactive-agent
description: "Create, list, and delete recurring scheduled tasks, reminders, and automated checks. Use when the user asks to set up a cron-style schedule, periodic reminder, or recurring automation like 'every morning at 8am' or 'check daily'."
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
  - "remind me every"
  - "set up a timer"
  - "periodic"
  - "cron"
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

The user wants to set up recurring tasks, automated checks, or periodic reminders. Common triggers: "every morning at 8am", "check daily", "remind me every week", or "automate".

## Workflow

1. **Parse the schedule** — extract frequency (`daily`, `weekly`, `hourly`) and time (24h format) from the user's request
2. **Build the job payload** — structure the task with a cron expression or interval
3. **Create the job** — `POST /api/scheduler/jobs`
4. **Confirm** — respond with the schedule summary and next run time
5. **List or delete** — use `GET` to show active jobs, `DELETE` to cancel

## API Endpoints

### Create a scheduled job
```
POST /api/scheduler/jobs
{
  "name": "morning-weather",
  "schedule": "0 7 * * *",
  "task": "weather_update",
  "params": {"location": "home"}
}
```

### List active jobs
```
GET /api/scheduler/jobs
```

### Delete a job
```
DELETE /api/scheduler/jobs/morning-weather
```

## Example

**User:** "Give me a weather update every day at 7am"

**Steps:**
- Parse: frequency=`daily`, time=`07:00`, task=`weather_update`
- Build: cron=`0 7 * * *`, name=`morning-weather`
- `POST /api/scheduler/jobs` with payload
- Respond: "Done — you'll get a weather update every day at 7:00 AM. Next run: tomorrow at 7:00 AM."

## Error Handling

- **Invalid schedule**: If the time or frequency is ambiguous, ask for clarification ("What time should I check?")
- **Duplicate job**: Check `GET /api/scheduler/jobs` for existing jobs with the same task and warn before creating a duplicate
- **API failure**: Inform the user the job could not be created and suggest retrying

## Rate Limits

Each integration has rate limits to prevent excessive API calls. Default limits are generous but configurable per user. Jobs running more frequently than every 5 minutes may be throttled.
