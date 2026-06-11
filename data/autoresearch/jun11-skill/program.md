# Auto Research Run: Auto Research Engineer Skill

Goal: improve the Multica Auto Research Engineer skill so it more faithfully implements Karpathy-style autoresearch while preserving Zoe governance.

Why: this is the first live Codex run of the workflow we just added to Multica.

Locked rules:
- Editable asset allowlist: skills/autoresearch-engineer/SKILL.md only.
- Locked scoring command: python3 data/autoresearch/jun11-skill/score.py
- Higher score is better.
- Do not edit this program file, score.py, populate_multica.py, tests, dependencies, or unrelated files during the loop.
- Use one hypothesis and one focused asset change per round.
- Keep only improvements; revert worse/equal changes unless the scorer rewards simplification.
- Run bounded in Codex: one baseline plus one variation for this demonstration, then report.

Stop condition: after one variation round or score reaches 20.
