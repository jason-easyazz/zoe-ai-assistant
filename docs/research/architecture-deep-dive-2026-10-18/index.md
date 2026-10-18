---
type: research-evidence
title: Architecture deep dive 2026-10-18 - lane reports (evidence bundle)
date: 2026-10-18
status: evidence for ../architecture-deep-dive-2026-10-18.md; read-only audits, nothing built or changed
description: The six lane reports behind the deep dive, kept verbatim so every claim in the synthesis can be traced to a path:line or a source URL with its evidence tag. Lane reports were written by sub-agents and spot-checked by the author with negative controls (synthesis section 1).
---

# Lane reports

| Lane | File | Scope |
|---|---|---|
| A | [lane-A-core.md](lane-A-core.md) | zoe-data request path, god files, brain lanes, flags, lifecycle, data layer, auth, observability, tests |
| B | [lane-B-comms-substrate.md](lane-B-comms-substrate.md) | voice, panel, identity and communications substrate: what exists for every comms primitive, LiveKit, Telegram, announcements, remote access, backups |
| C | [lane-C-brain-ops-roadmap.md](lane-C-brain-ops-roadmap.md) | memory stack, brain side and night window, harness generations, ops, CI and review pipeline, roadmap coherence, ledger triage, docs retention |
| D | [lane-D-apple-homeview.md](lane-D-apple-homeview.md) | Apple HomeView (announced 2026-10-13) and the Amazon / Google field, with a ranked "what Apple has that Zoe does not" |
| E | [lane-E-field-scan.md](lane-E-field-scan.md) | open-source and commercial field since 2026-09-25: HA/MA, voice stacks, companion agents, home comms stacks, the phone route |
| F | [lane-F-comms-stack.md](lane-F-comms-stack.md) | comms stack options for PA, intercom, messages, calls and backups on the411, with Matrix and Element as the incumbent and the one-Zoe-app question |

Evidence tags: `[verified]` read in code or live state; `[inferred]` reasoned, not executed; web `[P]`/`[verified]` primary page read, `[S]`/`[secondary]` snippet, `[K]`/`[unverified]` from memory. One known conflict between lanes: lane F calls the `livekit` container OOM-killed; lane B's `docker inspect` (`OOMKilled=false`, on-demand reap) is the verified state and the synthesis uses it.
