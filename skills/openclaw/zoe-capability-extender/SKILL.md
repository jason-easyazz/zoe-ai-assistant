# zoe-capability-extender

Add new capabilities to Zoe: new intents, MCP tools, or OpenClaw skills.

## Trigger conditions

This skill activates when the system message begins with `[ZOE_SELF_BUILD: capability]`, or when the user asks to add a new ability, command, feature, or integration to Zoe.

## Prerequisites

- Caller must have admin role. Check via `zoe_self_capabilities` tool. If not admin, stop.
- Read the relevant architectural files before writing anything:
  - `services/zoe-data/intent_router.py` — for intent pattern additions
  - `services/zoe-data/mcp_server.py` — for MCP tool additions
  - `/home/zoe/.openclaw/workspace/skills/` — for skill-level additions
- **No hardcoded NLU in `chat.py`**: never add `if/else` keyword detection to the production chat router.

## Identifying the right layer

Choose the layer that matches the capability scope:

| Layer | When to use | Where to edit |
|-------|-------------|---------------|
| **Intent router pattern** | New phrase/intent that maps to existing logic | `intent_router.py` — add a pattern + handler |
| **MCP tool** | New structured action Zoe can take (API call, DB write, etc.) | `mcp_server.py` — add a `@mcp_tool` function |
| **OpenClaw skill** | Multi-step reasoning task or domain knowledge | New `SKILL.md` in `/home/zoe/.openclaw/workspace/skills/<name>/` |
| **Proactive trigger** | Background check that fires unprompted | New `Trigger` class in `services/zoe-data/proactive/triggers/` + register in `main.py` |

## Step-by-step workflow

### 1. Understand and classify the request
Ask if ambiguous:
- Is this a new phrase Zoe should understand, or a new action she should take?
- Should it run in the background, or only when asked?
- Does it need external data (API, DB, HA)?

### 2. Spec + confirm
Draft a brief spec (≤5 bullet points) describing:
- Which layer will be used and why
- What the new capability does end-to-end
- Any new data sources or credentials needed

Present the spec to the user and wait for confirmation.

### 3. Implement minimally

**Intent router pattern** (edit `intent_router.py`):
```python
# Add to the relevant pattern group — do NOT create a new if/else block in chat.py
{"pattern": r"(?i)\bnew phrase\b", "intent": "new_intent", "handler": existing_or_new_handler}
```

**MCP tool** (edit `mcp_server.py`):
```python
@mcp_tool(name="tool_name", description="One-line description")
async def tool_name(param: str) -> dict:
    ...
```

**OpenClaw skill** (create `SKILL.md`):
- Follow the format of `zoe-widget-builder/SKILL.md`
- Describe trigger conditions, step-by-step workflow, output contract, and error handling
- Place in `/home/zoe/.openclaw/workspace/skills/<skill-name>/SKILL.md`

**Proactive trigger** (create `.py` + register):
- Subclass `ProactiveTrigger` from `proactive.triggers.base`
- Implement `async def check(self, db) -> list[TriggerResult]`
- Register in `main.py` lifespan with `register_trigger(MyTrigger())`

### 4. Test

- For intent/MCP changes: restart `zoe-data` (`systemctl --user restart zoe-data.service`) and exercise the new path manually
- For skills: test via OpenClaw with a representative prompt
- For triggers: check `journalctl --user -u zoe-data.service -n 30` for "Registered trigger" and confirm no import errors

### 5. Confirm with user

Report exactly what was added, which file was changed, and how to exercise the new capability. Do not add speculative follow-on features.

## Constraints

- **No NLU if/else in `chat.py`** — this is a hard rule from the design charter
- Minimum change for the stated request; no speculative abstractions
- Touch only the files the task requires
- All new credentials go in `.env`, never hardcoded
- New MCP tools must be documented with a clear `description=` string — Zoe uses this for routing
