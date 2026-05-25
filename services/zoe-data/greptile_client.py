"""Async client for Greptile's HTTP MCP endpoint."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

MCP_URL = os.environ.get("GREPTILE_MCP_URL", "https://api.greptile.com/mcp")
ENV_FILE = Path(os.environ.get("GREPTILE_ENV_FILE", Path.home() / ".config/zoe/greptile.env"))
DEFAULT_REMOTE = "github"
DEFAULT_BRANCH = "main"
DEFAULT_REPO = os.environ.get("ZOE_GITHUB_REPO", "jason-easyazz/zoe-ai-assistant")


class GreptileAuthError(RuntimeError):
    """Raised when Greptile credentials are unavailable."""


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
        resp = await client.post(MCP_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
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
    confidence = None
    import re

    match = re.search(r"Confidence Score:\s*(\d)/5", body, re.I)
    if match:
        confidence = int(match.group(1))
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
    return {"repo": repo, "prNumber": pr, "total": len(comments), "comments": comments}


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
