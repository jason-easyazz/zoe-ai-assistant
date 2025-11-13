# Cleanup Plan - Post-Migration Tasks
**Date:** 2025-11-13  
**Context:** After llama.cpp migration & unified memory implementation

---

## 🎯 CLEANUP OBJECTIVES

1. Remove deprecated vLLM files (BLOCKED migration)
2. Clean up temporary test/debug files
3. Remove duplicate/obsolete documentation
4. Update references to point to new architecture
5. Consolidate documentation into single source of truth

---

## 📁 FILES TO REMOVE

### Category 1: vLLM Migration (BLOCKED - Can be removed)

**Reason:** vLLM migration failed due to PyTorch CUDA allocator bug. Switched to llama.cpp successfully.

#### Documentation (Safe to Archive)
```bash
# Move to docs/archive/ for historical reference
- VLLM_MIGRATION_STATUS.md
- VLLM_BUILD_CHALLENGES.md
- VLLM_EXHAUSTIVE_DEBUG_SUMMARY.md
- VLLM_MIGRATION_SUMMARY.md
- vllm-debug-log.md
- VLLM_MIGRATION_BLOCKED.md
- docs/architecture/VLLM_PRODUCTION_ARCHITECTURE.md
```

**Action:** Archive, don't delete (historical value for future Jetson developers)

#### Code (Safe to Delete)
```bash
# These are unused
- services/zoe-vllm/vllm_server.py
- services/zoe-vllm/Dockerfile
- services/zoe-vllm/entrypoint*.sh
- services/zoe-vllm/test_*.py
```

**Action:** Delete entire `services/zoe-vllm/` directory

---

### Category 2: Temporary Test Files

```bash
# Benchmark scripts (one-time use)
- /tmp/bench_llamacpp.sh
- /tmp/test_unified_memory.sh
- /tmp/test_unified_memory_v2.sh

# Migration scripts (already executed)
- scripts/migrations/create_self_entries.py  # Keep for re-runs if needed
```

**Action:** Remove /tmp files, keep migrations for reference

---

### Category 3: Duplicate Documentation

```bash
# User profile system (deprecated)
- services/zoe-core/user_profile_schema.py  # Keep but mark deprecated
- services/zoe-core/profile_analyzer.py     # Keep but mark deprecated

# Old architecture docs (superseded)
- Check for any Ollama-specific docs that reference old setup
```

**Action:** Add deprecation warnings, don't delete yet (grace period)

---

### Category 4: Old Model Weights (If Downloaded)

```bash
# AWQ models (if downloaded for vLLM - never used)
- models/*awq*
- models/*vllm*

# Keep GGUF models (active)
- models/llama-3.2-3b-gguf/  ✅ KEEP
- models/qwen2.5-coder-7b-gguf/  ✅ KEEP
```

**Action:** Check models/ directory, remove unused formats

---

## 🔍 AUDIT STEPS

### Step 1: Validate Critical Files
```bash
# Ensure these exist and are up-to-date
✅ services/zoe-llamacpp/Dockerfile
✅ services/zoe-llamacpp/entrypoint.sh
✅ services/zoe-core/routers/people.py (unified)
✅ services/zoe-core/routers/chat.py (updated prompts)
✅ services/zoe-mcp-server/main.py (store_self_fact)
```

### Step 2: Check File References
```bash
# Search for references to removed files
grep -r "vllm" services/zoe-core/
grep -r "user_profiles" services/zoe-core/ | grep -v "# deprecated"
```

### Step 3: Verify Docker Compose
```bash
# Ensure docker-compose.yml only references active services
- zoe-llamacpp ✅
- zoe-core ✅
- zoe-mcp-server ✅
- zoe-mem-agent ✅

# Remove any zoe-vllm references
```

---

## 📋 CLEANUP SCRIPT

