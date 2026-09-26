---
type: Runbook
title: Home Assistant 2026.5.2 → 2026.9 upgrade runbook (B0.12)
description: Step-by-step, one monthly release at a time, container upgrade of the Zoe-hosted Home Assistant from 2026.5.2 to 2026.9.x — preconditions, the 2026.9 LLM tool-name prefix change and how Zoe's bridge tolerates both schemes, the pending `config_entry_only_mcp_server` repair, the 2026.10 MCP `require_admin` default and `device_id` meta (follow-up), verification and rollback.
tags: [home-assistant, upgrade, runbook, mcp, llm-tools, b0.12]
timestamp: 2026-09-26T00:00:00Z
---

# Home Assistant 2026.5.2 → 2026.9 upgrade runbook (B0.12)

Program item **B0.12** in [beat-the-bar-2026-program.md](../architecture/beat-the-bar-2026-program.md).
Background: [state-of-zoe-review-2026-09-25.md §5.7](state-of-zoe-review-2026-09-25.md).

## What changed upstream (verified 2026-09-26)

| fact | source |
|---|---|
| **2026.9 prefixes every LLM / Assist-API / MCP tool name with the offering domain**, separator `__`: `GetLiveContext` → `homeassistant__GetLiveContext`, `HassTurnOn` → `intent__HassTurnOn`, `GetDateTime` → `llm__GetDateTime`, scripts → `script__<name>`. Intents are prefixed by the integration that **registers** them, not `intent__` across the board: `light__HassLightSet`, `climate__HassClimateSetTemperature`, `media_player__HassMediaPause`, `assist_satellite__HassBroadcast`, `todo__HassListAddItem`. Two tools were renamed outright: `calendar_get_events` → `calendar__get_events`, `todo_get_items` → `todo__get_items`. | core PR [#179938](https://github.com/home-assistant/core/pull/179938) (merged 2026-08-24); [2026.9 release notes](https://www.home-assistant.io/blog/2026/09/02/release-20269/) |
| Unprefixed names are reported via `frame.report_usage` (error for core, warning for custom integrations) from 2026.9 and **stop working in 2027.3** (`TOOL_PREFIX_BREAKS_IN_HA_VERSION = "2027.3"`). | `homeassistant/components/llm/__init__.py` in #179938 |
| **MCP server `require_admin`** (core PR [#180713](https://github.com/home-assistant/core/pull/180713), merged 2026-08-30 → ships in 2026.10): restricts `/api/mcp`, `/mcp_server/sse`, `/mcp_server/messages` to admin users. **New config entries enable it; a minor-version migration adds it *disabled* to existing entries**, so upgrading does not change behaviour — but any entry Zoe creates after the upgrade will require an admin token. Even before this, non-`assist` LLM APIs on the MCP server are admin-only. | PR body; `mcp_server/http.py` on `dev` |
| **MCP `device_id` meta** (core PR [#182057](https://github.com/home-assistant/core/pull/182057), merged 2026-09-13 → 2026.10): a client sends `params._meta["io.home-assistant/device_id"]` and HA uses it as `LLMContext.device_id`, so area-aware tools ("turn on the lights") resolve to that device's area. An HTTP-header variant (`Home-Assistant-Device-Id`, PR #182625) is still open. | PR bodies |
| Breaking changes in the intermediate releases (2026.6, 2026.7, 2026.8) touch none of Zoe's surface (conversation/Assist, wyoming, auth, http, recorder, custom-component API). 2026.9's other breaking items: Update install/skip and Z-Wave lock credential actions now need an admin account; vacuum `battery_level` removed; persistent-notification update type renamed; KNX/UniFi Protect/Flexit changes. | release notes 2026.6 / .7 / .8 / .9 |

## Where Zoe names HA LLM tools — the sweep result

The sweep (2026-09-26, this repo at `main`) found **no live call site** that sends an HA LLM tool name to HA:

- `services/homeassistant-mcp-bridge/main.py` is a pure REST bridge (`/api/states`, `/api/services/<domain>/<service>`, `/api/config`); it never speaks the Assist LLM API or HA's MCP server.
- `services/zoe-data` talks to HA only through that bridge (`ZOE_HA_BRIDGE_URL`, port 8007): `routers/ha_control.py`, `smart_home_service.py`, `zoe_agent.py`, `intent_router.py`, `mcp_server.py`. No `HassTurnOn`/`GetLiveContext` strings; `intent_router.py` only *mentions* `HassGetCurrentTime/Date` in comments (its own clock intent mirrors them).
- The router corpus (`labs/router-90-campaign`, `services/zoe-data/models`) and the `labs/flue-zoe-brain-2x` tools use Zoe's own tool names, not HA's.
- `homeassistant/custom_components/zoe_conversation` runs *inside* HA and routes Assist text **to** Zoe; it does not consume the LLM API.
- `tests/intent_system/test_classification.py` asserts `intent.name == "HassTurnOn"` on a Zoe-internal classifier (`intent_system.classifiers`) that no longer exists in the tree — a dead legacy test, not an HA call site.
- HA's own `mcp_server` integration is **not loaded** on the live 2026.5.2 (`/mcp_server/sse` → 404; not in `/api/config` `components`). Zoe does not consume it today.

So the upgrade cannot break a tool-name call site today. The exposure is forward-looking — the moment Zoe consumes HA's MCP server or Assist LLM API (the B0.12 `device_id` follow-up does exactly that), names must be spelled per the connected HA's scheme. That is now centralised:

- **`services/homeassistant-mcp-bridge/ha_tool_names.py`** — base-name → domain table, `ha_tool_name(base, scheme)`, `script_tool_name(id, scheme)`, `normalize_tool_name(inbound, scheme=…)` (accepts either scheme, **rejects unknown names**; the detected scheme is what lets a legacy HA's bare script names — `morning`, `_3am_check` — resolve, since those are otherwise indistinguishable from unknown bare names), and `HaToolNameSchemeDetector` (reads `/api/config` `version` once, caches, logs the chosen scheme once; `HA_TOOL_NAME_SCHEME=legacy|prefixed` pins it).
- **`GET /tools/names`** on the bridge (port 8007) returns `{scheme, ha_version, tools: {base: spelling}}`; **`GET /tools/names/<name>`** normalises a name in either scheme (404 for unknown). `GET /` reports `tool_name_scheme` once detected.
- Tests: `services/homeassistant-mcp-bridge/tests/test_ha_tool_names.py` (ci_safe; enumerated in `validate.yml`).

## Preconditions

1. **This PR merged and the bridge container rebuilt** (`docker compose build homeassistant-mcp-bridge && docker compose up -d homeassistant-mcp-bridge`; `/app` is volume-mounted, so a restart alone also picks the code up). `curl -s localhost:8007/tools/names | python3 -c 'import json,sys; print(json.load(sys.stdin)["scheme"])'` → `legacy` against 2026.5.2 (no `jq` on the box).
2. **Backup the config dir** (bind-mounted `./homeassistant:/config` in `docker-compose.yml`, i.e. `/home/zoe/assistant/homeassistant/` — `.storage/` holds the registries, config entries and auth, `home-assistant_v2.db` is the recorder history; neither is in git). The upgrade block below takes it **with HA stopped** (a hot copy of the SQLite recorder can be inconsistent) and **verifies the archive lists both `.storage/` and the recorder db before anything else runs**. Take a fresh one before EACH monthly step — `.storage` migrations AND recorder schema bumps are forward-only, and the backup from immediately before a step is that step's only rollback point.
3. **Pin the image tag.** `docker-compose.yml` uses `ghcr.io/home-assistant/home-assistant:stable` (the `# pinned: sha256:…` comment is documentation, not a pin). A bare `docker compose pull` would jump 2026.5.2 → 2026.9.3 in one hop. For the stepped upgrade, edit the tag per step (`:2026.6.4`, `:2026.7.x`, `:2026.8.x`, `:2026.9.3` — use the latest patch of each month from `gh api repos/home-assistant/core/releases`) and only return to `:stable` at the end if that policy is kept.
4. **Custom components on the box** (`/home/zoe/assistant/homeassistant/custom_components/`): `auth_oidc` (Zoe SSO — the login path), `hacs`, `localtuya`, `zoe_conversation`. These are the likeliest breakers; check each one's release page for the target HA version before every step, and keep the local `homeassistant` auth provider (it is configured — see `configuration.yaml`) as the fallback login if `auth_oidc` fails.
5. Quiet window: HA Assist → `zoe_conversation` → bridge `/voice/*` is part of the panel's voice path; do this while nobody needs the panel. Do **not** touch the brain/STT/TTS services.
6. Free disk for four image pulls (~1.5 GB each; 127 dangling images were noted in the review — `docker image prune` first if tight).

## Upgrade — one monthly release at a time

For each of `2026.6.x`, `2026.7.x`, `2026.8.x`, `2026.9.3`. **Run the block as a script, never pasted line by line**: save it as `~/ha-upgrade-step.sh` and run `bash ~/ha-upgrade-step.sh 2026.6.4` from `/home/zoe/assistant` (the live checkout, **not** a worktree — compose reads the live tree). It is `set -euo pipefail` with an `EXIT` trap that knows which phase failed: the target image is pulled **before** HA is stopped (a registry failure never costs downtime), and any failure between `stop` and `up -d` restores the previous tag and restarts HA on it — HA is never left stopped. A failure after HA has started on the new tag leaves it running for inspection and prints the exact rollback command, because a plain retag over forward-migrated `.storage` is precisely what the rollback script exists to avoid. In an interactive paste none of that fires and the shell would carry on into the retag.

```bash
#!/usr/bin/env bash
# usage: bash ~/ha-upgrade-step.sh <TAG>     (from /home/zoe/assistant)
set -euo pipefail
TAG=${1:?usage: ha-upgrade-step.sh <TAG>}   # 2026.6.4, then 2026.7.x, 2026.8.x, 2026.9.3
cd /home/zoe/assistant
# HA_ACCESS_TOKEN lives in the repo .env; compose passes it to containers but never exports it into
# this shell, so read it here — otherwise every curl below sends an empty bearer and gets 401.
HA_ACCESS_TOKEN=$(grep -E '^HA_ACCESS_TOKEN=' .env | cut -d= -f2-)
[ -n "$HA_ACCESS_TOKEN" ] || { echo "HA_ACCESS_TOKEN not found in .env — STOP"; exit 1; }
PREV=$(sed -nE 's#.*home-assistant/home-assistant:([^ ]+).*#\1#p' docker-compose.yml | head -1)
[ -n "$PREV" ] || { echo "cannot read the current image tag from docker-compose.yml — STOP"; exit 1; }
mkdir -p ~/backups
BK=~/backups/homeassistant-$(date +%F)-pre-$TAG.tgz

# Failure handler. set -e makes every failing command land here with its status; PHASE says what
# has already happened so the handler undoes exactly that and never leaves HA stopped.
PHASE=pulling
on_exit() {
  rc=$?; [ "$rc" -eq 0 ] && return 0
  trap - EXIT
  case "$PHASE" in
    pulling)  echo "STEP FAILED (rc=$rc) before HA was touched — HA still running on $PREV; nothing to undo" ;;
    stopped)  echo "STEP FAILED (rc=$rc) with HA stopped — restoring tag $PREV and restarting HA"
              sed -i "s#home-assistant/home-assistant:[^ ]*#home-assistant/home-assistant:$PREV#" docker-compose.yml
              docker compose up -d homeassistant
              echo "HA restarted on $PREV; step NOT applied; backup $BK left in place" ;;
    started)  echo "STEP FAILED (rc=$rc) after HA started on $TAG — left RUNNING on $TAG for inspection (docker logs homeassistant)."
              echo "Do NOT just retag: .storage may already be migrated forward. If $TAG is not recoverable, run:"
              echo "  bash ~/ha-rollback.sh $BK $PREV" ;;
  esac
  exit "$rc"
}
trap on_exit EXIT

# 1. Pull the target image FIRST — needs no downtime, and a registry failure stops here with HA untouched.
docker pull "ghcr.io/home-assistant/home-assistant:$TAG"

# 2. Backup with HA stopped (consistent .storage + recorder db, no open WAL), then PROVE the archive
#    lists both before the tag changes. From here every failure goes through on_exit → HA restarted on $PREV.
PHASE=stopped
docker compose stop homeassistant
tar -C /home/zoe/assistant -czf "$BK" homeassistant/
tar -tzf "$BK" > "$BK.list"
grep -q '^homeassistant/\.storage/' "$BK.list" || { echo "backup lacks .storage/ — BACKUP INCOMPLETE ($BK)"; exit 1; }
grep -q '^homeassistant/home-assistant_v2\.db$' "$BK.list" || { echo "backup lacks the recorder db — BACKUP INCOMPLETE ($BK)"; exit 1; }

# 3. Retag and start on the already-pulled image.
sed -i "s#home-assistant/home-assistant:[^ ]*#home-assistant/home-assistant:$TAG#" docker-compose.yml
docker compose up -d homeassistant
PHASE=started

# 4. Wait for startup (registries migrate on first boot; 1–3 min on the Orin). Bounded: a bad token or
#    a boot loop must not spin forever — 10 min, then exit 1 with the rollback command printed.
for _ in $(seq 1 120); do
  curl -sf -H "Authorization: Bearer $HA_ACCESS_TOKEN" localhost:8123/api/config >/dev/null && break
  sleep 5
done
curl -sf -H "Authorization: Bearer $HA_ACCESS_TOKEN" localhost:8123/api/config >/dev/null \
  || { echo "HA not answering /api/config with this token after 10 min"; exit 1; }
# (jq is not installed on the box — python3 is)
curl -s -H "Authorization: Bearer $HA_ACCESS_TOKEN" localhost:8123/api/config \
  | python3 -c 'import json,sys; c=json.load(sys.stdin); print("version", c["version"], "components", len(c["components"]))'
docker logs homeassistant --since 10m 2>&1 | { grep -iE "error|migrat|deprecat|custom integration" || true; } | head -40
echo "STEP OK: HA up on $TAG (was $PREV); backup $BK"
```

Per-step verification (all read-only; for the manual `curl`s below, export the token into your shell first — `export HA_ACCESS_TOKEN=$(grep -E '^HA_ACCESS_TOKEN=' /home/zoe/assistant/.env | cut -d= -f2-)` — compose does not do it for you):

- `version` matches `$TAG`; component count roughly unchanged (166 on 2026.5.2).
- Settings → Repairs: no NEW repair beyond the pre-existing `config_entry_only_mcp_server` (see below).
- Login via Zoe SSO (`auth_oidc`) still works; local login also works.
- `curl -s -X POST -H "Authorization: Bearer $HA_ACCESS_TOKEN" -H 'Content-Type: application/json' localhost:8123/api/conversation/process -d '{"text":"what time is it","language":"en"}'` returns speech.
- Bridge: `curl -s localhost:8007/ | python3 -m json.tool | grep -E '"(status|ha_connected|tool_name_scheme)"'` is healthy; `docker restart homeassistant-mcp-bridge` then `curl -s localhost:8007/tools/names | python3 -m json.tool | grep -E '"(scheme|ha_version)"'` (no `jq` on the box) — `legacy` for 2026.6–2026.8, **`prefixed` from 2026.9** (the detector caches per process; the restart is what re-reads the version).
- Panel: one wake + one "turn on <light>" through HA Assist → `zoe_conversation` → bridge, and one Zoe chat command through the bridge (`routers/ha_control.py`).
- `wyoming` (faster-whisper / piper) config entries still load — they are registered but not on Zoe's voice path (Moonshine/Kokoro are), so a failure here is a repair to note, not a rollback trigger.

Do not skip a month on the first pass. HA supports multi-version jumps, but stepping keeps each `.storage` migration and each custom-component check attributable to one release; if a step fails, the backup taken immediately before it is the rollback point.

## The pending repair: `config_entry_only_mcp_server`

Live repair registry: `homeassistant config_entry_only_mcp_server`, created 2025-11-10. HA raises it when a config-entry-only integration is configured in YAML. **There is no `mcp_server:` key in any tracked `homeassistant/*.yaml` today**, so the YAML that caused it is already gone and the issue is a leftover. After the first restart, check Settings → Repairs: if it is still listed, dismiss it (it is informational). Do **not** re-add `mcp_server:` to YAML — when Zoe adopts HA's MCP server it is set up as a config entry (Settings → Integrations → Model Context Protocol Server).

## `require_admin` — the bridge's HA user (2026.10+)

`HA_ACCESS_TOKEN` is a long-lived token for a specific HA user. Today the bridge only uses REST, which is unaffected. When the MCP server config entry is created on 2026.10+ it will default to `require_admin: true`, so **either the token's user is an administrator or the option is switched off in that entry's options flow**. Prefer keeping `require_admin` on and giving the bridge an admin token scoped to that purpose; record the choice in `config/AGENTS.md`'s key-material notes (locations only, never values).

## `device_id` — follow-up (B0.12 part 2)

Once on 2026.10, Zoe can pass the panel's HA device id in `params._meta["io.home-assistant/device_id"]` on each MCP `tools/call`, and area-aware intents resolve "the lights" to that room. Prerequisites: the panel exists as an HA device (register `zoe-touch-pi` as an `assist_satellite`/device so it has an id and an area), the bridge (or zoe-data) speaks the MCP server with names via `ha_tool_names.tool_table("prefixed")`, and `require_admin` is handled as above. Track as its own PR; do not fold it into the upgrade window.

## Rollback

- **Same-month patch rollback** (e.g. 2026.9.3 → 2026.9.1): change the tag, `docker compose up -d homeassistant`. Safe.
- **Cross-month rollback**: `.storage` migrations and recorder schema bumps are forward-only, so restore the backup from immediately before that step. **Validate the archive before touching live state, and set the newer state aside rather than deleting it** — a mistyped date/tag, a backup that failed, or a damaged archive must fail *before* the registries, config entries, auth state and history are gone, not after:

  Same rule as the upgrade step: **run it as a script** (`bash ~/ha-rollback.sh <archive> <previous-tag>`), not pasted — the preflight is only connected to the destructive block by `set -e` + `exit 1`, and the `EXIT` trap that puts the newer state back and restarts HA if anything fails after the stop only exists inside the script. Each attempt gets its own `mktemp -d` set-aside directory (printed at the start), so two same-day rollbacks cannot collide.

  ```bash
  #!/usr/bin/env bash
  # usage: bash ~/ha-rollback.sh ~/backups/homeassistant-<date>-pre-<TAG>.tgz <previous-tag>
  #        (archive = the backup taken immediately before the failed step; previous-tag e.g. 2026.8.9)
  set -euo pipefail
  BK=${1:?usage: ha-rollback.sh <archive.tgz> <previous-tag>}
  PREV=${2:?usage: ha-rollback.sh <archive.tgz> <previous-tag>}
  cd /home/zoe/assistant
  # 1. Prove the archive is readable and holds what we are about to replace. exit 1 here means live
  #    state was never touched.
  tar -tzf "$BK" > /tmp/ha-rollback.list || { echo "ARCHIVE UNREADABLE ($BK) — STOP; live state untouched"; exit 1; }
  grep -q '^homeassistant/\.storage/' /tmp/ha-rollback.list || { echo "ARCHIVE LACKS .storage/ ($BK) — STOP; live state untouched"; exit 1; }
  grep -q '^homeassistant/home-assistant_v2\.db$' /tmp/ha-rollback.list || { echo "ARCHIVE LACKS the recorder db ($BK) — STOP; live state untouched"; exit 1; }
  # A fresh set-aside dir per attempt (two same-day rollbacks must not collide on the mv).
  ASIDE=$(mktemp -d "$HOME/backups/homeassistant-rolled-back-$(date +%F)-XXXXXX")
  echo "newer state will be set aside in $ASIDE"

  # 2. Failure handler for everything after HA is stopped: put the set-aside state back (discarding any
  #    partial extraction), restart HA on whatever state is now consistent, and say so. HA is never left stopped.
  PHASE=stopped
  on_exit() {
    rc=$?; [ "$rc" -eq 0 ] && return 0
    trap - EXIT
    echo "ROLLBACK FAILED (rc=$rc) with HA stopped (phase=$PHASE)"
    if [ "$PHASE" = aside ]; then
      rm -rf homeassistant/.storage; mv "$ASIDE/.storage" homeassistant/
      for f in "$ASIDE"/home-assistant_v2.db*; do [ -e "$f" ] && { rm -f "homeassistant/$(basename "$f")"; mv "$f" homeassistant/; }; done
      echo "newer .storage + recorder db put back from $ASIDE; tag unchanged"
    elif [ "$PHASE" = restored ]; then
      sed -i "s#home-assistant/home-assistant:[^ ]*#home-assistant/home-assistant:$PREV#" docker-compose.yml
      echo "backup state is restored and the tag is $PREV; newer state kept in $ASIDE"
    fi
    docker compose up -d homeassistant
    echo "HA restarted; inspect and re-run"
    exit "$rc"
  }
  trap on_exit EXIT

  # 3. Stop HA, move (not rm) the newer state aside, restore .storage AND the recorder db (an older core
  #    can refuse a newer recorder schema), then downgrade the tag.
  docker compose stop homeassistant
  mv homeassistant/.storage "$ASIDE"/
  mv homeassistant/home-assistant_v2.db* "$ASIDE"/
  PHASE=aside
  tar -C /home/zoe/assistant -xzf "$BK" --wildcards homeassistant/.storage 'homeassistant/home-assistant_v2.db*'
  PHASE=restored
  sed -i "s#home-assistant/home-assistant:[^ ]*#home-assistant/home-assistant:$PREV#" docker-compose.yml
  docker compose up -d homeassistant
  echo "ROLLBACK OK: HA up on $PREV from $BK; newer state kept in $ASIDE"
  ```

  Rolling back the recorder db means **history recorded after that backup is lost** (energy/statistics included); that is the price of a consistent older-schema database, and it is why the backup is taken with HA stopped immediately before each step. The set-aside copy (the `$ASIDE` path the script prints) keeps the newer state for forensics; delete it once the rollback is confirmed good.
- The bridge needs nothing on rollback: the scheme is re-detected on its next restart (or pin `HA_TOOL_NAME_SCHEME=legacy` in `.env` for the window and remove it afterwards).
- Panel voice path is untouched by any of this; if it misbehaves after a step, the suspect is `zoe_conversation` or `auth_oidc` under the new core, not Zoe's services.
