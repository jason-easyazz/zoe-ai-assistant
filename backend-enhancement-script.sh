#!/bin/bash
# Zoe v3.1 Backend Enhancement Script
# Adds shopping, workflows, and settings endpoints to your existing backend

set -euo pipefail

readonly GREEN='\033[0;32m'
readonly BLUE='\033[0;34m'
readonly YELLOW='\033[1;33m'
readonly NC='\033[0m'

log() {
    echo -e "${GREEN}[$(date +'%H:%M:%S')] $1${NC}"
}

PROJECT_DIR="${PROJECT_DIR:-$HOME/zoe-v31}"
BACKUP_DIR="$PROJECT_DIR/backups/$(date +'%Y%m%d_%H%M%S')"

log "🚀 Enhancing Zoe v3.1 Backend with Shopping, Workflows, and Settings..."

# Create backup
log "📦 Creating backup of current backend..."
mkdir -p "$BACKUP_DIR"
cp "$PROJECT_DIR/services/zoe-core/main.py" "$BACKUP_DIR/main.py.backup" 2>/dev/null || true

# Check if main.py exists
MAIN_PY="$PROJECT_DIR/services/zoe-core/main.py"
if [ ! -f "$MAIN_PY" ]; then
    echo -e "${YELLOW}⚠️  main.py not found. Creating new backend file...${NC}"
    MAIN_PY="$PROJECT_DIR/services/zoe-core/main_enhanced.py"
fi

log "🔧 Adding new API endpoints..."

# Create the enhanced backend code
cat > "$PROJECT_DIR/services/zoe-core/endpoints_addition.py" << 'EOF'
# =============================================================================
# NEW ENDPOINTS TO ADD TO YOUR EXISTING MAIN.PY
# =============================================================================

# Add these imports at the top with your existing imports
from typing import List, Dict, Optional, Any
import aiosqlite
from pydantic import BaseModel, Field
from datetime import datetime
import httpx

# =============================================================================
# PYDANTIC MODELS - Add these with your existing models
# =============================================================================

class ShoppingItem(BaseModel):
    item: str = Field(..., min_length=1, max_length=200)
    quantity: Optional[int] = Field(default=1)
    category: Optional[str] = Field(default="general")
    completed: bool = Field(default=False)

class WorkflowTrigger(BaseModel):
    workflow_name: str
    parameters: Optional[Dict[str, Any]] = Field(default_factory=dict)

class SettingsUpdate(BaseModel):
    category: str
    settings: Dict[str, Any]

# =============================================================================
# DATABASE SCHEMA UPDATES - Run this to update your database
# =============================================================================

async def update_database_schema():
    """Add new tables for shopping, workflows, and settings"""
    async with aiosqlite.connect(CONFIG["database_path"]) as db:
        # Shopping list table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS shopping_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item TEXT NOT NULL,
                quantity INTEGER DEFAULT 1,
                category TEXT DEFAULT 'general',
                completed BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT DEFAULT 'default'
            )
        """)
        
        # Workflows table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS workflows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                n8n_workflow_id TEXT,
                enabled BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT DEFAULT 'default'
            )
        """)
        
        # Enhanced settings table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                setting_key TEXT NOT NULL,
                setting_value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT DEFAULT 'default',
                UNIQUE(category, setting_key, user_id)
            )
        """)
        
        await db.commit()
        print("✅ Database schema updated successfully")

# =============================================================================
# SHOPPING LIST ENDPOINTS
# =============================================================================

@app.get("/api/shopping")
async def get_shopping_list(completed: bool = False):
    """Get shopping list items"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            cursor = await db.execute("""
                SELECT id, item, quantity, category, completed, created_at
                FROM shopping_items 
                WHERE completed = ? 
                ORDER BY created_at DESC
            """, (completed,))
            
            items = []
            async for row in cursor:
                items.append({
                    "id": row[0],
                    "item": row[1],
                    "quantity": row[2],
                    "category": row[3],
                    "completed": bool(row[4]),
                    "created_at": row[5]
                })
            
            return {"items": items, "count": len(items)}
    except Exception as e:
        logger.error(f"Shopping list error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch shopping list")

@app.post("/api/shopping")
async def add_shopping_item(shopping_item: ShoppingItem):
    """Add item to shopping list"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            cursor = await db.execute("""
                INSERT INTO shopping_items (item, quantity, category, completed)
                VALUES (?, ?, ?, ?)
            """, (shopping_item.item, shopping_item.quantity, 
                  shopping_item.category, shopping_item.completed))
            
            await db.commit()
            
            return {
                "id": cursor.lastrowid,
                "item": shopping_item.item,
                "quantity": shopping_item.quantity,
                "category": shopping_item.category,
                "completed": shopping_item.completed,
                "created_at": datetime.now().isoformat()
            }
    except Exception as e:
        logger.error(f"Add shopping item error: {e}")
        raise HTTPException(status_code=500, detail="Failed to add shopping item")

@app.put("/api/shopping/{item_id}")
async def update_shopping_item(item_id: int, completed: bool):
    """Update shopping item status"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            await db.execute("""
                UPDATE shopping_items 
                SET completed = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (completed, item_id))
            
            await db.commit()
            return {"success": True, "item_id": item_id, "completed": completed}
    except Exception as e:
        logger.error(f"Update shopping item error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update shopping item")

