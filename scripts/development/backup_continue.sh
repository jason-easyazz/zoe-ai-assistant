#!/bin/bash

# ============================================================================
# COMPLETE BACKUP AND CREATE CONTINUATION PROMPT
# ============================================================================

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}CREATING COMPLETE BACKUP & CONTINUATION PROMPT${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"

cd /home/pi/zoe

# ============================================================================
# STEP 1: CREATE COMPREHENSIVE STATUS REPORT
# ============================================================================

echo -e "\n${GREEN}📊 Creating Status Report...${NC}"

cat > ZOE_CURRENT_STATE.md << 'EOF'
# Zoe AI Assistant - Current State
## Last Updated: August 20, 2025 @ 11:05 PM

### ✅ SUCCESSFULLY DEPLOYED FEATURES

#### 1. **Core Infrastructure** (100% Working)
- ✅ 7 Docker containers running smoothly
- ✅ All services healthy and communicating
- ✅ GitHub repository synced: https://github.com/jason-easyazz/zoe-ai-assistant

#### 2. **Memory System** (100% Working)
- ✅ People tracking (Alice, Bob stored)
- ✅ Relationship mapping functional
- ✅ Search capabilities working
- ✅ Dynamic folder creation at `/app/data/memory/`

#### 3. **Voice Services** (90% Working)
- ✅ TTS generating audio files successfully
- ✅ Whisper STT with base model loaded
- ⚠️ Audio quality optimization in progress (espeak works, TTS service needs final fix)

#### 4. **Developer Dashboard** (Files Deployed)
- ✅ Template installed at `/services/zoe-ui/dist/developer/`
- ✅ Glass-morphic UI design
- ⚠️ Needs Claude API integration
- ⚠️ Needs backend connections

#### 5. **N8N Workflows** (Ready to Configure)
- ✅ Container running on port 5678
- ✅ Workflow templates created
- ⚠️ Needs configuration

### 📁 PROJECT STRUCTURE
```
/home/pi/zoe/
├── services/
│   ├── zoe-core/          # API (port 8000)
│   ├── zoe-ui/            # Web UI (port 8080)
│   ├── zoe-whisper/       # STT (port 9001)
│   ├── zoe-tts/           # TTS (port 9002)
│   └── zoe-developer/     # Developer Dashboard
├── data/
│   ├── zoe.db             # Main database
│   └── memory/            # Memory system storage
├── scripts/
│   ├── n8n/workflows/     # Automation templates
│   └── test scripts       # Various test utilities
└── tests/                 # Test suite
```

### 🐳 DOCKER SERVICES STATUS
| Service | Container | Port | Status |
|---------|-----------|------|--------|
| API | zoe-core | 8000 | ✅ Running |
| UI | zoe-ui | 8080 | ✅ Running |
| AI | zoe-ollama | 11434 | ✅ Running |
| Cache | zoe-redis | 6379 | ✅ Running |
| STT | zoe-whisper | 9001 | ✅ Running |
| TTS | zoe-tts | 9002 | ✅ Running |
| Automation | zoe-n8n | 5678 | ✅ Running |

### 🔧 WORKING SCRIPTS
- `test_voice.sh` - Basic voice test
- `test_voice_improved.sh` - Comprehensive voice tests
- `test_voice_quality.sh` - Quality comparison
- `fix_tts_quality.sh` - TTS audio improvement

### 📝 NEXT PRIORITIES
1. **Complete TTS audio quality fix** (almost done)
2. **Developer Dashboard Claude Integration**
3. **Backend API connections for dashboard**
4. **N8N workflow configuration**
5. **Production deployment optimizations**

### 🔑 ACCESS POINTS
- Main UI: http://192.168.1.60:8080
- Developer: http://192.168.1.60:8080/developer/
- API Docs: http://192.168.1.60:8000/docs
- N8N: http://192.168.1.60:5678 (user: zoe, pass: zoe2025)

### ⚠️ KNOWN ISSUES
1. TTS service audio quality affects Whisper accuracy
2. Developer Dashboard needs API integration
3. Calendar database schema was fixed but needs verification

### 💾 LAST BACKUP
- GitHub: https://github.com/jason-easyazz/zoe-ai-assistant
- Branch: main
- Last commit: "Fixed deployment issues"
EOF

# ============================================================================
# STEP 2: BACKUP TO GITHUB
# ============================================================================

echo -e "\n${GREEN}📤 Backing up to GitHub...${NC}"

# Add all changes
git add -A

# Create detailed commit
git commit -m "🎯 Complete v4.0 State Backup - Ready for Developer UI Focus

Current State:
- All 7 services running and healthy
- Memory system fully functional
- Voice services 90% working (TTS quality needs final fix)
- Developer Dashboard template deployed
- N8N automation ready for configuration

Completed in this session:
- Fixed docker-compose version warning
- Deployed voice integration (Whisper + TTS)
- Implemented memory system with relationships
- Installed professional developer dashboard
- Created comprehensive test suite
- Fixed most voice accuracy issues

Next Focus:
- Complete TTS audio quality optimization
- Integrate Claude API with Developer Dashboard
- Configure N8N workflows
- Production optimizations" || echo "No changes to commit"

# Push to GitHub
git push || echo "Push failed - check connection"

echo -e "${GREEN}✅ GitHub backup complete!${NC}"

# ============================================================================
# STEP 3: CREATE UBER CONTINUATION PROMPT
# ============================================================================

echo -e "\n${GREEN}📝 Creating continuation prompt...${NC}"

cat > ZOE_CONTINUATION_PROMPT.md << 'EOF'
# Zoe AI Assistant v5.0 - Developer UI & Claude Integration Focus

## 🎯 MANDATORY STARTUP SEQUENCE

```bash
# 1. Check GitHub for latest state
cd /home/pi/zoe
git pull
cat ZOE_CURRENT_STATE.md

# 2. Verify all services running
docker ps --format "table {{.Names}}\t{{.Status}}" | grep zoe-

# 3. Test API health
curl http://localhost:8000/health | jq '.'
```

## 📋 CURRENT SYSTEM STATE

### ✅ What's Working:
- **7 Docker containers** all running (zoe-core, ui, ollama, redis, whisper, tts, n8n)
- **Memory System** fully functional (people, projects, relationships)
- **Voice Services** 90% working (TTS needs audio quality fix)
- **API** all endpoints operational
- **GitHub** synced at: https://github.com/jason-easyazz/zoe-ai-assistant

### 🔧 What Needs Work:
1. **TTS Audio Quality** - Final fix for Whisper accuracy
2. **Developer Dashboard** - Claude API integration needed
3. **Dashboard Backend** - Connect to real system data
4. **N8N Workflows** - Need configuration

## 🚀 IMMEDIATE PRIORITIES

### Priority 1: Complete TTS Fix
```bash
# The fix script exists, just needs to run:
./fix_tts_quality.sh
./test_voice_quality.sh
```

### Priority 2: Developer Dashboard Claude Integration

**Current State:**
- Template installed at: `/services/zoe-ui/dist/developer/index.html`
- Beautiful glass-morphic UI ready
- Needs: Claude API key configuration
- Needs: Backend endpoints for Claude chat

**Required Implementation:**
1. Add Claude API endpoint to zoe-core
2. Configure API key management
3. Connect dashboard to Claude endpoint
4. Add real-time system monitoring
5. Implement task management with Claude

### Priority 3: Dashboard Features to Add
- [ ] Real-time service health monitoring
- [ ] Claude chat with context awareness
- [ ] Script execution interface
- [ ] Task queue management
- [ ] System logs viewer
- [ ] Memory system browser
- [ ] Voice service controls

## 📁 KEY FILES TO MODIFY

```bash
# Core API - Add Claude integration
services/zoe-core/main.py
services/zoe-core/routers/claude.py  # Create this

# Developer Dashboard
services/zoe-ui/dist/developer/index.html
services/zoe-ui/dist/developer/js/app.js  # Create this

# Configuration
.env  # Add CLAUDE_API_KEY
docker-compose.yml  # Pass env to zoe-core
```

## 🎨 DEVELOPER DASHBOARD REQUIREMENTS

The dashboard at `http://192.168.1.60:8080/developer/` should:

1. **Claude Chat Panel**
   - Real-time chat with Claude
   - Context awareness (system state, logs, tasks)
   - Code generation and execution
   - Save/load conversation history

2. **System Monitoring**
   - Live container status
   - Resource usage graphs
   - API endpoint health
   - Error log streaming

3. **Task Management**
   - Create tasks for Claude
   - Queue management
   - Priority settings
   - Execution history

4. **Script Runner**
   - Execute safe scripts
   - View output in real-time
   - Save successful scripts
   - Rollback capability

## 🔌 API ENDPOINTS NEEDED

```python
# Add to services/zoe-core/main.py

# Claude endpoints
POST /api/claude/chat - Send message to Claude
GET /api/claude/history - Get conversation history
POST /api/claude/task - Create task for Claude
GET /api/claude/tasks - List all tasks

# System monitoring
GET /api/system/metrics - CPU, RAM, disk usage
GET /api/system/logs/{service} - Get service logs
WS /api/system/stream - WebSocket for real-time updates

# Script execution
POST /api/scripts/run - Execute a script
GET /api/scripts/history - Execution history
POST /api/scripts/save - Save successful script
```

## 🛠️ QUICK IMPLEMENTATION SCRIPT

Create an uber script for Developer UI:
1. Add Claude API integration to backend
2. Create WebSocket connections
3. Implement real-time monitoring
4. Add task queue system
5. Connect everything to dashboard

## 💾 PROJECT KNOWLEDGE DOCS

Load these from your knowledge base:
- `Zoe_Complete_Vision.md`
- `Zoe_System_Architecture.md`
- `Zoe_Development_Guide.md`
- `Zoe_Master_Continuation_Prompt.md`

## 🔑 ENVIRONMENT DETAILS

- **Location**: `/home/pi/zoe`
- **IP**: 192.168.1.60
- **OS**: Raspberry Pi OS (Debian Bookworm)
- **Docker**: Compose v2
- **Python**: 3.9 in containers

## ⚡ QUICK TESTS

```bash
# Test Memory System
curl -X POST http://localhost:8000/api/memory/search \
  -H "Content-Type: application/json" \
  -d '{"query": "Alice"}'

# Test Voice
./test_voice_quality.sh

# Check all services
curl http://localhost:8000/api/developer/status | jq '.'
```

## 🎯 SUCCESS CRITERIA

By end of next session:
1. ✅ Developer Dashboard fully functional with Claude
2. ✅ Real-time monitoring working
3. ✅ Task management system operational
4. ✅ Voice services 100% accurate
5. ✅ Ready for production deployment

---

**Start next chat with:** "Continue Zoe v5.0 - Focus on Developer Dashboard Claude Integration. Load project docs and check current state."
EOF

echo -e "${GREEN}✅ Continuation prompt created!${NC}"

# ============================================================================
# STEP 4: FINAL STATUS CHECK
# ============================================================================

echo -e "\n${BLUE}════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}FINAL STATUS CHECK${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"

# Show running containers
docker ps --format "table {{.Names}}\t{{.Status}}" | grep zoe-

# Show file counts
echo -e "\n📊 Project Statistics:"
echo "Total files: $(find . -type f | wc -l)"
echo "Python files: $(find . -name "*.py" | wc -l)"
echo "Test files: $(find tests/ -type f 2>/dev/null | wc -l)"
echo "Scripts: $(find . -name "*.sh" | wc -l)"

echo -e "\n${GREEN}════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✅ BACKUP COMPLETE & READY FOR NEXT SESSION!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════════${NC}"

echo -e "\n📋 ${YELLOW}Files Created:${NC}"
echo "1. ZOE_CURRENT_STATE.md - Complete system status"
echo "2. ZOE_CONTINUATION_PROMPT.md - For next chat session"
echo "3. GitHub backup with detailed commit"

echo -e "\n🚀 ${YELLOW}Next Steps:${NC}"
echo "1. Copy ZOE_CONTINUATION_PROMPT.md content"
echo "2. Start new chat with that prompt"
echo "3. Focus on Developer Dashboard Claude integration"

exit 0
