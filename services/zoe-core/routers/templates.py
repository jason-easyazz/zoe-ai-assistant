"""
Template Management System
Handles template storage, retrieval, and page generation
"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import sqlite3
import json
import os
import re
from pathlib import Path

router = APIRouter(prefix="/api/templates", tags=["templates"])

# Paths
TEMPLATE_DIR = Path("/app/templates")
GENERATED_DIR = Path("/app/services/zoe-ui/dist")
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

# Ensure directories exist
TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
for subdir in ["main-ui", "developer-ui", "components", "layouts"]:
    (TEMPLATE_DIR / subdir).mkdir(exist_ok=True)

def init_templates_db():
    """Initialize templates database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            description TEXT,
            placeholders JSON,
            usage_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS generated_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_name TEXT NOT NULL,
            template_used TEXT,
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            replacements JSON
        )
    """)
    
    conn.commit()
    conn.close()

init_templates_db()

# Models
class TemplateCreate(BaseModel):
    name: str
    category: str  # main-ui, developer-ui, components, layouts
    content: str
    description: Optional[str] = ""

class PageGenerate(BaseModel):
    template_name: str
    page_name: str
    replacements: Dict[str, str]

class TemplateResponse(BaseModel):
    id: int
    name: str
    category: str
    description: str
    placeholders: List[str]
    usage_count: int

@router.get("/")
async def list_templates(category: Optional[str] = None):
    """List all templates or filter by category"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    if category:
        cursor.execute("""
            SELECT id, name, category, description, placeholders, usage_count
            FROM templates WHERE category = ?
            ORDER BY usage_count DESC, name
        """, (category,))
    else:
        cursor.execute("""
            SELECT id, name, category, description, placeholders, usage_count
            FROM templates
            ORDER BY category, usage_count DESC, name
        """)
    
    templates = []
    for row in cursor.fetchall():
        templates.append({
            "id": row[0],
            "name": row[1],
            "category": row[2],
            "description": row[3],
            "placeholders": json.loads(row[4]) if row[4] else [],
            "usage_count": row[5]
        })
    
    conn.close()
    return {"templates": templates, "count": len(templates)}

@router.get("/{template_name}")
async def get_template(template_name: str):
    """Get a specific template"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM templates WHERE name = ?
    """, (template_name,))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Template not found")
    
    return {
        "id": row[0],
        "name": row[1],
        "category": row[2],
        "content": row[3],
        "description": row[4],
        "placeholders": json.loads(row[5]) if row[5] else [],
        "usage_count": row[6]
    }

@router.post("/upload")
async def upload_template(
    file: UploadFile = File(...),
    category: str = Form(...),
    description: str = Form("")
):
    """Upload a new template file"""
    # Read file content
    content = await file.read()
    content_str = content.decode('utf-8')
    
    # Extract placeholders ({{PLACEHOLDER_NAME}})
    placeholders = list(set(re.findall(r'\{\{([A-Z_]+)\}\}', content_str)))
    
    # Extract name from filename
    name = file.filename.replace('.html', '').replace('.template', '')
    
    # Save to database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO templates (name, category, content, description, placeholders)
            VALUES (?, ?, ?, ?, ?)
        """, (name, category, content_str, description, json.dumps(placeholders)))
        
        template_id = cursor.lastrowid
        conn.commit()
        
        # Also save to file system
        file_path = TEMPLATE_DIR / category / f"{name}.html"
        file_path.write_text(content_str)
        
    except sqlite3.IntegrityError:
        # Update existing template
        cursor.execute("""
            UPDATE templates 
            SET content = ?, description = ?, placeholders = ?, updated_at = CURRENT_TIMESTAMP
            WHERE name = ?
        """, (content_str, description, json.dumps(placeholders), name))
        conn.commit()
        
        # Update file
        file_path = TEMPLATE_DIR / category / f"{name}.html"
        file_path.write_text(content_str)
        
        template_id = cursor.lastrowid
    
    conn.close()
    
    return {
        "message": "Template uploaded successfully",
        "name": name,
        "category": category,
        "placeholders": placeholders
    }

@router.post("/generate")
async def generate_page(request: PageGenerate):
    """Generate a new page from a template"""
    # Get template
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT content, category FROM templates WHERE name = ?
    """, (request.template_name,))
    
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Template not found")
    
    template_content, category = row
    
    # Replace placeholders
    page_content = template_content
    for key, value in request.replacements.items():
        page_content = page_content.replace(f"{{{{{key}}}}}", value)
    
    # Clear any remaining placeholders
    page_content = re.sub(r'\{\{[A-Z_]+\}\}', '', page_content)
    
    # Determine output path based on category
    if category == "main-ui":
        output_path = GENERATED_DIR / f"{request.page_name}.html"
    elif category == "developer-ui":
        output_path = GENERATED_DIR / "developer" / f"{request.page_name}.html"
    else:
        output_path = GENERATED_DIR / "components" / f"{request.page_name}.html"
    
    # Ensure directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Save the generated page
    output_path.write_text(page_content)
    
    # Update usage count
    cursor.execute("""
        UPDATE templates 
        SET usage_count = usage_count + 1 
        WHERE name = ?
    """, (request.template_name,))
    
    # Log generation
    cursor.execute("""
        INSERT INTO generated_pages (page_name, template_used, replacements)
        VALUES (?, ?, ?)
    """, (request.page_name, request.template_name, json.dumps(request.replacements)))
    
    conn.commit()
    conn.close()
    
    return {
        "message": "Page generated successfully",
        "path": str(output_path),
        "page_name": request.page_name
    }

