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
