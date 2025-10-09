# Database Consolidation Plan
**Created**: October 9, 2025  
**Status**: Ready for Execution  
**Safety**: Full backups created at `data/backup/pre-consolidation-20251009_150213/`

## 📊 Current State Analysis

### Databases Found: 19 total
- **441 references** across **95 files**
- **Critical duplicates** causing data inconsistency
- **Split-brain architecture** with multiple sources of truth

### Database Breakdown

| Database | References | Files | Primary Use | Action Plan |
|----------|-----------|-------|-------------|-------------|
| `zoe.db` | 204 | 52 | **Main operational data** | ✅ Keep as PRIMARY |
| `developer_tasks.db` | 151 | 22 | Development tasks | ⚠️ Consolidate INTO zoe.db |
| `memory.db` | 37 | 13 | Light RAG embeddings | ✅ Keep SEPARATE (specialized) |
| `auth.db` | 6 | 2 | User authentication | ⚠️ Consolidate INTO zoe.db |
| `sessions.db` | 2 | 2 | User sessions | ⚠️ Consolidate INTO zoe.db |
| `satisfaction.db` | 2 | 1 | User feedback | ⚠️ Consolidate INTO zoe.db |
| `self_awareness.db` | 2 | 2 | AI self-awareness | ⚠️ Consolidate INTO zoe.db |
| `learning.db` | 4 | 2 | AI learning patterns | ⚠️ Consolidate INTO zoe.db |
| `agent_planning.db` | 3 | 1 | Agent planning | ⚠️ Consolidate INTO zoe.db |
| `tool_registry.db` | 3 | 1 | Tool registry | ⚠️ Consolidate INTO zoe.db |
| `snapshots.db` | 4 | 2 | System snapshots | ⚠️ Consolidate INTO zoe.db |
| `model_performance.db` | 4 | 2 | Model metrics | ⚠️ Consolidate INTO zoe.db |
| `context_cache.db` | 2 | 1 | Context caching | ⚠️ Consolidate INTO zoe.db |
| `knowledge.db` | 1 | 1 | Knowledge base | ⚠️ Consolidate INTO zoe.db |
| `aider_conversations.db` | 6 | 3 | Aider conversations | ⚠️ Consolidate INTO zoe.db |
| Others | Various | Various | Misc | Evaluate case-by-case |

## 🎯 Consolidation Strategy

### **TWO-DATABASE ARCHITECTURE** (Recommended)

```
┌─────────────────────────────────────┐
│         zoe.db (PRIMARY)            │
│  ✅ Single Source of Truth          │
│  - Users & Authentication           │
│  - People, Projects, Notes          │
│  - Calendar Events                  │
│  - Lists & Tasks                    │
│  - Developer Tasks                  │
│  - Journal Entries                  │
│  - System Metrics                   │
│  - All operational data             │
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│      memory.db (SPECIALIZED)        │
│  ✅ Light RAG Semantic Search       │
│  - Entity Embeddings                │
│  - Relationship Embeddings          │
│  - Vector Search Cache              │
│  - Large embedding data             │
└─────────────────────────────────────┘
```

### Why This Approach?

1. **Light RAG is Specialized**: Embeddings are large binary data, separate architecture makes sense
2. **Operational Clarity**: Main app data separate from ML/AI data
3. **Performance**: Embedding queries don't impact operational queries
4. **Backup Strategy**: Can backup operational vs. ML data separately

## 🚨 Critical Duplicate Issues

### 1. **Users Table** (CRITICAL)
- **zoe.db**: 4 users (testuser, admin, user, system)
- **auth.db**: 2 users (admin, user) - **MISSING testuser & system**
- **developer_tasks.db**: 1 user
- **Problem**: Authentication fails for users not in auth.db
- **Solution**: Merge all into zoe.db, update zoe-auth service to use zoe.db

### 2. **People Table**
- **zoe.db**: 17 people
- **memory.db**: 0 people
- **Problem**: Light RAG has no data despite being configured
- **Solution**: Keep in zoe.db, populate memory.db embeddings as needed

### 3. **Sessions Table**
- **auth.db**: sessions table
- **sessions.db**: sessions table
- **Problem**: Session fragmentation
- **Solution**: Consolidate into zoe.db

## 📋 Migration Steps

### Phase 1: Preparation ✅ COMPLETED
- [x] Comprehensive database audit (441 references in 95 files)
- [x] Full backup created (6.0MB backed up to `pre-consolidation-20251009_150213/`)
- [x] Analysis of duplicate tables
- [x] Migration plan documented

### Phase 2: Schema Updates (PENDING)
- [ ] Verify zoe.db has all necessary tables
- [ ] Add missing tables from other databases
- [ ] Create unified schema if needed
- [ ] Test schema compatibility

### Phase 3: Data Migration (PENDING)
- [ ] Migrate users (merge from auth.db, developer_tasks.db)
- [ ] Migrate sessions (merge from auth.db, sessions.db)
- [ ] Migrate developer tasks
- [ ] Migrate all other tables
- [ ] Verify data integrity

