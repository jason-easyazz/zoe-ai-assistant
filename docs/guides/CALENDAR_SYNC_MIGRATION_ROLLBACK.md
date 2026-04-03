# Calendar Sync Migration and Rollback Playbook

## Phase rollout

1. Connect provider account + read-only sync.
2. Enable create-only writes.
3. Enable updates.
4. Enable delete propagation.

Enable each phase behind feature flags and validate before advancing.

## Preflight checklist

- `docker-compose.yml` has `zoe-network` explicit name.
- Keeper service health endpoint responds.
- Calendar settings endpoint returns sync controls.
- At least one account is connected and in `connected` status.

## Rollback strategy

- Disable `ZOE_CALENDAR_SYNC_ENABLED`.
- Keep local calendar CRUD active.
- Mark outstanding sync records as `paused` or `error` for replay.
- Run reconciliation pass before re-enabling two-way sync.

## Incident triage quick map

- Token expiry/revocation: reconnect account and refresh status.
- Provider outage: pause sync and keep local writes only.
- Duplicate events: reconcile by event link mapping and idempotency keys.
- Recurrence mismatch: disable two-way for affected account and resolve series manually.
