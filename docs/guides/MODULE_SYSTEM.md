# Zoe Modules

`modules/` holds optional add-on **containers**. This page is the practical answer to
"how do I add one". The binding contract is
[modules/AGENTS.md](../../modules/AGENTS.md); the architectural framing is
[architecture/EXTENSIBILITY.md](../architecture/EXTENSIBILITY.md).

---

## What a module actually is today

There is **exactly one**: `modules/omnigent`. It is a `Dockerfile`, an `entrypoint.sh`,
an `.mcp.json`, a `docker-compose.module.yml` and a `README.md` — no `main.py`, no
`/tools/*` routes, no `intents/`, no widgets.

It deploys from its **own** compose file under its **own** compose project
(`com.docker.compose.project=omnigent`), joins `zoe-network`, and zoe-data reaches it by
URL (`ZOE_OMNIGENT_URL`, default `http://127.0.0.1:6767` —
`services/zoe-data/omnigent_issue_executor.py`).

That is the whole live integration. **A module is a container on `zoe-network` that
something calls by URL.** Everything richer that used to be documented is dead.

---

## What is NOT wired — do not build against it

`docs/modules/` held four guides (~1,900 lines) describing an MCP-router / intent /
widget module system. They were **deleted 2026-08-06** rather than repaired, because
every integration seam they described has zero live consumers. Each row below was
measured, not assumed:

| documented mechanism | status |
|---|---|
| `zoe-mcp-server` tool router on `:8003` | **gone.** `services/zoe-mcp-server/` does not exist; nothing listens on `:8003`; `docker-compose.yml` marks it RETIRED |
| `/api/mcp/tools/*` HTTP surface | **404.** `services/zoe-ui/nginx.conf` has no `location /api/mcp/`. `dist/js/lib/mcp-client.js` is still script-included by two dashboards, and every call it makes dead-ends here |
| module widget auto-discovery (`/modules/<name>/widget/manifest`) | **no loader runs.** `module-widget-loader.js` and `widget-registry.js` are script-included by zero pages (negative control: `js/auth.js` and `js/common.js` *are* found by the same grep, so the search works) |
| intent auto-discovery from `modules/*/intents/` | **no consumer.** Nothing under `services/` or `tools/` scans that path |
| `tools/zoe_module.py enable` → `docker-compose.generated-modules.yml` | **no consumer.** `config/modules.yaml` has `enabled_modules: []` and the generated file has `services: {}`. No CI job, deploy script or systemd unit reads it. Both CLIs still *print* a manual `docker compose -f docker-compose.yml -f docker-compose.generated-modules.yml up -d` hint (`zoe_module.py:252`, `generate_module_compose.py:239`) — but with `services: {}` that command deploys nothing |
| `zoe-core` as the service modules talk to | **not running.** `:8000` is host-native zoe-data; the `zoe-core` container is RETIRED in `docker-compose.yml` |
| modules "served under `/modules/` by nginx" | the only such route is `/modules/music-assistant/`, proxying the upstream Music Assistant container — which is **not** a `modules/` tree module. `omnigent` has no nginx route |

The `js/widgets/core/` and `js/widgets/music/` trees in zoe-ui are a *different*,
live system (the dashboard's own widgets). They are not module-provided and are
unaffected by any of the above.

---

## Adding a module

1. **Create `modules/<name>/`** with `docker-compose.module.yml`, `Dockerfile` and
   `README.md`. There is no scaffold to copy: `omnigent` is container-only, and
   copying it collides with the live `zoe-omnigent` deployment.

2. **Join the shared network** so in-cluster callers reach the module by service name.
   Note the two `networks:` keys are at *different* levels — one under the service,
   one at the top of the file:

   ```yaml
   services:
     your-module:
       # ...
       networks:
         - zoe-network        # service-level: attach this service

   networks:                  # top-level: declare the network itself
     zoe-network:
       name: zoe-network      # prevents Docker's project-name prefix
       external: true         # join the existing network, don't create one
   ```

3. **Publish on loopback only.** A bare `"PORT:PORT"` binds `0.0.0.0` and `[::]`,
   exposing the module to the whole LAN. In-cluster reach is by service name, so
   nothing legitimate needs the wider bind:

   ```yaml
   ports:
     - "127.0.0.1:PORT:PORT"
   ```

4. **Token-gate every state-changing route, failing closed.** This is the module
   contract in `modules/AGENTS.md`, not optional hardening. Keep `/health` open so
   the container healthcheck works:

   ```python
   SERVICE_TOKEN = os.getenv("ZOE_YOURMODULE_SERVICE_TOKEN", "")

   def require_service_token(x_zoe_service_token: str = Header(default="")) -> None:
       if not SERVICE_TOKEN:
           # Fail CLOSED: unconfigured is never open.
           raise HTTPException(503, "module service token not configured")
       if not secrets.compare_digest(x_zoe_service_token, SERVICE_TOKEN):
           raise HTTPException(401, "bad or missing X-Zoe-Service-Token")

   @app.post("/tools/your_action", dependencies=[Depends(require_service_token)])
   async def tool_your_action(request: YourRequest): ...
   ```

   The caller sends the same value as `X-Zoe-Service-Token`. Provision the secret via
   the environment (`ZOE_YOURMODULE_SERVICE_TOKEN=${ZOE_YOURMODULE_SERVICE_TOKEN:-}`);
   never commit it.

5. **Deploy it under its own compose project**, as `omnigent` does:

   ```bash
   docker compose -p <name> -f modules/<name>/docker-compose.module.yml up -d
   ```

6. **Wire the caller explicitly.** Nothing discovers a module. Give zoe-data (or
   whatever consumes it) an env-configured URL and put the call behind a flag, so core
   Zoe still runs with the module absent — that is the one module rule that has never
   stopped being true.

7. **Check RAM before building.** A module image build alongside the ~6 GB
   `llama-server` can OOM the live brain, and a finished build leaves GBs in page cache
   that CUDA cannot use. See `modules/AGENTS.md` → *Work Guidance*.

---

## Existing tooling — what it is, and what it is not

- **`tools/validate_module.py <name>`** — structure and safety checks (required files,
  compose network config, no `eval`/`exec`, no committed secrets, `/health` present).
  Takes a module *name*; `modules/` is prepended. It assumes a FastAPI-shaped module, so
  `omnigent` deterministically **FAILS** it and is meant to.
- **`tools/generate_module_compose.py` + `config/modules.yaml`** — generate
  `docker-compose.generated-modules.yml`. Nothing consumes the output today, and the
  generator hardcodes `zoe-network` as the only top-level network, so it cannot represent
  `omnigent` (which also needs `zoe-codeintel`). Do not hand-edit the generated file.
- **`tools/zoe_module.py`** — `list` / `enable` / `disable` / `status` over
  `config/modules.yaml`. Enabling a module records it there; it does not deploy anything.

`docker-compose.modules.yml` is a different, hand-maintained file: it holds host services
(Music Assistant, ytmusic-potoken, Multica), not `modules/` tree modules.

---

## Recovering the deleted guides

```bash
git log --all -- docs/modules
```

They are history, not reference. Nothing in them describes a system that runs.
