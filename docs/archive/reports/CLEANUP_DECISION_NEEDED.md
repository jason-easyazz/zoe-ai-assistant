# 🚨 CRITICAL: Duplicate Project Structure

## Problem

You have the Zoe project cloned **TWICE**:

### Location 1: `/home/pi/`
- Has: `.git`, `services/`, `data/`, `scripts/`, `tests/`, `config/`
- Git remote: `https://github.com/jason-easyazz/zoe-ai-assistant.git`
- Files like `docker-compose.yml`, `CHANGELOG.md`, `ARCHITECTURE_PROTECTION.md` are here

### Location 2: `/home/pi/zoe/`
- Has: `.git`, `services/`, `data/`, `scripts/`, `tests/`, `config/`
- Git remote: `git@github.com:jason-easyazz/zoe-ai-assistant.git`
- All the same structure

## Why This Is A Problem

1. **Confusion**: Which directory is the "real" project?
2. **Wasted Space**: Everything is duplicated
3. **Git Conflicts**: Two separate git histories
4. **File Management**: Changes in one don't affect the other
5. **Docker Mounts**: Which paths are containers using?

## Decision Needed

### Option A: Keep `/home/pi/zoe/` (RECOMMENDED)
**Reasoning**:
- Follows standard convention (project in subdirectory)
- Matches all the rules in `.cursorrules`
- Keeps `/home/pi` clean for system files
- Uses SSH for Git (more secure)

**Steps**:
```bash
# 1. Verify /home/pi/zoe is current
cd /home/pi/zoe
git status
git log -5

# 2. Delete /home/pi project files (DANGEROUS - BACKUP FIRST!)
cd /home/pi
# Remove project dirs (keeping zoe/)
rm -rf services/ data/ scripts/ tests/ config/ docs/ documentation/
rm -f docker-compose*.yml CHANGELOG.md ARCHITECTURE_PROTECTION.md
rm -rf .git

# 3. Work exclusively from /home/pi/zoe
cd /home/pi/zoe
```

### Option B: Keep `/home/pi/` 
**Reasoning**:
- If this is the one you've been actively using
- Has more recent changes

**Steps**:
```bash
# 1. Delete /home/pi/zoe subdirectory
rm -rf /home/pi/zoe

# 2. Update all references from /home/pi/zoe to /home/pi
# This includes .cursorrules, docker paths, scripts, etc.
```

## Current State Analysis

Run these to decide:

```bash
# Check which has more recent commits
cd /home/pi && git log -1 --format="%ci"
cd /home/pi/zoe && git log -1 --format="%ci"

# Check which has more recent file modifications
cd /home/pi && find services/ -name "*.py" -mtime -1 | wc -l
cd /home/pi/zoe && find services/ -name "*.py" -mtime -1 | wc -l

# Check which docker-compose is being used
docker ps --format "{{.Mounts}}" | grep -o "/home/pi[^:]*" | head -1
```

## Recommendation

**I strongly recommend Option A: Keep `/home/pi/zoe/`**

This follows best practices and all the governance rules we just set up expect the project to be in `/home/pi/zoe/`.

---

**DO NOT PROCEED until you decide which one to keep!**

*Created: October 8, 2025*

