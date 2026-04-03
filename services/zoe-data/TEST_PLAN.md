# Zoe Test Plan

## Tier 1: Intent Router (< 1.5s)

| # | Test Case | Input | Expected |
|---|-----------|-------|----------|
| I1 | Shopping add | "add milk to my shopping list" | Intent matched, item added |
| I2 | Shopping add multiple | "add bread and eggs to my shopping list" | Both items added |
| I3 | Shopping show | "whats on my shopping list" | Lists items |
| I4 | Shopping remove | "remove bread from my shopping list" | Item removed |
| I5 | Calendar show | "what is on my calendar today" | Shows events |
| I6 | Calendar show (contraction) | "what's on my schedule this week" | Shows events |
| I7 | Calendar create (formal) | "create an appointment called dentist on Friday" | Event created |
| I8 | Calendar create (natural) | "add dinner to my calendar on Saturday at 7pm" | Event created (not list) |
| I9 | Reminder create | "remind me to call mum tomorrow at 10am" | Reminder created |
| I10 | Reminder list | "show my reminders" | Lists reminders |
| I11 | Note create | "make a note that wifi password is abc123" | Note saved |
| I12 | Contact add | "add contact John Smith phone 0412345678" | Contact created |
| I13 | Contact search | "find John" | Returns matching contacts |
| I14 | Implicit shopping | "we need butter" | Added to shopping list |
| I15 | Non-intent fallthrough | "tell me a joke" | Falls to OpenClaw |

## Tier 2: OpenClaw + GPT-4o-mini (5-30s)

| # | Test Case | Input | Expected |
|---|-----------|-------|----------|
| O1 | Simple conversation | "How are you?" | Friendly response |
| O2 | Personality | "Who are you?" | Identifies as Zoe |
| O3 | Multi-turn | "My daughter is Sophie" then "What's her name?" | Recalls Sophie |
| O4 | Web search | "Search for latest AI news" | Real results with URLs |
| O5 | Web fetch | "Fetch https://api.github.com/zen" | Returns actual content |
| O6 | Cron | "Set a daily reminder at 8am" | Cron job created |
| O7 | Memory write | "Remember that Charlie is our dog" | Updates USER.md |
| O8 | Memory recall (cross-session) | Reset session, ask "What's our dog's name?" | Recalls Charlie |
| O9 | Exec (allowed) | "Run mcporter-safe call zoe-data.calendar_list_events" | Returns data |
| O10 | Exec (blocked) | "Run rm -rf /" | Denied by allowlist |

## WebSocket Push

| # | Test Case | Action | Expected |
|---|-----------|--------|----------|
| W1 | Lists push | Add item via API | WS client receives list_updated |
| W2 | Calendar push | Create event via API | WS client receives event_created |
| W3 | Reconnection | Disconnect, create event, reconnect | Client catches up |
| W4 | Ping/pong | Send "ping" on WS | Receives {"type": "pong"} |

## Visibility & Security

| # | Test Case | Expected |
|---|-----------|----------|
| V1 | Personal note not visible to other user | User B cannot see User A's note |
| V2 | Family event visible to all | Both users see family events |
| V3 | Soft delete notification | Delete family event, others notified |
| V4 | Trust gate blocks unknown | Non-allowlisted contact is dropped |
| V5 | Gateway token required | Request without token is rejected |
| V6 | Denied tools stay denied | browser/process/canvas tools blocked |

## Performance Targets

| Tier | Target | Measured |
|------|--------|----------|
| Intent router | < 1.5s | ~0.8-1.3s |
| OpenClaw conversation | < 10s | ~5-6s |
| OpenClaw web search | < 30s | ~18-25s |
| OpenClaw exec/tools | < 30s | ~8-26s |
| WebSocket push | < 500ms | < 100ms |
