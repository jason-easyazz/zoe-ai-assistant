"""Auto Research Engineer status surface."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter


router = APIRouter(prefix="/api/autoresearch", tags=["autoresearch"])


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _run_root() -> Path:
    configured = Path(os.environ.get("ZOE_AUTORESEARCH_RUN_ROOT", "data/autoresearch"))
    return configured if configured.is_absolute() else _repo_root() / configured


def _latest_result(run_dir: Path) -> dict[str, Any]:
    results = run_dir / "results.tsv"
    if not results.exists():
        return {}
    try:
        with results.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
    except (OSError, csv.Error, UnicodeError):
        return {"results_readable": False}
    if not rows:
        return {"rounds": 0, "results_readable": True}
    latest = rows[-1]
    return {
        "rounds": len(rows),
        "results_readable": True,
        "latest_round": latest.get("round") or latest.get("round_id"),
        "latest_change": latest.get("change") or latest.get("hypothesis") or latest.get("description"),
        "latest_before": latest.get("before") or latest.get("score_before"),
        "latest_after": latest.get("after") or latest.get("score_after") or latest.get("score"),
        "latest_decision": latest.get("decision") or latest.get("kept") or latest.get("status"),
    }


def _run_summary(run_dir: Path) -> dict[str, Any] | None:
    try:
        stat = run_dir.stat()
    except OSError:
        return None
    summary: dict[str, Any] = {
        "id": run_dir.name,
        "path": str(run_dir.relative_to(_repo_root())) if run_dir.is_relative_to(_repo_root()) else str(run_dir),
        "updated_at": stat.st_mtime,
    }
    summary.update(_latest_result(run_dir))
    return summary


@router.get("/status")
async def autoresearch_status() -> dict[str, Any]:
    """Return the latest local autoresearch run state for integrations."""
    root = _run_root()
    if not root.exists():
        return {"ok": True, "surface": "autoresearch", "status": "idle", "run_count": 0, "latest": None, "runs": []}

    try:
        run_dirs = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return {"ok": False, "surface": "autoresearch", "status": "unavailable", "run_count": 0, "runs": []}

    summaries = (_run_summary(p) for p in run_dirs)
    runs = sorted((row for row in summaries if row is not None), key=lambda row: row["updated_at"], reverse=True)
    latest = runs[0] if runs else None
    return {
        "ok": True,
        "surface": "autoresearch",
        "status": "running_or_recorded" if latest else "idle",
        "run_count": len(runs),
        "latest": latest,
        "runs": runs[:10],
    }
