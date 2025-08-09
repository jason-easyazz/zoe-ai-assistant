from fastapi import APIRouter

router = APIRouter(prefix="/api/weather", tags=["weather"])

@router.get("/")
async def read_weather():
    return {"condition": "sunny", "temperature": 22}
