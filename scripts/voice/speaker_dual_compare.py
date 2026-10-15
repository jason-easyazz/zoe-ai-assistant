#!/usr/bin/env python3
"""Compare the two speaker models from a ``ZOE_SPEAKER_MODEL=dual`` shadow week - AGGREGATES ONLY.

Reads the panel's ``speaker_shadow_metrics.jsonl`` (one row per turn: the acting model's decision at the top level, ResNet34's under
``secondary``; no audio, no vectors - docs/knowledge/biometric-retention-policy.md) after the operator has filled each row's ``truth``
during the W5 review (docs/architecture/panel-identity-plan.md): the member id who actually spoke, or ``none`` / ``guest`` / ``other`` for a
voice that is not enrolled. Prints, per model, how often the right member was accepted (TA), a member was missed or mistaken for another
(FR), and a non-member was accepted (FA), plus the ResNet34 added cost. It never prints a clip name, transcript or embedding.

    python3 scripts/voice/speaker_dual_compare.py ~/.zoe-voice/speaker_shadow_metrics.jsonl [--legacy-threshold 0.82]

Rows that were not SCORED (``source`` error / encoder_unavailable / no_profiles) are counted and excluded, as the W5 review says.
Pure stdlib; the logic is ``compare(rows)`` so a test can feed it planted rows.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys

SCORED = ("local", "server")
NOT_ENROLLED = ("none", "guest", "other", "stranger")


def _accept(model: str, row: dict, legacy_threshold: float) -> bool:
    if row.get("accept") is not None:
        return bool(row["accept"])
    score = row.get("score")
    return isinstance(score, (int, float)) and score >= legacy_threshold and bool(row.get("user_id"))


def _tally(into: dict, truth: str, user, accepted: bool) -> None:
    if truth.lower() in NOT_ENROLLED:
        into["impostor"] += 1
        into["fa"] += 1 if accepted else 0
    else:
        into["genuine"] += 1
        if accepted and user == truth:
            into["ta"] += 1
        else:
            into["fr"] += 1
            into["misid"] += 1 if accepted else 0


def compare(rows: list, legacy_threshold: float = 0.82) -> dict:
    """Per-model counts over the labelled, scored rows. Keys: models -> dict(genuine, impostor, ta, fr, misid, fa), plus counts and ms."""
    out = dict(rows=len(rows), labelled=0, both=0, unscored=0, models=dict(), added_ms=[])
    for r in rows:
        truth = r.get("truth")
        sec = r.get("secondary") or None
        if sec and isinstance(sec.get("ms"), (int, float)):
            out["added_ms"].append(float(sec["ms"]))
        if truth in (None, ""):
            continue
        out["labelled"] += 1
        views = [(r.get("model_id") or "resemblyzer", r)]
        if sec:
            views.append((sec.get("model_id") or "secondary", sec))
            out["both"] += 1
        for model, view in views:
            if view.get("source") not in SCORED:
                out["unscored"] += 1
                continue
            t = out["models"].setdefault(model, dict(genuine=0, impostor=0, ta=0, fr=0, misid=0, fa=0))
            _tally(t, str(truth), view.get("user_id"), _accept(model, view, legacy_threshold))
    return out


def render(res: dict) -> str:
    lines = ["rows %d, labelled %d, labelled with both models %d, not scored (excluded) %d"
             % (res["rows"], res["labelled"], res["both"], res["unscored"])]
    for model, t in sorted(res["models"].items()):
        g, i = max(t["genuine"], 1), max(t["impostor"], 1)
        lines.append("%s: genuine %d -> TA %.1f%% FR %.1f%% (mistaken for another member %d) | non-members %d -> FA %.1f%%"
                     % (model, t["genuine"], 100.0 * t["ta"] / g, 100.0 * t["fr"] / g, t["misid"], t["impostor"], 100.0 * t["fa"] / i))
    ms = sorted(res["added_ms"])
    if ms:
        lines.append("ResNet34 added ms: p50 %.0f p95 %.0f over %d turns" % (statistics.median(ms), ms[max(0, int(0.95 * len(ms)) - 1)], len(ms)))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("metrics", help="speaker_shadow_metrics.jsonl")
    ap.add_argument("--legacy-threshold", type=float, default=0.82, help="the server's ZOE_SPEAKER_ID_THRESHOLD for Resemblyzer")
    a = ap.parse_args()
    rows = []
    with open(a.metrics, encoding="utf-8") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue              # a torn tail line is skippable, as in the daemon's own writer
    print(render(compare(rows, a.legacy_threshold)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
