# 🚀 CURSOR QUICK START - ZOE AI SYSTEM

## Your Task Queue Commands
```bash
# List all tasks
./scripts/development/cursor_task_helper.sh list

# Get task details  
./scripts/development/cursor_task_helper.sh get 1c68a54b

# Mark task complete
./scripts/development/cursor_task_helper.sh complete 1c68a54b
```

## Current High Priority Tasks
1. **1c68a54b** - Fix Zack Code Generation
2. **1e199bae** - Complete RouteLLM + LiteLLM Integration  

## Key Files to Edit
- `services/zoe-core/routers/developer.py` - Zack AI
- `services/zoe-core/ai_client.py` - AI response generation
- `services/zoe-core/llm_models.py` - RouteLLM configuration

## Test Your Changes
```bash
# Rebuild after code changes
docker compose up -d --build zoe-core

# Check logs
docker logs zoe-core --tail 20

# Test Zack
curl -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Generate a Python function"}'
```

## API Base URLs
- Backend: http://localhost:8000
- Frontend: http://localhost:8080
- Developer UI: http://localhost:8080/developer/

## Docker Commands
```bash
# Check container health
docker ps | grep zoe-

# Restart a service
docker compose restart zoe-core

# View real-time logs
docker logs -f zoe-core

# Enter container for debugging
docker exec -it zoe-core bash
```

## Testing Workflow
1. **Pick a task**: `./scripts/development/cursor_task_helper.sh list`
2. **Make changes** in Cursor
3. **Rebuild**: `docker compose up -d --build zoe-core`
4. **Test**: Use the curl commands above
5. **Check logs**: `docker logs zoe-core --tail 30`
6. **Mark complete**: `./scripts/development/cursor_task_helper.sh complete TASK_ID`

## File Structure Quick Reference
```
/home/pi/zoe/
├── services/
│   ├── zoe-core/          # Backend API
│   │   ├── main.py        # FastAPI entry
│   │   ├── routers/       # API endpoints
│   │   │   ├── developer.py       # Zack AI
│   │   │   ├── developer_tasks.py # Task system
│   │   │   └── chat.py           # User chat
│   │   └── ai_client.py  # AI response generation
│   └── zoe-ui/dist/       # Frontend files
├── data/                  # Databases
│   ├── zoe.db            # Main database
│   └── developer_tasks.db # Task queue
└── scripts/development/   # Helper scripts
```

## Common Fixes

### If API not responding:
```bash
docker ps | grep zoe-core  # Check if running
docker logs zoe-core --tail 50  # Check errors
docker compose restart zoe-core  # Restart
```

### If changes not showing:
```bash
docker compose up -d --build zoe-core  # Force rebuild
docker exec zoe-core ls -la /app/routers/  # Verify files
```

### If task system errors:
```bash
# Check database
sqlite3 data/developer_tasks.db ".tables"
sqlite3 data/developer_tasks.db "SELECT * FROM dynamic_tasks;"
```

## Remember
- Always test before marking complete
- Use existing glass-morphic UI style
- Keep offline-first approach
- Document your changes in ZOE_CURRENT_STATE.md
- Never rebuild zoe-ollama (takes hours)
- Always use zoe- prefix for containers

## Your First Task
Start with **1c68a54b - Fix Zack Code Generation**:
1. Open `services/zoe-core/routers/developer.py`
2. Find the chat endpoint
3. Make it return actual code, not descriptions
4. Test with: `curl -X POST http://localhost:8000/api/developer/chat -d '{"message": "Create a Python hello world function"}'`
5. Should return executable Python code!

---

**Ready to code? Open this file in Cursor for quick reference!**