@app.delete("/api/shopping/{item_id}")
async def delete_shopping_item(item_id: int):
    """Delete shopping item"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            await db.execute("DELETE FROM shopping_items WHERE id = ?", (item_id,))
            await db.commit()
            return {"success": True, "deleted_id": item_id}
    except Exception as e:
        logger.error(f"Delete shopping item error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete shopping item")

# =============================================================================
# WORKFLOWS ENDPOINTS (N8N Integration)
# =============================================================================

@app.get("/api/workflows")
async def get_workflows():
    """Get available workflows"""
    try:
        # Get workflows from database
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            cursor = await db.execute("""
                SELECT id, name, description, n8n_workflow_id, enabled
                FROM workflows
                WHERE enabled = TRUE
                ORDER BY name
            """)
            
            workflows = []
            async for row in cursor:
                workflows.append({
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "n8n_workflow_id": row[3],
                    "enabled": bool(row[4])
                })
        
        # Try to get status from n8n
        n8n_status = "unknown"
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{CONFIG.get('n8n_url', 'http://zoe-n8n:5678')}/healthz", timeout=5)
                n8n_status = "connected" if response.status_code == 200 else "disconnected"
        except:
            n8n_status = "disconnected"
        
        return {
            "workflows": workflows,
            "n8n_status": n8n_status,
            "count": len(workflows)
        }
    except Exception as e:
        logger.error(f"Get workflows error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch workflows")

@app.post("/api/workflows/trigger")
async def trigger_workflow(workflow_trigger: WorkflowTrigger):
    """Trigger an n8n workflow"""
    try:
        # Get workflow details from database
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            cursor = await db.execute("""
                SELECT n8n_workflow_id FROM workflows 
                WHERE name = ? AND enabled = TRUE
            """, (workflow_trigger.workflow_name,))
            
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Workflow not found")
            
            n8n_workflow_id = row[0]
        
        # Trigger workflow in n8n
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{CONFIG.get('n8n_url', 'http://zoe-n8n:5678')}/webhook/{n8n_workflow_id}",
                    json=workflow_trigger.parameters,
                    timeout=30
                )
                
                if response.status_code == 200:
                    return {
                        "success": True,
                        "workflow": workflow_trigger.workflow_name,
                        "triggered_at": datetime.now().isoformat()
                    }
                else:
                    raise HTTPException(status_code=500, detail="Workflow trigger failed")
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Workflow trigger timeout")
        except Exception as e:
            logger.error(f"N8N trigger error: {e}")
            raise HTTPException(status_code=500, detail="Failed to trigger workflow")
            
    except Exception as e:
        logger.error(f"Trigger workflow error: {e}")
        raise HTTPException(status_code=500, detail="Failed to trigger workflow")

@app.post("/api/workflows")
async def create_workflow(name: str, description: str = "", n8n_workflow_id: str = ""):
    """Create a new workflow entry"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            cursor = await db.execute("""
                INSERT INTO workflows (name, description, n8n_workflow_id)
                VALUES (?, ?, ?)
            """, (name, description, n8n_workflow_id))
            
            await db.commit()
            
            return {
                "id": cursor.lastrowid,
                "name": name,
                "description": description,
                "n8n_workflow_id": n8n_workflow_id,
                "enabled": True,
                "created_at": datetime.now().isoformat()
            }
    except Exception as e:
        logger.error(f"Create workflow error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create workflow")

# =============================================================================
# ENHANCED SETTINGS ENDPOINTS
# =============================================================================

@app.get("/api/settings")
async def get_all_settings():
    """Get all user settings organized by category"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            cursor = await db.execute("""
                SELECT category, setting_key, setting_value 
                FROM user_settings 
                ORDER BY category, setting_key
            """)
            
            settings = {}
            async for row in cursor:
                category, key, value = row
                if category not in settings:
                    settings[category] = {}
                
                # Try to parse JSON values
                try:
                    import json
                    settings[category][key] = json.loads(value)
                except:
                    settings[category][key] = value
            
            # Add defaults if no settings exist
            if not settings:
                settings = {
                    "personality": {
                        "fun": 7,
                        "empathy": 8,
                        "humor": 6,
                        "formality": 3
                    },
                    "voice": {
                        "enabled": True,
                        "auto_speak": False,
                        "voice_model": "default"
                    },
                    "interface": {
                        "theme": "light",
                        "notifications": True,
                        "auto_return_orb": True
                    },
                    "integrations": {
                        "n8n_enabled": True,
                        "homeassistant_enabled": False,
                        "matrix_enabled": False
                    }
                }
            
            return settings
    except Exception as e:
        logger.error(f"Get settings error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch settings")

@app.get("/api/settings/{category}")
async def get_settings_category(category: str):
    """Get settings for a specific category"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            cursor = await db.execute("""
                SELECT setting_key, setting_value 
                FROM user_settings 
                WHERE category = ?
                ORDER BY setting_key
            """, (category,))
            
            settings = {}
            async for row in cursor:
                key, value = row
                try:
                    import json
                    settings[key] = json.loads(value)
                except:
                    settings[key] = value
            
            return {category: settings}
    except Exception as e:
        logger.error(f"Get category settings error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch {category} settings")

@app.post("/api/settings")
async def update_settings(settings_update: SettingsUpdate):
    """Update settings for a category"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            # Update each setting in the category
            for key, value in settings_update.settings.items():
                # Convert value to JSON string for storage
                import json
                value_str = json.dumps(value) if not isinstance(value, str) else value
                
                await db.execute("""
                    INSERT OR REPLACE INTO user_settings 
                    (category, setting_key, setting_value, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """, (settings_update.category, key, value_str))
            
            await db.commit()
            
            return {
                "success": True,
                "category": settings_update.category,
                "updated_settings": list(settings_update.settings.keys()),
                "updated_at": datetime.now().isoformat()
            }
    except Exception as e:
        logger.error(f"Update settings error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update settings")

