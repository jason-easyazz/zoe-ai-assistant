# ZOE AI ASSISTANT - MASTER PROJECT INSTRUCTIONS
# THIS IS THE SINGLE SOURCE OF TRUTH FOR DEVELOPMENT

## 🔴 FOR HUMANS: Starting Any Work Session

### With Claude:
1. Paste this: `cat /home/pi/zoe/GENERIC_CONTINUATION.txt`
2. Claude will check GitHub and continue from last state
3. Choose what to work on from the menu

### Without Claude:
1. Run: `bash scripts/permanent/deployment/master_enhancements.sh`
2. Choose feature to install
3. System auto-syncs to GitHub

## 🟢 FOR CLAUDE: MANDATORY BEHAVIOR

### BEFORE ANY WORK:
1. Check GitHub: https://github.com/jason-easyazz/zoe-ai-assistant
2. Read CLAUDE_CURRENT_STATE.md
3. Run: `cd /home/pi/zoe && git pull && docker ps | grep zoe-`

### AFTER EACH STEP:
1. Test the change immediately
2. Commit to GitHub:
   ```bash
   git add .
   git commit -m "✅ [What was done]"
   git push
   ```
3. Update state: `bash scripts/permanent/maintenance/update_state.sh`

### SCRIPT REQUIREMENTS:
Every script MUST:
- Start with: `cd /home/pi/zoe`
- Show location: `echo "📍 Working in: $(pwd)"`
- End with GitHub sync
- Include complete file contents (no truncation)
- Test immediately after changes

## 🔵 PROJECT RULES (NEVER BREAK)

1. **Directory**: Always work in `/home/pi/zoe`
2. **Containers**: Always use `zoe-` prefix
3. **Ollama**: NEVER rebuild (loses model)
4. **Docker Compose**: ONE file only
5. **Testing**: IMMEDIATE after changes
6. **GitHub**: ALWAYS sync after changes
7. **Backups**: BEFORE major changes

## 🟡 QUICK COMMANDS

```bash
# Check status
docker ps | grep zoe-

# Update state for Claude
bash scripts/permanent/maintenance/update_state.sh

# Quick sync to GitHub
bash scripts/permanent/maintenance/quick_sync.sh

# Run enhancement menu
bash scripts/permanent/deployment/master_enhancements.sh

# Start new Claude chat
cat GENERIC_CONTINUATION.txt
```

## 🟣 PROJECT STRUCTURE

```
/home/pi/zoe/
├── docker-compose.yml (services)
├── services/
│   ├── zoe-core/ (backend)
│   └── zoe-ui/ (frontend)
├── data/ (databases)
├── scripts/
│   └── permanent/
│       ├── deployment/ (install scripts)
│       └── maintenance/ (sync scripts)
├── documentation/
│   ├── core/ (THIS FILE)
│   └── dynamic/ (auto-updated)
└── CLAUDE_CURRENT_STATE.md (auto-updated)
```

## 🔄 CONTINUITY SYSTEM

### For New Claude Chat:
1. Human: `cat GENERIC_CONTINUATION.txt`
2. Paste to Claude
3. Claude reads GitHub state
4. Continue from exact point

### Auto-Updates:
- State file updates after changes
- GitHub syncs automatically
- Claude always has current context

## 📊 CHECKING SYSTEM STATE

```bash
# Full state check
bash scripts/permanent/maintenance/check_status.sh

# Quick service check
docker ps --format "table {{.Names}}\t{{.Status}}" | grep zoe-

# Database stats
sqlite3 data/zoe.db "SELECT COUNT(*) FROM conversations;"
```

## 🚀 CURRENT CAPABILITIES

### Working:
- ✅ AI Chat (llama3.2:3b)
- ✅ Basic UI (port 8080)
- ✅ API Backend (port 8000)
- ✅ Event System
- ✅ GitHub Sync

### Ready to Install:
- 🔧 Enhanced Glass-Morphic UI
- 🔧 Natural Language Calendar
- 🔧 Developer Dashboard
- 🔧 Memory System
- 🔧 Voice Integration

---
*This document is permanent. Claude checks this on GitHub before starting work.*
*Last system update: Check CLAUDE_CURRENT_STATE.md for timestamp*
