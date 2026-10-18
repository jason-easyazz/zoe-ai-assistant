---
type: research-evidence
title: Architecture deep dive round 2 (2026-10-19) - lane reports (evidence bundle)
date: 2026-10-19
status: evidence for ../architecture-deep-dive-2-2026-10-19.md; read-only audits, nothing exploited, nothing restarted
description: Five of the six lane reports behind the second deep dive, kept verbatim; the security lane is held privately until its findings are fixed. Every claim carries path:line and [verified]/[inferred], or a web-evidence tag. The traffic lane holds counts, rates and durations only; its intermediate files were deleted before this bundle was written.
---

# Lane reports, round 2

| Lane | File | Scope |
|---|---|---|
| H | held privately (`~/.zoe/security-review/2026-10-19/` on the box, 0600) | security and exposure; published here only after each finding's fix has merged |
| I | [lane-I-panel-voice.md](lane-I-panel-voice.md) | the panel and voice experience end to end: daemon, latency budget, wake word, identity in practice, barge-in, kiosk, enrolment, panel health, corpus coverage |
| J | [lane-J-data.md](lane-J-data.md) | Postgres inventory, duplicate truths, SQLite and file stores, Chroma, retention, restore completeness, schema hygiene, access patterns, multi-household readiness |
| K | [lane-K-tests.md](lane-K-tests.md) | test-suite truthfulness: shape, mock density, the voice gate and the Samantha instruments as instruments, vacuous contract tests, flakiness, risk-path coverage, a truthful-suite proposal |
| L | [lane-L-traffic.md](lane-L-traffic.md) | what the real traffic says, aggregates only: volume and channels, router truth, voice quality, failure capture, memory in use, features used vs built, health signals |
| M | [lane-M-iphone-app.md](lane-M-iphone-app.md) | the Zoe iPhone app: distribution, stack, push, calls and intercom, the Omi pendant, security, sequence, Android |

Author re-checks with negative controls are listed in the synthesis frontmatter. 