### Phase 4: Code Updates (PENDING)
- [ ] Update ALL 95 files with database references
- [ ] Point all services to zoe.db (except Light RAG → memory.db)
- [ ] Update environment variables in docker-compose.yml
- [ ] Update zoe-auth service configuration

### Phase 5: Protection Mechanisms (PENDING)
- [ ] Create database validation script
- [ ] Add pre-commit hook to prevent new DB files
- [ ] Update PROJECT_STRUCTURE_RULES.md
- [ ] Add automated enforcement

### Phase 6: Testing & Verification (PENDING)
- [ ] Test all API endpoints
- [ ] Verify user authentication
- [ ] Test memory operations
- [ ] Run full system test suite
- [ ] Verify no regressions

## 🛡️ Safety Measures

### Before Migration
- ✅ Full backup created (6.0MB at `data/backup/pre-consolidation-20251009_150213/`)
- ✅ Audit report saved (`database_audit_report.json`)
- ✅ Migration plan documented

### During Migration
- [ ] Dry-run mode available
- [ ] Transaction-based migrations (rollback capable)
- [ ] Verification at each step
- [ ] Detailed logging

### After Migration
- [ ] Integrity checks
- [ ] Data count verification
- [ ] Service health checks
- [ ] Performance benchmarks

## 📝 Files Requiring Updates

### Critical Service Files (Must Update)
1. **services/zoe-auth/simple_main.py** - Currently uses `auth.db`
2. **services/zoe-core/routers/auth.py** - Uses `zoe.db` (correct)
3. **services/zoe-core/routers/developer_tasks.py** - Uses `developer_tasks.db`
4. **services/zoe-core/session_manager.py** - Uses `sessions.db`
5. **services/zoe-core/user_satisfaction.py** - Uses `satisfaction.db`
6. **services/zoe-core/self_awareness.py** - Uses `self_awareness.db`
7. **services/zoe-core/learning_system.py** - Uses `learning.db`

### Docker Configuration
- **docker-compose.yml** - Environment variables
- **services/zoe-auth/Dockerfile** - Database path configuration

### Total Files Needing Review: 95 files

## 🔐 Protection Rules (Post-Migration)

### New DATABASE Rules for PROJECT_STRUCTURE_RULES.md

```markdown
## 🗄️ DATABASE MANAGEMENT RULES

### CRITICAL: Single Source of Truth
- **PRIMARY DATABASE**: `/app/data/zoe.db` ONLY
- **SPECIALIZED DATABASE**: `/app/data/memory.db` (Light RAG embeddings ONLY)
- **FORBIDDEN**: Creating new .db files without architectural review

### Database File Rules
- ❌ **NEVER CREATE**: New .db files in /data/ directory
- ❌ **FORBIDDEN**: auth.db, sessions.db, tasks.db, or any duplicate DBs
- ✅ **ALLOWED**: zoe.db (primary), memory.db (Light RAG only)
- ✅ **TEMPORARY**: Test databases in /tmp/ only, never committed

### Code Rules
- **ALL services** must use DATABASE_PATH environment variable
- **DEFAULT** should always be `/app/data/zoe.db`
- **Light RAG ONLY** may use `/app/data/memory.db`
- **NO hardcoded** database paths except in config

### Enforcement
- Pre-commit hook checks for new .db files
- Validation script runs on CI/CD
- Automated warnings for database references
```

## 📊 Expected Outcomes

### After Consolidation
- ✅ **Single source of truth** for all operational data
- ✅ **No more duplicate users** or auth issues
- ✅ **Consistent data** across all services
- ✅ **Simpler architecture** (2 DBs instead of 19)
- ✅ **Better performance** (no cross-DB queries)
- ✅ **Easier backups** (2 files instead of 19)

### Performance Impact
- **Minimal**: SQLite handles multi-table DBs efficiently
- **Positive**: Eliminates cross-file queries
- **Storage**: Reduce from 19 files to 2 files

## 🚀 Rollback Plan

### If Migration Fails
1. **Stop all services**: `./stop-zoe.sh`
2. **Restore backups**:
   ```bash
   cp data/backup/pre-consolidation-20251009_150213/* data/
   ```
3. **Restart services**: `./start-zoe.sh`
4. **Verify**: Run health checks

### Rollback Time
- **Estimated**: < 5 minutes
- **Automated**: Script available at `scripts/rollback_database.sh`

## 📚 Documentation Updates Needed

1. **PROJECT_STRUCTURE_RULES.md** - Add database rules
2. **ARCHITECTURE.md** - Update database architecture diagram
3. **README.md** - Update database section
4. **MAINTENANCE.md** - Add database management procedures
5. **This document** - Reference for future

---

**Next Steps**: Review this plan, then execute Phase 2-6 systematically.

**Approval Required**: User confirmation before executing migration.

