#!/usr/bin/env python3
"""populate_multica.py — Idempotent workspace population for the Zoe Multica board.

Run from /home/zoe/assistant:
    python3 scripts/setup/populate_multica.py

Safe to run multiple times — checks for existing resources before creating.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import requests

# ── Config ────────────────────────────────────────────────────────────────────

_ENV_PATH = Path(__file__).parent.parent.parent / ".env"
_SKILL_BASE = Path(__file__).parent.parent.parent / "skills"
_DB_CONTAINER = "zoe-database"
_DB_USER = "zoe"
_DB_NAME = "multica"

def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    env.update(os.environ)
    return env

_ENV = _load_env()
BASE = _ENV.get("MULTICA_BASE_URL", "http://localhost:8080").rstrip("/")
TOKEN = _ENV.get("MULTICA_API_TOKEN", "")
WORKSPACE_ID = _ENV.get("MULTICA_WORKSPACE_ID", "681a418b-c994-437b-98c4-e85e95abd925")
OWNER_USER_ID = "d31a975d-917f-4b51-9920-31fefc91452d"

if not TOKEN:
    print("ERROR: MULTICA_API_TOKEN not set in .env", file=sys.stderr)
    sys.exit(1)

_HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

# ── Counters ──────────────────────────────────────────────────────────────────

_counts: dict[str, int] = {
    "labels": 0, "projects": 0, "runtime": 0,
    "skills": 0, "agents": 0, "squads": 0,
    "autopilots": 0, "issues": 0,
}
_runtime_id: str = ""

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(path: str, params: dict | None = None) -> Any:
    p = dict(params or {})
    p["workspace_id"] = WORKSPACE_ID
    r = requests.get(f"{BASE}{path}", headers=_HEADERS, params=p, timeout=15)
    if r.status_code == 200:
        return r.json()
    return None


def _post(path: str, payload: dict) -> dict | None:
    params = {"workspace_id": WORKSPACE_ID}
    r = requests.post(f"{BASE}{path}", headers=_HEADERS, json=payload,
                      params=params, timeout=15)
    if r.status_code in (200, 201):
        return r.json()
    print(f"  POST {path} → {r.status_code}: {r.text[:200]}")
    return None


def _patch(path: str, payload: dict) -> dict | None:
    r = requests.patch(f"{BASE}{path}", headers=_HEADERS, json=payload, timeout=15)
    if r.status_code in (200, 201, 204):
        return r.json() if r.content else {}
    print(f"  PATCH {path} → {r.status_code}: {r.text[:200]}")
    return None


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _post_with_workspace(path: str, payload: dict) -> dict | None:
    """POST with workspace_id as query param (used for sub-resource endpoints)."""
    params = {"workspace_id": WORKSPACE_ID}
    r = requests.post(f"{BASE}{path}", headers=_HEADERS, json=payload,
                      params=params, timeout=15)
    if r.status_code in (200, 201, 204):
        return r.json() if r.content else {}
    print(f"  POST {path} → {r.status_code}: {r.text[:200]}")
    return None


# Keep old name as alias for backwards compatibility within the file
_post_no_workspace = _post_with_workspace


def _db_exec(sql: str) -> tuple[bool, str]:
    """Run SQL against the multica DB via docker exec."""
    cmd = ["docker", "exec", _DB_CONTAINER, "psql", "-U", _DB_USER, "-d", _DB_NAME, "-c", sql]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return True, result.stdout
        return False, result.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)


def _db_update_one(sql: str) -> tuple[bool, str]:
    ok, msg = _db_exec(sql)
    if not ok:
        return False, msg
    command_tags = [line.strip() for line in msg.splitlines() if line.strip().startswith("UPDATE ")]
    if command_tags != ["UPDATE 1"]:
        return False, (msg.strip() or "update did not report row count")
    return True, msg


def _db_name_id_map(table: str, names: list[str]) -> dict[str, str]:
    if not names:
        return {}
    allowed_tables = {"agent", "squad"}
    if table not in allowed_tables:
        print(f"  ⚠ Refusing to inspect unsupported table {table!r}")
        return {}
    names_sql = ", ".join(_sql_literal(name) for name in names)
    sql = (
        "select name, id from "
        f"{table} where workspace_id={_sql_literal(WORKSPACE_ID)} "
        f"and name in ({names_sql}) "
        "order by name, archived_at nulls first, updated_at desc;"
    )
    cmd = [
        "docker", "exec", _DB_CONTAINER,
        "psql", "-U", _DB_USER, "-d", _DB_NAME,
        "-t", "-A", "-F", "\t", "-c", sql,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"  ⚠ Failed to inspect existing {table} rows: {exc}")
        return {}
    if result.returncode != 0:
        print(f"  ⚠ Failed to inspect existing {table} rows: {(result.stderr or result.stdout)[:100]}")
        return {}
    found: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "\t" not in line:
            continue
        name, row_id = line.split("\t", 1)
        name = name.strip()
        row_id = row_id.strip()
        if name and row_id and name not in found:
            found[name] = row_id
    return found


# ── Step A — Update workspace context ─────────────────────────────────────────

def step_a_update_workspace():
    print("\n[A] Updating workspace context...")
    payload = {
        "context": (
            "Zoe is a self-hosted AI assistant running on a Jetson Orin NX 16GB. "
            "She is conversational, proactive, and self-improving. This workspace tracks "
            "everything: her capabilities, her agents, her scheduled behaviours, and her "
            "evolution. Every intent gap she cannot answer becomes a backlog issue. "
            "Every improvement she deploys is measured here. This is how Zoe grows."
        ),
        "description": (
            "Operational mirror of the Zoe AI system — agents, skills, evolution, and capability growth"
        ),
    }
    result = _patch(f"/api/workspaces/{WORKSPACE_ID}", payload)
    if result is not None:
        print("  ✓ Workspace context updated")
    else:
        print("  ⚠ Workspace update may have failed (check above)")


# ── Step B — Create labels ────────────────────────────────────────────────────

_LABELS = [
    # Domain labels
    ("home-automation", "#3B82F6"),
    ("voice", "#06B6D4"),
    ("calendar", "#8B5CF6"),
    ("music", "#EC4899"),
    ("memory-people", "#F59E0B"),
    ("journal", "#10B981"),
    ("finance", "#84CC16"),
    ("platform", "#6B7280"),
    ("security", "#EF4444"),
    ("conversation", "#F97316"),
    # Type labels
    ("evolution-proposal", "#7C3AED"),
    ("intent-gap", "#DC2626"),
    ("user-frustration", "#B45309"),
    ("charter-gap", "#1D4ED8"),
    ("bug", "#EF4444"),
    ("feature", "#059669"),
    ("infrastructure", "#374151"),
    ("self-improvement", "#6D28D9"),
    ("autoresearch", "#14B8A6"),
]


def step_b_create_labels() -> dict[str, str]:
    """Returns name → label_id mapping."""
    print("\n[B] Creating labels...")
    existing_raw = _get("/api/labels") or []
    existing: list[dict] = existing_raw if isinstance(existing_raw, list) else existing_raw.get("labels", [])
    existing_names = {lbl["name"]: lbl["id"] for lbl in existing}

    label_ids: dict[str, str] = dict(existing_names)
    for name, color in _LABELS:
        if name in existing_names:
            print(f"  ↩ Label '{name}' exists — skipping")
            continue
        result = _post("/api/labels", {"name": name, "color": color})
        if result and "id" in result:
            label_ids[name] = result["id"]
            _counts["labels"] += 1
            print(f"  ✓ Created label '{name}'")
        else:
            print(f"  ✗ Failed to create label '{name}'")

    return label_ids


# ── Step C — Create projects ──────────────────────────────────────────────────

_PROJECTS = [
    ("Conversational Core", "in_progress",
     "Greetings, daily briefing, morning/evening routines, casual conversation, LLM fallthrough"),
    ("Home Automation", "in_progress",
     "Smart home control, HA bridge integration, lights, switches, thermostats, sensors"),
    ("Calendar & Time", "in_progress",
     "Calendar events, time/date queries, countdown timers"),
    ("Reminders & Tasks", "in_progress",
     "Reminders, notes, todo lists, shopping lists"),
    ("Memory & People", "in_progress",
     "People contacts, personal facts, memory management"),
    ("Music & Media", "in_progress",
     "Music Assistant integration, playback control, volume, recipes"),
    ("Wellbeing & Journal", "in_progress",
     "Journal entries, streaks, prompts, daily wellbeing check-ins"),
    ("Finance", "planned",
     "Transaction logging, income/expense tracking, financial summaries"),
    ("Self-Improvement Engine", "in_progress",
     "Evolution proposals, capability extension, self-improvement skill, NOTICE/PROPOSE/EXECUTE/MEASURE loop"),
    ("Infrastructure & Platform", "in_progress",
     "Auth, nginx, PostgreSQL, Docker services, OIDC provider, security"),
    ("Autoresearch Lab", "planned",
     "Karpathy-style fixed-budget asset optimization runs: one locked program, one editable asset, one objective score"),
    ("Agent Orchestration", "in_progress",
     "OpenClaw, Hermes, Agent Zero, A2A federation, task routing, board status"),
    ("Voice & Presence", "in_progress",
     "LiveKit voice, portraits, proactive push notifications, channel integrations"),
]


def step_c_create_projects() -> dict[str, str]:
    """Returns title → project_id mapping."""
    print("\n[C] Creating projects...")
    existing_raw = _get("/api/projects") or []
    existing: list[dict] = existing_raw if isinstance(existing_raw, list) else existing_raw.get("projects", [])
    existing_titles = {p["title"]: p["id"] for p in existing}

    project_ids: dict[str, str] = dict(existing_titles)
    for title, status, description in _PROJECTS:
        if title in existing_titles:
            print(f"  ↩ Project '{title}' exists — skipping")
            continue
        result = _post("/api/projects", {
            "title": title,
            "status": status,
            "description": description,
        })
        if result and "id" in result:
            project_ids[title] = result["id"]
            _counts["projects"] += 1
            print(f"  ✓ Created project '{title}'")
        else:
            print(f"  ✗ Failed to create project '{title}'")

    return project_ids


# ── Step D — Create runtime ───────────────────────────────────────────────────

def step_d_create_runtime() -> str:
    """Returns runtime_id. Inserts directly into DB since POST /api/runtimes returns 405."""
    global _runtime_id
    print("\n[D] Creating/verifying runtime...")

    target_name = "Zoe Home Server (Jetson Orin NX)"

    # Check existing via API
    existing_raw = _get("/api/runtimes") or []
    existing: list[dict] = existing_raw if isinstance(existing_raw, list) else existing_raw.get("runtimes", [])
    for rt in existing:
        if rt["name"] == target_name:
            _runtime_id = rt["id"]
            print(f"  ↩ Runtime '{target_name}' exists ({_runtime_id}) — skipping")
            return _runtime_id

    # Insert directly into DB (API returns 405 for POST /api/runtimes)
    rt_id = str(uuid.uuid4())
    metadata = json.dumps({
        "url": "http://host.docker.internal:8000",
        "device": "Jetson Orin NX 16GB",
        "gpu": "1024-core Ampere",
        "llm": "Llama-3.2-3B-Instruct-Q4_K_M",
        "llm_url": "http://localhost:8080",
    }).replace("'", "''")
    sql = (
        f"INSERT INTO agent_runtime "
        f"(id, workspace_id, name, runtime_mode, provider, status, metadata, owner_id, visibility, timezone) "
        f"VALUES ("
        f"'{rt_id}', "
        f"'{WORKSPACE_ID}', "
        f"'{target_name}', "
        f"'local', "
        f"'zoe', "
        f"'offline', "
        f"'{metadata}'::jsonb, "
        f"'{OWNER_USER_ID}', "
        f"'public', "
        f"'Australia/Perth'"
        f") ON CONFLICT DO NOTHING;"
    )
    ok, msg = _db_exec(sql)
    if ok:
        # Fetch the actual ID in case of conflict
        check_sql = (
            f"SELECT id FROM agent_runtime "
            f"WHERE workspace_id='{WORKSPACE_ID}' AND name='{target_name}';"
        )
        _, out = _db_exec(check_sql)
        for line in out.splitlines():
            line = line.strip()
            if line and not line.startswith("-") and not line.startswith("id") and len(line) == 36:
                _runtime_id = line
                break
        if not _runtime_id:
            _runtime_id = rt_id
        _counts["runtime"] += 1
        print(f"  ✓ Runtime created ({_runtime_id})")
    else:
        # Fall back to first available runtime
        if existing:
            _runtime_id = existing[0]["id"]
            print(f"  ⚠ DB insert failed, using existing runtime: {_runtime_id}")
            print(f"    Error: {msg[:100]}")
        else:
            print(f"  ✗ No runtime available: {msg[:100]}")

    return _runtime_id


# ── Step E — Create skills ────────────────────────────────────────────────────

_SKILL_DEFS = [
    ("skills/agent-zero-research/SKILL.md", "Agent Zero Research"),
    ("skills/calendar-events/SKILL.md", "Calendar Events"),
    ("skills/channel-setup/SKILL.md", "Channel Setup"),
    ("skills/personal-facts/SKILL.md", "Personal Facts"),
    ("skills/proactive-agent/SKILL.md", "Proactive Agent"),
    ("skills/self-improvement/SKILL.md", "Self-Improvement"),
    ("skills/shopping-list/SKILL.md", "Shopping List"),
    ("skills/smart-home/SKILL.md", "Smart Home"),
    ("skills/openclaw/zoe-capability-extender/SKILL.md", "Zoe Capability Extender"),
    ("skills/autoresearch-engineer/SKILL.md", "Auto Research Engineer"),
    ("skills/openclaw/zoe-page-builder/SKILL.md", "Zoe Page Builder"),
    ("skills/openclaw/zoe-widget-builder/SKILL.md", "Zoe Widget Builder"),
]

_REPO_ROOT = Path(__file__).parent.parent.parent


def _read_skill_content(rel_path: str, name: str) -> str:
    p = _REPO_ROOT / rel_path
    if p.exists():
        return p.read_text(errors="replace")
    # Search fallback
    search_name = Path(rel_path).name
    for found in _REPO_ROOT.rglob(search_name):
        return found.read_text(errors="replace")
    return f"# {name}\n\nSkill file not found at {rel_path}. Please add SKILL.md content here.\n"


def step_e_create_skills() -> dict[str, str]:
    """Returns name → skill_id mapping."""
    print("\n[E] Creating skills...")
    existing_raw = _get("/api/skills") or []
    existing: list[dict] = existing_raw if isinstance(existing_raw, list) else existing_raw.get("skills", [])
    existing_names = {s["name"]: s["id"] for s in existing}

    skill_ids: dict[str, str] = dict(existing_names)
    for rel_path, name in _SKILL_DEFS:
        if name in existing_names:
            print(f"  ↩ Skill '{name}' exists — skipping")
            continue
        content = _read_skill_content(rel_path, name)
        result = _post("/api/skills", {
            "name": name,
            "content": content,
            "description": f"Loaded from {rel_path}",
        })
        if result and "id" in result:
            skill_ids[name] = result["id"]
            _counts["skills"] += 1
            print(f"  ✓ Created skill '{name}'")
        else:
            print(f"  ✗ Failed to create skill '{name}'")

    return skill_ids


# ── Step F — Create agents ────────────────────────────────────────────────────

_AGENT_DEFS = [
    {
        "name": "Zoe Core",
        "description": (
            "Main conversational router. Handles 51 intents via fast path + LLM fallback. "
            "The primary user-facing agent."
        ),
        "instructions": (
            "You are Zoe, a warm and capable home AI assistant. You handle greetings, "
            "calendar, reminders, smart home control, music, weather, memory, journal, "
            "finance, and general conversation. Route specialised tasks to the appropriate "
            "skill. Always be concise, helpful, and personal. Your wake word is 'Hey Zoe'."
        ),
        "model": "Llama-3.2-3B-Instruct-Q4_K_M",
        "runtime_provider": "zoe",
    },
    {
        "name": "OpenClaw",
        "description": (
            "Agentic execution runtime. Handles browser automation, code execution, and skill "
            "building. In Multica issue capture, route simple-English ticket creation through "
            "the Hermes runtime until OpenClaw's Gemma 4 E2B-backed harness has non-rate-limited capacity."
        ),
        "instructions": (
            "You are OpenClaw, Zoe's native agentic execution runtime. For Multica simple-English "
            "issue capture, use the Hermes runtime path so ticket creation remains reliable and cost-controlled; "
            "run browser/tool tasks only when explicitly assigned, and report blockers clearly. "
            "Do not decide Zoe engineering phase advancement; Zoe/Hermes harness owns that workflow."
        ),
        "model": "gpt-5.4",
        "fallback_model": "main",
        "runtime_provider": "hermes",
    },
    {
        "name": "Hermes",
        "description": (
            "Zoe engineering and reasoning runtime. Handles Multica issue execution, architecture "
            "review, code review, Greptile/PR loops, and deterministic harness repair through "
            "the native Multica daemon."
        ),
        "instructions": (
            "You are Hermes, Zoe's default engineering and reasoning agent. For Multica work, "
            "follow the issue context, use available CLI/tools, keep changes scoped, provide "
            "evidence, and surface blockers. Zoe driver owns phase advancement; do not create "
            "unmanaged Kanban chains or bypass dispatch gates."
        ),
        # Multica daemon selector for the Hermes CLI profile, not a public OpenAI model id.
        "model": "gpt-5.4",
        "fallback_model": "main",
        "runtime_provider": "hermes",
    },
    {
        "name": "Agent Zero",
        "description": (
            "Autonomous deep-research agent. Handles research, planning, and comparison tasks "
            "via web browsing and multi-step reasoning. Entry point: port 50001."
        ),
        "instructions": (
            "You are Agent Zero, Zoe's research specialist. You handle deep-dive research, "
            "competitive analysis, planning breakdowns, and comparison tasks. You can browse "
            "the web, reason across multiple sources, and produce structured reports. "
            "Be thorough and cite your sources."
        ),
        "model": "Llama-3.2-3B-Instruct-Q4_K_M",
        "runtime_provider": "zoe",
    },
    {
        "name": "Auto Research Engineer",
        "description": (
            "Karpathy-style autonomous optimizer for approved assets. It turns one business "
            "question into one objective metric, changes only the declared asset, scores via "
            "the locked evaluator, keeps improvements, reverts losses, and logs each round."
        ),
        "instructions": (
            "You are Zoe's Auto Research Engineer. Before any run, perform the fit check: "
            "the target must have one objective numeric score, feedback in minutes or hours, "
            "and approved write access to exactly the asset files. Create or verify the three-file "
            "setup: a human-owned instructions/program file, one or more agent-editable asset files, "
            "and a locked scoring file. During a run, follow the Karpathy autoresearch loop: establish "
            "baseline, make one hypothesis and one asset change, run the scorer, keep only better "
            "results, revert worse or crashed changes, and append every round to an untracked results "
            "log. Never edit the instructions/program file, scoring file, evaluators, dependencies, "
            "or undeclared assets. Zoe/Hermes approval gates and branch/PR processes still apply."
        ),
        "model": "gpt-5.4",
        "fallback_model": "main",
        "runtime_provider": "hermes",
    },
    {
        "name": "Self-Improvement Agent",
        "description": (
            "Specialised agent for the evolution loop. Reviews evolution proposals, implements "
            "intent handlers, measures outcomes. Owns the NOTICE→PROPOSE→EXECUTE→MEASURE cycle."
        ),
        "instructions": (
            "You are Zoe's self-improvement specialist. Your job is to review evolution proposals "
            "generated from intent miss logs, implement missing intent handlers, add new skills, "
            "fix routing regressions, and measure the impact of changes after 48 hours. You work "
            "systematically: read the proposal, understand the gap, implement the minimal fix, "
            "test it, commit it, and report back."
        ),
        "model": "Llama-3.2-3B-Instruct-Q4_K_M",
        "runtime_provider": "zoe",
    },
]


def _runtime_ids_by_provider(default_runtime_id: str) -> dict[str, str]:
    """Return provider -> online runtime id, falling back to the Zoe host runtime."""
    runtime_ids = {"zoe": default_runtime_id}
    sql = (
        "select provider, id from agent_runtime "
        f"where workspace_id={_sql_literal(WORKSPACE_ID)} "
        "and status='online' "
        "and provider in ('hermes') "
        "order by provider, daemon_id is null, updated_at desc;"
    )
    cmd = [
        "docker", "exec", _DB_CONTAINER,
        "psql", "-U", _DB_USER, "-d", _DB_NAME,
        "-t", "-A", "-F", "\t", "-c", sql,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"  ⚠ Runtime provider lookup failed: {exc}")
        return runtime_ids
    if result.returncode != 0:
        print(f"  ⚠ Runtime provider lookup failed: {(result.stderr or result.stdout)[:100]}")
        return runtime_ids
    for line in result.stdout.splitlines():
        if "\t" not in line:
            continue
        provider, row_id = [part.strip() for part in line.split("\t", 1)]
        if provider and row_id and provider not in runtime_ids:
            runtime_ids[provider] = row_id
    return runtime_ids


def step_f_create_agents(runtime_id: str) -> dict[str, str]:
    """Returns name → agent_id mapping."""
    print("\n[F] Creating agents...")
    runtime_ids = _runtime_ids_by_provider(runtime_id)
    existing_raw = _get("/api/agents") or []
    existing: list[dict] = existing_raw if isinstance(existing_raw, list) else existing_raw.get("agents", [])
    managed_names = [str(defn["name"]) for defn in _AGENT_DEFS]
    existing_names = _db_name_id_map("agent", managed_names)
    for agent in existing:
        name = agent.get("name")
        if name in managed_names and name not in existing_names:
            existing_names[name] = agent["id"]

    agent_ids: dict[str, str] = dict(existing_names)
    for defn in _AGENT_DEFS:
        name = defn["name"]
        runtime_provider = str(defn.get("runtime_provider") or "zoe")
        target_runtime_id = runtime_ids.get(runtime_provider, runtime_id)
        target_model = defn["model"]
        if runtime_provider != "zoe" and runtime_provider not in runtime_ids:
            target_model = str(defn.get("fallback_model") or target_model)
            print(
                f"  ⚠ Provider '{runtime_provider}' not online — using Zoe runtime for '{name}' "
                f"with fallback model '{target_model}'"
            )
        if name in existing_names:
            agent_id = existing_names[name]
            sql = (
                "update agent set "
                f"description={_sql_literal(defn['description'])}, "
                f"instructions={_sql_literal(defn['instructions'])}, "
                f"model={_sql_literal(target_model)}, "
                f"runtime_id={_sql_literal(target_runtime_id)}, "
                "runtime_mode='local', visibility='workspace', "
                "archived_at=NULL, archived_by=NULL, updated_at=now() "
                f"where id={_sql_literal(agent_id)};"
            )
            ok, msg = _db_update_one(sql)
            if ok:
                print(f"  ↻ Agent '{name}' exists ({agent_id}) — refreshed managed fields")
            else:
                print(f"  ⚠ Agent '{name}' exists ({agent_id}) — refresh failed: {msg[:100]}")
            continue
        result = _post("/api/agents", {
            "name": name,
            "description": defn["description"],
            "instructions": defn["instructions"],
            "model": target_model,
            "runtime_id": target_runtime_id,
            "runtime_mode": "local",
            "visibility": "workspace",
        })
        if result and "id" in result:
            agent_ids[name] = result["id"]
            _counts["agents"] += 1
            print(f"  ✓ Created agent '{name}'")
        else:
            print(f"  ✗ Failed to create agent '{name}'")

    return agent_ids


# ── Step G — Assign skills to agents ─────────────────────────────────────────

_SKILL_ASSIGNMENTS: dict[str, list[str]] = {
    "Zoe Core": ["Calendar Events", "Personal Facts", "Shopping List", "Smart Home",
                 "Proactive Agent", "Self-Improvement"],
    "OpenClaw": ["Zoe Capability Extender", "Zoe Page Builder", "Zoe Widget Builder"],
    "Hermes": ["Auto Research Engineer"],
    "Agent Zero": ["Agent Zero Research"],
    "Self-Improvement Agent": ["Self-Improvement", "Zoe Capability Extender"],
    "Auto Research Engineer": ["Auto Research Engineer"],
}


def step_g_assign_skills(agent_ids: dict[str, str], skill_ids: dict[str, str]):
    """Use PUT /api/agents/{id}/skills to set skill list (idempotent replacement)."""
    print("\n[G] Assigning skills to agents...")
    for agent_name, skill_names in _SKILL_ASSIGNMENTS.items():
        if not skill_names:
            continue
        agent_id = agent_ids.get(agent_name)
        if not agent_id:
            print(f"  ⚠ Agent '{agent_name}' not found — skipping skill assignments")
            continue

        # Resolve skill IDs
        wanted_skill_ids = []
        for sn in skill_names:
            sid = skill_ids.get(sn)
            if sid:
                wanted_skill_ids.append(sid)
            else:
                print(f"  ⚠ Skill '{sn}' not found — skipping")

        if not wanted_skill_ids:
            continue

        # PUT replaces all skills at once — idempotent
        params = {"workspace_id": WORKSPACE_ID}
        r = requests.put(
            f"{BASE}/api/agents/{agent_id}/skills",
            headers=_HEADERS,
            json={"skill_ids": wanted_skill_ids},
            params=params,
            timeout=15,
        )
        if r.status_code in (200, 201, 204):
            print(f"  ✓ Skills set for '{agent_name}': {skill_names}")
        else:
            print(f"  ✗ Failed to set skills for '{agent_name}': {r.status_code} {r.text[:100]}")


# ── Step H — Create squads ────────────────────────────────────────────────────

def step_h_create_squads(agent_ids: dict[str, str]) -> dict[str, str]:
    """Returns name → squad_id mapping."""
    print("\n[H] Creating squads...")
    existing_raw = _get("/api/squads") or []
    existing: list[dict] = existing_raw if isinstance(existing_raw, list) else existing_raw.get("squads", [])

    squads_to_create = [
        {
            "name": "Zoe Operations Squad",
            "leader": "Hermes",
            "description": (
                "Core operations team. Hermes leads engineering and harness work; OpenClaw "
                "is available for browser/tool-heavy execution; Zoe Core provides user context."
            ),
            "instructions": (
                "Use Hermes as the default engineering lead for Multica source-of-truth tickets. "
                "Delegate browser/tool-heavy execution to OpenClaw only when required. Keep work "
                "cost-controlled and report blockers on the ticket."
            ),
            "members": ["Zoe Core", "Hermes", "Self-Improvement Agent"],
        },
        {
            "name": "Research & Planning Squad",
            "leader": "Agent Zero",
            "description": (
                "Deep research and strategic planning. Agent Zero leads investigation, "
                "Zoe Core frames questions."
            ),
            "instructions": (
                "Handle deep research, competitive analysis, and strategic planning tasks. "
                "Agent Zero leads investigation. Zoe Core frames questions from user intent."
            ),
            "members": ["Zoe Core", "Auto Research Engineer"],
        },
        {
            "name": "Autoresearch Lab",
            "leader": "Auto Research Engineer",
            "description": (
                "Bounded asset optimization squad for approved Karpathy-style autoresearch runs."
            ),
            "instructions": (
                "Run only after a fit check passes and the human has approved the exact asset and "
                "scoring file. Keep the evaluator locked, change only declared assets, record every "
                "round, and preserve Zoe's branch, evidence, and rollback rules."
            ),
            "members": ["Hermes", "Self-Improvement Agent"],
        },
    ]

    managed_squad_names = [str(sq["name"]) for sq in squads_to_create]
    existing_names = _db_name_id_map("squad", managed_squad_names)
    for squad in existing:
        name = squad.get("name")
        if name in managed_squad_names and name not in existing_names:
            existing_names[name] = squad["id"]

    squad_ids: dict[str, str] = dict(existing_names)

    for sq in squads_to_create:
        name = sq["name"]
        leader_id = agent_ids.get(sq["leader"])
        if not leader_id:
            print(f"  ⚠ Leader agent '{sq['leader']}' not found — skipping squad '{name}'")
            continue

        if name in existing_names:
            squad_id = existing_names[name]
            sql = (
                "update squad set "
                f"description={_sql_literal(sq['description'])}, "
                f"instructions={_sql_literal(sq['instructions'])}, "
                f"leader_id={_sql_literal(leader_id)}, "
                "archived_at=NULL, archived_by=NULL, updated_at=now() "
                f"where id={_sql_literal(squad_id)};"
            )
            ok, msg = _db_update_one(sql)
            if ok:
                print(f"  ↻ Squad '{name}' exists ({squad_id}) — refreshed leader/context")
            else:
                print(f"  ⚠ Squad '{name}' exists ({squad_id}) — refresh failed: {msg[:100]}")
        else:
            result = _post("/api/squads", {
                "name": name,
                "description": sq["description"],
                "instructions": sq["instructions"],
                "leader_id": leader_id,
                "leader_type": "agent",
            })
            if not result or "id" not in result:
                print(f"  ✗ Failed to create squad '{name}'")
                continue
            squad_id = result["id"]
            squad_ids[name] = squad_id
            _counts["squads"] += 1
            print(f"  ✓ Created squad '{name}'")

        # Add members (leader is auto-added; handle "already in squad" gracefully)
        for member_name in sq["members"]:
            member_id = agent_ids.get(member_name)
            if not member_id:
                print(f"    ⚠ Member '{member_name}' not found — skipping")
                continue
            r = _post(
                f"/api/squads/{squad_id}/members",
                {"member_id": member_id, "member_type": "agent"},
            )
            if r is not None:
                print(f"    ✓ Added '{member_name}' to '{name}'")
            else:
                print(f"    ↩ '{member_name}' already in '{name}' (or error)")

    return squad_ids


# ── Step I — Create autopilots ────────────────────────────────────────────────

_AUTOPILOTS = [
    {
        "title": "Morning Checkin",
        "agent": "Zoe Core",
        "status": "paused",
        "execution_mode": "run_only",
        "cron": "30 7 * * *",
        "issue_title_template": "",
    },
    {
        "title": "Evening Wind Down",
        "agent": "Zoe Core",
        "status": "paused",
        "execution_mode": "run_only",
        "cron": "0 21 * * *",
        "issue_title_template": "",
    },
    {
        "title": "Evolution Nightly Notice",
        "agent": "Self-Improvement Agent",
        "execution_mode": "create_issue",
        "cron": "0 2 * * *",
        "issue_title_template": "Nightly Evolution Analysis — {date}",
    },
    {
        "title": "Evolution Weekly Digest",
        "agent": "Self-Improvement Agent",
        "execution_mode": "create_issue",
        "cron": "0 18 * * 5",
        "issue_title_template": "Weekly Evolution Digest — {date}",
    },
    {
        "title": "Reminder Scan",
        "agent": "Zoe Core",
        "status": "paused",
        "execution_mode": "run_only",
        "cron": "*/5 * * * *",
        "issue_title_template": "Reminder Scan",
    },
    {
        "title": "Platform Health Check",
        "agent": "Hermes",
        "execution_mode": "create_issue",
        "cron": "0 6 * * *",
        "issue_title_template": "Platform Health — {date}",
    },
]


def step_i_create_autopilots(agent_ids: dict[str, str]):
    print("\n[I] Creating autopilots...")
    existing_raw = _get("/api/autopilots") or {}
    existing: list[dict] = existing_raw if isinstance(existing_raw, list) else existing_raw.get("autopilots", [])
    existing_titles = {ap["title"]: ap["id"] for ap in existing}

    for apdef in _AUTOPILOTS:
        title = apdef["title"]
        agent_id = agent_ids.get(apdef["agent"])
        if not agent_id:
            print(f"  ⚠ Agent '{apdef['agent']}' not found — skipping autopilot '{title}'")
            continue

        if title in existing_titles:
            ap_id = existing_titles[title]
            status_sql = (
                f"status={_sql_literal(apdef['status'])}, "
                if "status" in apdef else ""
            )
            sql = (
                "update autopilot set "
                f"assignee_id={_sql_literal(agent_id)}, "
                f"{status_sql}"
                f"execution_mode={_sql_literal(apdef['execution_mode'])}, "
                f"issue_title_template={_sql_literal(apdef.get('issue_title_template', ''))}, "
                "updated_at=now() "
                f"where id={_sql_literal(ap_id)};"
            )
            ok, msg = _db_update_one(sql)
            if ok:
                print(f"  ↻ Autopilot '{title}' exists ({ap_id}) — refreshed settings")
            else:
                print(f"  ⚠ Autopilot '{title}' refresh failed: {msg[:100]}")
                continue
        else:
            payload = {
                "title": title,
                "status": apdef.get("status", "active"),
                "execution_mode": apdef["execution_mode"],
                "issue_title_template": apdef.get("issue_title_template", ""),
                "assignee_id": agent_id,
                "assignee_type": "agent",
                "description": f"Scheduled autopilot: {apdef['cron']} (Perth)",
            }
            result = _post("/api/autopilots", payload)
            if not result or "id" not in result:
                print(f"  ✗ Failed to create autopilot '{title}'")
                continue
            ap_id = result["id"]
            _counts["autopilots"] += 1
            print(f"  ✓ Created autopilot '{title}' ({ap_id})")

        # Ensure cron trigger matches the managed schedule.
        ap_detail = _get(f"/api/autopilots/{ap_id}")
        existing_triggers = ap_detail.get("triggers", []) if ap_detail else []
        schedule_triggers = [t for t in existing_triggers if t.get("kind") == "schedule"]
        desired_cron = apdef["cron"]
        desired_timezone = "Australia/Perth"
        has_matching_schedule = any(
            t.get("cron_expression") == desired_cron
            and t.get("timezone") == desired_timezone
            for t in schedule_triggers
        )
        if has_matching_schedule:
            print(f"    ↩ Trigger already set: {desired_cron}")
            continue
        replacing_schedule = bool(schedule_triggers)
        trig = _post(
            f"/api/autopilots/{ap_id}/triggers",
            {
                "kind": "schedule",
                "cron_expression": desired_cron,
                "timezone": desired_timezone,
            },
        )
        if trig and "id" in trig:
            if replacing_schedule:
                delete_sql = (
                    "delete from autopilot_trigger "
                    f"where autopilot_id={_sql_literal(ap_id)} "
                    "and kind='schedule' "
                    f"and id <> {_sql_literal(trig['id'])};"
                )
                ok, msg = _db_exec(delete_sql)
                if ok:
                    print(f"    ↻ Replaced schedule trigger with: {desired_cron}")
                else:
                    print(f"    ⚠ New trigger created but old trigger cleanup failed for '{title}': {msg[:100]}")
            else:
                print(f"    ✓ Trigger set: {desired_cron}")
        else:
            print(f"    ⚠ Trigger creation returned: {trig}")


# ── Step J — Create issues from evolution proposals ───────────────────────────

_NOISE_PATTERNS = [
    r"Please run:",
    r"Run this Python",
    r"Run this Python script",
    r"Run:\n",
    r"nginx document root",
    r"unicode test",
    r"⚠️",
]

def _is_noise(title: str) -> bool:
    for pat in _NOISE_PATTERNS:
        if re.search(pat, title, re.IGNORECASE):
            return True
    if "测试" in title:
        return True
    return False


def _classify_proposal(title: str, ptype: str) -> tuple[str, str, str]:
    """Returns (project_title, priority, domain_label)."""
    t = title.lower()

    if ptype == "charter_gap":
        return "Infrastructure & Platform", "medium", "charter-gap"
    if ptype == "user_frustration":
        if any(w in t for w in ["pharmacy", "dentist", "remind"]):
            return "Reminders & Tasks", "high", "user-frustration"
        return "Conversational Core", "medium", "user-frustration"

    # intent_pattern classification
    if any(w in t for w in ["light", "switch", "thermostat", "turn off", "turn on", "smart home", "homeassistant"]):
        return "Home Automation", "medium", "home-automation"
    if any(w in t for w in ["volume", "voice", "portrait", "push notification"]):
        return "Voice & Presence", "medium", "voice"
    if any(w in t for w in ["remind", "pharmacy", "dentist", "todo", "shopping"]):
        return "Reminders & Tasks", "medium", "intent-gap"
    if any(w in t for w in ["hello", "hi", "greet", "good morning", "good evening"]):
        return "Conversational Core", "high", "conversation"
    if any(w in t for w in ["news", "tech news", "what is", "2+2", "weather"]):
        return "Conversational Core", "medium", "conversation"
    if any(w in t for w in ["extend", "capabilit", "self-improve", "improve"]):
        return "Self-Improvement Engine", "medium", "self-improvement"
    if any(w in t for w in ["calendar", "time", "date", "count"]):
        return "Calendar & Time", "medium", "calendar"
    if any(w in t for w in ["music", "play", "song"]):
        return "Music & Media", "medium", "music"
    if any(w in t for w in ["journal", "wellbeing", "mood"]):
        return "Wellbeing & Journal", "medium", "journal"
    if any(w in t for w in ["finance", "money", "transaction"]):
        return "Finance", "medium", "finance"
    if any(w in t for w in ["memory", "remember", "person", "contact"]):
        return "Memory & People", "medium", "memory-people"

    return "Conversational Core", "medium", "intent-gap"


def step_j_create_issues(
    project_ids: dict[str, str],
    label_ids: dict[str, str],
    agent_ids: dict[str, str],
):
    print("\n[J] Creating issues from evolution proposals + infrastructure...")

    self_imp_agent_id = agent_ids.get("Self-Improvement Agent")
    openclaw_id = agent_ids.get("OpenClaw")
    evolution_label_id = label_ids.get("evolution-proposal", "")

    # Fetch existing issues to avoid duplicates
    existing_raw = _get("/api/issues", {"limit": "200"}) or {}
    existing_issues: list[dict] = existing_raw if isinstance(existing_raw, list) else existing_raw.get("issues", [])
    existing_titles = {iss["title"] for iss in existing_issues}

    def create_issue(
        title: str,
        description: str,
        status: str,
        project_id: str | None,
        assignee_id: str | None,
        priority: str = "medium",
        label_ids_list: list[str] | None = None,
    ) -> str | None:
        if title in existing_titles:
            print(f"  ↩ Issue '{title[:60]}' exists — skipping")
            return None
        payload: dict[str, Any] = {
            "title": title,
            "description": description,
            "status": status,
            "priority": priority,
        }
        if project_id:
            payload["project_id"] = project_id
        if assignee_id:
            payload["assignee_id"] = assignee_id
            payload["assignee_type"] = "agent"
        result = _post("/api/issues", payload)
        if not result or "id" not in result:
            print(f"  ✗ Failed to create issue '{title[:60]}'")
            return None

        issue_id = result["id"]
        _counts["issues"] += 1

        # Assign labels
        for lid in (label_ids_list or []):
            if lid:
                _post(f"/api/issues/{issue_id}/labels", {"label_id": lid})
        return issue_id

    # ── Query evolution proposals from zoe DB ──────────────────────────────
    sql = (
        "SELECT id, type, title, description, evidence, status "
        "FROM evolution_proposals "
        "ORDER BY proposed_at DESC;"
    )
    ok, output = _db_exec_zoe(sql)
    proposals: list[dict] = []
    if ok:
        proposals = _parse_psql_output(output)
        print(f"  Found {len(proposals)} evolution proposals")
    else:
        print(f"  ⚠ Could not query evolution_proposals: {output[:100]}")

    for row in proposals:
        title = row.get("title", "")
        ptype = row.get("type", "intent_pattern")
        desc = row.get("description", "") or ""
        evidence = row.get("evidence", "") or ""
        db_status = row.get("status", "pending")

        full_title = title
        full_desc = f"{desc}\n\n**Evidence:** {evidence}" if evidence else desc

        if _is_noise(title):
            status = "cancelled"
            project_id = None
            priority = "low"
            assignee_id = self_imp_agent_id
            lids = [evolution_label_id]
        else:
            project_name, priority, domain_label = _classify_proposal(title, ptype)
            project_id = project_ids.get(project_name)
            assignee_id = self_imp_agent_id
            lids = [l for l in [evolution_label_id, label_ids.get(domain_label)] if l]

            if db_status == "approved":
                status = "in_progress"
            elif db_status in ("validated", "deployed"):
                status = "done"
            elif db_status == "failed":
                status = "cancelled"
            else:
                status = "backlog"

        created_id = create_issue(
            title=full_title,
            description=full_desc,
            status=status,
            project_id=project_id,
            assignee_id=assignee_id,
            priority=priority,
            label_ids_list=lids,
        )
        if created_id:
            print(f"  ✓ Issue: '{title[:60]}' [{status}]")

    # ── 5 infrastructure issues ──────────────────────────────────────────────
    infra_label = label_ids.get("infrastructure", "")
    infra_project = project_ids.get("Infrastructure & Platform")
    agent_orch_project = project_ids.get("Agent Orchestration")

    infra_issues = [
        (
            "HA OIDC auth provider — activate when HA ships it",
            "Home Assistant is working on an OIDC provider. When shipped, activate the "
            "OAUTH_DISCOVERY_URL for HA in the Multica backend config.",
            "backlog", infra_project, openclaw_id, "low",
        ),
        (
            "Multica OIDC — activate when Multica ships it",
            "Multica OIDC SSO integration is blocked on Multica shipping its OIDC client. "
            "Credentials are already configured in .env (MULTICA_OIDC_CLIENT_ID/SECRET).",
            "backlog", infra_project, openclaw_id, "low",
        ),
        (
            "Email backend for Multica (Resend/SMTP)",
            "Multica is running with no email backend — verification codes are printed to logs. "
            "Configure RESEND_API_KEY or SMTP_HOST in docker-compose.modules.yml.",
            "todo", infra_project, openclaw_id, "medium",
        ),
        (
            "Set MULTICA_DEV_VERIFICATION_CODE for stable dev login",
            "MULTICA_DEV_CODE=888888 is set but MULTICA_DEV_VERIFICATION_CODE env var should "
            "be confirmed stable for the backend. Verify env mapping in docker-compose.",
            "todo", infra_project, openclaw_id, "medium",
        ),
        (
            "Wire Multica autopilot triggers to Zoe APScheduler",
            "Multica autopilot cron triggers are defined but not yet wired to the Zoe "
            "APScheduler in services/zoe-data/. Implement polling or webhook bridge so "
            "autopilot schedules actually fire Zoe tasks.",
            "todo", agent_orch_project, openclaw_id, "high",
        ),
    ]
    for title, desc, status, project_id, assignee_id, priority in infra_issues:
        created_id = create_issue(
            title=title,
            description=desc,
            status=status,
            project_id=project_id,
            assignee_id=assignee_id,
            priority=priority,
            label_ids_list=[infra_label],
        )
        if created_id:
            print(f"  ✓ Infra issue: '{title[:60]}'")


# ── DB helpers for zoe database ───────────────────────────────────────────────

def _db_exec_zoe(sql: str) -> tuple[bool, str]:
    """Run SQL against the zoe DB (evolution_proposals table), returning JSON output."""
    # Wrap query in json_agg to get clean JSON regardless of multiline values
    json_sql = f"SELECT json_agg(row_to_json(t)) FROM ({sql.rstrip(';')}) t;"
    cmd = ["docker", "exec", _DB_CONTAINER, "psql", "-U", _DB_USER, "-d", "zoe",
           "-t", "-A", "-c", json_sql]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)


def _parse_psql_output(output: str) -> list[dict]:
    """Parse JSON output from psql (produced by json_agg wrapper)."""
    output = output.strip()
    if not output or output == "\\N":
        return []
    try:
        data = json.loads(output)
        if isinstance(data, list):
            return [
                {k: (v or "") for k, v in row.items()}
                for row in data
                if isinstance(row, dict)
            ]
    except json.JSONDecodeError as e:
        print(f"  ⚠ JSON parse error: {e} — output: {output[:200]}")
    return []


# ── Step K — Print summary ────────────────────────────────────────────────────

def step_k_summary():
    print("\n" + "=" * 50)
    print("=== Multica Workspace Population Complete ===")
    print(f"Labels created:    {_counts['labels']}")
    print(f"Projects created:  {_counts['projects']}")
    print(f"Runtime created:   {_runtime_id or '(used existing)'}")
    print(f"Skills created:    {_counts['skills']}")
    print(f"Agents created:    {_counts['agents']}")
    print(f"Squads created:    {_counts['squads']}")
    print(f"Autopilots created:{_counts['autopilots']}")
    print(f"Issues created:    {_counts['issues']}")
    print("=" * 50)
    print("=== Done ===")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Connecting to Multica at {BASE}")
    print(f"Workspace: {WORKSPACE_ID}")

    step_a_update_workspace()
    label_ids = step_b_create_labels()
    project_ids = step_c_create_projects()
    runtime_id = step_d_create_runtime()
    skill_ids = step_e_create_skills()
    agent_ids = step_f_create_agents(runtime_id)
    step_g_assign_skills(agent_ids, skill_ids)
    step_h_create_squads(agent_ids)
    step_i_create_autopilots(agent_ids)
    step_j_create_issues(project_ids, label_ids, agent_ids)
    step_k_summary()


if __name__ == "__main__":
    main()
