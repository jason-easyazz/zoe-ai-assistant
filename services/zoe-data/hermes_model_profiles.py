"""Host-scoped Hermes model profile management.

This module intentionally exposes structured profile fields instead of a raw
YAML editor. Hermes config lives outside the repo under ``~/.hermes``.
"""

from __future__ import annotations

import difflib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

PROFILE_PATHS = {
    "main": "config.yaml",
    "zoe-planner": "profiles/zoe-planner/config.yaml",
    "zoe-coder": "profiles/zoe-coder/config.yaml",
    "zoe-reviewer": "profiles/zoe-reviewer/config.yaml",
}

ALLOWED_PROVIDERS = {"openai-codex", "openrouter", "local"}
PAID_OPENROUTER_MODEL = "openrouter/auto"


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser().resolve()


def draft_path() -> Path:
    return hermes_home() / "model-profile-draft.json"


def audit_path() -> Path:
    return hermes_home() / "model-profile-audit.jsonl"


def rollback_dir() -> Path:
    return hermes_home() / "rollback" / "model-profiles"


def _append_audit(record: dict[str, Any]) -> None:
    audit_path().parent.mkdir(parents=True, exist_ok=True)
    with audit_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def _profile_path(profile: str) -> Path:
    if profile not in PROFILE_PATHS:
        raise ValueError(f"Unknown profile: {profile}")
    root = hermes_home()
    path = (root / PROFILE_PATHS[profile]).resolve()
    if root not in path.parents and path != root:
        raise ValueError("Profile path escapes HERMES_HOME")
    return path


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {path}")
    return data


def _dump_yaml(path: Path, data: dict[str, Any]) -> None:
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _profile_from_yaml(name: str, data: dict[str, Any]) -> dict[str, Any]:
    model = data.get("model") if isinstance(data.get("model"), dict) else {}
    fallbacks = data.get("fallback_providers")
    if not isinstance(fallbacks, list):
        fallbacks = []
    normalized_fallbacks = []
    for item in fallbacks:
        if isinstance(item, dict):
            normalized_fallbacks.append(
                {
                    "provider": str(item.get("provider") or ""),
                    "model": str(item.get("model") or ""),
                }
            )
    return {
        "name": name,
        "path": str(_profile_path(name)),
        "provider": str(model.get("provider") or ""),
        "model": str(model.get("default") or ""),
        "context_length": model.get("context_length"),
        "fallbacks": normalized_fallbacks,
    }


def list_profiles() -> list[dict[str, Any]]:
    profiles = []
    for name in PROFILE_PATHS:
        path = _profile_path(name)
        profiles.append(_profile_from_yaml(name, _load_yaml(path)))
    return profiles


def _validate_entry(entry: dict[str, Any], *, profile_name: str) -> dict[str, Any]:
    allowed_keys = {"name", "provider", "model", "fallbacks", "context_length"}
    forbidden = sorted(set(entry) - allowed_keys)
    if forbidden:
        raise ValueError(f"{profile_name}: unsupported fields: {', '.join(forbidden)}")

    provider = str(entry.get("provider") or "").strip()
    model = str(entry.get("model") or "").strip()
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError(f"{profile_name}: provider must be one of {sorted(ALLOWED_PROVIDERS)}")
    if not model:
        raise ValueError(f"{profile_name}: model is required")

    fallbacks = entry.get("fallbacks") or []
    if not isinstance(fallbacks, list):
        raise ValueError(f"{profile_name}: fallbacks must be a list")
    clean_fallbacks = []
    for idx, fallback in enumerate(fallbacks):
        if not isinstance(fallback, dict):
            raise ValueError(f"{profile_name}: fallback {idx} must be an object")
        fp = str(fallback.get("provider") or "").strip()
        fm = str(fallback.get("model") or "").strip()
        if fp not in ALLOWED_PROVIDERS:
            raise ValueError(f"{profile_name}: fallback {idx} provider is not allowed")
        if not fm:
            raise ValueError(f"{profile_name}: fallback {idx} model is required")
        clean_fallbacks.append({"provider": fp, "model": fm})

    result = {
        "name": profile_name,
        "provider": provider,
        "model": model,
        "fallbacks": clean_fallbacks,
    }
    if entry.get("context_length") is not None:
        result["context_length"] = int(entry["context_length"])
    return result


def validate_profiles(profiles: list[dict[str, Any]], *, confirm_paid_auto: bool = False) -> dict[str, Any]:
    seen = set()
    clean = []
    paid_auto = []
    for entry in profiles:
        if not isinstance(entry, dict):
            raise ValueError("Each profile must be an object")
        name = str(entry.get("name") or "").strip()
        if name not in PROFILE_PATHS:
            raise ValueError(f"Unknown profile: {name}")
        if name in seen:
            raise ValueError(f"Duplicate profile: {name}")
        seen.add(name)
        clean_entry = _validate_entry(entry, profile_name=name)
        all_models = [clean_entry["model"], *[f["model"] for f in clean_entry["fallbacks"]]]
        if PAID_OPENROUTER_MODEL in all_models:
            paid_auto.append(name)
        clean.append(clean_entry)

    if paid_auto and not confirm_paid_auto:
        raise ValueError(f"OpenRouter Auto requires confirmation for: {', '.join(paid_auto)}")
    return {"profiles": clean, "paid_auto_profiles": paid_auto}


