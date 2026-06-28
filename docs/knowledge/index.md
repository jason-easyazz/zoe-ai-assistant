---
type: index
title: Zoe Knowledge Bundle
description: Curated, agent- and human-readable reference knowledge for Zoe, in Open Knowledge Format (OKF). Governed by docs/AGENTS.md.
---

# Zoe Knowledge (OKF bundle)

Open Knowledge Format bundle — markdown + YAML frontmatter, cross-linked, readable by humans without tooling and by agents without bespoke SDKs.

This is **knowledge / records** (descriptive facts), not a DOX contract. See the root `AGENTS.md` rule "Knowledge vs. Records (OKF)": binding rules live in `AGENTS.md`; this bundle curates what is *true* about Zoe and may be maintained by the autonomous knowledge loop.

## Concepts

- [Zoe tool stack](zoe-tool-stack.md) — the installed agent tooling (graphify, opensrc, Multica, Pi, Hermes, OpenClaw, MemPalace, SkillSpector) and how the pieces relate.
- [Runtime topology](runtime-topology.md) — the live runtime: host, services, ports, where each is served from and logs to, the touch panel, and the no-pipeline deploy. Orientation before touching the running system.
- [Voice pipeline](voice-pipeline.md) — the STT → brain → TTS path, the replay-sample regression corpus, and the caveat that the warm harness understates real live latency.
- [Merge & deploy discipline](merge-and-deploy.md) — merged ≠ live, the protected-`main` gates, and the Greptile/greploop gotchas (large-PR skip, thread resolution, REST-not-GraphQL).

## Bundles

- [Multica autopilot loop contracts](autopilots/index.md) — Loop-Engineering contracts (Job / Inputs / Allowed / Forbidden / Output / Evaluation) for the three live Multica autopilots.
