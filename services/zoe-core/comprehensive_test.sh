#!/bin/bash
echo "🧪 COMPREHENSIVE ZOE CALENDAR SYSTEM TEST SUITE"
echo "=================================================="
echo "Testing all aspects of your enhanced calendar system"
echo ""

# Test counter
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test functions
run_test() {
    local test_name="$1"
    local test_command="$2"
    local expected_pattern="$3"
    
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    echo -e "${BLUE}Test $TOTAL_TESTS: $test_name${NC}"
    
    # Run the test
    result=$(eval "$test_command" 2>&1)
    
    # Check if result matches expected pattern
    if echo "$result" | grep -q "$expected_pattern"; then
        echo -e "  ${GREEN}✅ PASSED${NC}"
        PASSED_TESTS=$((PASSED_TESTS + 1))
        echo "  Result: $(echo "$result" | head -1)"
    else
        echo -e "  ${RED}❌ FAILED${NC}"
        FAILED_TESTS=$((FAILED_TESTS + 1))
        echo "  Expected: $expected_pattern"
        echo "  Got: $(echo "$result" | head -2)"
    fi
    echo ""
}

# Database test function
test_database() {
    local test_name="$1"
    local sql_query="$2"
    local expected_count="$3"
    
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    echo -e "${BLUE}DB Test $TOTAL_TESTS: $test_name${NC}"
    
    result=$(docker exec zoe-core python3 -c "
import sqlite3
conn = sqlite3.connect('/app/data/zoe.db')
cursor = conn.execute('$sql_query')
result = cursor.fetchall()
print(len(result))
conn.close()
" 2>/dev/null)
    
    if [ "$result" -eq "$expected_count" ]; then
        echo -e "  ${GREEN}✅ PASSED${NC} - Found $result records"
        PASSED_TESTS=$((PASSED_TESTS + 1))
    else
        echo -e "  ${RED}❌ FAILED${NC} - Expected $expected_count, got $result"
        FAILED_TESTS=$((FAILED_TESTS + 1))
    fi
    echo ""
}

# Start testing
echo "🔍 PHASE 1: SYSTEM HEALTH TESTS"
echo "================================"

run_test "Container Health Check" \
    "curl -s http://localhost:8000/health" \
    "healthy"

run_test "Database Connectivity" \
    "docker exec zoe-core python3 -c 'import sqlite3; conn = sqlite3.connect(\"/app/data/zoe.db\"); print(\"connected\"); conn.close()'" \
    "connected"

test_database "Events Table Structure" \
    "SELECT name FROM sqlite_master WHERE type='table' AND name='events'" \
    1

echo "🎯 PHASE 2: EVENT DETECTION TESTS"
echo "=================================="

run_test "Basic Appointment Detection" \
    "curl -s -X POST http://localhost:8000/api/chat -H 'Content-Type: application/json' -d '{\"message\": \"I have a doctor appointment tomorrow\"}'" \
    "event_created"

run_test "Meeting Detection" \
    "curl -s -X POST http://localhost:8000/api/chat -H 'Content-Type: application/json' -d '{\"message\": \"I have a team meeting friday\"}'" \
    "event_created"

run_test "Birthday Detection" \
    "curl -s -X POST http://localhost:8000/api/chat -H 'Content-Type: application/json' -d '{\"message\": \"my birthday is march 24th\"}'" \
    "event_created"

echo "🕐 PHASE 3: TIME PARSING TESTS"
echo "==============================="

run_test "Time Parsing - PM Format" \
    "curl -s -X POST http://localhost:8000/api/chat -H 'Content-Type: application/json' -d '{\"message\": \"appointment tomorrow at 2pm\"}'" \
    "event_created"

run_test "Time Parsing - AM Format" \
    "curl -s -X POST http://localhost:8000/api/chat -H 'Content-Type: application/json' -d '{\"message\": \"meeting tomorrow at 9am\"}'" \
    "event_created"

run_test "Time Parsing - Minutes Format" \
    "curl -s -X POST http://localhost:8000/api/chat -H 'Content-Type: application/json' -d '{\"message\": \"appointment tomorrow at 3:30pm\"}'" \
    "event_created"

echo "📅 PHASE 4: DATE PARSING TESTS" 
echo "==============================="

run_test "Date Parsing - Tomorrow" \
    "curl -s -X POST http://localhost:8000/api/chat -H 'Content-Type: application/json' -d '{\"message\": \"meeting tomorrow\"}'" \
    "event_created"

run_test "Date Parsing - Friday" \
    "curl -s -X POST http://localhost:8000/api/chat -H 'Content-Type: application/json' -d '{\"message\": \"appointment friday\"}'" \
    "event_created"

run_test "Date Parsing - Today" \
    "curl -s -X POST http://localhost:8000/api/chat -H 'Content-Type: application/json' -d '{\"message\": \"meeting today\"}'" \
    "event_created"

echo "💾 PHASE 5: DATABASE PERSISTENCE TESTS"
echo "======================================="

# Get current event count for baseline
INITIAL_COUNT=$(docker exec zoe-core python3 -c "
import sqlite3
conn = sqlite3.connect('/app/data/zoe.db')
cursor = conn.execute('SELECT COUNT(*) FROM events')
print(cursor.fetchone()[0])
conn.close()
" 2>/dev/null)

echo "Current events in database: $INITIAL_COUNT"

# Test database persistence
curl -s -X POST http://localhost:8000/api/chat -H 'Content-Type: application/json' -d '{"message": "database test appointment tomorrow at 4pm"}' > /dev/null

NEW_COUNT=$(docker exec zoe-core python3 -c "
import sqlite3
conn = sqlite3.connect('/app/data/zoe.db')
cursor = conn.execute('SELECT COUNT(*) FROM events')
print(cursor.fetchone()[0])
conn.close()
" 2>/dev/null)

TOTAL_TESTS=$((TOTAL_TESTS + 1))
if [ "$NEW_COUNT" -gt "$INITIAL_COUNT" ]; then
    echo -e "${GREEN}✅ Test $TOTAL_TESTS: Database Persistence - PASSED${NC}"
    PASSED_TESTS=$((PASSED_TESTS + 1))
    echo "  Events increased from $INITIAL_COUNT to $NEW_COUNT"
else
    echo -e "${RED}❌ Test $TOTAL_TESTS: Database Persistence - FAILED${NC}"
    FAILED_TESTS=$((FAILED_TESTS + 1))
    echo "  Events count didn't increase: $INITIAL_COUNT to $NEW_COUNT"
fi
echo ""

test_database "Time Data in Database" \
    "SELECT * FROM events WHERE start_time IS NOT NULL AND start_time != ''" \
    1

echo "🔗 PHASE 6: API ENDPOINT TESTS"
echo "==============================="

run_test "Events API Endpoint" \
    "curl -s http://localhost:8000/api/events" \
    "events"

run_test "Today's Events API" \
    "curl -s http://localhost:8000/api/events/today" \
    "events"

run_test "API JSON Format" \
    "curl -s http://localhost:8000/api/events | jq '.count'" \
    "[0-9]"

echo "📊 PHASE 7: DATA QUALITY TESTS"
echo "==============================="

# Test event titles
TOTAL_TESTS=$((TOTAL_TESTS + 1))
echo -e "${BLUE}Test $TOTAL_TESTS: Event Title Quality${NC}"
TITLE_RESULT=$(docker exec zoe-core python3 -c "
import sqlite3
conn = sqlite3.connect('/app/data/zoe.db')
cursor = conn.execute('SELECT title FROM events WHERE title IS NOT NULL AND title != \"\"')
titles = [row[0] for row in cursor.fetchall()]
print(f'Valid titles: {len(titles)}')
conn.close()
" 2>/dev/null)

if echo "$TITLE_RESULT" | grep -q "Valid titles: [1-9]"; then
    echo -e "  ${GREEN}✅ PASSED${NC} - $TITLE_RESULT"
    PASSED_TESTS=$((PASSED_TESTS + 1))
else
    echo -e "  ${RED}❌ FAILED${NC} - No valid titles found"
    FAILED_TESTS=$((FAILED_TESTS + 1))
fi
echo ""

# Test date format validation
TOTAL_TESTS=$((TOTAL_TESTS + 1))
echo -e "${BLUE}Test $TOTAL_TESTS: Date Format Validation${NC}"
DATE_RESULT=$(docker exec zoe-core python3 -c "
import sqlite3
from datetime import datetime
conn = sqlite3.connect('/app/data/zoe.db')
cursor = conn.execute('SELECT start_date FROM events')
valid_dates = 0
for row in cursor.fetchall():
    try:
        datetime.strptime(row[0], '%Y-%m-%d')
        valid_dates += 1
    except:
        pass
print(f'Valid dates: {valid_dates}')
conn.close()
" 2>/dev/null)

if echo "$DATE_RESULT" | grep -q "Valid dates: [1-9]"; then
    echo -e "  ${GREEN}✅ PASSED${NC} - $DATE_RESULT"
    PASSED_TESTS=$((PASSED_TESTS + 1))
else
    echo -e "  ${RED}❌ FAILED${NC} - Invalid date formats found"
    FAILED_TESTS=$((FAILED_TESTS + 1))
fi
echo ""

echo "📈 FINAL DATABASE STATE"
echo "======================"

echo "Current Database Contents:"
docker exec zoe-core python3 -c "
import sqlite3
conn = sqlite3.connect('/app/data/zoe.db')
cursor = conn.execute('SELECT id, title, start_date, start_time, source FROM events ORDER BY created_at DESC LIMIT 10')
print('Recent Events:')
for i, row in enumerate(cursor.fetchall(), 1):
    print(f'  {i}. {row[1]} - {row[2]} {row[3] or \"\"} ({row[4]})')
conn.close()
"

echo ""
echo "🎯 TEST RESULTS SUMMARY"
echo "========================"
echo -e "Total Tests Run: ${BLUE}$TOTAL_TESTS${NC}"
echo -e "Tests Passed: ${GREEN}$PASSED_TESTS${NC}"
echo -e "Tests Failed: ${RED}$FAILED_TESTS${NC}"

if [ "$FAILED_TESTS" -eq 0 ]; then
    echo -e "${GREEN}🎉 ALL TESTS PASSED! Your Zoe Calendar System is working perfectly!${NC}"
    exit 0
else
    PASS_RATE=$(( (PASSED_TESTS * 100) / TOTAL_TESTS ))
    echo -e "${YELLOW}⚠️  Pass Rate: $PASS_RATE%${NC}"
    if [ "$PASS_RATE" -ge 80 ]; then
        echo -e "${YELLOW}System is mostly functional with minor issues${NC}"
        exit 1
    else
        echo -e "${RED}System has significant issues that need attention${NC}"
        exit 2
    fi
fi