def _apply_to_yaml(data: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    model = data.setdefault("model", {})
    if not isinstance(model, dict):
        raise ValueError(f"{profile['name']}: model section must be a mapping")
    model["provider"] = profile["provider"]
    model["default"] = profile["model"]
    if "context_length" in profile:
        model["context_length"] = profile["context_length"]
    data["fallback_providers"] = [
        {"provider": f["provider"], "model": f["model"]} for f in profile["fallbacks"]
    ]
    return data


def _diff_for_profile(profile: dict[str, Any]) -> str:
    path = _profile_path(profile["name"])
    old = path.read_text(encoding="utf-8").splitlines(keepends=True)
    new_data = _apply_to_yaml(_load_yaml(path), profile)
    new = yaml.safe_dump(new_data, sort_keys=False, allow_unicode=False).splitlines(keepends=True)
    return "".join(difflib.unified_diff(old, new, fromfile=str(path), tofile=str(path)))


def build_diff(profiles: list[dict[str, Any]], *, confirm_paid_auto: bool = False) -> dict[str, Any]:
    validated = validate_profiles(profiles, confirm_paid_auto=confirm_paid_auto)
    diffs = {p["name"]: _diff_for_profile(p) for p in validated["profiles"]}
    return {**validated, "diffs": diffs}


def save_draft(profiles: list[dict[str, Any]], *, confirm_paid_auto: bool = False) -> dict[str, Any]:
    diff = build_diff(profiles, confirm_paid_auto=confirm_paid_auto)
    path = draft_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(diff["profiles"], indent=2), encoding="utf-8")
    return diff


def load_draft() -> list[dict[str, Any]] | None:
    path = draft_path()
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Draft must contain a profile list")
    return data


def count_running_workers() -> int:
    try:
        result = subprocess.run(
            ["pgrep", "-af", "hermes .*work kanban task"],
            check=False,
            text=True,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OSError("Unable to determine running Kanban workers") from exc
    return len([line for line in result.stdout.splitlines() if "work kanban task" in line])


def apply_profiles(
    profiles: list[dict[str, Any]] | None,
    *,
    actor: str,
    confirm_paid_auto: bool = False,
    restart: bool = False,
    force_restart: bool = False,
) -> dict[str, Any]:
    selected = profiles if profiles is not None else load_draft()
    if selected is None:
        raise ValueError("No profiles supplied and no draft exists")
    diff = build_diff(selected, confirm_paid_auto=confirm_paid_auto)

    if restart and not force_restart:
        running_workers = count_running_workers()
        if running_workers:
            raise RuntimeError(f"{running_workers} Kanban worker(s) running; retry after drain or force restart")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = rollback_dir() / ts
    backup_root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    try:
        for profile in diff["profiles"]:
            path = _profile_path(profile["name"])
            backup_path = backup_root / profile["name"] / path.name
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup_path)
            _dump_yaml(path, _apply_to_yaml(_load_yaml(path), profile))
            written.append(profile["name"])
    except Exception as exc:
        restored: list[str] = []
        for profile_name in written:
            backup_file = backup_root / profile_name / _profile_path(profile_name).name
            if backup_file.exists():
                shutil.copy2(backup_file, _profile_path(profile_name))
                restored.append(profile_name)
        _append_audit(
            {
                "timestamp": ts,
                "actor": actor,
                "status": "failed",
                "error": str(exc),
                "profiles": [p["name"] for p in diff["profiles"]],
                "written": written,
                "restored": restored,
                "backup_dir": str(backup_root),
            }
        )
        raise

    restart_result = None
    if restart:
        restarted = subprocess.run(
            ["systemctl", "--user", "restart", "hermes-agent.service"],
            check=False,
            text=True,
            capture_output=True,
            timeout=30,
        )
        restart_result = {
            "returncode": restarted.returncode,
            "stderr": restarted.stderr[-4000:],
        }

    audit = {
        "timestamp": ts,
        "actor": actor,
        "status": "applied",
        "profiles": [p["name"] for p in diff["profiles"]],
        "restart": restart,
        "force_restart": force_restart,
        "backup_dir": str(backup_root),
    }
    _append_audit(audit)
    return {**diff, "backup_dir": str(backup_root), "restart": restart_result}


def rollback_profiles(backup_dir_value: str | None, *, actor: str) -> dict[str, Any]:
    root = rollback_dir().resolve()
    if backup_dir_value:
        selected = Path(backup_dir_value).expanduser().resolve()
    else:
        backups = sorted([p for p in root.iterdir() if p.is_dir()]) if root.exists() else []
        if not backups:
            raise ValueError("No rollback backups found")
        selected = backups[-1].resolve()
    if root not in selected.parents:
        raise ValueError("Rollback path escapes Hermes rollback directory")

    restored = []
    try:
        for profile_name in PROFILE_PATHS:
            backup_file = selected / profile_name / _profile_path(profile_name).name
            if not backup_file.exists():
                continue
            target = _profile_path(profile_name)
            shutil.copy2(backup_file, target)
            restored.append(profile_name)
    except Exception as exc:
        _append_audit(
            {
                "timestamp": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                "actor": actor,
                "status": "rollback_failed",
                "error": str(exc),
                "rollback_dir": str(selected),
                "restored": restored,
            }
        )
        raise
    if not restored:
        raise ValueError("Selected rollback backup contains no known profile configs")

    audit = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "actor": actor,
        "status": "rolled_back",
        "rollback_dir": str(selected),
        "restored": restored,
    }
    _append_audit(audit)
    return {"ok": True, "rollback_dir": str(selected), "restored": restored}

