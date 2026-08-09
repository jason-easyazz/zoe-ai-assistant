# Docker Networking Rules - MANDATORY

**Critical Rule**: All containerised Zoe services MUST be on the SAME Docker network
(`zoe-network`, with an explicit `name:`) to reach each other by service name.

> **The rule is live; the old cast is not.** Every service this document originally used
> as an example — `zoe-core` (the old Docker monolith), `zoe-llamacpp`, `zoe-mcp-server`,
> `zoe-mem-agent` — is **RETIRED** (see the `RETIRED SERVICES` block in
> `docker-compose.yml`). They were replaced by host-native processes, which do not join a
> Docker network at all. Examples below use the containers that are actually running;
> verify with `docker network inspect zoe-network` rather than trusting this list.

## 🚨 The Problem We Fixed

**Date**: 2025-11-10 (historical — the services named here no longer exist)
**Issue**: Services were on different Docker networks:
- the old `zoe-core` monolith → `assistant_zoe-network`
- LLM service → `zoe-network`

**Result**: 100% test failure because the API container couldn't reach the LLM service
- Error: `[Errno -2] Name or service not known`
- All LLM requests failed → generic greetings returned
- Tests showed 0% success rate

The failure MODE is why this document exists and is still exactly right. Only the
participants changed.

## ✅ The Solution

### 1. EXPLICIT Network Naming
In `docker-compose.yml`, define network with explicit name to prevent auto-prefixing:

```yaml
networks:
  zoe-network:
    name: zoe-network  # ✅ REQUIRED: Prevents "assistant_" or "zoe_" prefix
    driver: bridge
```

### 2. All Services Use Same Network
Every service MUST specify `zoe-network`:

```yaml
services:
  zoe-auth:
    networks:
      - zoe-network

  zoe-ui:
    networks:
      - zoe-network

  homeassistant-mcp-bridge:
    networks:
      - zoe-network
```

> `modules/omnigent` is the one deliberate exception to "one network": it joins
> `zoe-network` **and** a second `internal` network, `zoe-codeintel`, whose only member is
> pinned at `172.28.0.2` so the `serena-bridge.socket` allowlist can scope it. Adding a
> second member to that network widens whole-repo read access — see `modules/AGENTS.md`.

## 📋 Validation Checklist

**BEFORE deploying or modifying docker-compose.yml:**

1. ✅ Network has explicit `name:` field
2. ✅ All services specify `networks: [zoe-network]`
3. ✅ Confirm every container actually joined it (command below)
4. ✅ Test connectivity by service name between two LIVE containers

> There is no `tools/docker/validate_networks.sh`. It is referenced in older docs but was
> never committed; use the commands below.

## 🛠️ Validation Commands

### Check Current Networks
```bash
# List all networks
docker network ls

# Check which network each container is on
docker ps --format "{{.Names}}: {{.Networks}}"

# Authoritative: who is actually attached to zoe-network right now
docker network inspect zoe-network --format '{{range .Containers}}{{.Name}} {{end}}'

# Verify name resolution between two containers that are actually running
# (pick names from the command above — do not copy a name from this doc)
docker exec zoe-ui ping -c 2 zoe-auth
```

### Fix Mismatched Networks
```bash
# Connect missing container to zoe-network
docker network connect zoe-network <container-name>

# OR restart with fixed docker-compose.yml
cd /home/zoe/assistant
docker compose down
docker compose up -d
```

## 🚫 NEVER Do This

### ❌ DON'T use different networks
```yaml
# BAD - Different networks will break communication!
services:
  zoe-auth:
    networks:
      - assistant_zoe-network  # ❌ WRONG

  zoe-ui:
    networks:
      - zoe-network  # ❌ DIFFERENT from zoe-auth
```

### ❌ DON'T forget explicit network name
```yaml
# BAD - Docker will add prefix based on directory
networks:
  zoe-network:
    driver: bridge  # ❌ MISSING "name:" field
# Results in: assistant_zoe-network or zoe_zoe-network
```

## 🎯 Why This Matters

**Inter-Service Communication** — live container-to-container paths, each verified
2026-08-06 against the config that declares it:

