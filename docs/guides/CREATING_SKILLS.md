# Creating Skills

## Quick Start

1. Create a directory: `skills/{skill-name}/`
2. Create `SKILL.md` with YAML frontmatter
3. Restart Zoe or call `POST /api/skills/reload`
4. Your skill is now active

## SKILL.md Format

```yaml
---
name: my-skill            # Required: unique identifier
description: What it does  # Required: shown in skills list
version: 1.0.0            # Required: semver
author: your-name         # Required: attribution
api_only: true            # MANDATORY: must be true
triggers:                  # Required: keywords for Tier 3 matching
  - "keyword1"
  - "keyword2"
allowed_endpoints:         # Required: API whitelist
  - "POST /api/endpoint"
  - "GET /api/other"
tags:                      # Optional: categorization
  - tag1
priority: 5               # Optional: higher = checked first (default: 0)
---
# Skill Title

## When to Use
Describe when this skill should activate.

## API Endpoints
Document the APIs the skill can call.

## Examples
Show example user messages and expected behavior.

## Important Notes
Any caveats or restrictions.
```

## Security Rules

1. `api_only: true` is **mandatory** -- skills without it are rejected
2. Skills can **only** call endpoints listed in `allowed_endpoints`
3. Only `localhost` and Docker network hosts are allowed
4. No shell commands, file access, or process control
5. Modified skills are deactivated until user re-approves

## Skill Locations

Skills are loaded from three locations (highest to lowest precedence):

| Location | Description | Overrides |
|----------|-------------|-----------|
| `~/.zoe/skills/` | User-created skills | Everything |
| `modules/{name}/skills/` | Module-shipped skills | Core skills |
| `skills/` | Core skills shipped with Zoe | Nothing |

## Testing Your Skill

1. Check it loads: `curl /api/skills -H "X-Session-ID: dev-localhost"`
2. Check triggers: send a message matching your trigger keywords
3. Check the skills audit: `curl /api/skills/audit/calls`

## Module-Shipped Skills

If your module needs skills, create them at `modules/{module}/skills/{skill-name}/SKILL.md`.
They're automatically loaded when the module is enabled.
