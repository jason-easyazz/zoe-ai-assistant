# Skybridge Action Loop

Skybridge cards are not just display widgets. They are the visible state for a
voice-first action loop:

`utterance -> intent -> optional mutation -> authoritative read -> card refresh`

The first supported write domains are calendar, lists, and people. Weather stays
read-only.

## Runtime Contract

Every handled Skybridge response may include:

- `intent`: the server-classified domain/action.
- `cards`: card-contract or renderer-native cards to display.
- `actions`: persisted mutations Zoe performed.
- `spoken_summary`: short user-facing result.
- `skybridge_context`: the next-turn context to send back.

The client sends `skybridge_context` with the next typed command. The local voice
WebSocket keeps the same context in session memory and emits refreshed card
batches with `type: "cards"`.

## Page And Feature Standard

Any Zoe surface that wants voice-edit support should expose the same pieces:

- A read resolver that returns authoritative card data.
- A mutation resolver that writes through the existing domain API/service.
- A refresh step that re-reads data after mutation.
- A card renderer that can represent both normal data and clean empty states.
- A confirmation card when the target is ambiguous or the action is risky.

The screen should update from the refreshed card, not from optimistic local-only
state. Optimistic UI can be added later, but the authoritative read remains the
source of truth.

## V1 Actions

- `calendar.create_event`: e.g. "add pick up the groceries at 3pm" while the
  calendar card is active.
- `calendar.update_time`: e.g. "move my 3pm appointment to 4pm".
- `lists.add_item`: e.g. "add bread to the shopping list".
- `people.remember_fact`: e.g. "remember that Sarah likes flowers and her
  birthday is the 1st of May".

## Ambiguity Rule

Skybridge must not silently edit the wrong thing. If an utterance does not name a
single safe target, return a status/confirmation card asking for clarification.

Future calendar improvements can layer on participant notification, conflict
checking, and trust gates without changing the core loop.
