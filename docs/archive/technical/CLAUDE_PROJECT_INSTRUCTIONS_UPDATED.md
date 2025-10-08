# Zoe Master Instructions - MANDATORY BEHAVIOR
# Updated: September 2025 - v6.0 with Enterprise Security & Touch Interface

## 🔴 MANDATORY: HOW CLAUDE WORKS WITH THIS PROJECT

### What Claude CAN Do:
- **Search project knowledge** for repository files and documentation
- **Read files** from the project knowledge base
- **Generate complete executable scripts** for users to run
- **Provide guidance** based on documentation
- **Create artifacts** with code and configurations
- **Design secure systems** that protect API keys

### What Claude CANNOT Do:
- Browse to github.com directly
- Execute commands on the system
- Modify files directly
- Access the live system status
- See actual API keys or sensitive data

### 📚 HOW TO ACCESS THE GITHUB REPOSITORY IN PROJECT KNOWLEDGE

#### The Repository Is Already Loaded
Your GitHub repository (jason-easyazz/zoe-ai-assistant) is stored in Claude's project knowledge. This means:
- All your repository files are searchable
- Claude uses `project_knowledge_search()` to find files
- Multiple searches may be needed to find all components
- Search results return actual file contents

#### How to Search for Repository Files
```python
# METHOD 1: Search by exact file path
project_knowledge_search("services/zoe-core/main.py")

# METHOD 2: Search by file content
project_knowledge_search("from fastapi import FastAPI")

# METHOD 3: Search by multiple terms
project_knowledge_search("docker-compose.yml zoe-core zoe-ui")

# METHOD 4: Search by function/class names
project_knowledge_search("def generate_response async")

# METHOD 5: Search for configuration
project_knowledge_search(".env.example OPENAI_API_KEY")
```

## 📋 PROJECT INSTRUCTIONS - ESSENTIAL WORKFLOW

### For Starting ANY Work Session

#### 1. Claude's First Actions
```python
# MANDATORY: Search for current documentation
project_knowledge_search("ZOES_CURRENT_STATE.md")
project_knowledge_search("docker-compose.yml")
project_knowledge_search("README.md")

# Understand what exists
project_knowledge_search("services/zoe-core routers")
project_knowledge_search("services/zoe-ui/dist html")
project_knowledge_search("services/zoe-auth")
```

#### 2. User's First Commands
```bash
#!/bin/bash
# Run this check_state.sh script first
cd /home/pi/zoe
echo "🔍 Current Zoe State"
echo "==================="

# Pull latest
git pull

# Check containers
docker ps --format "table {{.Names}}\t{{.Status}}" | grep zoe-

# Check health
curl -s http://localhost:8000/health | jq '.'
curl -s http://localhost:8002/health | jq '.'

# Check current state file
cat ZOES_CURRENT_STATE.md | tail -20

# Check script organization
ls -la scripts/
```

## 🏗️ SYSTEM ARCHITECTURE SUMMARY

### Hardware
- **Device**: Raspberry Pi 5 (8GB RAM)
- **Storage**: 128GB SD Card
- **Network**: 192.168.1.60
- **OS**: Raspberry Pi OS 64-bit (Bookworm)

### Docker Services (ALL with zoe- prefix)
- `zoe-core` → Port 8000 (FastAPI backend)
- `zoe-ui` → Port 80/443 (Nginx with SSL, serves both UIs)
- `zoe-auth` → Port 8002 (Authentication service)
- `zoe-litellm` → Port 8001 (LLM routing)
- `zoe-ollama` → Port 11434 (Local AI)
- `zoe-redis` → Port 6379 (Cache)
- `zoe-whisper` → Port 9001 (STT)
- `zoe-tts` → Port 9002 (TTS)
- `zoe-n8n` → Port 5678 (Automation)
- `zoe-cloudflared` → Tunnel service
- `touch-panel-discovery` → Auto-discovery service

### UI Pages Structure
```
services/zoe-ui/dist/
├── index.html         # Main chat interface
├── dashboard.html     # Overview tiles
├── calendar.html      # Event management
├── lists.html         # Shopping/Bucket/Tasks/Custom lists
├── memories.html      # People & projects
├── workflows.html     # N8N automation
├── settings.html      # User preferences & API keys
├── auth.html          # Authentication interface
├── touch/             # Touch interface system
│   ├── dashboard.html
│   ├── calendar.html
│   ├── lists.html
│   └── index.html
└── developer/
    └── index.html     # Developer dashboard (Claude)
```