@router.delete("/{template_name}")
async def delete_template(template_name: str):
    """Delete a template"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get category for file deletion
    cursor.execute("SELECT category FROM templates WHERE name = ?", (template_name,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Template not found")
    
    category = row[0]
    
    # Delete from database
    cursor.execute("DELETE FROM templates WHERE name = ?", (template_name,))
    conn.commit()
    conn.close()
    
    # Delete file
    file_path = TEMPLATE_DIR / category / f"{template_name}.html"
    if file_path.exists():
        file_path.unlink()
    
    return {"message": "Template deleted", "name": template_name}

@router.get("/history/generated")
async def get_generation_history(limit: int = 50):
    """Get history of generated pages"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT page_name, template_used, generated_at, replacements
        FROM generated_pages
        ORDER BY generated_at DESC
        LIMIT ?
    """, (limit,))
    
    history = []
    for row in cursor.fetchall():
        history.append({
            "page_name": row[0],
            "template_used": row[1],
            "generated_at": row[2],
            "replacements": json.loads(row[3]) if row[3] else {}
        })
    
    conn.close()
    return {"history": history}

# For Claude/AI integration
@router.get("/ai/analyze/{page_request}")
async def analyze_page_request(page_request: str):
    """Analyze a page request and suggest template and replacements"""
    # This endpoint helps Claude understand what template to use
    
    # Simple keyword analysis (can be enhanced with AI)
    page_request_lower = page_request.lower()
    
    suggestion = {
        "suggested_template": "",
        "suggested_category": "",
        "suggested_replacements": {},
        "confidence": 0.0
    }
    
    # Determine template based on request
    if "journal" in page_request_lower or "diary" in page_request_lower:
        suggestion = {
            "suggested_template": "page-template",
            "suggested_category": "main-ui",
            "suggested_replacements": {
                "PAGE_TITLE": "Journal",
                "PAGE_CONTENT": "<div class='journal-container'><h1>Daily Journal</h1></div>",
                "PAGE_STYLES": ".journal-container { padding: 20px; }",
                "PAGE_SCRIPTS": "// Journal functionality",
                "PAGE_INIT": "loadJournalEntries();"
            },
            "confidence": 0.9
        }
    elif "monitor" in page_request_lower or "metrics" in page_request_lower:
        suggestion = {
            "suggested_template": "page-template",
            "suggested_category": "developer-ui",
            "suggested_replacements": {
                "PAGE_TITLE": "System Monitor",
                "PAGE_CONTENT": "<div class='monitor-container'></div>",
                "MONITOR_ACTIVE": "active"
            },
            "confidence": 0.85
        }
    
    return suggestion
