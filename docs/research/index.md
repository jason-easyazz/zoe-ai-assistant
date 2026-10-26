---
type: index
title: Research records - status index
description: Every record under docs/research with a status (open | absorbed | retired), the absorb-then-retire rule at 30 days and the cap of 25 open records.
---
# Research records

Research records are **evidence**, not plans. The thing to do next lives in [`../ROADMAP.md`](../ROADMAP.md); a record is linked from the roadmap item it feeds.
This index gives every record one status.

## Status and the rules

| status | meaning |
|---|---|
| `open` | the record still drives unfinished work or an undecided owner question |
| `absorbed` | its findings have landed in code, a contract, an ADR, `docs/knowledge/` or `open-problems.md`; the record stays only until its retire date |
| `retired` | the file has been deleted (git history keeps it); retired records are removed from the table below and listed under "Retired" |

1. **Absorb, then retire at 30 days.** When a record's findings are absorbed, change its row to `absorbed` and write the date. Thirty days after that date, **delete the file** and move
   its row to "Retired". A record is never absorbed into a *new* parallel plan; the destination is code, a contract, `docs/knowledge/` or a ROADMAP item.
2. **At most 25 `open` records.** Opening a 26th means absorbing or retiring one first (`tests/unit/test_research_index.py` fails otherwise). New research starts `open` the day it merges.
3. **Every file in this directory appears here** exactly once (the same test), with a status. A new record without a row fails CI.
4. The architecture deep dive's evidence lanes (`architecture-deep-dive-2026-10-18/`) belong to that record and share its status.

## Records