```bash
#!/bin/bash
# cleanup_post_migration.sh

echo "🧹 Starting Post-Migration Cleanup"
echo "===================================="
echo ""

# Safety: Backup before cleanup
echo "1️⃣ Creating safety backup..."
cd /home/zoe/assistant
git add -A
git commit -m "Pre-cleanup safety commit" || echo "Nothing to commit"
git tag "pre-cleanup-$(date +%Y%m%d-%H%M%S)"

# Step 1: Archive vLLM docs
echo "2️⃣ Archiving vLLM documentation..."
mkdir -p docs/archive/vllm-migration-2025-11
mv VLLM_*.md docs/archive/vllm-migration-2025-11/
mv vllm-debug-log.md docs/archive/vllm-migration-2025-11/
mv docs/architecture/VLLM_PRODUCTION_ARCHITECTURE.md docs/archive/vllm-migration-2025-11/

# Step 2: Remove vLLM service directory
echo "3️⃣ Removing vLLM service directory..."
rm -rf services/zoe-vllm/

# Step 3: Clean /tmp test files
echo "4️⃣ Cleaning temporary test files..."
rm -f /tmp/bench_llamacpp.sh
rm -f /tmp/test_unified_memory*.sh
rm -f /tmp/llamacpp-build.log

# Step 4: Remove unused model formats (if any)
echo "5️⃣ Checking for unused model files..."
if [ -d "models" ]; then
    find models/ -name "*awq*" -type d | while read dir; do
        echo "   Would remove: $dir"
        # Uncomment to actually remove:
        # rm -rf "$dir"
    done
fi

# Step 5: Add deprecation warnings
echo "6️⃣ Adding deprecation warnings..."
cat > services/zoe-core/DEPRECATED_FILES.md << 'EOF'
# Deprecated Files

## user_profiles System
**Status:** Deprecated as of 2025-11-13
**Reason:** Replaced by unified people table with is_self flag
**Migration:** Use /api/people/self endpoints instead

**Deprecated Files:**
- user_profile_schema.py
- profile_analyzer.py
- routers/user_profile.py

**Do not delete yet:** Grace period until 2025-12-13
EOF

# Step 7: Update documentation references
echo "7️⃣ Updating documentation references..."
# Add note to main README about llama.cpp
grep -q "llama.cpp" README.md || echo "
## LLM Backend
Currently using llama.cpp with GGUF models for optimal Jetson performance.
See LLAMACPP_PERFORMANCE_REPORT.md for benchmarks.
" >> README.md

echo ""
echo "✅ Cleanup complete!"
echo ""
echo "📊 Summary:"
echo "   - vLLM docs: Archived to docs/archive/vllm-migration-2025-11/"
echo "   - vLLM service: Removed"
echo "   - Temp files: Cleaned"
echo "   - Deprecation notices: Added"
echo ""
echo "🔍 Next steps:"
echo "   1. Review changes: git status"
echo "   2. Test system: docker-compose up -d"
echo "   3. Commit: git commit -am 'Cleanup: Remove vLLM files, archive docs'"
```

---

## ⚠️ SAFETY CHECKS

Before executing cleanup:

1. **✅ Create git commit** - `git commit -am "Pre-cleanup safety"`
2. **✅ Create git tag** - `git tag pre-cleanup-20251113`
3. **✅ Test system works** - Verify llama.cpp responds
4. **✅ Backup database** - `cp data/zoe.db data/zoe.db.backup`
5. **✅ Document what's removed** - Keep this plan for reference

---

## 📅 TIMELINE

### Immediate (Now)
- [x] Create cleanup plan
- [ ] Review with user
- [ ] Get approval to proceed

### Phase 1 (15 minutes)
- [ ] Create safety backup
- [ ] Archive vLLM docs
- [ ] Remove vLLM service directory

### Phase 2 (5 minutes)
- [ ] Clean temporary files
- [ ] Add deprecation warnings
- [ ] Update README references

### Phase 3 (10 minutes)
- [ ] Test system after cleanup
- [ ] Verify all services start
- [ ] Commit changes

### Phase 4 (Grace Period - 30 days)
- [ ] Monitor for any issues
- [ ] After 30 days: Remove deprecated user_profiles files
- [ ] Final cleanup commit

---

## 📏 SIZE REDUCTION ESTIMATE

```
Before Cleanup:
- vLLM docs: ~150KB
- vLLM service: ~50KB
- Temp files: ~20KB
- Total: ~220KB

After Cleanup:
- Archived docs: refs only
- Service removed: 0KB
- Cleaner structure: Easier navigation

Disk savings: Minimal, but organization: Priceless
```

---

## ✅ VALIDATION CHECKLIST

After cleanup, verify:

- [ ] Docker services start: `docker-compose up -d`
- [ ] Health check passes: `curl localhost:8000/health`
- [ ] Llama.cpp responds: Test query
- [ ] Unified memory works: Store/retrieve personal fact
- [ ] MCP tools work: Test tool calling
- [ ] No broken references: `grep -r "vllm" services/`

---

## 🎯 SUCCESS CRITERIA

✅ vLLM files archived (not lost)  
✅ Active services unchanged  
✅ System still functional  
✅ No broken references  
✅ Documentation updated  
✅ Git history preserved  

**Goal:** Cleaner codebase without losing historical context.

---

**Status:** AWAITING APPROVAL  
**Risk Level:** LOW (safety backups in place)  
**Estimated Time:** 30 minutes  
**Reversible:** YES (git tags + archives)


