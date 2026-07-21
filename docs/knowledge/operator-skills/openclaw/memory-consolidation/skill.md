# Memory Consolidation Skill

<!-- metadata.when: running nightly memory consolidation, merging duplicate facts, archiving stale memories, or weekly deep-sleep pass (admin/scheduled only) -->


Use this skill when triggered by a cron job for memory maintenance.

## Tool Note

`memory_list`, `memory_cleanup`, `memory_delete`, and `memory_memorize` are **OpenClaw built-in memory tools** — they are NOT `zoe-data.*` MCP tools. Call them via the `exec` tool using their OpenClaw native interface, not via `mcporter-safe`. Example:

```
exec memory_list limit=100
exec memory_cleanup max_age_days=90
exec memory_delete id=<memory_id>
```

For creating notes (free-form text storage), use `zoe-data` via mcporter-safe:
```
mcporter-safe call zoe-data.note_create title="Memory Summary - [Month Year]" content="[summary]" category="memory-summary"
```

## Weekly Consolidation (runs every Sunday 3am)

1. Call `memory_list` with limit 100 to get all recent memories
2. Group memories by category
3. For each category, identify:
   - Duplicate or near-duplicate memories (merge them)
   - Patterns worth noting (e.g., "user always asks about weather on Mondays")
   - Contradictory memories (keep the most recent)
4. Call `memory_cleanup` with max_age_days=90 to prune old unreinforced memories
5. Call `memory_delete` on individual duplicates identified above
6. Report what was consolidated and pruned

## Monthly Distillation (runs 1st of each month at 4am)

1. Call `memory_list` with limit 200
2. Identify the most important facts across all categories
3. Summarize key knowledge into a note:
   ```
   mcporter-safe call zoe-data.note_create title="Memory Summary - [Month Year]" content="[summary of key facts and patterns]" category="memory-summary"
   ```
4. Call `memory_cleanup` with max_age_days=60 to prune stale memories
5. Report the distillation results

## Important

- `memory_memorize` requires a URL parameter — for free-form text storage, use `note_create` instead
- The goal is to keep memU lean and relevant, not to accumulate everything
- Personality traits, family preferences, and recurring patterns are highest priority to retain
