#!/usr/bin/env python3
"""Probe Zoe's local/offline Graphify extraction path.

This command is intentionally observe-only: it runs Graphify in a temporary
fixture or git worktree snapshot, parses the extraction log, and reports whether
that run is acceptable as evidence. It never syncs generated graph output back to
Zoe's committed graph.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

DEFAULT_ROOT = Path(os.environ.get("ZOE_ASSISTANT_ROOT", "/home/zoe/assistant"))
GRAPHIFY_BIN_CANDIDATES = (
    Path("/home/zoe/.local/share/uv/tools/graphifyy/bin/graphify"),
    Path("/home/zoe/.local/share/uv/tools/graphify/bin/graphify"),
)


def default_graphify_bin() -> Path:
    configured = os.environ.get("GRAPHIFY_BIN")
    if configured:
        return Path(configured)
    for candidate in GRAPHIFY_BIN_CANDIDATES:
        if candidate.exists():
            return candidate
    return GRAPHIFY_BIN_CANDIDATES[0]


DEFAULT_GRAPHIFY_BIN = default_graphify_bin()
DEFAULT_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "gemma-4-E2B-it-Q4_K_M.gguf")
STATUS_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class GraphifyLocalProbeConfig:
    root: Path = DEFAULT_ROOT
    graphify_bin: Path = DEFAULT_GRAPHIFY_BIN
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    ref: str = "origin/main"
    mode: str = "smoke"
    timeout_sec: int = 180
    cluster: bool = False
    keep_workdir: bool = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def graphify_env(config: GraphifyLocalProbeConfig) -> dict[str, str]:
    env = os.environ.copy()
    env["OLLAMA_BASE_URL"] = config.base_url
    env["OLLAMA_MODEL"] = config.model
    env.setdefault("OLLAMA_API_KEY", "local")
    # This probe must stay offline/local even when the repo .env contains cloud keys.
    env.pop("OPENAI_API_KEY", None)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("GEMINI_API_KEY", None)
    return env


def count_context_split_events(log_text: str) -> int:
    return sum(
        1
        for line in log_text.splitlines()
        if re.search(r"exceeded context|splitting in half", line, flags=re.IGNORECASE)
    )


def parse_graphify_log(log_text: str) -> dict[str, object]:
    found_match = re.search(r"found\s+(\d+)\s+code,\s+(\d+)\s+docs", log_text)
    wrote_match = re.search(r"wrote\s+\S*graph\.json\s+(?:-|\u2014)\s+(\d+)\s+nodes,\s+(\d+)\s+edges", log_text)
    built_match = re.search(r"Built from commit:\s*`?([0-9a-fA-F]{7,40})`?", log_text)
    return {
        "code_files": int(found_match.group(1)) if found_match else None,
        "doc_files": int(found_match.group(2)) if found_match else None,
        "nodes": int(wrote_match.group(1)) if wrote_match else None,
        "edges": int(wrote_match.group(2)) if wrote_match else None,
        "built_from_commit": built_match.group(1) if built_match else None,
        "invalid_json_chunks": len(re.findall(r"invalid JSON", log_text, flags=re.IGNORECASE)),
        "context_splits": count_context_split_events(log_text),
        "truncated_chunks": len(re.findall(r"truncated", log_text, flags=re.IGNORECASE)),
        "rate_limit_events": len(re.findall(r"rate.?limit", log_text, flags=re.IGNORECASE)),
        "insufficient_quota_events": len(re.findall(r"insufficient_quota", log_text, flags=re.IGNORECASE)),
        "ast_completed": "AST extraction" in log_text and "100%" in log_text,
    }


def classify_probe_result(
    *,
    exit_code: int,
    timed_out: bool,
    log_text: str,
    graph_json_exists: bool,
    graph_json_bytes: int,
    graph_report_exists: bool,
    cluster: bool,
) -> dict[str, object]:
    metrics = parse_graphify_log(log_text)
    blockers: list[str] = []
    warnings: list[str] = []

    if timed_out:
        blockers.append("graphify_timed_out")
    if exit_code != 0:
        blockers.append("graphify_exit_nonzero")
    if not graph_json_exists:
        blockers.append("graph_json_missing")
    elif graph_json_bytes <= 0:
        blockers.append("graph_json_empty")
    if cluster and not graph_report_exists:
        blockers.append("graph_report_missing")
    if metrics["invalid_json_chunks"]:
        blockers.append("invalid_json_chunks")
    if metrics["truncated_chunks"]:
        blockers.append("truncated_chunks")
    if metrics["insufficient_quota_events"]:
        blockers.append("cloud_quota_error")
    if metrics["context_splits"]:
        warnings.append("context_splits_observed")
    if metrics["rate_limit_events"]:
        warnings.append("rate_limit_events_observed")

    return {
        "accepted": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "metrics": metrics,
        "graph_json_exists": graph_json_exists,
        "graph_json_bytes": graph_json_bytes,
        "graph_report_exists": graph_report_exists,
    }


def _text_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_command(command: Sequence[str], *, cwd: Path, env: dict[str, str], timeout_sec: int) -> tuple[int, bool, str]:
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        stdout, _ = process.communicate(timeout=timeout_sec)
        return process.returncode or 0, False, stdout or ""
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            stdout, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                stdout, _ = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                stdout = b""
        output = "".join(_text_output(part) for part in (exc.stdout, stdout))
        return 124, True, output


def prepare_smoke_workdir(parent: Path) -> Path:
    workdir = parent / "graphify-local-smoke"
    workdir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=workdir, check=True)
    (workdir / "memory_contract.py").write_text(
        "def retain_memory(user_id: str, scope: str, evidence_refs: list[str]) -> dict:\n"
        "    if not user_id:\n"
        "        raise ValueError('user_id required')\n"
        "    if not scope:\n"
        "        raise ValueError('scope required')\n"
        "    return {'user_id': user_id, 'scope': scope, 'evidence_refs': evidence_refs}\n",
        encoding="utf-8",
    )
    return workdir


def prepare_repo_workdir(config: GraphifyLocalProbeConfig, parent: Path) -> Path:
    workdir = parent / "graphify-local-repo"
    subprocess.run(["git", "fetch", "--quiet", "origin", "main"], cwd=config.root, check=True)
    subprocess.run(["git", "worktree", "add", "--detach", str(workdir), config.ref], cwd=config.root, check=True)
    return workdir


def cleanup_repo_workdir(root: Path, workdir: Path) -> None:
    subprocess.run(["git", "worktree", "remove", "--force", str(workdir)], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    subprocess.run(["git", "worktree", "prune"], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def run_probe(config: GraphifyLocalProbeConfig) -> dict[str, object]:
    started_at = utc_now()
    temp_parent = Path(tempfile.mkdtemp(prefix="zoe-graphify-local-probe."))
    workdir: Path | None = None
    log_text = ""
    exit_code = 1
    timed_out = False
    try:
        if config.mode == "smoke":
            workdir = prepare_smoke_workdir(temp_parent)
        elif config.mode == "repo":
            workdir = prepare_repo_workdir(config, temp_parent)
        else:
            raise ValueError(f"unsupported probe mode: {config.mode}")

        command = [str(config.graphify_bin), "extract", ".", "--backend", "ollama", "--model", config.model]
        if not config.cluster:
            command.append("--no-cluster")
        exit_code, timed_out, log_text = run_command(
            command,
            cwd=workdir,
            env=graphify_env(config),
            timeout_sec=config.timeout_sec,
        )
        if config.cluster and exit_code == 0 and not timed_out:
            cluster_code, cluster_timed_out, cluster_log = run_command(
                [str(config.graphify_bin), "cluster-only", ".", "--no-viz"],
                cwd=workdir,
                env=graphify_env(config),
                timeout_sec=config.timeout_sec,
            )
            log_text = f"{log_text}\n{cluster_log}"
            if cluster_code != 0 or cluster_timed_out:
                exit_code = cluster_code
                timed_out = timed_out or cluster_timed_out

        graph_json = workdir / "graphify-out" / "graph.json"
        graph_report = workdir / "graphify-out" / "GRAPH_REPORT.md"
        classification = classify_probe_result(
            exit_code=exit_code,
            timed_out=timed_out,
            log_text=log_text,
            graph_json_exists=graph_json.exists(),
            graph_json_bytes=graph_json.stat().st_size if graph_json.exists() else 0,
            graph_report_exists=graph_report.exists(),
            cluster=config.cluster,
        )
        return {
            "schema_version": STATUS_SCHEMA_VERSION,
            "probe": "graphify_local_offline",
            "started_at": started_at,
            "ended_at": utc_now(),
            "mode": config.mode,
            "ref": config.ref,
            "model": config.model,
            "base_url": config.base_url,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "workdir": str(workdir) if config.keep_workdir and workdir else None,
            **classification,
        }
    except Exception as exc:  # noqa: BLE001 - operator probe must preserve JSON output.
        return {
            "schema_version": STATUS_SCHEMA_VERSION,
            "probe": "graphify_local_offline",
            "started_at": started_at,
            "ended_at": utc_now(),
            "mode": config.mode,
            "ref": config.ref,
            "model": config.model,
            "base_url": config.base_url,
            "accepted": False,
            "blockers": ["probe_error"],
            "warnings": [],
            "error": str(exc),
            "exit_code": exit_code,
            "timed_out": timed_out,
            "workdir": str(workdir) if config.keep_workdir and workdir else None,
        }
    finally:
        if not config.keep_workdir:
            if config.mode == "repo" and workdir is not None:
                cleanup_repo_workdir(config.root, workdir)
            shutil.rmtree(temp_parent, ignore_errors=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe Zoe's local/offline Graphify backend.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--graphify-bin", type=Path, default=DEFAULT_GRAPHIFY_BIN)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ref", default="origin/main")
    parser.add_argument("--mode", choices=("smoke", "repo"), default="smoke")
    parser.add_argument("--timeout-sec", type=int, default=180)
    parser.add_argument("--cluster", action="store_true")
    parser.add_argument("--keep-workdir", action="store_true")
    parser.add_argument("--status-json", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = GraphifyLocalProbeConfig(
        root=args.root,
        graphify_bin=args.graphify_bin,
        base_url=args.base_url,
        model=args.model,
        ref=args.ref,
        mode=args.mode,
        timeout_sec=args.timeout_sec,
        cluster=args.cluster,
        keep_workdir=args.keep_workdir,
    )
    status = run_probe(config)
    text = json.dumps(status, indent=2, sort_keys=True)
    if args.status_json:
        args.status_json.parent.mkdir(parents=True, exist_ok=True)
        args.status_json.write_text(f"{text}\n", encoding="utf-8")
    print(text)
    return 0 if status.get("accepted") else 1


if __name__ == "__main__":
    raise SystemExit(main())
