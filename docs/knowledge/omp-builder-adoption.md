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
4. **`web_search` disabled or provider-restricted** (operator judgement): 6 default providers
   scrape public endpoints with spoofed headers from the home IP.

## Scan record (skillspector waiver)

Static skillspector: **100/100 CRITICAL / "DO NOT INSTALL" — waived with itemised rationale**
(2026-08-03 evaluation): its top findings restate the product as threats (541 "External
Transmission" = the 40-provider catalogue; 406 "Credential Access" = reading those providers'
keys); remaining categories resolve to test files, Dockerfiles, and the Python eval kernel; its
CVE list is name-matched, not version-resolved. A real `bun audit` found **2 transitive highs**,
both on the optional local-ML path (off by default; postinstalls blocked by bun). No credential
harvesting, obfuscation, or covert telemetry beyond the autoqa endpoint fenced above. This section
is the recorded scan outcome required by the root AGENTS.md skill-safety rule.

## Status

Evaluation only — nothing installed into the live container, no config applied, no dispatch run.
Next steps: operator creates the capped key; then a supervised session applies the config block +
binary + fence (with `config get` read-backs), and trials omp head-to-head against the claude-sdk
lane on real Multica tickets before any builder-of-record change.
