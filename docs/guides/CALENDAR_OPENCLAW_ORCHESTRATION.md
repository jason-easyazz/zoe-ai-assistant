# OpenClaw Calendar Orchestration Guide

## Purpose

Define safe natural-language operations for calendar sync setup with Keeper.sh.

## Supported operator commands

- `show calendar sync status`
- `prepare provider connection`
- `run dry-run sync`
- `enable two-way sync for <provider/account>`
- `pause calendar sync`
- `resume calendar sync`

## Mandatory approval gates

The assistant must request explicit confirmation before:

- creating or updating OAuth client credentials
- granting new provider scopes
- enabling two-way writes
- running bulk reconciliation or delete propagation

## Safety workflow

1. Validate connected accounts and required scopes.
2. Run dry-run sync and show summary (creates, updates, deletes).
3. Ask for explicit approval with impact summary.
4. Apply change and write sync audit event.

## Disallowed autonomous actions

- No automatic delete propagation without confirmation.
- No token replacement without explicit operator action.
- No scope elevation without approval and audit entry.
