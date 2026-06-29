"""Async client for Greptile's HTTP MCP endpoint."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

MCP_URL = os.environ.get("GREPTILE_MCP_URL", "https://api.greptile.com/mcp")
ENV_FILE = Path(os.environ.get("GREPTILE_ENV_FILE", Path.home() / ".config/zoe/greptile.env"))
DEFAULT_REMOTE = "github"
DEFAULT_BRANCH = "main"
DEFAULT_REPO = os.environ.get("ZOE_GITHUB_REPO", "jason-easyazz/zoe-ai-assistant")
MCP_RESPONSE_MAX_BYTES = 2 * 1024 * 1024


class GreptileAuthError(RuntimeError):
    """Raised when Greptile credentials are unavailable."""


_CONFIDENCE_RE = re.compile(r"(?:Confidence\s+Score|confidence)\s*:?\s*([0-5])\s*/\s*5", re.I)


def parse_confidence_score(*sources: Any) -> int | None:
    """Return the first Greptile confidence score found in nested text-like data."""
    stack = list(sources)
    while stack:
        item = stack.pop(0)
        if item is None:
            continue
        if isinstance(item, str):
            match = _CONFIDENCE_RE.search(item)
            if match:
                return int(match.group(1))
            continue
        if isinstance(item, dict):
            direct = item.get("confidenceScore") or item.get("confidence_score")
            if isinstance(direct, int) and 0 <= direct <= 5:
                return direct
            stack.extend(item.values())
            continue
        if isinstance(item, list):
            stack.extend(item)
    return None


def normalize_pr_comment(comment: dict[str, Any]) -> dict[str, Any]:
    """Normalize Greptile/GitHub comment fields for guard packets."""
    body = comment.get("body") or comment.get("text") or comment.get("content") or ""
    path = comment.get("filePath") or comment.get("path") or comment.get("file") or ""
    line = (
        comment.get("line")
        or comment.get("lineStart")
        or comment.get("startLine")
        or comment.get("originalLine")
    )
    return {
        "id": str(comment.get("id") or comment.get("commentId") or comment.get("nodeId") or ""),
        "file_path": path,
        "line": line,
        "line_end": comment.get("lineEnd") or comment.get("endLine") or comment.get("originalEndLine"),
        "body": body,
        "suggested_code": comment.get("suggestedCode") or comment.get("suggestion"),
        "has_suggestion": bool(comment.get("hasSuggestion") or comment.get("suggestedCode")),
        "addressed": bool(comment.get("addressed", False)),
        "url": comment.get("url") or comment.get("htmlUrl") or comment.get("html_url"),
        "raw": comment,
    }


def normalize_pr_comments(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_pr_comment(comment) for comment in comments]


def review_is_running(status: dict[str, Any]) -> bool:
    """Best-effort Greptile running-state check from MCP/GitHub shaped payloads."""
    candidates = [
        status.get("reviewCompleteness"),
        status.get("state"),
        status.get("status"),
        status.get("conclusion"),
    ]
    for review in status.get("codeReviews") or []:
        if isinstance(review, dict):
            candidates.extend([review.get("status"), review.get("state"), review.get("conclusion")])
    running_states = {
        "running",
        "queued",
        "pending",
        "in_progress",
        "in-progress",
        "reviewing_files",
        "reviewing-files",
    }
    return any(str(value).lower() in running_states for value in candidates if value)


def _load_api_key() -> str:
    key = (os.environ.get("GREPTILE_API_KEY") or "").strip()
    if key:
        return key
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("GREPTILE_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise GreptileAuthError("Greptile API key missing")


async def _read_bounded_httpx_response(resp: httpx.Response, max_bytes: int) -> bytes:
    content_length = resp.headers.get("Content-Length")
    if content_length:
        try:
            declared_length = int(content_length)
        except (TypeError, ValueError):
            declared_length = None
        if declared_length is not None and declared_length > max_bytes:
            raise RuntimeError(f"Greptile MCP response exceeds {max_bytes} byte cap")

    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise RuntimeError(f"Greptile MCP response exceeds {max_bytes} byte cap")
        chunks.append(chunk)
    return b"".join(chunks)


async def _mcp_call(tool: str, arguments: dict[str, Any]) -> Any:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    headers = {
        "Authorization": f"Bearer {_load_api_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    async with httpx.AsyncClient(timeout=90) as client:
        async with client.stream("POST", MCP_URL, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            data = json.loads((await _read_bounded_httpx_response(resp, MCP_RESPONSE_MAX_BYTES)).decode())
    if data.get("error"):
        raise RuntimeError(f"Greptile MCP error: {data['error']}")
    result = data.get("result") or {}
    content = result.get("content") or []
    if content and content[0].get("type") == "text":
        text = content[0].get("text") or ""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}
    return result


def _repo_args(repo: str, default_branch: str) -> dict[str, Any]:
    return {"name": repo, "remote": DEFAULT_REMOTE, "defaultBranch": default_branch}


async def get_pr_status(
    repo: str = DEFAULT_REPO,
    pr_number: int | str = 0,
    default_branch: str = DEFAULT_BRANCH,
) -> dict[str, Any]:
    pr = int(pr_number)
    data = await _mcp_call("get_merge_request", {**_repo_args(repo, default_branch), "prNumber": pr})
    mr = data.get("mergeRequest") or data
    analysis = mr.get("reviewAnalysis") or {}
    body = mr.get("description") or ""
    confidence = parse_confidence_score(body, mr, analysis, mr.get("codeReviews") or [])
    head_sha = (
        mr.get("headSha")
        or mr.get("headSHA")
        or (mr.get("headRef") or {}).get("oid")
        or (mr.get("head") or {}).get("sha")
    )
    return {
        "repo": repo,
        "prNumber": pr,
        "title": mr.get("title"),
        "state": mr.get("state"),
        "reviewCompleteness": analysis.get("reviewCompleteness"),
        "unaddressedCount": len(analysis.get("unaddressedComments") or []),
        "hasNewCommitsSinceReview": analysis.get("hasNewCommitsSinceReview"),
        "lastReviewDate": analysis.get("lastReviewDate"),
        "confidenceScore": confidence,
        "headSha": head_sha,
        "reviewIsRunning": review_is_running({**mr, **analysis}),
        "codeReviews": mr.get("codeReviews") or [],
    }


async def list_pr_comments(
    repo: str = DEFAULT_REPO,
    pr_number: int | str = 0,
    default_branch: str = DEFAULT_BRANCH,
    greptile_only: bool = True,
    unaddressed_only: bool = True,
) -> dict[str, Any]:
    pr = int(pr_number)
    params: dict[str, Any] = {**_repo_args(repo, default_branch), "prNumber": pr}
    if greptile_only:
        params["greptileGenerated"] = True
    if unaddressed_only:
        params["addressed"] = False
    data = await _mcp_call("list_merge_request_comments", params)
    comments = data.get("comments") or []
    return {
        "repo": repo,
        "prNumber": pr,
        "total": len(comments),
        "comments": comments,
        "findings": normalize_pr_comments(comments),
    }


async def trigger_review(
    repo: str = DEFAULT_REPO,
    pr_number: int | str = 0,
    default_branch: str = DEFAULT_BRANCH,
    branch: str | None = None,
) -> dict[str, Any]:
    pr = int(pr_number)
    args = {**_repo_args(repo, default_branch), "prNumber": pr}
    if branch:
        args["branch"] = branch
    return await _mcp_call("trigger_code_review", args)
