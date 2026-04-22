import json
from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user, require_admin
from database import get_db
from guest_policy import (
    FIXED_ROLES,
    default_capability_matrix,
    get_matrix_for_role,
    role_from_user,
)

router = APIRouter(prefix="/api/system/capability-matrix", tags=["system"])


def _validate_matrix_shape(candidate: dict, defaults: dict) -> dict:
    merged = deepcopy(defaults)
    if not isinstance(candidate, dict):
        return merged
    for section in ("pages", "features", "voice_intents", "ui_action_classes"):
        incoming = candidate.get(section)
        if not isinstance(incoming, dict):
            continue
        if section == "features":
            for feature, actions in incoming.items():
                if feature not in merged["features"] or not isinstance(actions, dict):
                    continue
                for action, allowed in actions.items():
                    if action in merged["features"][feature]:
                        merged["features"][feature][action] = bool(allowed)
        else:
            for key, allowed in incoming.items():
                if key in merged[section]:
                    merged[section][key] = bool(allowed)
    return merged


@router.get("/me")
async def get_my_capability_matrix(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    role = role_from_user(user)
    matrix = await get_matrix_for_role(db, role)
    return {"role": role, "matrix": matrix}


@router.get("")
async def get_capability_matrix(
    admin: dict = Depends(require_admin),
    db=Depends(get_db),
):
    _ = admin
    defaults = default_capability_matrix()
    rows = {}
    cursor = await db.execute("SELECT role, matrix_json, updated_by, updated_at FROM role_capability_matrix")
    for row in await cursor.fetchall():
        role = row["role"]
        if role not in FIXED_ROLES:
            continue
        try:
            parsed = json.loads(row["matrix_json"] or "{}")
        except Exception:
            parsed = {}
        rows[role] = {
            "role": role,
            "matrix": _validate_matrix_shape(parsed, defaults[role]),
            "updated_by": row["updated_by"],
            "updated_at": row["updated_at"],
        }
    for role in FIXED_ROLES:
        if role not in rows:
            rows[role] = {
                "role": role,
                "matrix": deepcopy(defaults[role]),
                "updated_by": None,
                "updated_at": None,
            }
    return {"roles": [rows[r] for r in FIXED_ROLES]}


@router.put("/{role}")
async def update_capability_matrix_role(
    role: str,
    payload: dict,
    admin: dict = Depends(require_admin),
    db=Depends(get_db),
):
    if role not in FIXED_ROLES:
        raise HTTPException(status_code=404, detail="Unknown role")
    matrix = payload.get("matrix")
    if not isinstance(matrix, dict):
        raise HTTPException(status_code=400, detail="Expected {matrix: {...}}")
    defaults = default_capability_matrix()
    safe = _validate_matrix_shape(matrix, defaults[role])
    await db.execute(
        """INSERT INTO role_capability_matrix (role, matrix_json, updated_by, updated_at)
           VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(role) DO UPDATE SET
              matrix_json=excluded.matrix_json,
              updated_by=excluded.updated_by,
              updated_at=datetime('now')""",
        (role, json.dumps(safe), admin.get("user_id")),
    )
    await db.commit()
    return {"ok": True, "role": role, "matrix": safe}


@router.post("/reset-defaults")
async def reset_capability_matrix_defaults(
    admin: dict = Depends(require_admin),
    db=Depends(get_db),
):
    defaults = default_capability_matrix()
    for role in FIXED_ROLES:
        await db.execute(
            """INSERT INTO role_capability_matrix (role, matrix_json, updated_by, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(role) DO UPDATE SET
                  matrix_json=excluded.matrix_json,
                  updated_by=excluded.updated_by,
                  updated_at=datetime('now')""",
            (role, json.dumps(defaults[role]), admin.get("user_id")),
        )
    await db.commit()
    return {"ok": True, "roles": list(FIXED_ROLES)}
