# Cursor & AI Assistant Best Practices for Zoe

**Date**: October 18, 2025  
**Purpose**: Prevent work duplication and ensure context awareness

---

## 🎯 The Problem

When working with AI assistants like Cursor, there's a risk of:
- ❌ **Duplicating work** that was just completed
- ❌ **Not knowing about new tools** that were recently added
- ❌ **Breaking recent changes** by implementing conflicting features
- ❌ **Wasting time** recreating what exists

---

## ✅ The Solution: Multi-Layer Context System

We've implemented a **3-layer context system** to ensure Cursor/AI always knows what's been done:

### **Layer 1: RECENT_CHANGES.md (PRIMARY)**

**Location**: `/home/zoe/assistant/RECENT_CHANGES.md`

**Purpose**: Quick snapshot of recent work

**Cursor Should**:
- ✅ **Read this FIRST** before any new work
- ✅ Check "This Week" section for latest changes
- ✅ Check "Active Work" to avoid conflicts
- ✅ Check "New Tools Available" to use existing features
- ✅ Check "Don't Duplicate" list before creating new tools

**Update Frequency**: After every major feature/change

**Example Usage**:
```markdown
## 🆕 This Week (Oct 14-18)
- ✅ Implemented project governance system
- ✅ Created schema-based database management
- ✅ Added 12 automated compliance checks

## Don't Duplicate These (Already Done):
- ❌ Database initialization system (exists)
- ❌ CHANGELOG generator (exists)
- ❌ Commit validation (exists)
```

---

### **Layer 2: Context Briefing Script (AUTOMATED)**

**Location**: `/home/zoe/assistant/scripts/utilities/context_briefing.sh`

**Purpose**: Automated context gathering

**Run Before Starting Work**:
```bash
./scripts/utilities/context_briefing.sh
```

**Shows**:
- 📅 Recent changes summary
- 📊 Last 7 days activity
- 🏥 Repository health
- 📋 Last 10 commits
- 🏷️ Recent version tags
- 🔍 Uncommitted changes
- 📐 Structure compliance
- 🛠️ Available tools
- 📚 Must-read docs

**Benefits**:
- Complete overview in 30 seconds
- Identifies recent additions
- Shows current state
- Lists available tools

---

### **Layer 3: Weekly Summaries (HISTORICAL)**

**Command**: `./tools/reports/weekly_summary.sh [weeks_ago]`

**Purpose**: See what changed over time

**Usage**:
```bash
# Last week
./tools/reports/weekly_summary.sh

# Last 2 weeks
./tools/reports/weekly_summary.sh 2

# Last month
./tools/reports/weekly_summary.sh 4
```

**Shows**:
- Commit counts by type (feat, fix, db, docs, etc.)
- Contributors
- Files changed
- Lines added/removed
- Recent commits
- Tags created

---

## 📋 Standard Workflow for Cursor/AI

### **BEFORE Starting Any New Work**

```bash
# Step 1: Read recent changes (CRITICAL)
cat /home/zoe/assistant/RECENT_CHANGES.md

# Step 2: Run context briefing
./scripts/utilities/context_briefing.sh

# Step 3: Check specific areas
python3 tools/reports/repo_health.py
python3 tools/audit/enforce_structure.py
```

### **DURING Work**

```bash
# Check if tool/feature already exists
grep -r "function_name" /home/zoe/assistant/

# Check recent commits for related work
git log --all --grep="keyword" --oneline

# See who worked on this area recently
git log --since="1 week ago" -- path/to/file
```

### **AFTER Making Changes**

```bash
# 1. Update RECENT_CHANGES.md
# Add your work to "This Week" section

# 2. Verify structure compliance
python3 tools/audit/enforce_structure.py

# 3. Commit with conventional format
git commit -m "feat(component): Description"
```

---

## 🚨 Critical Checks Before Creating New Features

### **1. Does It Already Exist?**

**Check**:
```bash
# Search codebase
grep -r "feature_name" /home/zoe/assistant/

# Check tools directory
ls -la /home/zoe/assistant/tools/

# Check scripts
ls -la /home/zoe/assistant/scripts/

# Read RECENT_CHANGES.md
cat RECENT_CHANGES.md | grep -A 20 "New Tools"
```

**Already Exists**:
- ✅ Database initialization (`scripts/setup/init_databases.sh`)
- ✅ CHANGELOG generator (`tools/generators/generate_changelog.py`)
- ✅ Commit validation (`tools/audit/validate_commit_message.sh`)
- ✅ Weekly summaries (`tools/reports/weekly_summary.sh`)
- ✅ Repository health (`tools/reports/repo_health.py`)
- ✅ Structure enforcement (`tools/audit/enforce_structure.py` - 12 checks)
- ✅ Schema export (`scripts/maintenance/export_schema.sh`)

### **2. Will It Conflict?**

**Check**:
```bash
# See active work
cat RECENT_CHANGES.md | grep -A 10 "Active Work"

# Check uncommitted changes
git status

# See recent changes to same files
git log --since="1 week ago" -- path/to/file
```

### **3. Does Documentation Cover It?**

**Check**:
```bash
# Search all documentation
grep -r "topic" /home/zoe/assistant/docs/

# Check recent docs
ls -lt /home/zoe/assistant/docs/guides/ | head -10
```

