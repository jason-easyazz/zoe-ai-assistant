---
type: Reference
title: QA rounds as data, and the member-scoped lab household
description: How a conversational QA round is defined (a JSON file of scripted cases with stable ids), run in the lab (on a synthetic household of members, in CI) or handed to the private live harness, and read back (results JSON, a diffable markdown table, one scorecard line). What the lab household makes real and what it fakes.
tags: [qa, eval, lab, calendar, timers, alarms, lists, guest, zoe-data]
timestamp: 2026-11-14T20:00:00Z
---

# QA rounds as data

Until round 10a each QA round was driven by a private, ad-hoc script against the live service with synthetic DEMO users.
Two consequences: rounds could not be compared case by case, and every case that needs a real MEMBER scope (a repeating
event, a timer or alarm tied to a member and the panel, a private-until-shared list) was BLOCKED every round, because a demo
user is not a member.

## The pieces

| piece | where | what it is |
|---|---|---|
| lab household | `services/zoe-data/tests/lab_member.py` | two adults (`ari`, `bo`), a child (`cy`), an anonymous LAN `guest`, the registered `panel`; the real routers over HTTP on a temp SQLite store built from the real migrations. Its docstring lists exactly what is real and what is faked |
| blocked cases as tests | `services/zoe-data/tests/test_lab_household.py` (ci_safe) | repeating event create / edit one / edit all / delete one / delete all; a timer set by voice at the panel, seen, said once, cancelled; an alarm ringing at the panel and dismissed; a child kept out of an adult's private list until it is shared; the fridge rule; a guest reading nothing |
| a round | `services/zoe-data/tests/fixtures/qa_rounds/round-<id>.json` | cases with stable ids (`theme.subject.verb`); synthetic names and facts only |
| runner | `services/zoe-data/tests/qa_round.py` | `--lab <round>` (runs it on the household, under pytest so the store pins hold; `--out DIR` writes results) and `--live <round>` (prints the typed/voice turns as JSON lines for the private live harness; it never talks to a service) |
| CI | `services/zoe-data/tests/test_qa_round.py` (ci_safe) | the seeded round passes in the lab; negative controls: a deliberately wrong expected outcome fails the round, a `known_bug` that passes is an `unexpected_pass` (fails, like `xfail(strict=True)`) |
| scorecard row | `labs/felt-scorecard/scorecard.py` `qa_round` | the newest `round-*.json` in `--qa-results` (default `~/.zoe/qa-rounds`): passed/run per theme; `unmeasured` when there is no result |

## Statuses

`pass`, `fail`, `known_fail` (a case marked `known_bug` that still fails), `unexpected_pass` (a `known_bug` case that now
passes: update the round), `not_run` (`live_only`: needs the brain and the palace, so the lab cannot judge it). Results are
sorted by case id, so `diff round-10a-lab.md round-10b-lab.md` shows exactly what moved.

## Rules

- Never put a household utterance, name or fact in a round file. Rewrite the shape with invented people.
- A case the lab can run must not be `live_only`. A new behaviour that the lab cannot fake (memory, safety replies, the chat
  route) is `live_only` with an `expect_note` for the live judge.
- A real bug a case exposes is recorded with `known_bug` (one line), never by weakening the expectation; fix the product code
  in its own PR and remove the mark.
