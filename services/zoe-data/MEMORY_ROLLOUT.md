# People + Memory Rollout Flags

This rollout uses environment flags so the new memory stack can be enabled gradually.

## Flags

- `PEOPLE_CUSTOM_FIELDS=true`
  - Enables API/UI use of dynamic custom field schema.
- `SEMANTIC_MEMORY_GATEWAY=true`
  - Enables Atomic sidecar calls for semantic search + approved memory ingestion.
- `MEMORY_AUTO_INGEST=false`
  - When false, OpenClaw-extracted memories go to review queue.
  - When true, high-confidence preference memories may auto-approve.

## Suggested rollout stages

1. Enable `PEOPLE_CUSTOM_FIELDS=true` only.
2. Enable `SEMANTIC_MEMORY_GATEWAY=true` in staging.
3. Keep `MEMORY_AUTO_INGEST=false` until review queue quality is validated.
4. Enable `MEMORY_AUTO_INGEST=true` for a small cohort.
5. Monitor `memory_items.status` distribution and rejection rate.

## Rollback

- Set all flags to false and restart `zoe-data`.
- Existing `memory_items` records are preserved; only behavior changes.
- UI continues to function with local SQL memory list/search.
