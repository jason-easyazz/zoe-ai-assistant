# Change Management Guide - Zoe AI Assistant

**Version**: 1.0  
**Date**: October 18, 2025  
**Status**: 🔒 ACTIVE

This guide explains how to properly manage and track changes in the Zoe project using conventional commits, automated CHANGELOG generation, and change tracking tools.

---

## 📋 Table of Contents

1. [Conventional Commits](#conventional-commits)
2. [Commit Message Format](#commit-message-format)
3. [Automated CHANGELOG](#automated-changelog)
4. [Version Tagging](#version-tagging)
5. [Weekly Summaries](#weekly-summaries)
6. [Change Tracking Workflow](#change-tracking-workflow)

---

## 🎯 Conventional Commits

We use **Conventional Commits** standard for all commit messages. This enables:

- ✅ Automatic CHANGELOG generation
- ✅ Clear change categorization
- ✅ Easy filtering and searching
- ✅ Semantic versioning
- ✅ Better collaboration

**Official Spec**: [conventionalcommits.org](https://www.conventionalcommits.org)

---

## ✍️ Commit Message Format

### Basic Format

```
type(scope): description

[optional body]

[optional footer]
```

### Components

1. **Type** (required): Category of change
2. **Scope** (optional): Area affected
3. **Description** (required): Brief summary (min 10 chars)
4. **Body** (optional): Detailed explanation
5. **Footer** (optional): Breaking changes, issue references

### Valid Types

| Type | Purpose | Example |
|------|---------|---------|
| `feat` | New feature | `feat(chat): Add voice command support` |
| `fix` | Bug fix | `fix(calendar): Fix timezone conversion` |
| `db` | Database changes | `db: Add indexes for faster queries` |
| `docs` | Documentation | `docs: Update API reference` |
| `style` | Code formatting | `style: Fix indentation` |
| `refactor` | Code restructure | `refactor(auth): Simplify user lookup` |
| `perf` | Performance | `perf: Cache API responses` |
| `test` | Tests | `test: Add unit tests for chat router` |
| `build` | Build system | `build: Update Docker configuration` |
| `ci` | CI/CD | `ci: Add GitHub Actions workflow` |
| `chore` | Maintenance | `chore: Update dependencies` |

### Examples

#### Feature Addition
```bash
git commit -m "feat(chat): Add auto-discovery router system

Implements dynamic router loading instead of manual imports.
Reduces main.py complexity and improves maintainability.
"
```

#### Bug Fix
```bash
git commit -m "fix(auth): Database configuration for multi-user

Fixed issue where auth.db was not being used for authentication.
Updated to use consolidated zoe.db instead.

Closes #123
"
```

#### Database Change
```bash
git commit -m "db: Upgrade to v2.3.1 with connection pooling

Added SQLite connection pooling (5 connections)
Enabled WAL mode for better concurrency
Added 12 performance indexes

Performance improvement: 10-20x faster queries
"
```

#### Documentation
```bash
git commit -m "docs: Archive completion reports to docs/archive/"
```

#### Chore/Maintenance
```bash
git commit -m "chore: Clean up temporary test files"
```

---

## 🔄 Automated CHANGELOG

### How It Works

The `generate_changelog.py` script:
1. Reads commits since last git tag
2. Parses conventional commit messages
3. Groups changes by type
4. Updates CHANGELOG.md automatically

### Generate CHANGELOG

```bash
# Auto-detect version from last tag
python3 tools/generators/generate_changelog.py

# Specify version
python3 tools/generators/generate_changelog.py --version v2.4.0

# Preview without writing
python3 tools/generators/generate_changelog.py --dry-run
```

### Example Output

```markdown
## [v2.4.0] - 2025-10-18

### ✨ Features
- **chat**: Add auto-discovery router system (a1b2c3d)
- **ui**: Implement dark mode toggle (d4e5f6g)

### 🐛 Bug Fixes
- **auth**: Database configuration for multi-user (g7h8i9j)
- **calendar**: Fix timezone conversion (j1k2l3m)

### 💾 Database Changes
- Upgrade to v2.3.1 with connection pooling (m4n5o6p)
- Add indexes for faster queries (p7q8r9s)

### 📚 Documentation
- Archive completion reports (s1t2u3v)
- Update API reference (v4w5x6y)
```

---

## 🏷️ Version Tagging

### Tag Types

#### Release Tags
```bash
# Major release (breaking changes)
git tag -a v3.0.0 -m "Release v3.0.0: Complete redesign"

# Minor release (new features)
git tag -a v2.4.0 -m "Release v2.4.0: Governance improvements"

# Patch release (bug fixes)
git tag -a v2.3.2 -m "Release v2.3.2: Security patches"
```

#### Database Migration Tags
```bash
git tag -a db-upgrade-20251018-pooling -m "Database: Add connection pooling"
```

#### Feature Tags
```bash
git tag -a feat-voice-commands -m "Feature: Voice command support"
```

### Tagging Workflow

1. **Make changes and commit** using conventional format
2. **Generate CHANGELOG**:
   ```bash
   python3 tools/generators/generate_changelog.py --version v2.4.0
   ```
3. **Review** the generated CHANGELOG.md
4. **Commit the CHANGELOG**:
   ```bash
   git add CHANGELOG.md
   git commit -m "chore: Update CHANGELOG for v2.4.0"
   ```
5. **Create tag**:
   ```bash
   git tag -a v2.4.0 -m "Release v2.4.0: Governance improvements"
   ```
6. **Push** (include tags):
   ```bash
   git push origin main --tags
   ```

---

## 📊 Weekly Summaries

### Generate Summary

```bash
# Last week
./tools/reports/weekly_summary.sh

# Last 2 weeks
./tools/reports/weekly_summary.sh 2

# Last month (4 weeks)
./tools/reports/weekly_summary.sh 4
```

### Example Output

```
========================================
Zoe Weekly Change Summary
Week of 2025-10-11 to 2025-10-18
========================================

📊 Statistics
  Total commits: 15
  Features added: 3
  Bugs fixed: 2
  Database changes: 1
  Documentation: 4
  Refactoring: 1
  Chores: 4

👥 Contributors
  John Doe: 15 commits

📁 Impact
  Files changed: 45
  Lines added: +1,234
  Lines removed: -567

📝 Recent Commits
  • a1b2c3d feat(chat): Add voice commands
  • d4e5f6g fix(auth): Multi-user support
  • g7h8i9j db: Add connection pooling
  ...

🏷️ Tags Created
  • v2.4.0
```

---

## 🔄 Change Tracking Workflow

### Daily Workflow

1. **Start feature/fix**:
   ```bash
   git checkout -b feat/voice-commands
   ```

2. **Make changes** and commit using conventional format:
   ```bash
   git add .
   git commit -m "feat(chat): Add voice command parsing"
   ```

3. **Continue** until feature complete

4. **Merge** back to main:
   ```bash
   git checkout main
   git merge feat/voice-commands
   ```

### Weekly Workflow

1. **Review changes** made this week:
   ```bash
   ./tools/reports/weekly_summary.sh
   ```

2. **Identify** notable changes for release notes

3. **Document** any important decisions in PROJECT_STATUS.md

### Release Workflow

1. **Decide on version** (major.minor.patch)

2. **Generate CHANGELOG**:
   ```bash
   python3 tools/generators/generate_changelog.py --version v2.4.0
   ```

3. **Review and edit** CHANGELOG.md if needed

4. **Commit CHANGELOG**:
   ```bash
   git add CHANGELOG.md
   git commit -m "chore: Update CHANGELOG for v2.4.0"
   ```

5. **Tag release**:
   ```bash
   git tag -a v2.4.0 -m "Release v2.4.0: Governance improvements"
   ```

6. **Update PROJECT_STATUS.md** with release notes

7. **Push everything**:
   ```bash
   git push origin main --tags
   ```

---

## 🛠️ Tools Reference

### Available Scripts

| Script | Purpose | Usage |
|--------|---------|-------|
| `validate_commit_message.sh` | Validates commit format | Auto (commit-msg hook) |
| `generate_changelog.py` | Generates CHANGELOG | `python3 tools/generators/generate_changelog.py` |
| `weekly_summary.sh` | Weekly change report | `./tools/reports/weekly_summary.sh` |
| `repo_health.py` | Repository health check | `python3 tools/reports/repo_health.py` |

### Pre-commit Hooks

#### commit-msg Hook
Automatically validates commit messages before commit is created.

**Location**: `.git/hooks/commit-msg`

If commit message is invalid, you'll see:
```
❌ Invalid commit message format

Commit message must follow Conventional Commits:
  type(scope): description

Your message:
  Fixed a bug
```

Fix it by following the format!

---

## 📚 Additional Resources

- **Conventional Commits**: https://www.conventionalcommits.org
- **Semantic Versioning**: https://semver.org
- **Git Best Practices**: https://git-scm.com/book/en/v2

---

## ❓ FAQ

**Q: What if I make a typo in my commit message?**  
A: Amend it before pushing:
```bash
git commit --amend -m "fix(auth): Correct typo in commit message"
```

**Q: Can I skip the conventional format for quick fixes?**  
A: No. The commit-msg hook enforces the format. All commits must follow it.

**Q: What scope should I use?**  
A: Use the component/module affected: `chat`, `auth`, `calendar`, `db`, `ui`, etc.

**Q: How do I know which type to use?**  
A: Ask yourself: "Does this add a feature?" (feat), "Does this fix a bug?" (fix), "Does this change the database?" (db), etc.

**Q: Can I have multiple types in one commit?**  
A: No. Break it into multiple commits, each with a single purpose.

---

**Remember**: Good commit messages = Easy change tracking = Better collaboration! 🚀



