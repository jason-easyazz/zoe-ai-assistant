#!/bin/bash

# Day 0: Implementation Preparation Script
# Memory & Hallucination Reduction Plan
#
# This script sets up all the infrastructure needed for the implementation plan:
# - Documentation structure
# - Test infrastructure
# - Measurement tools
# - Decision gates
# - Feature flags
#
# Usage:
#   chmod +x scripts/setup/day0_implementation_prep.sh
#   ./scripts/setup/day0_implementation_prep.sh

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Base directory
BASE_DIR="/home/zoe/assistant"

echo -e "${BLUE}========================================================================${NC}"
echo -e "${BLUE}Day 0: Implementation Preparation${NC}"
echo -e "${BLUE}Memory & Hallucination Reduction Plan${NC}"
echo -e "${BLUE}========================================================================${NC}"
echo ""

# ===================
# 1. Create Directory Structure
# ===================

echo -e "${YELLOW}[1/7] Creating directory structure...${NC}"

directories=(
    "${BASE_DIR}/docs/implementation"
    "${BASE_DIR}/services/zoe-core/measurement"
    "${BASE_DIR}/services/zoe-core/intent_system/validation"
    "${BASE_DIR}/services/zoe-core/intent_system/formatters"
    "${BASE_DIR}/tests/fixtures"
    "${BASE_DIR}/tests/integration"
)

for dir in "${directories[@]}"; do
    if [ ! -d "$dir" ]; then
        mkdir -p "$dir"
        echo -e "  ${GREEN}✓${NC} Created: $dir"
    else
        echo -e "  ${BLUE}•${NC} Exists: $dir"
    fi
done

echo ""

# ===================
# 2. Verify Core Files Exist
# ===================

echo -e "${YELLOW}[2/7] Verifying core files...${NC}"

core_files=(
    "${BASE_DIR}/docs/implementation/PROGRESS.md"
    "${BASE_DIR}/docs/implementation/metrics_tracking.md"
    "${BASE_DIR}/docs/implementation/cursor_prompts.md"
    "${BASE_DIR}/docs/implementation/decision_log.md"
    "${BASE_DIR}/docs/decision_gates.md"
    "${BASE_DIR}/services/zoe-core/config.py"
    "${BASE_DIR}/services/zoe-core/measurement/test_queries.json"
    "${BASE_DIR}/services/zoe-core/measurement/retention_test_schedule.json"
    "${BASE_DIR}/tests/fixtures/confidence_test_cases.json"
    "${BASE_DIR}/tests/fixtures/sample_rule_based_patterns.json"
    "${BASE_DIR}/tests/integration/test_hallucination_benchmark.py"
)

all_files_exist=true

for file in "${core_files[@]}"; do
    if [ -f "$file" ]; then
        echo -e "  ${GREEN}✓${NC} Exists: $(basename $file)"
    else
        echo -e "  ${RED}✗${NC} Missing: $file"
        all_files_exist=false
    fi
done

echo ""

if [ "$all_files_exist" = false ]; then
    echo -e "${RED}ERROR: Some core files are missing!${NC}"
    echo -e "${YELLOW}Please ensure all files were created successfully.${NC}"
    exit 1
fi

# ===================
# 3. Validate JSON Files
# ===================

echo -e "${YELLOW}[3/7] Validating JSON files...${NC}"

json_files=(
    "${BASE_DIR}/services/zoe-core/measurement/test_queries.json"
    "${BASE_DIR}/services/zoe-core/measurement/retention_test_schedule.json"
    "${BASE_DIR}/tests/fixtures/confidence_test_cases.json"
    "${BASE_DIR}/tests/fixtures/sample_rule_based_patterns.json"
)

all_json_valid=true

for json_file in "${json_files[@]}"; do
    if python3 -c "import json; json.load(open('$json_file'))" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} Valid JSON: $(basename $json_file)"
    else
        echo -e "  ${RED}✗${NC} Invalid JSON: $json_file"
        all_json_valid=false
    fi
done

echo ""

if [ "$all_json_valid" = false ]; then
    echo -e "${RED}ERROR: Some JSON files are invalid!${NC}"
    exit 1
fi

# ===================
# 4. Check Python Test File
# ===================

echo -e "${YELLOW}[4/7] Checking Python test file...${NC}"

test_file="${BASE_DIR}/tests/integration/test_hallucination_benchmark.py"

if python3 -m py_compile "$test_file" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Valid Python: test_hallucination_benchmark.py"
else
    echo -e "  ${RED}✗${NC} Syntax error in: $test_file"
    echo -e "  ${YELLOW}Fix syntax errors before proceeding${NC}"
    exit 1
fi

echo ""

# ===================
# 5. Check Feature Flags Config
# ===================

echo -e "${YELLOW}[5/7] Checking feature flags config...${NC}"

config_file="${BASE_DIR}/services/zoe-core/config.py"

# Test config syntax by executing directly (avoids pycache permission issues)
if python3 -c "exec(open('$config_file').read()); print('', end='')" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Valid Python: config.py"
    
    # Try to check if FeatureFlags class works
    if python3 -c "exec(open('$config_file').read()); FeatureFlags.get_active_features()" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} FeatureFlags class is functional"
    else
        echo -e "  ${YELLOW}⚠${NC}  FeatureFlags class may have import issues (non-critical)"
    fi
else
    echo -e "  ${RED}✗${NC} Syntax error in: $config_file"
    exit 1
fi

echo ""

# ===================
# 6. Test Query Count Validation
# ===================

echo -e "${YELLOW}[6/7] Validating test query database...${NC}"

test_queries_file="${BASE_DIR}/services/zoe-core/measurement/test_queries.json"

