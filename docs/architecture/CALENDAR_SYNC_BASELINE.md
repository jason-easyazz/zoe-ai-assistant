# Calendar Sync Baseline

## Canonical backend decision

For Keeper.sh integration phase 1, `services/zoe-core` is the canonical backend path.

- In scope: `services/zoe-core/routers/calendar.py`, `services/zoe-core/routers/settings.py`
- Out of scope for phase 1: `zoe-data/routers/calendar.py` as a sync source

This avoids split-brain writes while two-way sync is being introduced.

## Endpoint compatibility matrix (current)

### UI-consumed endpoints that remain primary

- `GET /api/settings/calendar`
- `POST /api/settings/calendar`
- `POST /api/settings/calendar/api`
- `GET /api/calendar/events`
- `POST /api/calendar/events`
- `PUT /api/calendar/events/{event_id}`
- `DELETE /api/calendar/events/{event_id}`

### Planned additions for provider sync operations

- `GET /api/settings/calendar/accounts`
- `POST /api/settings/calendar/accounts`
- `POST /api/settings/calendar/accounts/{account_id}/refresh`
- `DELETE /api/settings/calendar/accounts/{account_id}`
- `GET /api/settings/calendar/sync/health`
- `POST /api/settings/calendar/sync/trigger`

## Operational rule

All provider writes flow through the calendar gateway layer with:

- feature flag gating (`ZOE_CALENDAR_SYNC_ENABLED`)
- idempotency key propagation
- sync audit log entry for every external operation
