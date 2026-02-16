# Extensibility Architecture

Zoe uses a **two-layer extensibility model**: Modules (heavy) and Skills (lightweight).

## Layer 1: Modules (Heavy Infrastructure)

Modules provide Docker services, intents, widgets, and MCP tools. They are Zoe's
heavy-weight extensibility layer for complex integrations.

**Directory structure:**
```
modules/{module-name}/
├── docker-compose.module.yml   # Docker services
├── main.py                     # Module entry point / bridge
├── intents/                    # HassIL intent YAML + handlers
│   ├── {name}.yaml
│   └── handlers.py
├── skills/                     # Module-shipped skills (auto-loaded)
│   └── {skill-name}/SKILL.md
├── widget/                     # Optional dashboard widgets
│   ├── manifest.json
│   └── index.html
└── README.md
```

**When to create a module:**
- Needs its own Docker service (API, database, etc.)
- Adds hardware integration (sensors, cameras)
- Requires significant compute (ML model, browser automation)
- Has complex state management

**Example:** Agent Zero module provides research, planning, and comparison via
its own Docker container with GPU access.

## Layer 2: Skills (Lightweight Instructions)

Skills are Markdown files with YAML frontmatter that tell the LLM how to handle
specific request types. They complement modules by providing quick instruction sets.

**Skill format:**
```yaml
---
name: skill-name
description: What this skill does
version: 1.0.0
author: zoe-team
api_only: true          # MANDATORY
triggers:
  - "keyword1"
  - "keyword2"
allowed_endpoints:
  - "POST /api/endpoint"
  - "GET /api/other"
---
# Skill Title

## When to Use
Instructions for the LLM...
```

**When to create a skill:**
- Adds LLM instructions without infrastructure
- Defines API call patterns for a use case
- Can be written in 5 minutes
- Doesn't need its own Docker service

**Skill precedence (highest to lowest):**
1. User skills (`~/.zoe/skills/`)
2. Module skills (`modules/{name}/skills/`)
3. Core skills (`skills/`)

## How They Work Together

```
User says "Research the best solar panels":
  Tier 0-2: No HassIL/keyword match
  Tier 3: Skill trigger "research" matches research/SKILL.md
  Tier 4: LLM reads skill instructions, calls Agent Zero API
  Result returned to user
```

The intent system catches exact patterns fast (Tier 0-2). The skills layer
catches broader patterns (Tier 3-4). Both can route to the same module backend.

## Security

All skills enforce:
- `api_only: true` -- mandatory, no command execution
- `allowed_endpoints` whitelist -- skills can only call declared APIs
- `skills.lock` integrity -- modified skills are deactivated until approved
- Localhost/Docker network only -- no external calls
