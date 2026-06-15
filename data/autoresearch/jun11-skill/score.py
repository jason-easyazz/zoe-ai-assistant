#!/usr/bin/env python3
from pathlib import Path

asset = Path("skills/autoresearch-engineer/SKILL.md")
text = asset.read_text(encoding="utf-8")
lower = text.lower()
checks = [
    ("frontmatter", text.startswith("---\n") and "\n---\n# Auto Research Engineer" in text),
    ("karpathy_reference", "karpathy" in lower and "autoresearch" in lower),
    ("one_asset", "one editable asset" in lower or "asset allowlist" in lower),
    ("locked_score", "locked scoring" in lower or "locked scorer" in lower),
    ("human_program", "human-owned" in lower and ("program" in lower or "instructions" in lower)),
    ("fit_check", "fit check" in lower and "must-haves" in lower),
    ("objective_metric", "numeric metric" in lower or "single metric" in lower),
    ("fast_feedback", "minutes or hours" in lower),
    ("editable_access", "write access" in lower or "approved write" in lower),
    ("branch_rule", "fresh branch" in lower),
    ("results_log", "results.tsv" in lower and "run.log" in lower),
    ("baseline_first", "baseline" in lower and "first" in lower),
    ("one_hypothesis", "one hypothesis" in lower),
    ("keep_revert", "keep" in lower and "revert" in lower),
    ("crash_policy", "crash" in lower),
    ("no_goalposts", "moving goalposts" in lower or "goalposts" in lower),
    ("zoe_governance", "zoe" in lower and "governance" in lower),
    ("bounded_run", "max time" in lower and "max rounds" in lower),
    ("morning_report", "morning report" in lower),
    ("setup_interview", "setup interview" in lower),
]
score = sum(1 for _, ok in checks if ok)
print(f"score: {score}")
for name, ok in checks:
    print(f"{name}: {1 if ok else 0}")
