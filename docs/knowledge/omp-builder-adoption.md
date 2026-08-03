---
type: decision-record
title: oh-my-pi (omp) as Omnigent builder harness — evaluation & adoption conditions
description: 2026-08-03 evaluation verdict (GO-WITH-CONDITIONS), the ACP wiring recipe, the security fence, and the skillspector waiver rationale.
lastReviewedAt: 2026-08-03
---

# omp as an Omnigent builder harness — GO-WITH-CONDITIONS (2026-08-03)

[oh-my-pi](https://github.com/can1357/oh-my-pi) (MIT fork of the pi coding agent, source cached at
`~/.opensrc/repos/github.com/can1357/oh-my-pi/main`) evaluated as a supercharged **builder-lane**
harness inside Omnigent — never as Zoe's brain (that stays Flue + pi-ai on the Gemma rock).

## Wiring (verified, not yet applied)

- Omnigent 0.7.0 ships a **generic `acp` harness**; omp ships a first-class `omp acp` server.
  Verified offline on this Tegra host: `omp acp` answers JSON-RPC `initialize` with a valid ACP
  result (`oh-my-pi 17.2.5`; streaming, interrupt, permission-mirroring). Adoption = one `acp:`
  block in `~/.omnigent/config.yaml` → harness id `acp:oh-my-pi`. Zero code either side.
- **Binary**: use the standalone `omp-linux-arm64` release binary (runs + speaks ACP on aarch64;
  sha256 prefix `5404bb60…35d2` at evaluation). The npm package is **Bun-only** (negative-control
  proven: Node 22 dies on `using` declarations) and the Omnigent container has Node only.
  `@oh-my-pi/pi-natives-linux-arm64` exists and installs cleanly — arm64 is a non-issue.
- **Trap**: Omnigent computes `acp` availability as `bool(acp_agents())` with **no binary check** —
  config alone flips the lane "usable". Verify the binary exists before enabling the config.
- **Trap**: `omp config set` prints success even when the value doesn't take effect in the runtime
  scope; every fence setting must be verified by `omp config get` read-back in that scope.

## Conditions (the fence) — all four before first dispatch

1. **Dedicated OpenRouter key with a vendor-side hard credit limit.** ACP agents own their own
   auth, so Omnigent's per-dispatch caps do NOT carry over — and `OMNIGENT_RUNNER_ENV_PASSTHROUGH`
   already exposes `OPENROUTER_API_KEY`, which omp reads natively. Without a dedicated capped key,
   omp inherits the lane's spending power with none of its caps. Operator action (dashboard).
2. **`PI_AUTO_QA=0`.** `dev.autoqa` defaults ON and posts model-authored free text (can contain
   repo fragments) plus a persistent install UUID to `https://qa.omp.sh/v1/grievances`.
3. **`marketplace.autoUpdate` pinned `off`.** Its `auto` mode executes `new Function()` over
   fetched plugin source; there is no global plugin kill switch.
4. **`web_search` disabled in the builder lane** — but the honest rationale is volume and
   autonomy, not the scraping itself: Zoe's own lookup chain already scrapes via `ddgs` with a
   randomized UA from the same IP (`zoe_agent.py` `DDGS().text()`, `backend="auto"` = 11 engines).
   The distinction that holds: user-triggered, low-volume lookups vs an autonomous builder
   hammering endpoints unattended. Align both sides when the search-stack decision
   (Tavily-primary + fallback tier) lands; until then the builder gets no scraping.

## Scan record (skillspector waiver)

Static skillspector: **100/100 CRITICAL / "DO NOT INSTALL" — waived with itemised rationale**
(2026-08-03 evaluation): its top findings restate the product as threats (541 "External
Transmission" = the 40-provider catalogue; 406 "Credential Access" = reading those providers'
keys); remaining categories resolve to test files, Dockerfiles, and the Python eval kernel; its
CVE list is name-matched, not version-resolved. A real `bun audit` found **2 transitive highs**,
both on the optional local-ML path (off by default; postinstalls blocked by bun). No credential
harvesting, obfuscation, or covert telemetry beyond the autoqa endpoint fenced above. This section
is the recorded scan outcome required by the root AGENTS.md skill-safety rule.

