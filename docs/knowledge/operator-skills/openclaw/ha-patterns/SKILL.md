# ha-patterns — Home Assistant Routine Pattern Detection

<!-- metadata.when: running pattern detection on Home Assistant history data (scheduled/admin only) -->


Detect household routine patterns from Home Assistant event data and store findings to MemPalace as shared-scope facts.

## Trigger conditions

- **Scheduled**: runs every Sunday (proactive trigger or cron-driven OpenClaw task)
- **On-demand**: user asks "what patterns have you noticed?" or "any routines you've spotted?"
- **System message prefix**: `[ZOE_SKILL: ha-patterns]`

## Data source

The HA bridge is available at the URL stored in `ZOE_HA_BRIDGE_URL` (environment variable).  
Use `GET $ZOE_HA_BRIDGE_URL/api/history/period/<ISO_DATE>?filter_entity_id=<entity>` to fetch state-change history.  
Do not query Home Assistant directly; always go through the bridge.

## Process

### 1. Fetch the last 7 days of device state changes

```
GET $ZOE_HA_BRIDGE_URL/api/history/period/<7-days-ago>
```

Focus on high-signal entity types:
- `light.*` — on/off transitions
- `switch.*` — on/off transitions  
- `binary_sensor.motion_*` — motion detection events
- `lock.*` — lock/unlock events
- `media_player.*` — play/pause/idle transitions

Discard entities that change fewer than 3 times per day (noise).

### 2. Identify repeating sequences

Look for temporal patterns:
- **Time-of-day patterns**: same entity state change occurring within ±30 min of the same time on 3+ days
- **Sequence patterns**: entity A changes state, then entity B changes within 5 minutes, on 3+ occasions
- **Day-of-week patterns**: routine present on weekdays but not weekends, or vice versa

Scoring: only surface patterns with confidence ≥ 0.7 (i.e. observed on ≥70% of applicable days).

### 3. Write findings to MemPalace

For each pattern with confidence ≥ 0.7, store a memory:

```python
mem_write(
    content="[HA Pattern] <entity> <state> around <time> on <days>. Confidence: <N>%.",
    scope="shared",
    tags=["ha-pattern", "routine", entity_domain],
)
```

Do NOT store low-confidence guesses. Overwrite any existing memory for the same pattern (use upsert / same key).

### 4. Compose a brief summary for the user

After writing memories, generate a 3–5 bullet summary:

```
I've noticed a few routines this week:
• Living room lights turn on around 6:15pm on weekdays (confidence 90%)
• Front door locks every night between 10–10:30pm (confidence 85%)
• Motion sensor in kitchen spikes around 7:30am daily (confidence 80%)
```

Push the summary via the proactive notification channel (or respond inline if on-demand).

## Output contract

- Always write memories BEFORE responding to the user
- Summary must be ≤6 bullet points; omit patterns below 70% confidence
- Do NOT propose automations unless the user explicitly asks — observe and report only
- If fewer than 2 patterns are found, respond: "I didn't spot any strong routines this week — I'll keep watching."

## What to track (scope guide)

| Entity class | What to note |
|---|---|
| Lights | On/off times, rooms, day-of-week |
| Locks | Lock/unlock times, correlation with motion |
| Motion | Active windows (morning routine, bedtime) |
| Media | TV/speaker usage times |
| Climate | Thermostat set-point changes |

## Limitations

- Observe and report only; do NOT create automations in Home Assistant
- Always respect privacy: do not infer or store presence/location of individuals
- If the HA bridge is unreachable, log the failure and skip silently (non-fatal)
- Maximum 20 patterns stored at one time; prune oldest if limit exceeded
