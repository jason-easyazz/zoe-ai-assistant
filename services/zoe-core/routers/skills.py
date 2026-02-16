"""
Skills Management Router
=========================

Phase 1a: API endpoints for managing skills.

Endpoints:
    GET  /api/skills              -- List all loaded skills
    GET  /api/skills/{name}       -- Get skill details
    POST /api/skills/{name}/approve -- Approve a deactivated skill
    POST /api/skills/reload       -- Hot-reload all skills
    GET  /api/skills/audit        -- View skill execution audit log
"""

from fastapi import APIRouter, HTTPException, Depends
from auth_integration import validate_session, AuthenticatedSession
from skills.registry import skills_registry
from skills.executor import skill_executor
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skills"])

# Initialize skills on module load
try:
    skills_registry.load()
    logger.info("Skills system initialized")
except Exception as e:
    logger.error(f"Failed to initialize skills system: {e}")


@router.get("")
async def list_skills(session: AuthenticatedSession = Depends(validate_session)):
    """List all loaded skills with their status."""
    skills = skills_registry.get_all_skills()
    return {
        "skills": [
            {
                "name": s.name,
                "description": s.description,
                "version": s.version,
                "author": s.author,
                "source": s.source,
                "active": s.active,
                "triggers": s.triggers,
                "allowed_endpoints": s.allowed_endpoints,
                "tags": s.tags,
            }
            for s in skills
        ],
        "count": len(skills),
        "active": sum(1 for s in skills if s.active),
    }


@router.get("/{name}")
async def get_skill(name: str, session: AuthenticatedSession = Depends(validate_session)):
    """Get detailed information about a specific skill."""
    skill = skills_registry.get_skill(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    return {
        "name": skill.name,
        "description": skill.description,
        "version": skill.version,
        "author": skill.author,
        "source": skill.source,
        "active": skill.active,
        "triggers": skill.triggers,
        "allowed_endpoints": skill.allowed_endpoints,
        "instructions": skill.instructions,
        "tags": skill.tags,
        "sha256": skill.sha256[:16] + "...",
        "file_path": skill.file_path,
    }


@router.post("/{name}/approve")
async def approve_skill(name: str, session: AuthenticatedSession = Depends(validate_session)):
    """Approve a deactivated skill (after hash change or new addition)."""
    success = skills_registry.approve_skill(name)
    if not success:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    return {"success": True, "message": f"Skill '{name}' approved and activated"}


@router.post("/reload")
async def reload_skills(session: AuthenticatedSession = Depends(validate_session)):
    """Hot-reload all skills from disk."""
    skills_registry.load()
    skills = skills_registry.get_all_skills()
    return {
        "success": True,
        "skills_loaded": len(skills),
        "active": sum(1 for s in skills if s.active),
        "deactivated": sum(1 for s in skills if not s.active),
    }


@router.get("/audit/calls")
async def get_skill_audit(session: AuthenticatedSession = Depends(validate_session)):
    """View recent skill API call audit log."""
    return {
        "calls": skill_executor.get_call_log(),
        "count": len(skill_executor.get_call_log()),
    }
