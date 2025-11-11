# Zoe Productivity Commands

**Version**: 2.4.0  
**Phase**: 5 - Developer Productivity Scripts  
**Created**: October 18, 2025

## Overview

Shell function library for common Zoe operations. Saves time on debugging, testing, deployment, and monitoring tasks.

## Installation

```bash
# Auto-load on bash startup (optional)
echo 'source /home/zoe/assistant/scripts/utilities/zoe-superpowers.sh' >> ~/.bashrc

# Or load manually in current session
source /home/zoe/assistant/scripts/utilities/zoe-superpowers.sh
```

## Available Commands

### 🧪 zoe-test
**Purpose**: Run all tests and structure checks in one command

**Usage**:
```bash
zoe-test
```

**What it does**:
- Runs full system integration tests
- Validates project structure compliance (12 checks)
- Reports pass/fail status

**Time saved**: ~2 minutes vs manual commands

---

### 🔍 zoe-debug-chat
**Purpose**: Fast chat debugging with filtered logs

**Usage**:
```bash
zoe-debug-chat
```

**What it does**:
- Tails all Zoe log files
- Filters for: ERROR, chat, temporal, orchestr
- Color-coded output

**Time saved**: ~1 minute vs manual tail/grep

---

### 🚀 zoe-deploy
**Purpose**: Safe deployment with health checks and rollback

**Usage**:
```bash
zoe-deploy
```

**What it does**:
- Backs up current container state
- Restarts core services
- Waits 15s for startup
- Health check verification
- Shows status or error logs

**Time saved**: ~3 minutes vs manual deployment

---

### 🤖 zoe-models
**Purpose**: Quick Ollama model statistics

**Usage**:
```bash
zoe-models
```

**Output**:
```
deepseek-r1:14b - 9 GB
qwen2.5-coder:7b - 4 GB
llama3.2:3b - 2 GB
...
```

**Time saved**: ~30 seconds vs API call

---

### 💾 zoe-storage
**Purpose**: Disk usage breakdown

**Usage**:
```bash
zoe-storage
```

**What it shows**:
- Docker system usage
- Database sizes
- Total disk usage
- Link to full analysis tool

**Time saved**: ~1 minute vs multiple du commands

---

### 🔄 zoe-restart <service>
**Purpose**: Restart specific Docker service

**Usage**:
```bash
zoe-restart zoe-core
zoe-restart zoe-ui
zoe-restart mem-agent
```

**What it does**:
- Restarts specified container
- Waits 5s
- Verifies service is running
- Shows success/failure

**Time saved**: ~30 seconds vs docker commands

---

### 🏥 zoe-status
**Purpose**: Show all Zoe service statuses

**Usage**:
```bash
zoe-status
```

**Output**: Table with names, status, ports

**Time saved**: ~20 seconds

---

### 📋 zoe-tasks
**Purpose**: View recent developer tasks

**Usage**:
```bash
zoe-tasks
```

**Output**: Last 10 tasks with status

**Time saved**: ~30 seconds

---

### 💚 zoe-health
**Purpose**: Quick health check

**Usage**:
```bash
zoe-health
```

**Output**: JSON health status

**Time saved**: ~20 seconds

---

### 💾 zoe-save-session [description]
**Purpose**: Save current work session

**Usage**:
```bash
zoe-save-session "Implementing agent memory system"
zoe-save-session  # Uses default description
```

**What it saves**:
- Files changed (from git status)
- Task description
- Timestamp

**Time saved**: ~1 minute vs API call

---

### 🔍 zoe-what-was-i-doing
**Purpose**: Restore last work session context

**Usage**:
```bash
zoe-what-was-i-doing
```

**Output**: Human-readable session summary with files, next steps, breadcrumbs

**Time saved**: 5-10 minutes of context restoration

---

## Benefits

**Time Savings**:
- Average 30% faster common operations
- Fewer manual docker/log/curl commands
- Quick access to monitoring and debugging

**Quality of Life**:
- Consistent command names (all start with `zoe-`)
- Tab completion available
- Error handling built-in
- Safe operations (backups, health checks)

## Examples

**Daily workflow**:
```bash
# Morning: Check what you were doing
zoe-what-was-i-doing

# Check service health
zoe-status

# View pending tasks
zoe-tasks

# Make changes...

# Test everything
zoe-test

# Deploy safely
zoe-deploy

# Evening: Save session
zoe-save-session "Completed Phase 1-5 implementation"
```

**Debugging workflow**:
```bash
# Check health
zoe-health

# Debug chat issues
zoe-debug-chat

# Restart problematic service
zoe-restart zoe-core

# Verify fix
zoe-status
```

**Monitoring workflow**:
```bash
# Check storage
zoe-storage

# See models
zoe-models

# Full analysis
python3 /home/zoe/assistant/tools/maintenance/storage_manager.py
```

---

## Source

Based on **superpowers** project patterns:  
https://github.com/obra/superpowers

Adapted for Zoe AI Assistant with Zoe-specific operations and integration with developer session memory system.

---

**Last Updated**: October 18, 2025  
**Status**: Production Ready