| path | declared in |
|---|---|
| `zoe-auth` → `zoe-database:5432` | `docker-compose.yml` (`POSTGRES_URL`) |
| `homeassistant-mcp-bridge` → `homeassistant:8123` | `docker-compose.yml` (`HA_BASE_URL`) |
| `zoe-cloudflared` → `zoe-ui:80` | `config/cloudflared-config.yml` ingress |
| `zoe-cloudflared` → `zoe-omnigent:6767` | `config/cloudflared-config.yml` ingress |

> **Not every co-resident container uses Docker DNS.** `zoe-multica-backend` sits on
> `zoe-network` but reaches Postgres via `host.docker.internal:5432`, **not**
> `zoe-database:5432` — so "on the same network" does not imply "talks over the network
> name". Check the service's own env before assuming a path exists.

> **Most of Zoe's hot path is NOT on this network at all.** The brain (`llama-server`),
> zoe-data, Kokoro TTS and the router run **host-native** and are reached over
> `localhost` / `host-gateway`, not Docker DNS. A change here cannot break them, and
> conversely a broken voice path is almost never a Docker-network problem.

**If networks don't match:**
- ❌ DNS resolution fails
- ❌ "Name or service not known" errors
- ❌ All dependent features break
- ❌ Silent failures with generic error responses

## 📊 Impact on Tests

**When networks are mismatched:**
- Natural language tests: 0% success
- Tool calling tests: 0% success  
- Integration tests: 0% success
- System appears "working" but produces generic responses

**When networks are correct:**
- Natural language tests: Target 90%+
- Tool calling tests: Target 95%+
- Integration tests: Target 95%+

## 🔄 Pre-Commit Hook

> This repo already has a tracked `.pre-commit-config.yaml` (arm it once per clone with
> `pre-commit install`). Prefer adding a hook there over hand-editing `.git/hooks/`, which
> is untracked and invisible to everyone else. The snippet below is illustrative only —
> note it assumes an `assistant/` path prefix that does not apply inside the repo.

Illustrative check:
```bash
#!/bin/bash
# Validate Docker network configuration
if git diff --cached --name-only | grep -q "docker-compose"; then
    echo "🔍 Validating Docker network configuration..."
    
    if ! grep -q 'name: zoe-network' assistant/docker-compose.yml; then
        echo "❌ ERROR: docker-compose.yml missing 'name: zoe-network'"
        echo "   All services must use explicit network name"
        exit 1
    fi
    
    # Check all services use zoe-network
    services_without_network=$(grep -A 20 "^  [a-z]" assistant/docker-compose.yml | \
                               grep -B 20 "networks:" | \
                               grep -v "zoe-network" | \
                               grep "^  [a-z]" || true)
    
    if [ ! -z "$services_without_network" ]; then
        echo "⚠️  WARNING: Some services may not be on zoe-network"
        echo "   Review: $services_without_network"
    fi
    
    echo "✅ Docker network validation passed"
fi
```

## 📖 Related Documentation

- **What is actually running**: `scripts/maintenance/zoe_ground_truth.sh` (read-only) —
  the authority on live state, over any doc including this one.
- **The one multi-network exception**: [`modules/AGENTS.md`](../../modules/AGENTS.md)
  (`omnigent` + `zoe-codeintel`).
- **Jetson/Pi Deployment**: Network config identical across platforms.

(There is no `docs/guides/DOCKER_TROUBLESHOOTING.md`; it was referenced here but never
committed.)

## 🆘 Troubleshooting

### Symptom: "Name or service not known"
**Cause**: Containers on different Docker networks, **or** the target is host-native and
was never on a Docker network to begin with (see the note above).
**Fix**: `docker network inspect zoe-network` to see who is actually attached; if the
target is host-native, use `host.docker.internal` / `host-gateway` instead of a service name.

### Symptom: Tests failing with 0% success
**Cause**: Likely networking issue preventing service communication
**Fix**: Check the logs of the container that is failing to connect (`docker logs <name>`)

### Symptom: Generic "Hi there!" responses
**Cause**: The brain is unreachable, falling back to a default reply
**Fix**: This is **not** a Docker-network symptom — `llama-server` is host-native.
Check the brain directly (`scripts/maintenance/zoe_ground_truth.sh`).

## 📌 Summary

**Golden Rule**: ONE network (`zoe-network`) for ALL services, with explicit `name:` field.

**Before ANY docker-compose.yml change:**
1. Validate network configuration
2. Test inter-container connectivity
3. Run test suite to verify

**This prevents hours of debugging mysterious failures.**