---

## 🎓 Best Practices for AI Assistants

### **DO**
- ✅ **Always** read `RECENT_CHANGES.md` first
- ✅ **Run** context briefing before major changes
- ✅ **Check** weekly summaries for recent activity
- ✅ **Search** codebase before creating new tools
- ✅ **Update** RECENT_CHANGES.md after your work
- ✅ **Use** conventional commits format
- ✅ **Follow** PROJECT_STRUCTURE_RULES.md

### **DON'T**
- ❌ Start work without checking context
- ❌ Create tools that already exist
- ❌ Ignore recent changes file
- ❌ Skip structure validation
- ❌ Commit without conventional format
- ❌ Duplicate documentation

---

## 📝 Updating RECENT_CHANGES.md

### **When to Update**
- After implementing any major feature
- After creating new tools/scripts
- After architectural changes
- After breaking changes
- Weekly (at minimum)

### **What to Include**

```markdown
## 🆕 This Week (Oct XX-XX, 2025)

### Major Implementation: [Feature Name] ✅
- ✅ What was done
- ✅ What changed
- ✅ New tools created

### New Files Created (X)
- List each new file with brief description

### Breaking Changes ⚠️
- Any changes that affect existing functionality

### New Tools Available
- Commands and their purposes

## 🚧 Active Work
- What's currently in progress
- Who's working on it
- Expected completion

## 📋 Next Planned
- What's coming next
```

---

## 🔧 Tools for Context Awareness

### **1. Context Briefing**
```bash
./scripts/utilities/context_briefing.sh
```
Shows complete project context in one command

### **2. Weekly Summary**
```bash
./tools/reports/weekly_summary.sh
```
Last week's changes by type

### **3. Repository Health**
```bash
python3 tools/reports/repo_health.py
```
Current state of project

### **4. Structure Validation**
```bash
python3 tools/audit/enforce_structure.py
```
12 compliance checks

### **5. Git Search**
```bash
# Search commits
git log --all --grep="keyword"

# Search code
grep -r "pattern" /home/zoe/assistant/

# Recent changes to file
git log --since="1 week ago" -- path/file
```

---

## 🎯 Example Workflow

### **Scenario: User Asks "Add a feature to track weekly progress"**

**Bad Approach** ❌:
```
1. Immediately start coding
2. Create new tool without checking
3. Duplicate existing weekly_summary.sh
4. Waste time on redundant work
```

**Good Approach** ✅:
```bash
# 1. Check context
cat RECENT_CHANGES.md | grep -i "weekly\|progress\|tracking"
# Found: "✅ Weekly summaries (exists)"

# 2. Check if it exists
ls -la tools/reports/
# Found: weekly_summary.sh

# 3. Test existing tool
./tools/reports/weekly_summary.sh
# Output: Perfect! This already does what's needed

# 4. Response to user
"The weekly progress tracking already exists!
Run: ./tools/reports/weekly_summary.sh
See: docs/guides/CHANGE_MANAGEMENT.md for details"
```

---

## 📊 Quick Reference

| Question | Command |
|----------|---------|
| What changed recently? | `cat RECENT_CHANGES.md` |
| Full context briefing? | `./scripts/utilities/context_briefing.sh` |
| Last week's changes? | `./tools/reports/weekly_summary.sh` |
| Repository health? | `python3 tools/reports/repo_health.py` |
| Structure compliant? | `python3 tools/audit/enforce_structure.py` |
| Does tool exist? | `ls tools/` or `grep -r "name" .` |
| Recent commits? | `git log --oneline -10` |
| Who changed file? | `git log -- path/file` |

---

## 🚀 For Cursor Specifically

### **Recommended Cursor Settings**

In your Cursor workspace, consider:

1. **Always Open Files**:
   - `RECENT_CHANGES.md`
   - `PROJECT_STATUS.md`
   - `PROJECT_STRUCTURE_RULES.md`

2. **Quick Commands** (Cursor Composer):
   ```
   @workspace Check recent changes
   @workspace Run context briefing
   @workspace Search for similar feature
   ```

3. **Before Any Task**:
   - Read `RECENT_CHANGES.md`
   - Run `./scripts/utilities/context_briefing.sh`
   - Search codebase for related work

---

## 🎓 Training New AI Sessions

When starting a new Cursor session:

```markdown
1. Read these files in order:
   - RECENT_CHANGES.md
   - PROJECT_STATUS.md
   - PROJECT_STRUCTURE_RULES.md
   - docs/guides/CHANGE_MANAGEMENT.md

2. Run context briefing:
   ./scripts/utilities/context_briefing.sh

3. Check for existing tools:
   ls tools/
   ls scripts/

4. Review recent commits:
   git log --oneline -20
```

---

## 📞 Questions?

**Q: How often should RECENT_CHANGES.md be updated?**  
A: After every major feature or at least weekly

**Q: What if context briefing script fails?**  
A: Manually check: `RECENT_CHANGES.md` + `git log` + `ls tools/`

**Q: Can I skip context checks for small changes?**  
A: No! Even small changes might duplicate existing features

**Q: What's the minimum Cursor should read?**  
A: At minimum: `RECENT_CHANGES.md` - it's the single source of truth

---

**Remember**: 2 minutes of context checking saves 2 hours of duplicate work! 🚀



