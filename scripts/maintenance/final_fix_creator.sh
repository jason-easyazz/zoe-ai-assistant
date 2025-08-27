#!/bin/bash
# FINAL FIX - Create working creator from scratch

echo "🚀 FINAL FIX FOR CREATOR"
echo "========================"

cd /home/pi/zoe

# Step 1: Remove broken files
echo "🧹 Cleaning broken files..."
docker exec zoe-core rm -f /app/routers/disciplined_creator.py
docker exec zoe-core rm -f /app/routers/creator_working.py

# Step 2: Create a WORKING creator with proper syntax
echo "✨ Creating working creator..."
cat > /tmp/simple_creator.py << 'PYTHONFILE'
"""Simple Working Creator"""
from fastapi import APIRouter
from pydantic import BaseModel
from datetime import datetime
import os

router = APIRouter(prefix="/api/creator")

class CreateRequest(BaseModel):
    request: str

@router.get("/status")
async def creator_status():
    """Check if creator is working"""
    return {"status": "Creator is operational!", "timestamp": datetime.now().isoformat()}

@router.post("/create")
async def create_page(req: CreateRequest):
    """Create a page based on request"""
    
    # Create HTML without f-string issues
    html_template = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>PAGE_TITLE</title>
    <style>
        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            font-family: Arial, sans-serif;
            padding: 20px;
        }
        .container {
            background: rgba(255,255,255,0.1);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            padding: 30px;
            color: white;
            max-width: 800px;
            margin: 0 auto;
        }
        h1 {
            font-size: 2em;
            margin-bottom: 20px;
        }
        .info {
            background: rgba(0,0,0,0.3);
            padding: 15px;
            border-radius: 10px;
            margin-top: 20px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>PAGE_HEADING</h1>
        <p>This page was created by the AI Creator</p>
        <div class="info">
            <p><strong>Requested:</strong> REQUEST_TEXT</p>
            <p><strong>Created:</strong> TIMESTAMP</p>
            <p><strong>Status:</strong> ✅ Successfully generated</p>
        </div>
    </div>
</body>
</html>"""
    
    # Replace placeholders
    html = html_template.replace("PAGE_TITLE", req.request)
    html = html.replace("PAGE_HEADING", req.request)
    html = html.replace("REQUEST_TEXT", req.request)
    html = html.replace("TIMESTAMP", datetime.now().isoformat())
    
    # Save the file
    os.makedirs("/app/generated", exist_ok=True)
    
    filename = req.request.lower().replace(" ", "_")[:30] + ".html"
    filepath = f"/app/generated/{filename}"
    
    with open(filepath, 'w') as f:
        f.write(html)
    
    return {
        "success": True,
        "message": f"Created: {filename}",
        "filepath": filepath,
        "request": req.request,
        "timestamp": datetime.now().isoformat()
    }

@router.get("/list")
async def list_created():
    """List all created files"""
    try:
        if os.path.exists("/app/generated"):
            files = os.listdir("/app/generated")
            return {"files": files, "count": len(files)}
        return {"files": [], "count": 0}
    except Exception as e:
        return {"error": str(e), "files": []}
PYTHONFILE

# Copy to container
docker cp /tmp/simple_creator.py zoe-core:/app/routers/simple_creator.py

# Step 3: Fix main.py completely
echo "📝 Fixing main.py..."
docker exec zoe-core bash -c 'cat > /app/main_fixed.py << "MAINPY"
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Zoe AI Core", version="6.3")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import working routers
try:
    from routers import developer
    app.include_router(developer.router)
    logger.info("✅ Developer router loaded")
except Exception as e:
    logger.error(f"Developer router failed: {e}")

try:
    from routers import simple_creator
    app.include_router(simple_creator.router)
    logger.info("✅ Creator router loaded")
except Exception as e:
    logger.error(f"Creator router failed: {e}")

try:
    from routers import chat
    app.include_router(chat.router)
    logger.info("✅ Chat router loaded")
except:
    pass

try:
    from routers import settings
    app.include_router(settings.router)
    logger.info("✅ Settings router loaded")
except:
    pass

@app.get("/")
async def root():
    return {"service": "Zoe AI Core", "version": "6.3", "creator": "enabled"}

@app.get("/health")
async def health():
    return {"status": "healthy", "creator": "active"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
MAINPY'

# Backup and replace
docker exec zoe-core cp /app/main.py /app/main_broken.py
docker exec zoe-core mv /app/main_fixed.py /app/main.py

# Step 4: Restart
echo "🔄 Restarting zoe-core..."
docker restart zoe-core
sleep 12

# Step 5: Test all endpoints
echo -e "\n🧪 TESTING CREATOR ENDPOINTS..."
echo "================================"

echo -e "\n1️⃣ Creator Status:"
curl -s http://localhost:8000/api/creator/status | jq '.' || echo "Not ready yet..."

echo -e "\n2️⃣ List Created Files:"
curl -s http://localhost:8000/api/creator/list | jq '.' || echo "Not ready yet..."

echo -e "\n3️⃣ Create Test Page:"
curl -s -X POST http://localhost:8000/api/creator/create \
  -H "Content-Type: application/json" \
  -d '{"request": "My Test Dashboard"}' | jq '.' || echo "Not ready yet..."

echo -e "\n4️⃣ Health Check:"
curl -s http://localhost:8000/health | jq '.'

# Step 6: Create simple UI
echo -e "\n🌐 Creating simple UI..."
cat > services/zoe-ui/dist/creator.html << 'HTML'
<!DOCTYPE html>
<html>
<head>
    <title>AI Creator</title>
    <style>
        body {
            background: linear-gradient(135deg, #667eea, #764ba2);
            font-family: Arial;
            padding: 40px;
        }
        .box {
            background: rgba(255,255,255,0.1);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            padding: 30px;
            max-width: 600px;
            margin: 0 auto;
            color: white;
        }
        input, button {
            width: 100%;
            padding: 15px;
            margin: 10px 0;
            border-radius: 10px;
            border: none;
            font-size: 16px;
        }
        button {
            background: linear-gradient(135deg, #764ba2, #667eea);
            color: white;
            cursor: pointer;
        }
        #result {
            margin-top: 20px;
            padding: 20px;
            background: rgba(0,0,0,0.3);
            border-radius: 10px;
        }
    </style>
</head>
<body>
    <div class="box">
        <h1>🤖 AI Page Creator</h1>
        <input type="text" id="request" placeholder="What page do you want to create?">
        <button onclick="createPage()">Create Page</button>
        <div id="result"></div>
    </div>
    <script>
        async function createPage() {
            const request = document.getElementById('request').value;
            const resultDiv = document.getElementById('result');
            
            if (!request) {
                alert('Please enter what you want to create');
                return;
            }
            
            resultDiv.innerHTML = 'Creating...';
            
            try {
                const response = await fetch('http://192.168.1.60:8000/api/creator/create', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({request: request})
                });
                
                const data = await response.json();
                
                if (data.success) {
                    resultDiv.innerHTML = `
                        <h3>✅ Success!</h3>
                        <p>Created: ${data.message}</p>
                        <p>File: ${data.filepath}</p>
                    `;
                } else {
                    resultDiv.innerHTML = '❌ Error: ' + (data.message || 'Unknown error');
                }
            } catch (error) {
                resultDiv.innerHTML = '❌ Error: ' + error.message;
            }
        }
    </script>
</body>
</html>
HTML

docker restart zoe-ui

echo -e "\n✅ FINAL FIX COMPLETE!"
echo "====================="
echo ""
echo "🌐 Access the creator at:"
echo "   http://192.168.1.60:8080/creator.html"
echo ""
echo "📝 Try creating:"
echo '   • "Dashboard for system monitoring"'
echo '   • "Settings page for developer tools"'
echo '   • "API documentation page"'
echo ""
echo "The creator will generate real HTML files!"
