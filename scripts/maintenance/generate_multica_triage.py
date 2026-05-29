#!/usr/bin/env python3
"""Generate Multica triage ledger JSON for board repair (read-only API)."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

DUPLICATE_CLOSE = {
    "improve shopping list ux": "ZOE-45",
    "user frustration: 'remind me about the dentist'": "ZOE-44",
    "memory scope enforcement: personal/shared/ambient": "ZOE-43",
    "intent gap: 'what is happening in tech news today'": "ZOE-41",
    "intent gap: 'show me the news'": "ZOE-18",
    "intent gap: 'can't be...'": "ZOE-14",
    "intent gap: 'can you extend your capabilities'": "ZOE-11",
    "intent gap: 'what time does the pharmacy close'": "ZOE-8",
}

WONT_FIX_PATTERNS = [
    re.compile(r"run this python", re.I),
    re.compile(r"import os", re.I),
    re.compile(r"import socket", re.I),
    re.compile(r"phase [45]:", re.I),
    re.compile(r"smoke test", re.I),
]

LANE_RULES = [
    (re.compile(r"intent gap:", re.I), "F-intent"),
    (re.compile(r"websocket|/ws/push|panel_id", re.I), "A-websocket"),
    (re.compile(r"\bmcp\b|visibility", re.I), "C-mcp"),
    (re.compile(r"touch|panel|orb|kiosk", re.I), "D-touch"),
    (re.compile(r"notification|proactive|quiet-hours", re.I), "E-notifications"),
    (re.compile(r"auth|session|guest|internal/broadcast", re.I), "B-auth"),
    (re.compile(r"agent-sync|timer|graphify|systemd", re.I), "G-ops"),
]


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v.strip()
                os.environ.setdefault(k.strip(), v.strip())
    return env


def _lane(title: str) -> str:
    for pat, lane in LANE_RULES:
        if pat.search(title):
            return lane
    return "G-misc"


def _disposition(issue: dict) -> tuple[str, str]:
    title = (issue.get("title") or "").strip()
    ident = issue.get("identifier") or ""
    t_lower = title.lower()
    if title.startswith("Autopilot:"):
        return "wont_fix", "autopilot wrapper noise"
    if t_lower in DUPLICATE_CLOSE and ident == DUPLICATE_CLOSE[t_lower]:
        return "duplicate", f"duplicate; keep newer sibling"
    for pat in WONT_FIX_PATTERNS:
        if pat.search(title):
            return "wont_fix", "injection/roadmap/smoke — do not implement"
    if ident == "ZOE-1054":
        return "monitor", "OpenHuman cap observation"
    if ident == "ZOE-48":
        return "config", "Multica email Resend/SMTP"
    if ident == "ZOE-4893":
        return "open_pr", "WS auth cluster PR #87-#92"
    if "internal/broadcast" in t_lower:
        return "open_pr", "PR #88 / P0 hotfix"
    if re.search(r"websocket|ws/push|ws ", t_lower):
        return "needs_pr", "websocket lane A"
    return "needs_pr", "triage default"


def main() -> int:
    import httpx

    env = _load_env()
    base = env.get("MULTICA_BASE_URL", "").rstrip("/")
    token = env.get("MULTICA_API_TOKEN", "")
    ws = env.get("MULTICA_WORKSPACE_ID", "")
    if not (base and token and ws):
        print("Multica not configured", file=sys.stderr)
        return 1

    headers = {"Authorization": f"Bearer {token}", "X-Workspace-ID": ws}
    entries: list[dict] = []

    for status in ("backlog", "todo", "in_progress", "in_review"):
        r = httpx.get(
            f"{base}/api/issues",
            headers=headers,
            params={"workspace_id": ws, "status": status, "limit": 500},
            timeout=60,
        )
        r.raise_for_status()
        batch = r.json()
        if isinstance(batch, dict):
            issues = batch.get("issues", batch.get("items", []))
        else:
            issues = batch
        for issue in issues:
            if str(issue.get("title", "")).startswith("Autopilot:") and status != "in_review":
                if status == "backlog":
                    continue
            disp, notes = _disposition(issue)
            if issue.get("title", "").startswith("Autopilot:"):
                disp, notes = "wont_fix", "close in hygiene pass"
            entries.append(
                {
                    "identifier": issue.get("identifier"),
                    "uuid": issue.get("id"),
                    "title": issue.get("title"),
                    "status": status,
                    "priority": issue.get("priority"),
                    "disposition": disp,
                    "lane": _lane(issue.get("title") or ""),
                    "pr": None,
                    "notes": notes,
                }
            )

    out = ROOT / ".cursor" / "tmp" / "multica-triage.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated": True, "count": len(entries), "issues": entries}, indent=2))
    print(f"Wrote {len(entries)} entries to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