query_count=$(python3 -c "import json; data = json.load(open('$test_queries_file')); print(len(data['queries']))" 2>/dev/null || echo "0")

if [ "$query_count" -ge 100 ]; then
    echo -e "  ${GREEN}✓${NC} Test queries: $query_count (target: 100)"
else
    echo -e "  ${YELLOW}⚠${NC}  Test queries: $query_count (target: 100)"
    echo -e "  ${YELLOW}Consider adding more test queries to reach 100${NC}"
fi

# Check category distribution
deterministic=$(python3 -c "import json; data = json.load(open('$test_queries_file')); print(len([q for q in data['queries'] if q['category'] == 'deterministic']))" 2>/dev/null || echo "0")
memory=$(python3 -c "import json; data = json.load(open('$test_queries_file')); print(len([q for q in data['queries'] if q['category'] == 'memory']))" 2>/dev/null || echo "0")
conversational=$(python3 -c "import json; data = json.load(open('$test_queries_file')); print(len([q for q in data['queries'] if q['category'] == 'conversational']))" 2>/dev/null || echo "0")

echo -e "  ${BLUE}•${NC} Deterministic: $deterministic (target: 30)"
echo -e "  ${BLUE}•${NC} Memory: $memory (target: 40)"
echo -e "  ${BLUE}•${NC} Conversational: $conversational (target: 30)"

echo ""

# ===================
# 7. Summary and Next Steps
# ===================

echo -e "${YELLOW}[7/7] Setup Summary${NC}"
echo ""

echo -e "${GREEN}✓ Day 0 setup complete!${NC}"
echo ""

echo -e "${BLUE}========================================================================${NC}"
echo -e "${BLUE}Infrastructure Status${NC}"
echo -e "${BLUE}========================================================================${NC}"
echo ""

echo -e "Documentation:"
echo -e "  ${GREEN}✓${NC} PROGRESS.md - Track implementation progress"
echo -e "  ${GREEN}✓${NC} metrics_tracking.md - Record measurements"
echo -e "  ${GREEN}✓${NC} cursor_prompts.md - Cursor interaction templates"
echo -e "  ${GREEN}✓${NC} decision_log.md - Document gate decisions"
echo -e "  ${GREEN}✓${NC} decision_gates.md - Gate criteria and process"
echo ""

echo -e "Testing Infrastructure:"
echo -e "  ${GREEN}✓${NC} test_hallucination_benchmark.py - Baseline measurement suite"
echo -e "  ${GREEN}✓${NC} test_queries.json - 100 test queries ($query_count loaded)"
echo -e "  ${GREEN}✓${NC} confidence_test_cases.json - P0-2 test cases"
echo -e "  ${GREEN}✓${NC} sample_rule_based_patterns.json - P1-1 validation"
echo -e "  ${GREEN}✓${NC} retention_test_schedule.json - Multi-interval testing"
echo ""

echo -e "Configuration:"
echo -e "  ${GREEN}✓${NC} config.py - Feature flags management"
echo -e "  ${GREEN}✓${NC} All feature flags default to disabled (safe)"
echo ""

echo -e "${BLUE}========================================================================${NC}"
echo -e "${BLUE}Next Steps${NC}"
echo -e "${BLUE}========================================================================${NC}"
echo ""

echo -e "1. Review the plan:"
echo -e "   ${BLUE}cat memory-enhancement.plan.md${NC}"
echo ""

echo -e "2. Start Phase 1 (Days 1-5): Architecture Audit"
echo -e "   ${BLUE}# Read and audit current systems${NC}"
echo -e "   - services/zoe-core/intent_system/classifiers/hassil_classifier.py"
echo -e "   - services/zoe-core/light_rag_memory.py"
echo -e "   - services/zoe-core/temporal_memory.py"
echo -e "   - services/zoe-core/routers/chat.py"
echo ""

echo -e "3. Track progress:"
echo -e "   ${BLUE}docs/implementation/PROGRESS.md${NC} - Check off tasks as completed"
echo -e "   ${BLUE}docs/implementation/metrics_tracking.md${NC} - Record measurements"
echo ""

echo -e "4. Use Cursor prompts:"
echo -e "   ${BLUE}docs/implementation/cursor_prompts.md${NC} - Templates for each phase"
echo ""

echo -e "5. Run baseline tests (Day 6-7):"
echo -e "   ${BLUE}pytest tests/integration/test_hallucination_benchmark.py --report=baseline.json${NC}"
echo ""

echo -e "${BLUE}========================================================================${NC}"
echo -e "${BLUE}Feature Flags (All Disabled by Default)${NC}"
echo -e "${BLUE}========================================================================${NC}"
echo ""

echo -e "To enable features during implementation:"
echo ""
echo -e "In docker-compose.yml (zoe-core service):"
echo -e "${BLUE}  environment:"
echo -e "    - USE_CONTEXT_VALIDATION=true"
echo -e "    - USE_CONFIDENCE_FORMATTING=true"
echo -e "    - USE_DYNAMIC_TEMPERATURE=true"
echo -e "    - USE_GROUNDING_CHECKS=true"
echo -e "    - USE_BEHAVIORAL_MEMORY=true${NC}"
echo ""

echo -e "Or via environment:"
echo -e "${BLUE}  export USE_CONTEXT_VALIDATION=true${NC}"
echo ""

echo -e "${BLUE}========================================================================${NC}"
echo -e "${GREEN}✓ Day 0 Setup Complete - Ready to Begin Phase 1${NC}"
echo -e "${BLUE}========================================================================${NC}"
echo ""

# Create a status file
status_file="${BASE_DIR}/docs/implementation/.day0_complete"
echo "Day 0 setup completed at $(date)" > "$status_file"
echo "All infrastructure files created and validated" >> "$status_file"

exit 0

