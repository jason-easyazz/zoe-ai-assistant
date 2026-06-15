#!/usr/bin/env python3
"""Fail-closed local/offline Graphify refresh for Zoe.

This wrapper is the sync-capable companion to graphify_local_probe.py. It runs
Graphify against Zoe's local llama.cpp endpoint in a temporary repo snapshot and
syncs graphify-out back to the target repo only when the probe status is fully
accepted. Rejected runs leave the committed graph untouched.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from graphify_local_probe import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_GRAPHIFY_BIN,
    DEFAULT_MODEL,
    DEFAULT_ROOT,
    GraphifyLocalProbeConfig,
    cleanup_repo_workdir,
    run_probe,
    utc_now,
)

ERROR_MARKER = Path("graphify-out/.last_refresh_error")
REQUIRED_GRAPHIFY_FILES = ("graph.json", "GRAPH_REPORT.md")


def status_is_syncable(status: dict[str, object]) -> bool:
    if not status.get("accepted"):
        return False
    if status.get("blockers"):
        return False
    if not status.get("graph_json_exists"):
        return False
    if int(status.get("graph_json_bytes") or 0) <= 0:
        return False
    if not status.get("graph_report_exists"):
        return False
    if not status.get("workdir"):
        return False
    return True


def write_status(path: Path | None, status: dict[str, object]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_error_marker(root: Path, message: str) -> None:
    marker = root / ERROR_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{utc_now()} {message}\n", encoding="utf-8")


def remove_error_marker(root: Path) -> None:
    marker = root / ERROR_MARKER
    if marker.exists():
        marker.unlink()


def sync_graphify_out(*, root: Path, workdir: Path, dry_run: bool = False, rsync_timeout_sec: int = 120) -> dict[str, object]:
    source = workdir / "graphify-out"
    destination = root / "graphify-out"
    missing = [name for name in REQUIRED_GRAPHIFY_FILES if not (source / name).exists()]
    if missing:
        return {"ok": False, "error": "missing_required_graphify_files", "missing": missing}
    if dry_run:
        return {"ok": True, "dry_run": True, "source": str(source), "destination": str(destination)}
    destination.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            ["rsync", "-a", "--delete", f"{source}/", f"{destination}/"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=rsync_timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return {"ok": False, "error": "rsync_timed_out", "output": output}
    if completed.returncode != 0:
        return {"ok": False, "error": "rsync_failed", "output": completed.stdout}
    return {"ok": True, "source": str(source), "destination": str(destination)}


def cleanup_probe_workdir(root: Path, status: dict[str, object]) -> None:
    workdir_text = status.get("workdir")
    if not workdir_text:
        return
    workdir = Path(str(workdir_text))
    cleanup_repo_workdir(root, workdir)
    shutil.rmtree(workdir.parent, ignore_errors=True)


def run_local_refresh(config: GraphifyLocalProbeConfig, *, status_json: Path | None = None, dry_run: bool = False) -> dict[str, object]:
    if config.mode != "repo":
        raise ValueError("local refresh requires repo mode")
    if not config.cluster:
        raise ValueError("local refresh requires cluster=True so GRAPH_REPORT.md is generated")
    if not config.keep_workdir:
        raise ValueError("local refresh requires keep_workdir=True so accepted output can be synced")

    status = run_probe(config)
    status["refresh"] = {
        "attempted": True,
        "accepted_for_sync": False,
        "dry_run": dry_run,
        "synced": False,
    }
    try:
        if not status_is_syncable(status):
            blockers = [str(item) for item in (status.get("blockers") or ["not_syncable"])]
            status["refresh"]["blockers"] = blockers
            write_error_marker(config.root, f"local Graphify refresh rejected for {config.ref}: {','.join(blockers)}")
            return status

        workdir = Path(str(status["workdir"]))
        sync_result = sync_graphify_out(root=config.root, workdir=workdir, dry_run=dry_run)
        status["refresh"].update(sync_result)
        status["refresh"]["accepted_for_sync"] = bool(sync_result.get("ok"))
        status["refresh"]["synced"] = bool(sync_result.get("ok")) and not dry_run
        if sync_result.get("ok") and not dry_run:
            remove_error_marker(config.root)
        elif not sync_result.get("ok"):
            write_error_marker(config.root, f"local Graphify refresh sync failed for {config.ref}: {sync_result.get('error')}")
        return status
    finally:
        cleanup_probe_workdir(config.root, status)
        status["workdir"] = None
        write_status(status_json, status)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a fail-closed local/offline Zoe Graphify refresh.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--graphify-bin", type=Path, default=DEFAULT_GRAPHIFY_BIN)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ref", default="origin/main")
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument("--status-json", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = GraphifyLocalProbeConfig(
        root=args.root,
        graphify_bin=args.graphify_bin,
        base_url=args.base_url,
        model=args.model,
        ref=args.ref,
        mode="repo",
        timeout_sec=args.timeout_sec,
        cluster=True,
        keep_workdir=True,
    )
    status = run_local_refresh(config, status_json=args.status_json, dry_run=args.dry_run)
    print(json.dumps(status, indent=2, sort_keys=True))
    refresh = status.get("refresh") or {}
    return 0 if refresh.get("accepted_for_sync") and (args.dry_run or refresh.get("synced")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
