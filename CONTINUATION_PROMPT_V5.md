# Zoe AI Assistant v5.0 - Developer UI & Claude Integration Focus

## 🎯 MANDATORY STARTUP SEQUENCE

```bash
# 1. Check GitHub for latest state
cd /home/pi/zoe
git pull
cat CLAUDE_CURRENT_STATE.md

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
