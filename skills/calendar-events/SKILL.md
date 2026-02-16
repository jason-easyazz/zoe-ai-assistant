# Calendar Events Skill

Create, view, and manage calendar events and appointments.

Replaces hardcoded regex patterns in chat.py for calendar operations.

## Triggers

- schedule
- create event
- create an event
- book appointment
- add to calendar
- add to my calendar
- show my calendar
- what's on my calendar
- upcoming events
- cancel event
- reschedule
- move my meeting
- remind me about
- when is my next

## Behavior

1. Parse the user's intent (create, view, update, cancel)
2. Extract event details: title, date/time, duration, location
3. For ambiguous times, ask for clarification
4. Call the appropriate API endpoint
5. Confirm with event details and time

## API Endpoints (api_only)

- `GET /api/calendar/events` - List upcoming events
- `POST /api/calendar/events` - Create new event
- `PUT /api/calendar/events/{id}` - Update event
- `DELETE /api/calendar/events/{id}` - Cancel event
- `GET /api/calendar/events/today` - Today's events
- `GET /api/calendar/events/week` - This week's events

## Response Style

Clear and time-aware. Always confirm the exact date/time of created events.
Use relative time references ("in 2 hours", "tomorrow at 3pm").
