---
name: calendar-events
description: "Create, view, reschedule, and cancel calendar events and appointments. Use when the user asks to schedule a meeting, check their calendar, book an appointment, reschedule, or set a reminder for an event."
version: 1.0.0
author: zoe-team
api_only: true
priority: 5
tags:
  - calendar
  - events
  - scheduling
triggers:
  - "schedule"
  - "create event"
  - "create an event"
  - "book appointment"
  - "add to calendar"
  - "add to my calendar"
  - "show my calendar"
  - "what's on my calendar"
  - "upcoming events"
  - "cancel event"
  - "reschedule"
  - "move my meeting"
  - "remind me about"
  - "when is my next"
  - "free slot"
  - "am I free"
  - "set up a meeting"
allowed_endpoints:
  - "GET /api/calendar/events"
  - "POST /api/calendar/events"
  - "PUT /api/calendar/events/{id}"
  - "DELETE /api/calendar/events/{id}"
  - "GET /api/calendar/events/today"
  - "GET /api/calendar/events/week"
---
# Calendar Events Skill

## Workflow

1. **Parse intent** — determine the action: create, view, update, or cancel
2. **Extract details** — title, date/time (ISO 8601), duration (minutes), location (optional)
3. **Clarify ambiguity** — if date or time is missing or ambiguous, ask the user before proceeding
4. **Call the API** — use the matching endpoint (see below)
5. **Confirm result** — respond with the event summary including exact date/time and any relevant details

## API Endpoints

### Create an event
```
POST /api/calendar/events
{
  "title": "Dentist appointment",
  "start": "2026-04-10T14:00:00Z",
  "duration": 60,
  "location": "123 Main St"
}
```

### View today's events
```
GET /api/calendar/events/today
```

### View this week's events
```
GET /api/calendar/events/week
```

### Update an event
```
PUT /api/calendar/events/{id}
{
  "start": "2026-04-10T15:00:00Z"
}
```

### Cancel an event
```
DELETE /api/calendar/events/{id}
```

## Example

**User:** "Schedule a dentist appointment for Thursday at 2pm"

**Steps:**
- Parse intent: create
- Extract: title=`Dentist appointment`, start=`2026-04-10T14:00:00Z`, duration=`60`
- `POST /api/calendar/events` with extracted details
- Respond: "Done — Dentist appointment is set for Thursday, April 10 at 2:00 PM (1 hour)."

## Error Handling

- **Conflicting event**: Check `GET /api/calendar/events` for overlaps and warn the user before creating
- **Past date**: Reject and ask for a future date
- **Missing time**: Ask "What time works for you?" rather than guessing

## Response Style

Always confirm the exact date and time. Use relative references when helpful ("in 2 hours", "tomorrow at 3pm").