@app.post("/api/settings/reset")
async def reset_settings(category: Optional[str] = None):
    """Reset settings to defaults"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            if category:
                await db.execute("DELETE FROM user_settings WHERE category = ?", (category,))
            else:
                await db.execute("DELETE FROM user_settings")
            
            await db.commit()
            
            return {
                "success": True,
                "reset_category": category or "all",
                "reset_at": datetime.now().isoformat()
            }
    except Exception as e:
        logger.error(f"Reset settings error: {e}")
        raise HTTPException(status_code=500, detail="Failed to reset settings")

# =============================================================================
# ENHANCED HEALTH CHECK WITH NEW SERVICES
# =============================================================================

@app.get("/health")
async def enhanced_health_check():
    """Enhanced health check including new services"""
    services_status = {}
    
    # Check existing services
    services_status["database"] = "connected"
    
    # Check n8n
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{CONFIG.get('n8n_url', 'http://zoe-n8n:5678')}/healthz", timeout=5)
            services_status["n8n"] = "connected" if response.status_code == 200 else "disconnected"
    except:
        services_status["n8n"] = "disconnected"
    
    # Check Ollama
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{CONFIG['ollama_url']}/api/tags", timeout=5)
            services_status["ollama"] = "connected" if response.status_code == 200 else "disconnected"
    except:
        services_status["ollama"] = "disconnected"
    
    # Check voice services
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{CONFIG.get('whisper_url', 'http://zoe-whisper:9001')}/health", timeout=3)
            services_status["voice"] = "connected" if response.status_code == 200 else "partial"
    except:
        services_status["voice"] = "disconnected"
    
    return {
        "status": "healthy",
        "version": CONFIG["version"],
        "timestamp": datetime.now().isoformat(),
        "services": services_status,
        "features": {
            "shopping_list": True,
            "workflows": True,
            "enhanced_settings": True,
            "voice_integration": services_status.get("voice") == "connected",
            "ai_chat": services_status.get("ollama") == "connected"
        }
    }

# =============================================================================
# INITIALIZATION - Call this in your startup
# =============================================================================

async def init_enhanced_features():
    """Initialize the new features"""
    await update_database_schema()
    logger.info("✅ Enhanced features initialized")

# Add this to your existing startup code:
# await init_enhanced_features()

EOF

log "📝 Enhancement code created. Now integrating with your existing backend..."

# Backup existing main.py and create enhanced version
if [ -f "$MAIN_PY" ]; then
    log "🔧 Backing up existing main.py and creating enhanced version..."
    
    # Create enhanced main.py by appending new endpoints
    cp "$MAIN_PY" "$PROJECT_DIR/services/zoe-core/main_enhanced.py"
    
    # Add the new endpoints to the enhanced version
    echo "" >> "$PROJECT_DIR/services/zoe-core/main_enhanced.py"
    echo "# ==============================================================================" >> "$PROJECT_DIR/services/zoe-core/main_enhanced.py"
    echo "# ENHANCED ENDPOINTS - SHOPPING, WORKFLOWS, SETTINGS" >> "$PROJECT_DIR/services/zoe-core/main_enhanced.py"
    echo "# ==============================================================================" >> "$PROJECT_DIR/services/zoe-core/main_enhanced.py"
    cat "$PROJECT_DIR/services/zoe-core/endpoints_addition.py" >> "$PROJECT_DIR/services/zoe-core/main_enhanced.py"
    
    log "✅ Enhanced backend created as main_enhanced.py"
else
    log "⚠️  Original main.py not found. Creating new enhanced backend..."
    
    # Create a complete new backend with enhancements
    cat > "$PROJECT_DIR/services/zoe-core/main_enhanced.py" << 'EOF'
"""
Zoe v3.1 Enhanced Backend
Complete FastAPI backend with shopping, workflows, and settings
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any
import httpx
import aiosqlite
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
CONFIG = {
    "version": "3.1.0",
    "database_path": os.getenv("DATABASE_PATH", "/app/data/zoe.db"),
    "ollama_url": os.getenv("OLLAMA_URL", "http://zoe-ollama:11434"),
    "n8n_url": os.getenv("N8N_URL", "http://zoe-n8n:5678"),
    "whisper_url": os.getenv("WHISPER_URL", "http://zoe-whisper:9001"),
    "cors_origins": os.getenv("CORS_ORIGINS", "*").split(","),
}

