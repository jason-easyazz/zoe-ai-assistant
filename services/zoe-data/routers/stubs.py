"""
Stub routers for frontend endpoints that don't have full implementations yet.
Returns empty/default data so the frontend doesn't get 404 errors.
"""
from fastapi import APIRouter, Depends
from auth import get_current_user

router = APIRouter(tags=["stubs"])


@router.get("/api/projects")
@router.get("/api/projects/")
async def list_projects(user: dict = Depends(get_current_user)):
    return {"projects": [], "count": 0}


@router.post("/api/projects")
@router.post("/api/projects/")
async def create_project(user: dict = Depends(get_current_user)):
    return {"error": "Projects feature coming soon", "status": "not_implemented"}


@router.get("/api/collections")
@router.get("/api/collections/")
async def list_collections(user: dict = Depends(get_current_user)):
    return {"collections": [], "count": 0}


@router.get("/api/collections/{collection_id}/tiles")
async def get_collection_tiles(collection_id: str, user: dict = Depends(get_current_user)):
    return {"tiles": [], "count": 0}


@router.get("/api/user/layout")
async def get_user_layout(user: dict = Depends(get_current_user)):
    return {"layout": None}


@router.post("/api/user/layout")
async def save_user_layout(user: dict = Depends(get_current_user)):
    return {"status": "ok"}