## 🔍 FINDING SPECIFIC INFORMATION

### To Understand Current Features
```python
# List all API endpoints
project_knowledge_search("@router.get @router.post @router.put @router.delete")

# Find all UI pages
project_knowledge_search("services/zoe-ui/dist .html DOCTYPE")

# Locate all database tables
project_knowledge_search("CREATE TABLE IF NOT EXISTS")

# Find all Docker services
project_knowledge_search("container_name: zoe-")
```

### To Debug Issues
```python
# Find error handling
project_knowledge_search("HTTPException try except raise")

# Locate logging statements
project_knowledge_search("logger.error logger.info logging")

# Find validation
project_knowledge_search("BaseModel Optional validator")

# Check dependencies
project_knowledge_search("requirements.txt import from")
```

### To Add New Features
```python
# Find similar implementations
project_knowledge_search("[similar_feature] router @router")

# Locate integration points
project_knowledge_search("app.include_router main.py")

# Find UI templates
project_knowledge_search("[similar_ui] html css js")

# Check database schema
project_knowledge_search("ALTER TABLE ADD COLUMN")
```

### Important Files to Always Check

| Priority | File | Purpose | Key Content |
|----------|------|---------|-------------|
| 1 | `docker-compose.yml` | Service architecture | All containers, ports, volumes |
| 2 | `services/zoe-core/main.py` | API entry point | Router registration, middleware |
| 3 | `services/zoe-core/ai_client.py` | AI personalities | Prompts, temperature settings |
| 4 | `services/zoe-core/requirements.txt` | Dependencies | All Python packages |
| 5 | `ZOES_CURRENT_STATE.md` | Current status | System state and capabilities |
| 6 | `services/zoe-ui/nginx.conf` | Web routing | URL path configurations |
| 7 | Database schema | Data structure | Table definitions, relationships |

## 🔒 SECURITY REQUIREMENTS

### Authentication System
- **Multi-User Security**: Enterprise-grade authentication with data isolation
- **Session Validation**: All endpoints validate via zoe-auth service
- **Privileged Endpoint Protection**: Admin-only access enforced
- **RBAC System**: Role-based access control
- **SSO Integration**: Single sign-on with Matrix, HomeAssistant, N8N
- **Touch Panel Auth**: Quick authentication for touch panels

### API Keys - NEVER in GitHub
```bash
# Files that must be in .gitignore:
.env
data/api_keys.json
data/.encryption_key
data/*.db
secrets/
*.key

# What CAN go to GitHub:
services/zoe-core/     # Code for managing keys
services/zoe-ui/       # Settings interface
services/zoe-auth/     # Authentication service
```

## ⚠️ CRITICAL RULES

### NEVER:
- Create scripts with manual nano editing steps
- Put API keys in scripts or GitHub
- Rebuild zoe-ollama (loses model)
- Create multiple docker-compose files
- Skip testing in scripts
- Create messy file structures
- Forget to make scripts executable

### ALWAYS:
- Search project knowledge first
- Create complete, executable scripts
- Place scripts in organized folders
- Include clear output and confirmations
- Test everything in the script
- Keep sensitive data local only
- Use zoe- prefix for containers
- Include rollback/recovery steps

## 📝 EXAMPLE: Complete Feature Implementation

When user asks to add a feature, Claude creates:

```bash
#!/bin/bash
# ADD_REMINDER_FEATURE.sh
# Location: scripts/development/add_reminder_feature.sh

set -e

echo "🎯 Adding Reminder Feature to Zoe"
echo "=================================="

cd /home/pi/zoe

# Step 1: Backup
echo -e "\n📦 Creating backup..."
cp -r services services.backup_$(date +%Y%m%d_%H%M%S)

# Step 2: Create backend endpoint
echo -e "\n🔧 Creating reminder endpoint..."
cat > services/zoe-core/routers/reminders.py << 'EOF'
from fastapi import APIRouter
from datetime import datetime

router = APIRouter(prefix="/api/reminders")

@router.post("/")
async def create_reminder(title: str, time: str):
    # Implementation here
    return {"status": "created", "title": title}
EOF

# Step 3: Update main.py
echo -e "\n📝 Updating main.py..."
if ! grep -q "reminders" services/zoe-core/main.py; then
    sed -i '/from routers import/a from routers import reminders' services/zoe-core/main.py
    sed -i '/app.include_router/a app.include_router(reminders.router)' services/zoe-core/main.py
fi

# Step 4: Rebuild
echo -e "\n🐳 Rebuilding service..."
docker compose up -d --build zoe-core

# Step 5: Test
echo -e "\n✅ Testing new endpoint..."
sleep 5
curl -X POST http://localhost:8000/api/reminders \
  -H "Content-Type: application/json" \
  -d '{"title": "Test", "time": "10:00"}' | jq '.'

echo -e "\n✅ Reminder feature added successfully!"
echo "Next steps:"
echo "  - Add UI in lists.html"
echo "  - Test with: curl http://localhost:8000/api/reminders"
echo "  - Commit: git add . && git commit -m '✅ Feature: Add reminders'"
```

