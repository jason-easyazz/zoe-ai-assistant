# Calendar Sync Security Controls

## Token and secret handling

- Store provider credentials as encrypted references, not plaintext.
- Limit file permissions for settings and encrypted blobs to owner-only.
- Require explicit reconnect flow for revoked/expired credentials.

## Scope governance

- Default to least-privilege scopes.
- Display granted scopes in Settings.
- Block write operations if required write scope is missing.

## Auditability

- Record all provider sync operations in `calendar_sync_audit_logs`.
- Include operation, status, idempotency key, payload summary, and timestamp.
- Keep recent sync history visible in Settings sync health panel.

## Data protection policy

- Prefer soft-delete/tombstone during early rollout.
- Keep private-event handling policy explicit and documented.
- Enforce access checks with user-scoped queries for account/event links.