| record | added | status | note |
|---|---|---|---|
| [ARCHITECTURE_DIAGRAM.md](ARCHITECTURE_DIAGRAM.md) | 2025-11-18 | absorbed 2026-10-18 | November 2025 memory-hallucination study; long superseded; retire by 2026-11-17 |
| [EXECUTIVE_SUMMARY.md](EXECUTIVE_SUMMARY.md) | 2025-11-18 | absorbed 2026-10-18 | November 2025 memory-hallucination study; long superseded; retire by 2026-11-17 |
| [IMPLEMENTATION_GUIDE_P0.md](IMPLEMENTATION_GUIDE_P0.md) | 2025-11-18 | absorbed 2026-10-18 | November 2025 memory-hallucination study; long superseded; retire by 2026-11-17 |
| [MEMORY_HALLUCINATION_ANALYSIS.md](MEMORY_HALLUCINATION_ANALYSIS.md) | 2025-11-18 | absorbed 2026-10-18 | November 2025 memory-hallucination study; long superseded; retire by 2026-11-17 |
| [README.md](README.md) | 2025-11-18 | absorbed 2026-10-18 | index of the November 2025 study only; replaced by this index; retire by 2026-11-17 |
| [agent-sessions-off-box-2026-10-04.md](agent-sessions-off-box-2026-10-04.md) | 2026-10-04 | absorbed 2026-10-18 | engineering-off-box.md in knowledge; retire by 2026-11-17 |
| [architecture-deep-dive-2026-10-18.md](architecture-deep-dive-2026-10-18.md) | 2026-10-18 | open | task list A-D still being worked; ROADMAP derives from it |
| [arousal-detection-licence-scope-2026-10-04.md](arousal-detection-licence-scope-2026-10-04.md) | 2026-10-04 | open | licence decision before any build |
| [tone-of-voice-deep-dive-2026-10-26.md](tone-of-voice-deep-dive-2026-10-26.md) | 2026-10-26 | open | tone-of-voice options and phased plan; owner decisions pending |
| [omi-data-to-teach-zoe-2026-10-26.md](omi-data-to-teach-zoe-2026-10-26.md) | 2026-10-26 | open | pendant data as a teacher: ranked use cases, night-window budget, AU recording law (WA worked example), phased plan; sister of the tone record; owner decisions pending |
| [bakeoff-ram-latency-optimisation-2026-10-06.md](bakeoff-ram-latency-optimisation-2026-10-06.md) | 2026-10-07 | absorbed 2026-10-18 | bake-off closed; retire by 2026-11-17 |
| [bakeoff-run-20261006-0935.md](bakeoff-run-20261006-0935.md) | 2026-10-06 | absorbed 2026-10-18 | run record; verdict in 10-08 decision; retire by 2026-11-17 |
| [bakeoff-run-20261008-1805.md](bakeoff-run-20261008-1805.md) | 2026-10-08 | absorbed 2026-10-18 | run record; verdict in 10-08 decision; retire by 2026-11-17 |
| [bakeoff-setup-verification-2026-10-06.md](bakeoff-setup-verification-2026-10-06.md) | 2026-10-06 | absorbed 2026-10-18 | instrument check; done; retire by 2026-11-17 |
| [barge-in-duck-decide-resume-2026-10-04.md](barge-in-duck-decide-resume-2026-10-04.md) | 2026-10-04 | open | Pi lab outstanding |
| [best-ideas-register-2026-10-09.md](best-ideas-register-2026-10-09.md) | 2026-10-09 | open | ranked borrow list, items still being picked |
| [brain-extraction-stack-best-practice-2026-10-05.md](brain-extraction-stack-best-practice-2026-10-05.md) | 2026-10-05 | absorbed 2026-10-18 | findings fixed or in open-problems; retire by 2026-11-17 |
| [common-questions-ha-intents-2026-10.md](common-questions-ha-intents-2026-10.md) | 2026-10-14 | open | everyday-command follow-ups open |
| [communication-preferences-2026-10.md](communication-preferences-2026-10.md) | 2026-10-16 | open | design only |
| [companion-field-vs-samantha-2026-10-03.md](companion-field-vs-samantha-2026-10-03.md) | 2026-10-03 | absorbed 2026-10-18 | folded into beat-the-bar and ROADMAP; retire by 2026-11-17 |
| [credential-vault-2026-10.md](credential-vault-2026-10.md) | 2026-10-18 | open | design only; owner decisions pending |
| [failure-fed-proposals-2026-10-04.md](failure-fed-proposals-2026-10-04.md) | 2026-10-04 | absorbed 2026-10-26 | built as the struggle detector phases 1-3 (docs/architecture/struggle-detector.md; phase 3 flag-dark); retire by 2026-11-25 |
| [flue-and-agent-runtimes-2026-10-03.md](flue-and-agent-runtimes-2026-10-03.md) | 2026-10-04 | absorbed 2026-10-18 | Flue 2.x cutover done; retire by 2026-11-17 |
| [identity-presence-scorecard-2026-10.md](identity-presence-scorecard-2026-10.md) | 2026-10-13 | open | score-card engine not built |
| [incomplete-turn-marker-2026-10-04.md](incomplete-turn-marker-2026-10-04.md) | 2026-10-04 | absorbed 2026-10-18 | built (incomplete-turn marker); retire by 2026-11-17 |
| [inference-speech-stack-2026-10-03.md](inference-speech-stack-2026-10-03.md) | 2026-10-04 | open | operator decision list still pending |
| [infra-data-config-2026-10-03.md](infra-data-config-2026-10-03.md) | 2026-10-04 | absorbed 2026-10-18 | findings fixed or in open-problems; retire by 2026-11-17 |
| [integration-stack-docs-vs-zoe-2026-10-05.md](integration-stack-docs-vs-zoe-2026-10-05.md) | 2026-10-05 | absorbed 2026-10-18 | findings fixed or in open-problems; retire by 2026-11-17 |
| [memory-arm-hm-hindsight-mempalace-2026-10-06.md](memory-arm-hm-hindsight-mempalace-2026-10-06.md) | 2026-10-06 | absorbed 2026-10-18 | bake-off closed; retire by 2026-11-17 |
| [memory-bakeoff-decision-2026-10-08.md](memory-bakeoff-decision-2026-10-08.md) | 2026-10-08 | absorbed 2026-10-18 | decision KEEP_Z0 in docs/CANONICAL.md and memory; retire by 2026-11-17 |
| [memory-fidelity-audit-2026-10-05.md](memory-fidelity-audit-2026-10-05.md) | 2026-10-05 | open | follow-ups owed in the audit |
| [memory-system-decision-2026-10-05.md](memory-system-decision-2026-10-05.md) | 2026-10-05 | absorbed 2026-10-18 | decision recorded; superseded by 10-08 verdict; retire by 2026-11-17 |
| [mempalace-agent-arm-2026-10-07.md](mempalace-agent-arm-2026-10-07.md) | 2026-10-08 | absorbed 2026-10-18 | bake-off closed; retire by 2026-11-17 |
| [mempalace-chroma-best-practice-2026-10-05.md](mempalace-chroma-best-practice-2026-10-05.md) | 2026-10-05 | absorbed 2026-10-18 | findings fixed or in open-problems; retire by 2026-11-17 |
| [mempalace-deep-dive-2026-10-06.md](mempalace-deep-dive-2026-10-06.md) | 2026-10-06 | absorbed 2026-10-18 | bake-off closed; retire by 2026-11-17 |
| [night-mind-2026-10-09.md](night-mind-2026-10-09.md) | 2026-10-09 | absorbed 2026-10-18 | knowledge/night-mind.md; retire by 2026-11-17 |
| [omi-gap-audit-2026-10.md](omi-gap-audit-2026-10.md) | 2026-10-17 | open | findings still being closed (P0/P1 follow-ups) |
| [omi-press-to-record-firmware-2026-10.md](omi-press-to-record-firmware-2026-10.md) | 2026-10-13 | open | firmware path undecided |
| [panel-anywhere-2026-10.md](panel-anywhere-2026-10.md) | 2026-10-17 | open | design only; relay lab flag-dark |
| [panel-remote-access-2026-10.md](panel-remote-access-2026-10.md) | 2026-10-16 | open | design only; away P1 landed, link home not built |
| [people-graph-temporal-model-2026-10-05.md](people-graph-temporal-model-2026-10-05.md) | 2026-10-05 | absorbed 2026-10-18 | ADR-relationship-memory; retire by 2026-11-17 |
| [person-likeness-2026-10-09.md](person-likeness-2026-10-09.md) | 2026-10-09 | open | restraint/person half partly shadow |
| [personality-identity-layer-2026-10-04.md](personality-identity-layer-2026-10-04.md) | 2026-10-04 | absorbed 2026-10-18 | persona layer phase 0 built; retire by 2026-11-17 |
| [presence-without-sensors-2026-10-04.md](presence-without-sensors-2026-10-04.md) | 2026-10-04 | open | not built |
| [pull-not-push-inbox-2026-10-04.md](pull-not-push-inbox-2026-10-04.md) | 2026-10-04 | absorbed 2026-10-18 | proactive/pull.py and lines.py contract bullets; retire by 2026-11-17 |
| [samantha-context-engineering-2026-09-29.md](samantha-context-engineering-2026-09-29.md) | 2026-09-29 | absorbed 2026-10-18 | context manager built and live; retire by 2026-11-17 |
| [samantha-flags-ab-2026-10-06.md](samantha-flags-ab-2026-10-06.md) | 2026-10-06 | absorbed 2026-10-18 | A/B done; flags flipped; retire by 2026-11-17 |
| [samantha-gap-register-2026-10-09.md](samantha-gap-register-2026-10-09.md) | 2026-10-09 | open | ranked gaps still drive the memory work |
| [samantha-mind-layer-2026-10-07.md](samantha-mind-layer-2026-10-07.md) | 2026-10-08 | open | mind-layer items partly open |
| [scout-companion-voice-persona-2026-10-07.md](scout-companion-voice-persona-2026-10-07.md) | 2026-10-08 | absorbed 2026-10-18 | folded into what-gets-us-closer / best-ideas; retire by 2026-11-17 |
| [scout-memory-cognition-2026-10-07.md](scout-memory-cognition-2026-10-07.md) | 2026-10-08 | absorbed 2026-10-18 | folded into what-gets-us-closer / best-ideas; retire by 2026-11-17 |
| [scout-setups-inference-hardware-2026-10-07.md](scout-setups-inference-hardware-2026-10-07.md) | 2026-10-08 | absorbed 2026-10-18 | folded into what-gets-us-closer / best-ideas; retire by 2026-11-17 |
| [self-building-skills-2026-10-04.md](self-building-skills-2026-10-04.md) | 2026-10-04 | open | not built |
| [speaker-gate-rebuild-2026-10-04.md](speaker-gate-rebuild-2026-10-04.md) | 2026-10-04 | absorbed 2026-10-18 | speaker gate built; retire by 2026-11-17 |
| [speaker-gate-step1-results-2026-10-05.md](speaker-gate-step1-results-2026-10-05.md) | 2026-10-04 | absorbed 2026-10-18 | speaker gate built; retire by 2026-11-17 |
| [speaker-model-bakeoff-2026-10.md](speaker-model-bakeoff-2026-10.md) | 2026-10-12 | absorbed 2026-10-18 | ResNet34 dual-run built (docs/knowledge); retire by 2026-11-17 |
| [speaker-resnet34-optimisation-2026-10.md](speaker-resnet34-optimisation-2026-10.md) | 2026-10-13 | absorbed 2026-10-18 | built into zoe_speaker_resnet34.py; retire by 2026-11-17 |
| [speech-stack-docs-vs-zoe-2026-10-05.md](speech-stack-docs-vs-zoe-2026-10-05.md) | 2026-10-05 | absorbed 2026-10-18 | findings fixed or in open-problems; retire by 2026-11-17 |
| [structural-floors-2026-10-09.md](structural-floors-2026-10-09.md) | 2026-10-09 | open | enforce flip pending (shadow) |
| [telegram-calls-voice-photos-2026-10-04.md](telegram-calls-voice-photos-2026-10-04.md) | 2026-10-04 | open | not built |
| [tools-best-practice-program-2026-10-05.md](tools-best-practice-program-2026-10-05.md) | 2026-10-05 | absorbed 2026-10-18 | program items landed or in open-problems; retire by 2026-11-17 |
| [what-gets-us-closer-2026-10-07.md](what-gets-us-closer-2026-10-07.md) | 2026-10-08 | absorbed 2026-10-18 | folded into best-ideas register; retire by 2026-11-17 |
| [zoe-abilities-connectors-2026-10.md](zoe-abilities-connectors-2026-10.md) | 2026-10-16 | open | research done, nothing built |
| [zoe-action-agent-2026-10.md](zoe-action-agent-2026-10.md) | 2026-10-12 | open | research only; owner answers pending |
| [zoe-context-audit-2026-09-29.md](zoe-context-audit-2026-09-29.md) | 2026-09-29 | absorbed 2026-10-18 | audit PRs merged; retire by 2026-11-17 |
| [zoe-memory-bench-design-2026-10-05.md](zoe-memory-bench-design-2026-10-05.md) | 2026-10-05 | absorbed 2026-10-18 | bench built (zmb); retire by 2026-11-17 |

## Retired

None yet. Every absorbed record was marked absorbed on 2026-10-18 (the first pass of this index), so none is due before 2026-11-17; the five November 2025 files (`README.md` and the four memory-hallucination studies) are the oldest and the first to go.
