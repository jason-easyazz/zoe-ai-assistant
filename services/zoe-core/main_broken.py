from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

app = FastAPI(title="Zoe AI", version="6.2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import routers one by one
try:
    from routers import developer
    app.include_router(developer.router)
    print("✅ Developer loaded")
except Exception as e:
    print(f"❌ Developer failed: {e}")

try:
    from routers import creator_working
    app.include_router(creator_working.router)
    print("✅ Creator loaded")
except Exception as e:
    print(f"❌ Creator failed: {e}")

try:
    from routers import chat
    app.include_router(chat.router)
except:
    pass

try:
    from routers import settings
    app.include_router(settings.router)
except:
    pass

@app.get("/")
async def root():
    return {"service": "Zoe AI", "version": "6.2"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