## Supply-chain & runtime hardening (external verification, 2026-08-03)

- **Release binaries are unverifiable** — no signatures, checksums, or SLSA/Sigstore attestations
  on any asset (the npm-provenance in ci.yml covers only the dead `oh-my-pi` npm package). At
  ~1.5 releases/day, **pin one exact version and record the SHA-256 we audited** (see Wiring
  above) — never "latest".
- **Enforce the fence OUTSIDE the process.** Two open upstream issues make in-process settings
  insufficient: #3293 (read/write outside cwd without approval) and #2227 (MCP discovery scans
  11+ config dirs by default). Run omp in the Omnigent container with read-only mounts, no
  ambient MCP configs, and no ambient credentials beyond the dedicated capped key.
- **AutoQA discrepancy — unresolved, fence stands.** Upstream says the telemetry concern was
  fixed 2026-05-20 (#1224→#1226); our hands-on 17.2.5 check found `dev.autoqa` defaulting ON.
  Keep `PI_AUTO_QA=0` regardless and verify by `config get` read-back.
- **ACP is a safe seam**: wire protocol v1, stable since 2025-11; multi-vendor governance;
  60+ agents / 13+ editors. If any component needs the SDK, pin `@agentclientprotocol/sdk ~1.3.x`
  — the `@zed-industries/agent-client-protocol` package is **abandoned** (0.4.5, 2025-10). Watch
  the `schema-v2.0.0-alpha.*` drafts before any major SDK bump.
- **Project health**: 387 contributors but ~60% of 90-day commits are the author (plus the
  project's own bot at #2); badlogic (upstream pi author) contributes directly; zero advisories.
  arm64 binaries get ~30 downloads/release vs 331 x64 — expect to be an early finder of arm64
  bugs. Upstream pi itself moved to `earendil-works/pi` (v0.83.0, ~500 commits/month).

## Trial staging findings (2026-08-03, measured — staging dir `omp-trial/` in the session scratchpad, entry point APPLY.md)

- **Binary v17.2.5 installed** at `/home/zoe/.local/bin/omp`, full sha256 `5404bb60…5435d2` re-verified against the recorded fingerprint; that path is bind-mounted read-only into the container and on its PATH — no container change needed.
- **Omnigent's acp harness has NO per-agent env** (`acp_agents()` silently drops an `env:` key) and `acp_executor.py` copies the full runner env — an unwrapped omp inherits the SHARED OpenRouter key and bills it on first token. The fence is therefore a **wrapper script** (command = script path) that scrubs inherited credentials and promotes the dedicated key from `/home/zoe/.config/zoe/openrouter-omp.env` (0400; bind-mounted; `rm` = instant kill switch), verified at point of use via `/proc/<pid>/environ` of a live child.
- **`omp config set` fencing is defeatable by the agent itself**: a `<cwd>/.omp/config.yml` written by a misbehaving agent re-enabled web_search/autoqa/autoUpdate in a measured test. The authoritative fence is a **`PI_CONFIG_FILES` overlay** (highest-precedence merge layer, fail-closed on missing file) — all six fence values held against the hostile cwd config.
- **TRAP: an unpinned dispatch runs omp INSIDE THE LIVE CHECKOUT with auto-approved exec.** Default cwd falls back to `/workspace` = read-write bind of `/home/zoe/assistant`, and `tools.approvalMode` defaults to `yolo`. Every dispatch must pin a working folder (trial protocol does).
- **#2227's user-level half**: the container's `/root/.claude.json`, `/root/.cursor`, `/root/.codex` are live MCP/credential surfaces omp would scan; fenced by pointing `HOME` at a clean dir in the wrapper.
- No omnigent restart needed for apply/rollback — config is re-read per dispatch.

## Status

Evaluation only — nothing installed into the live container, no config applied, no dispatch run.
Next steps: operator creates the capped key; then a supervised session applies the config block +
binary + fence (with `config get` read-backs), and trials omp head-to-head against the claude-sdk
lane on real Multica tickets before any builder-of-record change.
