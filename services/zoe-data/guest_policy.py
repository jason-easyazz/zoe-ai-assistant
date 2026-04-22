"""Role capability policy helpers shared across API/voice/UI.

This module is the policy kernel for fixed roles (`admin`, `user`, `guest`).
It provides:
  - default capability matrix for each role
  - DB-backed matrix resolution
  - reusable role checks for features, pages, voice intents, and UI actions
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Mapping, Any, Optional

from fastapi import HTTPException
from prometheus_client import Counter

from memory_metrics import REGISTRY

# Intent names considered household-safe by default.
PUBLIC_HOUSEHOLD_INTENTS: frozenset[str] = frozenset({
    "time_query",
    "date_query",
    "weather",
    "recipe_search",
    "timer_create",
    "general_knowledge",
    "greeting",
    # Shared household operations.
    "list_show",
    "calendar_show",
})

# Intent names that should always require user identity.
USER_SCOPED_INTENTS: frozenset[str] = frozenset({
    "calendar_create", "calendar_update", "calendar_delete",
    "reminder_create", "reminder_list", "reminder_update", "reminder_delete",
    "note_create", "note_search", "note_update",
    "journal_create", "journal_streak", "journal_prompt", "journal_search",
    "transaction_create", "transaction_summary", "transaction_list",
    "people_create", "people_search", "people_update",
    "memory_forget_last", "memory_remember", "memory_recall",
    "daily_briefing", "agenda_show",
    "build_widget", "build_page", "extend_capability", "ha_full_setup",
})


# Page IDs used by settings/touch policy checks.
PAGE_IDS: frozenset[str] = frozenset({
    "dashboard", "calendar", "lists", "chat", "weather", "smarthome", "timers", "music",
    "notes", "journal", "people", "memories", "settings",
})

# Feature domains used by backend route checks.
FEATURE_ACTIONS: dict[str, tuple[str, ...]] = {
    "calendar": ("read", "create", "update", "delete"),
    "lists": ("read", "create", "update", "delete", "add_item", "update_item", "delete_item"),
    "reminders": ("read", "create", "update", "delete", "snooze", "acknowledge", "deliver_notification"),
    "notes": ("read", "create", "update", "delete"),
    "people": ("read", "create", "update", "delete", "manage_fields"),
    "journal": ("read", "create", "update", "delete"),
    "transactions": ("read", "create", "update", "delete", "patch_status"),
    "memories": ("read", "write", "review", "export"),
    "user_profile": ("read", "analyze"),
    "ui_actions": ("read", "create", "bind", "sync", "ack", "retry", "requeue"),
}

UI_ACTION_CLASSES: dict[str, str] = {
    "navigate": "safe_ui",
    "open_panel": "safe_ui",
    "focus": "safe_ui",
    "fill": "safe_ui",
    "submit": "safe_ui",
    "highlight": "safe_ui",
    "notify": "safe_ui",
    "refresh": "safe_ui",
    "click": "safe_ui",
    "panel_navigate": "safe_ui",
    "panel_navigate_fullscreen": "safe_ui",
    "panel_clear": "safe_ui",
    "panel_set_mode": "safe_ui",
    "panel_show_smart_home": "safe_ui",
    "panel_show_media": "safe_ui",
    "panel_announce": "safe_ui",
    "panel_request_auth": "safe_ui",
    "panel_show_fullscreen": "sensitive_ui",
    "panel_browser_frame": "sensitive_ui",
    "create_record": "sensitive_ui",
    "update_record": "sensitive_ui",
    "delete_record": "sensitive_ui",
}

FIXED_ROLES: tuple[str, ...] = ("admin", "user", "guest")

_ROLE_ALIAS = {
    "member": "user",
    "family-admin": "admin",
    "administrator": "admin",
}


def _all_true() -> dict[str, bool]:
    return {k: True for k in PAGE_IDS}


def _feature_all_true() -> dict[str, dict[str, bool]]:
    return {feature: {action: True for action in actions} for feature, actions in FEATURE_ACTIONS.items()}


def default_capability_matrix() -> dict[str, dict[str, Any]]:
    """Default matrix for fixed roles."""
    user_matrix = {
        "pages": _all_true(),
        "features": _feature_all_true(),
        "voice_intents": {intent: True for intent in (PUBLIC_HOUSEHOLD_INTENTS | USER_SCOPED_INTENTS)},
        "ui_action_classes": {"safe_ui": True, "sensitive_ui": True},
    }
    guest_matrix = {
        "pages": {
            "dashboard": True,
            "calendar": True,
            "lists": True,
            "chat": True,
            "weather": True,
            "smarthome": True,
            "timers": True,
            "music": True,
            "notes": False,
            "journal": False,
            "people": False,
            "memories": False,
            "settings": False,
        },
        "features": {
            "calendar": {"read": True, "create": False, "update": False, "delete": False},
            "lists": {
                "read": True, "create": False, "update": False, "delete": False,
                "add_item": False, "update_item": False, "delete_item": False,
            },
            "reminders": {
                "read": True, "create": False, "update": False, "delete": False,
                "snooze": False, "acknowledge": False, "deliver_notification": False,
            },
            "notes": {"read": False, "create": False, "update": False, "delete": False},
            "people": {"read": False, "create": False, "update": False, "delete": False, "manage_fields": False},
            "journal": {"read": False, "create": False, "update": False, "delete": False},
            "transactions": {"read": False, "create": False, "update": False, "delete": False, "patch_status": False},
            "memories": {"read": False, "write": False, "review": False, "export": False},
            "user_profile": {"read": False, "analyze": False},
            "ui_actions": {
                "read": True,
                "create": True,
                "bind": True,
                "sync": True,
                "ack": True,
                "retry": True,
                "requeue": False,
            },
        },
        "voice_intents": {
            **{intent: True for intent in PUBLIC_HOUSEHOLD_INTENTS},
            **{intent: False for intent in USER_SCOPED_INTENTS},
        },
        "ui_action_classes": {"safe_ui": True, "sensitive_ui": False},
    }
    return {
        "admin": user_matrix,
        "user": user_matrix,
        "guest": guest_matrix,
    }


def _canonical_role(role: str | None) -> str:
    r = str(role or "guest").strip().lower()
    r = _ROLE_ALIAS.get(r, r)
    if r not in FIXED_ROLES:
        return "guest"
    return r


async def get_matrix_for_role(db, role: str | None) -> dict[str, Any]:
    """Load effective matrix for role; falls back to defaults."""
    role = _canonical_role(role)
    defaults = default_capability_matrix()[role]
    try:
        cursor = await db.execute(
            "SELECT matrix_json FROM role_capability_matrix WHERE role = ?",
            (role,),
        )
        row = await cursor.fetchone()
        if not row:
            return deepcopy(defaults)
        parsed = json.loads(row["matrix_json"] or "{}")
        if not isinstance(parsed, dict):
            return deepcopy(defaults)
        merged = deepcopy(defaults)
        for key in ("pages", "features", "voice_intents", "ui_action_classes"):
            if isinstance(parsed.get(key), dict):
                if key == "features":
                    for feat, actions in parsed[key].items():
                        if feat in merged["features"] and isinstance(actions, dict):
                            for action, allow in actions.items():
                                if action in merged["features"][feat]:
                                    merged["features"][feat][action] = bool(allow)
                else:
                    for item, allow in parsed[key].items():
                        if item in merged[key]:
                            merged[key][item] = bool(allow)
        return merged
    except Exception:
        return deepcopy(defaults)


guest_policy_decision_count = Counter(
    "zoe_guest_policy_decision_count",
    "Guest-policy decisions across voice and API surfaces.",
    ["outcome", "surface", "resource", "action"],
    registry=REGISTRY,
)


def is_guest_user(user: Mapping[str, Any] | None) -> bool:
    """Return True for guest/unauthenticated identities."""
    if not user:
        return True
    role = str(user.get("role") or "").strip().lower()
    user_id = str(user.get("user_id") or "").strip().lower()
    return role == "guest" or user_id == "guest"


def role_from_user(user: Mapping[str, Any] | None) -> str:
    if not user:
        return "guest"
    if is_guest_user(user):
        return "guest"
    return _canonical_role(str(user.get("role") or "user"))


def record_policy_decision(outcome: str, *, surface: str, resource: str, action: str) -> None:
    """Best-effort metrics helper (never raises)."""
    try:
        guest_policy_decision_count.labels(
            outcome=outcome, surface=surface, resource=resource, action=action
        ).inc()
    except Exception:
        # Metrics are optional at runtime.
        pass


def require_authenticated_for_mutation(
    user: Mapping[str, Any] | None,
    *,
    resource: str,
    action: str,
) -> None:
    """Deprecated compatibility shim.

    New code should use `require_feature_access(...)` so access is decided by
    the role capability matrix instead of a hard-coded guest-only branch.
    """
    if is_guest_user(user):
        record_policy_decision(
            "scope_blocked", surface="api", resource=resource, action=action
        )
        raise HTTPException(
            status_code=403,
            detail="Authentication required for this action.",
        )
    record_policy_decision("auth_ok", surface="api", resource=resource, action=action)


async def require_feature_access(
    db,
    user: Mapping[str, Any] | None,
    *,
    feature: str,
    action: str,
    surface: str = "api",
) -> None:
    """Enforce role matrix for feature/action."""
    role = role_from_user(user)
    if db is None:
        matrix = default_capability_matrix().get(role, default_capability_matrix()["guest"])
    else:
        matrix = await get_matrix_for_role(db, role)
    allowed = bool(matrix.get("features", {}).get(feature, {}).get(action, False))
    record_policy_decision(
        "allowed" if allowed else "blocked",
        surface=surface,
        resource=feature,
        action=action,
    )
    if not allowed:
        if role == "guest":
            raise HTTPException(status_code=403, detail="Authentication required for this action.")
        raise HTTPException(status_code=403, detail="Your role does not have access to this action.")


async def can_use_voice_intent(db, user: Mapping[str, Any] | None, intent_name: str | None) -> bool:
    role = role_from_user(user)
    if db is None:
        matrix = default_capability_matrix().get(role, default_capability_matrix()["guest"])
    else:
        matrix = await get_matrix_for_role(db, role)
    return bool(matrix.get("voice_intents", {}).get(str(intent_name or ""), False))


async def can_use_ui_action(db, user: Mapping[str, Any] | None, action_type: str) -> bool:
    role = role_from_user(user)
    if db is None:
        matrix = default_capability_matrix().get(role, default_capability_matrix()["guest"])
    else:
        matrix = await get_matrix_for_role(db, role)
    klass = UI_ACTION_CLASSES.get(action_type, "sensitive_ui")
    return bool(matrix.get("ui_action_classes", {}).get(klass, False))


__all__ = [
    "FEATURE_ACTIONS",
    "FIXED_ROLES",
    "PAGE_IDS",
    "PUBLIC_HOUSEHOLD_INTENTS",
    "UI_ACTION_CLASSES",
    "USER_SCOPED_INTENTS",
    "can_use_ui_action",
    "can_use_voice_intent",
    "default_capability_matrix",
    "get_matrix_for_role",
    "guest_policy_decision_count",
    "is_guest_user",
    "record_policy_decision",
    "require_feature_access",
    "require_authenticated_for_mutation",
    "role_from_user",
]
