# Comprehensive Restoration Analysis
**Created:** 2025-10-19
**Issue:** Ultra-aggressive cleanup deleted critical files
**Status:** System functional but incomplete

## What Happened
During cleanup commit `0beb480`, 64 router files and multiple other files were deleted.
The blind restoration attempt failed due to dependency errors.

## Current System State

### ✅ Working Routers (68 registered)
- health, auth, lists, reminders, journeys, chat_sessions
- calendar, journal, memories, tasks, notifications
- weather, homeassistant, n8n_integration, matrix_integration
- And 54 more specialized routers

### ❌ Failed Routers (Need Analysis)
- chat: No module named 'route_llm'
- developer_chat: No module named 'route_llm'  
- developer_tasks: No module named 'routers.task_executor'
- lists_redesigned: Pydantic typing issue
- ai_task_integration: Missing import

### 📊 Frontend Status
- ✅ All CSS/JS files restored (27 files)
- ✅ Widget system working (8 widgets)
- ✅ Nginx properly configured
- ✅ No SyntaxErrors

### 🗄️ Database Status
- ✅ Single zoe.db at /home/pi/zoe/data/zoe.db
- ✅ Mounted at /app/data in container
- ✅ No duplicate databases
- ✅ DATABASE_PATH env var set correctly

## Files Still Missing/Broken

### Missing WebSocket/Intelligence Endpoints
- /api/ws/intelligence - 404
- /api/intelligence/stream - 502
- /api/status - 404

### SQL Schema Issues
- reminders.notifications: column 'is_read' missing
- performance_metrics: column 'value' missing
- user_context: column 'id' issue

## Next Steps (Structured Approach)

1. **Audit Phase**
   - [ ] List ALL deleted files from commit 0beb480
   - [ ] Categorize by type (routers, utils, configs, docs)
   - [ ] Identify dependencies for each file
   
2. **Analysis Phase**
   - [ ] Read each file to understand purpose
   - [ ] Check if functionality already exists elsewhere
   - [ ] Determine if truly needed vs redundant

3. **Restoration Phase (ONLY if needed)**
   - [ ] Fix SQL schemas first
   - [ ] Restore missing dependencies in order
   - [ ] Test incrementally after each restoration
   - [ ] Document why each file is being restored

4. **Cleanup Phase**
   - [ ] Remove truly redundant files
   - [ ] Consolidate duplicate functionality
   - [ ] Update .cursorrules with lessons learned
   - [ ] Add pre-commit validation

## Database Path Clarification
The database is NOT moved - it exists at one location on disk:
- **Host**: `/home/pi/zoe/data/zoe.db`
- **Container path 1**: `/app/data/zoe.db` (via ./data:/app/data mount)
- **Container path 2**: `/home/pi/zoe/data/zoe.db` (via /home/pi/zoe:/home/pi/zoe mount)

Both container paths point to the SAME file. Changed lists.py to use /app/data 
for consistency with other routers. No data was moved or lost.

---
**Analysis continues below...**