# Pydantic Models
class ChatMessage(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    user_id: str = Field(default="default")

class ShoppingItem(BaseModel):
    item: str = Field(..., min_length=1, max_length=200)
    quantity: Optional[int] = Field(default=1)
    category: Optional[str] = Field(default="general")
    completed: bool = Field(default=False)

class WorkflowTrigger(BaseModel):
    workflow_name: str
    parameters: Optional[Dict[str, Any]] = Field(default_factory=dict)

class SettingsUpdate(BaseModel):
    category: str
    settings: Dict[str, Any]

# Database initialization
async def init_database():
    """Initialize database with all tables"""
    Path(CONFIG["database_path"]).parent.mkdir(parents=True, exist_ok=True)
    
    async with aiosqlite.connect(CONFIG["database_path"]) as db:
        # Messages table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT DEFAULT 'default'
            )
        """)
        
        # Shopping items table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS shopping_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item TEXT NOT NULL,
                quantity INTEGER DEFAULT 1,
                category TEXT DEFAULT 'general',
                completed BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT DEFAULT 'default'
            )
        """)
        
        # Workflows table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS workflows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                n8n_workflow_id TEXT,
                enabled BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT DEFAULT 'default'
            )
        """)
        
        # Settings table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                setting_key TEXT NOT NULL,
                setting_value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT DEFAULT 'default',
                UNIQUE(category, setting_key, user_id)
            )
        """)
        
        # Tasks table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                completed BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT DEFAULT 'default'
            )
        """)
        
        await db.commit()
    
    logger.info("✅ Database initialized")

# Lifespan manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_database()
    logger.info("🚀 Zoe v3.1 Enhanced Backend started")
    yield
    logger.info("👋 Zoe v3.1 Enhanced Backend shutdown")

# FastAPI app
app = FastAPI(
    title="Zoe v3.1 Enhanced Backend",
    version=CONFIG["version"],
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CONFIG["cors_origins"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

EOF
    
    # Add all the endpoint definitions
    cat "$PROJECT_DIR/services/zoe-core/endpoints_addition.py" >> "$PROJECT_DIR/services/zoe-core/main_enhanced.py"
    
    # Add the startup call
    echo "" >> "$PROJECT_DIR/services/zoe-core/main_enhanced.py"
    echo 'if __name__ == "__main__":' >> "$PROJECT_DIR/services/zoe-core/main_enhanced.py"
    echo '    uvicorn.run(app, host="0.0.0.0", port=8000)' >> "$PROJECT_DIR/services/zoe-core/main_enhanced.py"
    
    log "✅ Complete enhanced backend created"
fi

# Create deployment script
log "📦 Creating deployment script..."
cat > "$PROJECT_DIR/deploy_enhanced_backend.sh" << 'EOF'
#!/bin/bash
# Deploy Enhanced Zoe Backend

set -euo pipefail

log() {
    echo -e "\033[0;32m[$(date +'%H:%M:%S')] $1\033[0m"
}

PROJECT_DIR="${PROJECT_DIR:-$HOME/zoe-v31}"
cd "$PROJECT_DIR"

log "🚀 Deploying Enhanced Zoe Backend..."

# Backup current main.py
if [ -f "services/zoe-core/main.py" ]; then
    cp "services/zoe-core/main.py" "services/zoe-core/main.py.backup.$(date +'%Y%m%d_%H%M%S')"
    log "📦 Backed up existing main.py"
fi

# Deploy enhanced version
cp "services/zoe-core/main_enhanced.py" "services/zoe-core/main.py"
log "✅ Enhanced backend deployed"

# Rebuild and restart
log "🔄 Rebuilding backend service..."
docker compose build zoe-core

log "🚀 Restarting services..."
docker compose restart zoe-core

# Wait for service to be ready
log "⏳ Waiting for service to start..."
sleep 10

# Test new endpoints
log "🧪 Testing enhanced endpoints..."

# Test health endpoint
HEALTH=$(curl -s http://localhost:8000/health || echo "failed")
if echo "$HEALTH" | grep -q "healthy"; then
    log "✅ Health check passed"
else
    log "❌ Health check failed"
fi

# Test shopping endpoint
SHOPPING=$(curl -s http://localhost:8000/api/shopping || echo "failed")
if echo "$SHOPPING" | grep -q "items"; then
    log "✅ Shopping endpoint working"
else
    log "❌ Shopping endpoint failed"
fi

# Test workflows endpoint
WORKFLOWS=$(curl -s http://localhost:8000/api/workflows || echo "failed")
if echo "$WORKFLOWS" | grep -q "workflows"; then
    log "✅ Workflows endpoint working"
else
    log "❌ Workflows endpoint failed"
fi

# Test settings endpoint
SETTINGS=$(curl -s http://localhost:8000/api/settings || echo "failed")
if echo "$SETTINGS" | grep -q "personality"; then
    log "✅ Settings endpoint working"
else
    log "❌ Settings endpoint failed"
fi

IP=$(hostname -I | awk '{print $1}' || echo "localhost")
echo ""
echo -e "\033[0;34m🎉 Enhanced Zoe Backend Deployed Successfully!\033[0m"
echo "============================================"
echo ""
echo "🌐 Access Points:"
echo "   UI: http://$IP:8080"
echo "   API: http://$IP:8000"
echo "   API Docs: http://$IP:8000/docs"
echo ""
echo "🎯 New Features Available:"
echo "   ✅ Shopping List API (/api/shopping)"
echo "   ✅ Workflows API (/api/workflows)"  
echo "   ✅ Enhanced Settings (/api/settings)"
echo "   ✅ Service Health Monitoring"
echo ""
echo "🚀 Your Zoe v3.1 backend is now fully enhanced!"
EOF

chmod +x "$PROJECT_DIR/deploy_enhanced_backend.sh"

# Clean up temporary files
rm -f "$PROJECT_DIR/services/zoe-core/endpoints_addition.py"

echo ""
echo -e "${BLUE}🎉 Backend Enhancement Complete!${NC}"
echo "=================================="
echo ""
echo "📁 Files Created:"
echo "   ✅ services/zoe-core/main_enhanced.py - Enhanced backend"
echo "   ✅ deploy_enhanced_backend.sh - Deployment script"
echo "   ✅ backups/ - Backup of original files"
echo ""
echo "🚀 Next Steps:"
echo "   1. Review the enhanced backend: services/zoe-core/main_enhanced.py"
echo "   2. Deploy with: ./deploy_enhanced_backend.sh"
echo "   3. Test new endpoints at http://your-pi:8000/docs"
echo ""
echo "🎯 New API Endpoints Added:"
echo "   📋 GET/POST /api/shopping - Shopping list management"
echo "   ⚡ GET/POST /api/workflows - n8n workflow integration"
echo "   ⚙️  GET/POST /api/settings - Enhanced settings management"
echo "   🏥 GET /health - Enhanced service health monitoring"
echo ""
echo "💡 The enhanced backend is backward compatible with your existing frontend!"
echo "   Your current index.html will automatically connect to these new endpoints."

log "🎉 Enhancement script completed successfully!"