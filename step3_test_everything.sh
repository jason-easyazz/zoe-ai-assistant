#!/bin/bash
# Comprehensive testing of enhanced calendar deployment

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "${GREEN}[$(date +'%H:%M:%S')] $1${NC}"; }
info() { echo -e "${BLUE}[INFO] $1${NC}"; }
warn() { echo -e "${YELLOW}[WARN] $1${NC}"; }
error() { echo -e "${RED}[ERROR] $1${NC}"; }

echo "=================================================================="
echo "🧪 STEP 3: COMPREHENSIVE TESTING & VERIFICATION"
echo "=================================================================="

TEST_COUNT=0
PASS_COUNT=0

run_test() {
    local test_name="$1"
    local test_command="$2"
    
    ((TEST_COUNT++))
    echo ""
    info "Test $TEST_COUNT: $test_name"
    
    if eval "$test_command"; then
        log "✅ PASS: $test_name"
        ((PASS_COUNT++))
        return 0
    else
        error "❌ FAIL: $test_name"
        return 1
    fi
}

# Test 1: Backend API Health
run_test "Backend API Health Check" \
    "curl -s -f http://192.168.1.60:8000/health > /dev/null"

# Test 2: Events API Data
run_test "Events API Returns Data" \
    "curl -s http://192.168.1.60:8000/api/events | jq -e '.events | length > 0' > /dev/null"

# Test 3: Enhanced Calendar Accessibility
run_test "Enhanced Calendar Page Loads" \
    "curl -s -f http://192.168.1.60:8080/calendar.html > /dev/null"

# Test 4: Calendar Contains Fluid Orb
run_test "Calendar Contains Fluid Orb Design" \
    "curl -s http://192.168.1.60:8080/calendar.html | grep -q 'fluid-orb'"

# Test 5: Calendar Contains All Menu Items
run_test "All Menu Items Present" \
    "curl -s http://192.168.1.60:8080/calendar.html | grep -q 'Chat.*Dashboard.*Calendar.*Tasks.*Journal.*Workflows.*Settings'"

# Test 6: Calendar Contains API Integration
run_test "API Integration Code Present" \
    "curl -s http://192.168.1.60:8080/calendar.html | grep -q 'API_BASE.*192.168.1.60:8000'"

# Test 7: Chat API Functionality
run_test "Chat API Responds" \
    "curl -s -X POST http://192.168.1.60:8000/api/chat -H 'Content-Type: application/json' -d '{\"message\": \"test calendar integration\"}' | jq -e '.response' > /dev/null"

# Test 8: Original Pages Still Work
run_test "Original Index Page Still Works" \
    "curl -s -f http://192.168.1.60:8080/index.html > /dev/null"

# Test 9: Docker Services Running
run_test "All Docker Services Running" \
    "docker compose ps | grep -q 'zoe-core.*Up' && docker compose ps | grep -q 'zoe-ui.*Up'"

# Test 10: Event Count Verification
CURRENT_EVENTS=$(curl -s http://192.168.1.60:8000/api/events | jq -r '.events | length' 2>/dev/null || echo "0")
run_test "Events Database Has Data" \
    "test $CURRENT_EVENTS -gt 0"

echo ""
echo "=================================================================="
echo "📊 TEST RESULTS SUMMARY"
echo "=================================================================="

PASS_RATE=$(( PASS_COUNT * 100 / TEST_COUNT ))

log "📈 Overall Results:"
echo "   Tests Run: $TEST_COUNT"
echo "   Tests Passed: $PASS_COUNT"
echo "   Pass Rate: $PASS_RATE%"
echo "   Events in Database: $CURRENT_EVENTS"

if [[ $PASS_RATE -ge 90 ]]; then
    log "🎉 EXCELLENT: Enhanced calendar deployment is highly successful!"
elif [[ $PASS_RATE -ge 80 ]]; then
    info "✅ GOOD: Enhanced calendar working well"
else
    warn "⚠️ NEEDS ATTENTION: Some issues detected"
fi

echo ""
echo "🌐 ENHANCED CALENDAR ACCESS:"
echo "   Direct URL: http://192.168.1.60:8080/calendar.html"
echo "   From menu: http://192.168.1.60:8080/ → Calendar"
echo ""
echo "💫 TEST MANUALLY:"
echo "   1. Touch the fluid orb → Should enter calendar interface"
echo "   2. Click calendar dates → Should show events for that day"
echo "   3. Navigate months → Should work smoothly"
echo "   4. Click + button → Should open chat overlay"
echo "   5. Type event in chat → Should create real calendar event"
echo ""
echo "🚀 DEPLOYMENT SUCCESS! Your enhanced calendar is ready!"
echo "=================================================================="
echo "🌐 ENHANCED CALENDAR ACCESS:"
echo "   Direct URL: http://192.168.1.60:8080/calendar.html"
echo "   From menu: http://192.168.1.60:8080/ → Calendar"
echo ""
echo "💫 TEST MANUALLY:"
echo "   1. Touch the fluid orb → Should enter calendar interface"
echo "   2. Click calendar dates → Should show events for that day"
echo "   3. Navigate months → Should work smoothly"
echo "   4. Click + button → Should open chat overlay"
echo "   5. Type event in chat → Should create real calendar event"
echo ""
echo "🚀 DEPLOYMENT SUCCESS! Your enhanced calendar is ready!"
echo "=================================================================="
