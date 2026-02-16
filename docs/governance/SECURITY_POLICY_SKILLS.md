# Security Policy for Skills

## Mandatory Rules

1. **`api_only: true`** -- Every skill MUST have this. Skills without it are rejected at load time.

2. **Endpoint whitelist** -- Skills MUST declare `allowed_endpoints`. The executor blocks any call not on the list.

3. **Internal hosts only** -- Skills can only call `localhost` and Docker network services. External URLs are blocked.

4. **No command execution** -- No `command-dispatch`, `command-tool`, shell access, file access, or process control. This is the root cause of OpenClaw's ClawHavoc vulnerability.

5. **No "Prerequisites" section** -- OpenClaw's primary attack vector. Skills must not request installation of packages.

6. **skills.lock integrity** -- Every active skill's SHA-256 is recorded. If a skill file changes on disk, it is deactivated until the user approves the change.

## Allowed API Hosts

```
localhost, 127.0.0.1, zoe-core, zoe-auth, zoe-n8n,
zoe-llamacpp, zoe-litellm, zoe-mem-agent, zoe-mcp-server,
zoe-agent0, agent-zero-bridge, zoe-ollama, homeassistant
```

## Self-Created Skills (Phase 8)

Self-created skills follow all the same rules plus:
- Require explicit user approval before activation
- Cannot modify Zoe core code
- Are API-only (no command execution)
- Are saved to `~/.zoe/skills/pending/` until approved
- Pattern detection requires 3+ occurrences spanning 7+ days

## Self-Created Widgets (Phase 8)

Self-created widgets:
- Declare `allowed_endpoints` in their manifest
- Widget runtime enforces the endpoint whitelist
- Are HTML/JS only (no server-side code)
- Cannot call undeclared API endpoints

## Reporting Issues

If you find a security issue with the skills system, please report it
by creating a private issue or contacting the project maintainers.
