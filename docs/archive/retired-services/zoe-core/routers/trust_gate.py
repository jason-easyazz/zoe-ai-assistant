"""
Trust Gate API Router
======================

Phase 0: API endpoints for managing the Trust Gate allowlist and
viewing audit logs.

Endpoints:
    GET  /api/trust-gate/allowlist          -- List user's trusted contacts
    POST /api/trust-gate/allowlist          -- Add a trusted contact
    DELETE /api/trust-gate/allowlist/{id}   -- Remove a trusted contact
    PUT  /api/trust-gate/allowlist/{id}/permissions -- Update permissions
    GET  /api/trust-gate/audit              -- View Trust Gate decisions
    POST /api/trust-gate/evaluate           -- Test a trust evaluation (dev)
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from auth_integration import validate_session, AuthenticatedSession
from security.allowlist import (
    add_contact, remove_contact, list_contacts, update_permissions,
    VALID_CONTACT_TYPES, VALID_PERMISSIONS,
)
from security.trust_gate import trust_gate
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trust-gate", tags=["trust-gate"])


# ---- Request Models ----

class AddContactRequest(BaseModel):
    contact_type: str       # phone, email, telegram_id, etc.
    contact_value: str      # The identifier
    label: str = ""         # Human-friendly name
    permissions: Optional[List[str]] = None  # Default: ["all"]


class UpdatePermissionsRequest(BaseModel):
    permissions: List[str]


class EvaluateRequest(BaseModel):
    source_type: str
    source_value: str
    channel: str = "web"
    content: str = ""
    requested_action: Optional[str] = None


# ---- Allowlist Endpoints ----

@router.get("/allowlist")
async def get_allowlist(session: AuthenticatedSession = Depends(validate_session)):
    """List all trusted contacts on the user's allowlist."""
    entries = list_contacts(session.user_id)
    return {
        "allowlist": entries,
        "count": len(entries),
        "valid_contact_types": sorted(VALID_CONTACT_TYPES),
        "valid_permissions": sorted(VALID_PERMISSIONS),
    }


@router.post("/allowlist")
async def add_to_allowlist(
    request: AddContactRequest,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Add a trusted contact to the user's allowlist."""
    result = add_contact(
        user_id=session.user_id,
        contact_type=request.contact_type,
        contact_value=request.contact_value,
        label=request.label,
        permissions=request.permissions,
    )

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])

    return result


@router.delete("/allowlist/{entry_id}")
async def remove_from_allowlist(
    entry_id: int,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Remove a contact from the user's allowlist (soft delete)."""
    result = remove_contact(session.user_id, entry_id)

    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["error"])

    return result


@router.put("/allowlist/{entry_id}/permissions")
async def update_contact_permissions(
    entry_id: int,
    request: UpdatePermissionsRequest,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Update the permissions for an allowlist entry."""
    result = update_permissions(session.user_id, entry_id, request.permissions)

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])

    return result


# ---- Audit Endpoint ----

@router.get("/audit")
async def get_audit_log(
    limit: int = Query(50, le=200, description="Max entries to return"),
    decision: Optional[str] = Query(None, description="Filter: ALLOWED or BLOCKED"),
    session: AuthenticatedSession = Depends(validate_session),
):
    """View recent Trust Gate decisions.

    Shows what the Trust Gate allowed or blocked, helping users
    identify false positives and approve legitimate contacts.
    """
    entries = trust_gate.get_audit_log(
        user_id=session.user_id,
        limit=limit,
        decision_filter=decision,
    )
    return {
        "decisions": entries,
        "count": len(entries),
        "user_id": session.user_id,
    }


# ---- Dev/Test Endpoint ----

@router.post("/evaluate")
async def evaluate_trust(
    request: EvaluateRequest,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Test a trust evaluation without processing content.

    Useful for testing whether a contact would be allowed or blocked.
    """
    decision = trust_gate.evaluate(
        user_id=session.user_id,
        source_type=request.source_type,
        source_value=request.source_value,
        channel=request.channel,
        content=request.content,
        requested_action=request.requested_action,
    )

    return {
        "mode": decision.mode,
        "allowed": decision.allowed,
        "permissions": decision.permissions,
        "label": decision.label,
        "reason": decision.reason,
    }
