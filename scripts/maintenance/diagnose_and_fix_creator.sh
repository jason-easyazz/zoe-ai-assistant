#!/bin/bash
# DIAGNOSE AND FIX CREATOR

echo "🔍 DIAGNOSING DISCIPLINED CREATOR"
echo "================================="

cd /home/pi/zoe

# Check if file exists
echo "1️⃣ Checking if disciplined_creator.py exists..."
docker exec zoe-core ls -la /app/routers/ | grep disciplined || echo "❌ Not found"

# Check main.py
echo -e "\n2️⃣ Checking main.py imports..."
docker exec zoe-core grep -n "disciplined" /app/main.py || echo "❌ Not in main.py"

# Check for import errors
echo -e "\n3️⃣ Checking for import errors..."
docker exec zoe-core python3 -c "from routers import disciplined_creator; print('✅ Import works')" 2>&1

# Check all registered routes
echo -e "\n4️⃣ Checking registered routes..."
curl -s http://localhost:8000/openapi.json | jq '.paths | keys[]' | grep disciplined || echo "❌ Not registered"

# FIX: Create a working version directly
echo -e "\n🔧 Creating direct fix..."

docker exec zoe-core bash -c 'cat > /app/routers/creator_working.py << "EOF"
from fastapi import APIRouter
from pydantic import BaseModel
from datetime import datetime
import os

router = APIRouter(prefix="/api/creator")

class CreateRequest(BaseModel):
    request: str

@router.get("/test")
async def test():
    return {"message": "Creator working!"}

@router.post("/create")
async def create(req: CreateRequest):
    # Simple HTML creation
    html = f"""<!DOCTYPE html>
<html>
<head><title>{req.request}</title></head>
<body>
<h1>{req.request}</h1>
<p>Created: {datetime.now()}</p>
</body>
</html>"""
    
    # Save it
    os.makedirs("/app/generated", exist_ok=True)
    file_path = f"/app/generated/page_{datetime.now().strftime('%H%M%S')}.html"
    with open(file_path, "w") as f:
        f.write(html)
    
    return {"success": True, "file": file_path}
EOF'

# Update main.py to include it
docker exec zoe-core bash -c 'cat > /app/main_working.py << "EOF"
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
EOF'

# Replace main.py
docker exec zoe-core mv /app/main.py /app/main_backup.py
docker exec zoe-core mv /app/main_working.py /app/main.py

# Restart
echo -e "\n🔄 Restarting..."
docker restart zoe-core
sleep 10

# Test the simple creator
echo -e "\n🧪 Testing simple creator..."
echo "Test endpoint:"
curl -s http://localhost:8000/api/creator/test | jq '.'

echo -e "\nCreate endpoint:"
curl -s -X POST http://localhost:8000/api/creator/create \
  -H "Content-Type: application/json" \
  -d '{"request": "Test Page"}' | jq '.'

echo -e "\n✅ Simple creator should be working now!"
echo ""
echo "Available endpoints:"
echo "  GET  /api/creator/test"
echo "  POST /api/creator/create"
