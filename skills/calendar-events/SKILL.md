---
name: calendar-events
description: Create, view, and manage calendar events and appointments
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
allowed_endpoints:
  - "GET /api/calendar/events"
  - "POST /api/calendar/events"
  - "PUT /api/calendar/events/{id}"
  - "DELETE /api/calendar/events/{id}"
  - "GET /api/calendar/events/today"
  - "GET /api/calendar/events/week"
---
# Calendar Events Skill

Create, view, and manage calendar events and appointments.

## Behavior

1. Parse the user's intent (create, view, update, cancel)
2. Extract event details: title, date/time, duration, location
3. For ambiguous times, ask for clarification
4. Call the appropriate API endpoint
5. Confirm with event details and time

## Response Style

Clear and time-aware. Always confirm the exact date/time of created events.
Use relative time references ("in 2 hours", "tomorrow at 3pm").
