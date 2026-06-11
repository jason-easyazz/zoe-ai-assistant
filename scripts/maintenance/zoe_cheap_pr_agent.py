#!/usr/bin/env python3
"""Cheap per-finding PR fixer for the Multica->Hermes Greptile loop.

Reads a GuardPacket JSON document on stdin (see
``services/zoe-data/greploop_guard.py``), asks a cheap LLM (DeepSeek via
OpenRouter by default) for the smallest possible edit that addresses ONE
Greptile finding in a single allowed file, applies it, runs the packet's
validation commands, then commits and pushes the fix to the PR branch.

Contract with greploop_guard._run_cheap_agent:
- We run with ``cwd`` set to the repo/worktree the guard operates in, on the
  PR branch. The guard captures HEAD before invoking us and diffs afterwards.
- Success means: focused fix committed + pushed, exit code 0, no "BLOCKED"
  token in our output. The guard then re-triggers Greptile.
- Anything we cannot safely do prints ``BLOCKED: <reason>`` and exits 0 so the
  guard classifies it as BLOCKED (never a crash, never a partial edit left
  behind). We never merge, force-push, amend, delete branches, or bypass hooks.

All output is secret-redacted before printing.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# --- secret redaction (mirrors greploop_guard.redact) ------------------------

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization|bearer)\s*[:=]\s*['\"]?[^'\"\s]+"
)
_BEARER_RE = re.compile(r"(?i)(authorization\s*:\s*bearer)\s+[^'\"\s]+")

_FORBIDDEN_GIT = ("--force", "--force-with-lease", "-f", "--amend", "--no-verify")
_DEFAULT_MODEL = os.environ.get("ZOE_CHEAP_PR_AGENT_MODEL", "deepseek/deepseek-chat-v3.1")
_OPENROUTER_URL = os.environ.get(
    "ZOE_CHEAP_PR_AGENT_API_URL", "https://openrouter.ai/api/v1/chat/completions"
)
_HERMES_ENV = Path.home() / ".hermes" / ".env"


def redact(text: str) -> str:
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)} <redacted>", text)
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)


def _blocked(reason: str) -> int:
    print(f"BLOCKED: {redact(str(reason))}")
    return 0


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)


def _load_openrouter_key() -> str | None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    # Fall back to the operator-local Hermes env file (never logged).
    try:
        for line in _HERMES_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"") or None
    except OSError:
        return None
    return None


def _number_lines(text: str) -> str:
    return "\n".join(f"{i + 1:>4}\t{ln}" for i, ln in enumerate(text.splitlines()))


def build_messages(*, file_path: str, file_text: str, issue_text: str, standard: str) -> list[dict]:
    """Build the chat messages for the fixer. Pure function for testability."""
    system = (
        "You are a precise code-review fixer. You address EXACTLY ONE review finding "
        "with the SMALLEST possible change to a single file. You do not refactor, "
        "reformat, rename, or touch anything the finding does not require.\n\n"
        "Respond with ONE JSON object and nothing else. Either:\n"
        '  {"edits": [{"old_string": "<exact text in file>", '
        '"new_string": "<replacement>"}], "summary": "<one line>"}\n'
        "where every old_string appears VERBATIM and EXACTLY ONCE in the current "
        "file (include enough surrounding context to be unique), or, if you cannot "
        "fix it safely within one file:\n"
        '  {"blocked": "<short reason>"}\n\n'
        "Keep the total changed lines small. Match the existing code style. Never "
        "add secrets, debug prints, or unrelated edits."
    )
    user = (
        f"Repository review standard (obey it):\n{standard}\n\n"
        f"File under review: {file_path}\n\n"
        f"Greptile finding to address:\n{issue_text}\n\n"
        f"Current file contents (with line numbers for reference only; do not "
        f"include line numbers in old_string/new_string):\n{_number_lines(file_text)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def call_llm(messages: list[dict], *, api_key: str, model: str = _DEFAULT_MODEL) -> str:
    import httpx

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "zoe-cheap-pr-agent",
    }
    with httpx.Client(timeout=120) as client:
        resp = client.post(_OPENROUTER_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"]


def parse_response(content: str) -> dict:
    """Extract the first JSON object from the model response."""
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(content[start : end + 1])
    raise ValueError("model did not return JSON")


def apply_edits(file_text: str, edits: list[dict]) -> str:
    """Apply exact-match edits. Each old_string must occur exactly once."""
    if not edits:
        raise ValueError("no edits returned")
    result = file_text
    for edit in edits:
        old = edit.get("old_string")
        new = edit.get("new_string")
        if old is None or new is None:
            raise ValueError("edit missing old_string/new_string")
        count = result.count(old)
        if count == 0:
            raise ValueError("old_string not found in file")
        if count > 1:
            raise ValueError("old_string is not unique in file")
        result = result.replace(old, new, 1)
    if result == file_text:
        raise ValueError("edits produced no change")
    return result


def _read_standard(repo: Path) -> str:
    try:
        text = (repo / ".greptile" / "rules.md").read_text(encoding="utf-8")
    except OSError:
        return "Smallest reviewable change; match existing patterns; no secrets; no junk files."
    # Pass the hygiene + checklist tail, which is what most findings concern.
    marker = "## Python And Test Hygiene"
    idx = text.find(marker)
    return text[idx:] if idx != -1 else text[-2000:]


def run(packet: dict, repo: Path) -> int:
    task_type = packet.get("task_type")
    if task_type != "FIX_GREPTILE_FINDING":
        return _blocked(f"unsupported task_type {task_type!r}")

    allowed = packet.get("allowed_files") or []
    if len(allowed) != 1:
        return _blocked(f"expected exactly one allowed file, got {len(allowed)}")
    rel = str(allowed[0]).strip()
    target = (repo / rel).resolve()
    if not str(target).startswith(str(repo.resolve())) or not target.is_file():
        return _blocked(f"allowed file is missing or outside repo: {rel}")

    issue_text = str(packet.get("issue_text") or "").strip()
    if not issue_text:
        return _blocked("finding has no issue_text")
    max_lines = int(packet.get("max_changed_lines") or 120)

    api_key = _load_openrouter_key()
    if not api_key:
        return _blocked("OPENROUTER_API_KEY not configured")

    original = target.read_text(encoding="utf-8")
    messages = build_messages(
        file_path=rel,
        file_text=original,
        issue_text=issue_text,
        standard=_read_standard(repo),
    )
    try:
        content = call_llm(messages, api_key=api_key)
        parsed = parse_response(content)
    except Exception as exc:  # network / parse / API failure
        return _blocked(f"llm call failed: {exc}")

    if parsed.get("blocked"):
        return _blocked(f"model declined: {parsed.get('blocked')}")

    try:
        updated = apply_edits(original, parsed.get("edits") or [])
    except Exception as exc:
        return _blocked(f"edit application failed: {exc}")

    target.write_text(updated, encoding="utf-8")

    def _revert() -> None:
        _git(["checkout", "--", rel], repo)

    # Syntax gate for Python.
    if rel.endswith(".py"):
        chk = subprocess.run(
            [sys.executable, "-m", "py_compile", rel], cwd=repo, text=True, capture_output=True
        )
        if chk.returncode != 0:
            _revert()
            return _blocked(f"py_compile failed: {chk.stderr.strip()[:400]}")

    # Validation commands from the packet.
    for command in packet.get("commands_to_run") or []:
        proc = subprocess.run(command, cwd=repo, shell=True, text=True, capture_output=True)
        if proc.returncode != 0:
            _revert()
            tail = (proc.stderr or proc.stdout or "").strip()[-400:]
            return _blocked(f"validation command failed ({command}): {tail}")

    # Self-check the diff shape before committing (guard re-checks too).
    changed = [
        p for p in _git(["diff", "--name-only"], repo).stdout.splitlines() if p.strip()
    ]
    if changed != [rel]:
        _revert()
        return _blocked(f"diff touched unexpected files: {changed}")
    numstat = _git(["diff", "--numstat"], repo).stdout.split()
    added = int(numstat[0]) if numstat and numstat[0].isdigit() else 0
    removed = int(numstat[1]) if len(numstat) > 1 and numstat[1].isdigit() else 0
    if added + removed > max_lines:
        _revert()
        return _blocked(f"change too large: {added + removed} > {max_lines} lines")

    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip()
    if not branch or branch in {"main", "master", "HEAD"}:
        _revert()
        return _blocked(f"refusing to commit on branch {branch!r}")

    summary = str(parsed.get("summary") or "address Greptile review finding").splitlines()[0][:72]
    add = _git(["add", "--", rel], repo)
    if add.returncode != 0:
        _revert()
        return _blocked(f"git add failed: {add.stderr.strip()[:200]}")
    commit = _git(["commit", "-m", f"fix(review): {summary}"], repo)
    if commit.returncode != 0:
        return _blocked(f"git commit failed: {commit.stderr.strip()[:200]}")
    push = _git(["push", "origin", f"HEAD:{branch}"], repo)
    if push.returncode != 0:
        return _blocked(f"git push failed: {push.stderr.strip()[:200]}")

    print(redact(f"APPLIED: {rel} — {summary} (+{added}/-{removed}), pushed to {branch}"))
    return 0


def main() -> int:
    raw = sys.stdin.read()
    try:
        packet = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _blocked(f"invalid packet JSON: {exc}")
    repo = Path(os.environ.get("ZOE_ASSISTANT_ROOT") or os.getcwd()).resolve()
    try:
        return run(packet, repo)
    except Exception as exc:  # never crash; BLOCKED is the safe terminal
        return _blocked(f"unexpected error: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