## 🚨 WHEN SEARCHES DON'T FIND EXPECTED FILES

### If Core Files Are Missing
If searches don't find expected files, Claude should:

1. **Try alternative search terms**
   ```python
   # If this doesn't work:
   project_knowledge_search("services/zoe-core/main.py")
   
   # Try these:
   project_knowledge_search("main.py FastAPI app")
   project_knowledge_search("from fastapi import")
   project_knowledge_search("zoe-core main")
   ```

2. **Inform the user what's missing**
   ```bash
   echo "⚠️ Expected file not found in project knowledge:"
   echo "  - services/zoe-core/routers/[feature].py"
   echo ""
   echo "This might mean:"
   echo "  1. The feature hasn't been implemented yet"
   echo "  2. The file has a different name"
   echo "  3. The repository snapshot is outdated"
   ```

3. **Create the missing implementation**
   ```bash
   echo "📝 Creating missing implementation..."
   cat > services/zoe-core/routers/[feature].py << 'EOF'
   # New implementation based on patterns from similar files
   EOF
   ```

### Alternative Search Strategies

If initial searches fail, try:
```python
# Search by functionality instead of filename
project_knowledge_search("calendar event date time schedule")

# Search by technology
project_knowledge_search("FastAPI router endpoint async def")

# Search by UI elements
project_knowledge_search("button onclick class id href")

# Search by error messages
project_knowledge_search("error exception failed invalid")

# Search comments and documentation
project_knowledge_search("TODO FIXME NOTE IMPORTANT")
```

## 📋 PROJECT INSTRUCTIONS - MANDATORY WORKFLOW

### Project Context
- **Name**: Zoe AI Assistant
- **Location**: `/home/pi/zoe` on Raspberry Pi 5 (128GB SD)
- **GitHub**: https://github.com/jason-easyazz/zoe-ai-assistant
- **IP**: 192.168.1.60
- **Architecture**: 10+ Docker containers with zoe- prefix
- **UI Pages**: 7 main pages + developer dashboard + touch interface
- **Goal**: Privacy-first, offline AI companion with dual personalities

### EVERY Work Session MUST Follow This Flow

#### Phase 1: Context Gathering (Claude)
```python
# Claude MUST search these in order:
1. project_knowledge_search("ZOES_CURRENT_STATE.md")  # Current status
2. project_knowledge_search("docker-compose.yml")     # Services
3. project_knowledge_search("services/zoe-core/main.py")   # Backend
4. project_knowledge_search("services/zoe-ui/dist")        # Frontend
5. project_knowledge_search("services/zoe-auth")           # Auth service
```

#### Phase 2: State Check (User Runs)
```bash
cd /home/pi/zoe
git pull
docker ps | grep zoe-
curl http://localhost:8000/health
curl http://localhost:8002/health
cat ZOES_CURRENT_STATE.md | tail -20
```

#### Phase 3: Implementation (Claude Creates)
- Complete executable scripts in `scripts/[category]/`
- No manual nano editing required
- Include all error handling and testing
- Never put sensitive data in scripts

#### Phase 4: Execution (User Runs)
```bash
chmod +x scripts/[category]/[script].sh
./scripts/[category]/[script].sh
```

#### Phase 5: Documentation (Both)
```bash
# Update state file
echo "$(date): [What was done]" >> ZOES_CURRENT_STATE.md

# Sync to GitHub (safe files only)
git add .
git status  # Verify no .env or keys
git commit -m "✅ [Description]"
git push
```

### Project Rules & Constraints

