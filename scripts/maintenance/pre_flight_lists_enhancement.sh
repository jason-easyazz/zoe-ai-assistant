#!/bin/bash
# Enhanced Lists System - Pre-Flight Check
# Run BEFORE starting Enhanced Lists implementation

# Don't exit on error - we want to run all checks
# set -e

echo "🚀 Enhanced Lists System - Pre-Flight Check"
echo "============================================"
echo ""

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

ERRORS=0
WARNINGS=0

# Function to print results
check_pass() {
    echo -e "${GREEN}   ✅ $1${NC}"
}

check_warn() {
    echo -e "${YELLOW}   ⚠️  $1${NC}"
    ((WARNINGS++))
}

check_fail() {
    echo -e "${RED}   ❌ $1${NC}"
    ((ERRORS++))
}

# 1. MCP Server Check
echo "1️⃣ Checking MCP server for reminder tools..."
if docker exec zoe-mcp-server ls /app/tools/reminders.py > /dev/null 2>&1; then
    check_pass "MCP reminder tool exists"
elif docker exec zoe-mcp-server grep -r "reminder\|set_reminder" /app > /dev/null 2>&1; then
    check_warn "Reminder code found but may need review"
    echo "      ACTION: Verify MCP reminder tool implementation"
else
    check_fail "MCP reminder tools NOT found - must implement first"
    echo "      ACTION: Add reminder tool to zoe-mcp-server before proceeding"
fi

# 2. WebSocket Check
echo ""
echo "2️⃣ Checking WebSocket support..."
if docker exec zoe-core grep -n "WebSocket\|websocket" /app/main.py > /dev/null 2>&1; then
    check_pass "WebSocket support detected"
    echo "      INFO: WebSocket available but plan uses simple refetch for rename"
else
    check_warn "No WebSocket support - will use polling for list rename"
    echo "      DECISION: Use simple refetch instead of WebSocket broadcast"
fi

# 3. Database Access
echo ""
echo "3️⃣ Checking database access..."
if [ -f /home/zoe/assistant/data/zoe.db ]; then
    check_pass "Database accessible"
    
    # Check current schema
    if sqlite3 /home/zoe/assistant/data/zoe.db "PRAGMA table_info(list_items)" 2>/dev/null | grep -q "parent_id"; then
        check_warn "parent_id column already exists - migration may have run"
    else
        check_pass "Schema ready for migration"
    fi
else
    check_fail "Cannot access database"
    echo "      ACTION: Verify database exists at /home/zoe/assistant/data/zoe.db"
fi

# 4. Platform Detection
echo ""
echo "4️⃣ Detecting platform..."
PLATFORM=$(docker exec zoe-core python3 -c "from model_config import detect_hardware; print(detect_hardware())" 2>/dev/null || echo "unknown")
if [ -n "$PLATFORM" ] && [ "$PLATFORM" != "unknown" ]; then
    check_pass "Platform: $PLATFORM"
    
    # Set depth limits based on platform
    case $PLATFORM in
        jetson)
            echo "      INFO: Max hierarchy depth will be 5 (GPU-accelerated)"
            ;;
        pi5)
            echo "      INFO: Max hierarchy depth will be 3 (CPU-optimized)"
            ;;
        *)
            check_warn "Unknown platform - will use conservative depth limit of 3"
            ;;
    esac
else
    check_warn "Platform detection failed - will use default settings"
    echo "      INFO: Will use conservative depth limit of 3"
fi

# 5. Current Performance Baseline
echo ""
echo "5️⃣ Testing current list performance..."
if curl -s http://localhost:8000/api/lists/shopping > /dev/null 2>&1; then
    START=$(date +%s%N)
    curl -s http://localhost:8000/api/lists/shopping > /dev/null
    END=$(date +%s%N)
    DURATION=$((($END - $START) / 1000000))
    
    if [ $DURATION -lt 200 ]; then
        check_pass "Response time: ${DURATION}ms (excellent)"
    elif [ $DURATION -lt 500 ]; then
        check_pass "Response time: ${DURATION}ms (good)"
    else
        check_warn "Response time: ${DURATION}ms (high latency detected)"
        echo "      RECOMMENDATION: Optimize before adding features"
    fi
else
    check_fail "Cannot connect to zoe-core API"
    echo "      ACTION: Verify zoe-core is running on port 8000"
fi

# 6. Check for existing migration tracking
echo ""
echo "6️⃣ Checking migration status..."
if docker exec zoe-core sqlite3 /app/data/zoe.db "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'" 2>/dev/null | grep -q "schema_migrations"; then
    check_pass "Migration tracking table exists"
    
    LAST_MIGRATION=$(docker exec zoe-core sqlite3 /app/data/zoe.db "SELECT MAX(version) FROM schema_migrations" 2>/dev/null || echo "0")
    echo "      INFO: Last migration version: ${LAST_MIGRATION:-0}"
else
    check_warn "No migration tracking - will create schema_migrations table"
    echo "      INFO: Migration script will create this table"
fi

# 7. Backup Check
echo ""
echo "7️⃣ Checking backup capability..."
if [ -f /home/zoe/assistant/data/zoe.db ]; then
    check_pass "Database file found for backup"
    
    # Create backup directory
    BACKUP_DIR="/home/zoe/assistant/backups/pre_lists_enhancement_$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$BACKUP_DIR"
    cp /home/zoe/assistant/data/zoe.db "$BACKUP_DIR/zoe.db" 2>/dev/null || check_warn "Could not create backup automatically"
    
    if [ -f "$BACKUP_DIR/zoe.db" ]; then
        check_pass "Backup created: $BACKUP_DIR"
    else
        check_warn "Backup directory created but file copy may have failed"
    fi
else
    check_warn "Database file not found at expected location"
    echo "      INFO: Database may be in Docker volume, backup will be handled by migration script"
fi

# Summary
echo ""
echo "============================================"
echo "Pre-Flight Check Complete"
echo "============================================"

if [ $ERRORS -gt 0 ]; then
    echo -e "${RED}❌ $ERRORS critical error(s) found - DO NOT PROCEED${NC}"
    echo ""
    echo "Fix all errors above before starting implementation."
    exit 1
elif [ $WARNINGS -gt 0 ]; then
    echo -e "${YELLOW}⚠️  $WARNINGS warning(s) found - review before proceeding${NC}"
    echo ""
    echo "Warnings are not blockers but should be addressed."
    echo ""
    read -p "Continue with implementation? (yes/no): " CONTINUE
    if [ "$CONTINUE" != "yes" ]; then
        echo "Aborting. Address warnings and run again."
        exit 1
    fi
else
    echo -e "${GREEN}✅ All checks passed! Ready to proceed.${NC}"
fi

echo ""
echo "Next Steps:"
echo "  1. Review findings above"
echo "  2. Run migration: python3 scripts/maintenance/migrate_lists_enhancements.py"
echo "  3. Begin Phase 2: Update backend API"
echo ""

exit 0

