#!/bin/bash
#
# Quarterly Archival Script for memvid Learning Archives
# Runs on first day of each quarter (Jan 1, Apr 1, Jul 1, Oct 1)
# Archives previous quarter's data for learning

set -e

# Calculate previous quarter
YEAR=$(date +%Y)
MONTH=$(date +%m)
QUARTER=$(( (MONTH - 1) / 3 + 1 ))

if [ $QUARTER -eq 1 ]; then
    PREV_QUARTER=4
    PREV_YEAR=$((YEAR - 1))
else
    PREV_QUARTER=$((QUARTER - 1))
    PREV_YEAR=$YEAR
fi

LOG_FILE="/tmp/memvid-archival-$(date +%Y%m%d-%H%M%S).log"

echo "============================================" | tee $LOG_FILE
echo "Zoe memvid Quarterly Archival" | tee -a $LOG_FILE
echo "Date: $(date)" | tee -a $LOG_FILE
echo "Archiving: $PREV_YEAR Q$PREV_QUARTER" | tee -a $LOG_FILE
echo "============================================" | tee -a $LOG_FILE
echo "" | tee -a $LOG_FILE

# Step 1: Dry run first (safety check)
echo "📊 Running dry-run to preview..." | tee -a $LOG_FILE
DRY_RUN_RESULT=$(curl -s -X POST http://localhost:8000/api/archives/create \
  -H "Content-Type: application/json" \
  -d "{\"year\": $PREV_YEAR, \"quarter\": $PREV_QUARTER, \"dry_run\": true}")

echo "$DRY_RUN_RESULT" | jq '.' | tee -a $LOG_FILE

# Check if there's data to archive
TOTAL_ITEMS=$(echo "$DRY_RUN_RESULT" | jq -r '.total_items // 0')

if [ "$TOTAL_ITEMS" -eq 0 ]; then
    echo "" | tee -a $LOG_FILE
    echo "ℹ️  No data to archive for $PREV_YEAR-Q$PREV_QUARTER" | tee -a $LOG_FILE
    echo "This is normal if quarter isn't old enough (>90 days)" | tee -a $LOG_FILE
    exit 0
fi

echo "" | tee -a $LOG_FILE
echo "📦 Will archive: $TOTAL_ITEMS items" | tee -a $LOG_FILE
echo "" | tee -a $LOG_FILE

# Step 2: Backup databases before archival
echo "💾 Creating database backup..." | tee -a $LOG_FILE
cp /home/zoe/assistant/data/zoe.db "/home/zoe/assistant/data/zoe.db.before-archive-$PREV_YEAR-Q$PREV_QUARTER"
cp /home/zoe/assistant/data/memory.db "/home/zoe/assistant/data/memory.db.before-archive-$PREV_YEAR-Q$PREV_QUARTER"
echo "✓ Databases backed up" | tee -a $LOG_FILE
echo "" | tee -a $LOG_FILE

# Step 3: Run actual archival
echo "🚀 Running actual archival..." | tee -a $LOG_FILE
ARCHIVE_RESULT=$(curl -s -X POST http://localhost:8000/api/archives/create \
  -H "Content-Type: application/json" \
  -d "{\"year\": $PREV_YEAR, \"quarter\": $PREV_QUARTER, \"dry_run\": false}")

echo "$ARCHIVE_RESULT" | jq '.' | tee -a $LOG_FILE

# Step 4: Verify archives created
echo "" | tee -a $LOG_FILE
echo "📊 Listing archives..." | tee -a $LOG_FILE
curl -s http://localhost:8000/api/archives/list | jq '.count, .archives[] | select(.name | contains("'$PREV_YEAR-Q$PREV_QUARTER'"))' | tee -a $LOG_FILE

# Step 5: Get final stats
echo "" | tee -a $LOG_FILE
echo "📈 Archive statistics:" | tee -a $LOG_FILE
curl -s http://localhost:8000/api/archives/stats | jq '.' | tee -a $LOG_FILE

echo "" | tee -a $LOG_FILE
echo "============================================" | tee -a $LOG_FILE
echo "✅ Quarterly archival complete!" | tee -a $LOG_FILE
echo "Log saved: $LOG_FILE" | tee -a $LOG_FILE
echo "============================================" | tee -a $LOG_FILE