#### Technical Requirements
- **Python**: 3.11+ with FastAPI
- **AI Model**: llama3.2:3b (already installed, don't rebuild)
- **Database**: SQLite at `/data/zoe.db`
- **Frontend**: Glass-morphic design, vanilla JS
- **Scripts**: Bash, organized in folders

#### Security Requirements
- API keys in `.env` (never commit)
- Encrypted storage for runtime keys
- No telemetry or external calls
- All data stays local
- Multi-user privacy boundaries (implemented)

#### Development Standards
- Docker containers use `zoe-` prefix
- Scripts go in organized folders
- Test immediately after changes
- Document everything
- GitHub sync after each feature

### Success Criteria for Every Change
- [ ] All containers running
- [ ] API health check passes
- [ ] UI loads without errors
- [ ] Feature works as expected
- [ ] Tests pass
- [ ] Documentation updated
- [ ] GitHub synced (no sensitive data)
- [ ] State file updated

## 📊 Quick Reference for Claude

### Testing Commands to Include in Scripts
```bash
# Health checks
curl http://localhost:8000/health
curl http://localhost:8002/health
curl http://localhost:8000/api/developer/status

# Test both personalities
curl -X POST http://localhost:8000/api/chat \
  -d '{"message": "Hi Zoe"}'

curl -X POST http://localhost:8000/api/developer/chat \
  -d '{"message": "System status"}'

# Check all endpoints
curl http://localhost:8000/openapi.json | jq '.paths | keys'
```

### Git Commands for Scripts
```bash
# Safe GitHub sync (sensitive data excluded)
git add .
git status  # Verify no .env or keys
git commit -m "✅ [Description]"
git push
```

## 💡 COMPLETE EXAMPLE: How Claude Should Work

### User Request: "Add a notes feature to Zoe"

#### Step 1: Claude Searches Project Knowledge (Repository in Memory)
```python
# Search the repository snapshot in project knowledge:

# 1. Check if notes already exist
results = project_knowledge_search("notes note-taking notebook")
# → Finds: No existing notes implementation

# 2. Find similar features for patterns  
results = project_knowledge_search("services/zoe-core/routers/lists.py")
# → Finds: Lists router with CRUD operations pattern

results = project_knowledge_search("lists.html shopping bucket")
# → Finds: Lists UI with card layout pattern

# 3. Understand the architecture
results = project_knowledge_search("docker-compose.yml services zoe-core")
# → Finds: Container configuration, port 8000

results = project_knowledge_search("main.py app.include_router")
# → Finds: How routers are registered

# 4. Find UI patterns
results = project_knowledge_search("dashboard.html glass-morphic card")
# → Finds: Glass-morphic styling patterns

# 5. Locate database patterns
results = project_knowledge_search("CREATE TABLE events lists")
# → Finds: SQLite table creation patterns

# 6. Find script patterns
results = project_knowledge_search("scripts/development add feature")
# → Finds: Script structure and organization
```

#### Step 2: Claude Creates Complete Implementation Script
```bash
#!/bin/bash
# ADD_NOTES_FEATURE.sh
# Location: scripts/development/add_notes_feature.sh
# Purpose: Add complete notes feature to Zoe

set -e

echo "🎯 Adding Notes Feature to Zoe"
echo "=============================="
echo ""
echo "This will add:"
echo "  1. Backend API for notes"
echo "  2. Database table"
echo "  3. Frontend UI page"
echo "  4. Navigation links"
echo ""
echo "Press Enter to continue or Ctrl+C to abort..."
read

cd /home/pi/zoe

# Backup
echo -e "\n📦 Creating backup..."
mkdir -p backups/$(date +%Y%m%d_%H%M%S)
cp -r services backups/$(date +%Y%m%d_%H%M%S)/

# Backend
echo -e "\n🔧 Creating backend router..."
cat > services/zoe-core/routers/notes.py << 'EOF'
"""Notes management system"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import sqlite3
import json

router = APIRouter(prefix="/api/notes")

class Note(BaseModel):
    title: str
    content: str
    tags: Optional[List[str]] = []
    user_id: str = "default"

@router.post("/")
async def create_note(note: Note):
    conn = sqlite3.connect("/app/data/zoe.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO notes (user_id, title, content, tags, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (note.user_id, note.title, note.content, 
          json.dumps(note.tags), datetime.now()))
    conn.commit()
    note_id = cursor.lastrowid
    conn.close()
    return {"id": note_id, "status": "created"}

@router.get("/")
async def get_notes(user_id: str = "default"):
    conn = sqlite3.connect("/app/data/zoe.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, title, content, tags, created_at 
        FROM notes 
        WHERE user_id = ?
        ORDER BY created_at DESC
    """, (user_id,))
    notes = cursor.fetchall()
    conn.close()
    
    return {
        "notes": [
            {
                "id": n[0],
                "title": n[1],
                "content": n[2],
                "tags": json.loads(n[3]) if n[3] else [],
                "created_at": n[4]
            }
            for n in notes
        ]
    }
EOF

# Database
echo -e "\n📊 Creating database table..."
docker exec zoe-core sqlite3 /app/data/zoe.db << 'SQL'
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT DEFAULT 'default',
    title TEXT NOT NULL,
    content TEXT,
    tags TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_notes_user ON notes(user_id);
SQL

# Frontend
echo -e "\n🎨 Creating frontend UI..."
cat > services/zoe-ui/dist/notes.html << 'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Zoe - Notes</title>
    <link rel="stylesheet" href="css/glass.css">
    <style>
        .notes-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
            padding: 20px;
        }
        .note-card {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 16px;
            padding: 20px;
            cursor: pointer;
            transition: transform 0.3s;
        }
        .note-card:hover {
            transform: translateY(-5px);
        }
    </style>
</head>
<body>
    <div class="nav-bar">
        <div class="nav-left">
            <div class="nav-menu">
                <a href="index.html">Chat</a>
                <a href="dashboard.html">Dashboard</a>
                <a href="notes.html" class="active">Notes</a>
                <a href="settings.html">Settings</a>
            </div>
        </div>
    </div>
    
    <div class="main-container">
        <h1>📝 My Notes</h1>
        <button onclick="createNote()" class="btn-primary">+ New Note</button>
        
        <div id="notesGrid" class="notes-grid">
            <!-- Notes will load here -->
        </div>
    </div>
    
    <script>
        const API_BASE = 'http://localhost:8000/api';
        
        async function loadNotes() {
            const response = await fetch(`${API_BASE}/notes`);
            const data = await response.json();
            
            const grid = document.getElementById('notesGrid');
            grid.innerHTML = data.notes.map(note => `
                <div class="note-card" onclick="editNote(${note.id})">
                    <h3>${note.title}</h3>
                    <p>${note.content.substring(0, 100)}...</p>
                    <small>${new Date(note.created_at).toLocaleDateString()}</small>
                </div>
            `).join('');
        }
        
        async function createNote() {
            const title = prompt('Note title:');
            if (!title) return;
            
            const content = prompt('Note content:');
            if (!content) return;
            
            await fetch(`${API_BASE}/notes`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({title, content})
            });
            
            loadNotes();
        }
        
        window.onload = loadNotes;
    </script>
</body>
</html>
EOF

# Update main.py
echo -e "\n📝 Updating main.py..."
if ! grep -q "notes" services/zoe-core/main.py; then
    sed -i '/from routers import/s/$/, notes/' services/zoe-core/main.py
    sed -i '/# Include routers/a app.include_router(notes.router)' services/zoe-core/main.py
fi

# Update navigation in other pages
echo -e "\n🔗 Adding navigation links..."
for page in index dashboard calendar lists memories workflows settings; do
    if [ -f "services/zoe-ui/dist/$page.html" ]; then
        # Add notes link if not present
        sed -i '/<a href="settings.html"/i <a href="notes.html">Notes</a>' \
            services/zoe-ui/dist/$page.html 2>/dev/null || true
    fi
done

# Rebuild
echo -e "\n🐳 Rebuilding services..."
docker compose up -d --build zoe-core
docker compose restart zoe-ui

# Test
echo -e "\n✅ Testing implementation..."
sleep 5

# Test API
echo "Testing API endpoint..."
curl -X POST http://localhost:8000/api/notes \
    -H "Content-Type: application/json" \
    -d '{"title": "Test Note", "content": "Testing the notes feature"}' | jq '.'

# Test retrieval
echo -e "\nRetrieving notes..."
curl http://localhost:8000/api/notes | jq '.'

# Test UI
echo -e "\nTesting UI access..."
curl -s -o /dev/null -w "UI HTTP Status: %{http_code}\n" \
    http://localhost:8080/notes.html

echo -e "\n✅ Notes feature successfully added!"
echo ""
echo "Access the notes at: http://192.168.1.60:8080/notes.html"
echo ""
echo "Next steps:"
echo "  1. Test the UI at http://192.168.1.60:8080/notes.html"
echo "  2. Commit: git add . && git commit -m '✅ Feature: Add notes system'"
echo "  3. Push: git push"
```

This example shows how Claude should:
1. Search thoroughly first
2. Create complete, runnable scripts
3. Include all components (backend, database, frontend)
4. Test everything
5. Provide clear next steps

---

**Built with ❤️ for perfect offline AI companionship**

*"Just like Samantha from Her - but yours to keep!"* 🌟

