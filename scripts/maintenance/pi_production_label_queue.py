#!/usr/bin/env python3
"""Build a review queue from real Pi hybrid production evidence.

The queue is read-only. It helps an operator label sanitized production records
through the existing production-label sidecar without dumping raw logs or
promoting routes automatically.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

from pi_intent_evidence import (  # noqa: E402
    apply_pi_hybrid_production_labels,
    build_pi_hybrid_production_label_queue,
    load_pi_hybrid_production_labels,
    load_pi_hybrid_production_records,
)

DEFAULT_ENV_FILES = (
    ROOT / ".env",
    ROOT / "services" / "zoe-data" / ".env",
    Path("/home/zoe/assistant/.env"),
    Path("/home/zoe/assistant/services/zoe-data/.env"),
)
DEFAULT_PRODUCTION_EVIDENCE_PATH = "~/.zoe/data/pi-hybrid-production-evidence.jsonl"
DEFAULT_PRODUCTION_LABELS_PATH = "~/.zoe/data/pi-hybrid-production-labels.jsonl"


def load_zoe_env(env_files: Iterable[str | Path] = DEFAULT_ENV_FILES) -> dict[str, str]:
    values: dict[str, str] = {}
    for env_file in env_files:
        path = Path(env_file).expanduser()
        if path.exists():
            values.update(_parse_env_file(path))
    values.update(os.environ)
    return values


def _parse_env_file(path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        if not key or key.startswith("#"):
            continue
        try:
            parts = shlex.split(raw_value, comments=True, posix=True)
        except ValueError:
            parts = [raw_value.strip().strip('"').strip("'")]
        parsed[key] = parts[0] if parts else ""
    return parsed


def _jsonl(rows: Sequence[Mapping[str, object]]) -> str:
    return "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a read-only Pi hybrid production labeling queue")
    parser.add_argument("--evidence-path", help="Override Pi hybrid production evidence JSONL path")
    parser.add_argument("--labels-path", help="Override Pi hybrid production labels JSONL path")
    parser.add_argument("--env-file", action="append", default=None, help="Additional env file to load")
    parser.add_argument("--group", action="append", default=None, help="Intent group filter; may be repeated or comma-separated")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--include-labeled", action="store_true")
    parser.add_argument("--include-rejected", action="store_true")
    parser.add_argument("--format", choices=("json", "jsonl"), default="json")
    parser.add_argument("--output", "-o", help="Output path; defaults to stdout")
    args = parser.parse_args(argv)

    env_files = [*DEFAULT_ENV_FILES, *(args.env_file or [])]
    env = load_zoe_env(env_files)
    evidence_path = args.evidence_path or env.get("ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH") or DEFAULT_PRODUCTION_EVIDENCE_PATH
    labels_path = args.labels_path or env.get("ZOE_PI_HYBRID_PRODUCTION_LABELS_PATH") or DEFAULT_PRODUCTION_LABELS_PATH
    records = load_pi_hybrid_production_records(evidence_path, limit=max(args.limit * 10, 500))
    labels = load_pi_hybrid_production_labels(labels_path)
    labeled_records = apply_pi_hybrid_production_labels(records, labels)
    payload = build_pi_hybrid_production_label_queue(
        labeled_records,
        groups=args.group,
        include_labeled=args.include_labeled,
        include_rejected=args.include_rejected,
        limit=args.limit,
    )
    payload["summary"]["path"] = str(Path(evidence_path).expanduser())
    payload["summary"]["labels_path"] = str(Path(labels_path).expanduser())
    text = _jsonl(payload["queue"]) if args.format == "jsonl" else json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
