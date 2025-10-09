"""Simple redirect handler for /api/chat"""
from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter()

@router.post("/api/chat")
async def redirect_to_slash():
    """Redirect /api/chat to /api/chat/"""
    return RedirectResponse(url="/api/chat/", status_code=308)
