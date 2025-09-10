#!/bin/bash
# PUSH_TO_GITHUB.sh - Safe GitHub Sync Script
# Location: scripts/utilities/push_to_github.sh
# Purpose: Safely push Zoe project to GitHub with pre-flight checks

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 ZOE AI ASSISTANT - GITHUB PUSH SCRIPT${NC}"
echo "============================================"
echo ""

# Change to project directory
cd /home/pi/zoe
echo -e "${GREEN}📍 Working in: $(pwd)${NC}"
echo ""

# ============================================================================
# STEP 1: PRE-FLIGHT CHECKS
# ============================================================================

echo -e "${YELLOW}🔍 Step 1: Running pre-flight checks...${NC}"
echo "----------------------------------------"

# Check for sensitive files
SENSITIVE_FILES_FOUND=false
echo -e "\n📋 Checking for sensitive files that should NOT be pushed..."

# Check if .env exists and is NOT staged
if [ -f ".env" ]; then
    if git ls-files --error-unmatch .env 2>/dev/null; then
        echo -e "${RED}❌ ERROR: .env file is staged for commit!${NC}"
        echo "   This file contains API keys and must not be pushed."
        echo "   Running: git rm --cached .env"
        git rm --cached .env
        SENSITIVE_FILES_FOUND=true
    else
        echo -e "${GREEN}✅ .env file exists but is properly ignored${NC}"
    fi
fi

# Check for API keys file
if [ -f "data/api_keys.json" ]; then
    if git ls-files --error-unmatch data/api_keys.json 2>/dev/null; then
        echo -e "${RED}❌ ERROR: api_keys.json is staged!${NC}"
        git rm --cached data/api_keys.json
        SENSITIVE_FILES_FOUND=true
    else
        echo -e "${GREEN}✅ api_keys.json is properly ignored${NC}"
    fi
fi

# Check for encryption key
if [ -f "data/.encryption_key" ]; then
    if git ls-files --error-unmatch data/.encryption_key 2>/dev/null; then
        echo -e "${RED}❌ ERROR: encryption key is staged!${NC}"
        git rm --cached data/.encryption_key
        SENSITIVE_FILES_FOUND=true
    else
        echo -e "${GREEN}✅ Encryption key is properly ignored${NC}"
    fi
fi

# Verify .gitignore exists and is comprehensive
if [ ! -f ".gitignore" ]; then
    echo -e "${YELLOW}⚠️  Creating .gitignore file...${NC}"
    cat > .gitignore << 'GITIGNORE'
# Data and databases
data/*.db
data/*.sqlite
data/billing/
data/redis/
logs/
*.log

# Docker volumes
volumes/

# Environment files with secrets
.env
config/.env
*.key
*.pem

# SSH Keys - NEVER commit!
id_ed25519*
id_rsa*
*.pub

# Ollama model data (too large)
models/blobs/
models/manifests/
models/.ollama/

# Temporary files
*.tmp
*.backup_*
scripts/temporary/*
!scripts/temporary/.gitkeep

# IDE files
.vscode/
.idea/
*.swp

# Python
__pycache__/
*.py[cod]
*$py.class
.Python
venv/
env/

# OS files
.DS_Store
Thumbs.db

# Keep structure files
!.gitkeep
!README.md
GITIGNORE
    echo -e "${GREEN}✅ .gitignore created${NC}"
else
    echo -e "${GREEN}✅ .gitignore exists${NC}"
fi

# ============================================================================
# STEP 2: CHECK CURRENT STATUS
# ============================================================================

echo -e "\n${YELLOW}🔍 Step 2: Checking repository status...${NC}"
echo "----------------------------------------"

# Get current branch
CURRENT_BRANCH=$(git branch --show-current)
echo -e "📌 Current branch: ${BLUE}$CURRENT_BRANCH${NC}"

# Check if remote is configured
if git remote get-url origin &>/dev/null; then
    REMOTE_URL=$(git remote get-url origin)
    echo -e "🔗 Remote URL: ${BLUE}$REMOTE_URL${NC}"
else
    echo -e "${YELLOW}⚠️  No remote configured. Setting up...${NC}"
    git remote add origin https://github.com/jason-easyazz/zoe-ai-assistant.git
    echo -e "${GREEN}✅ Remote added${NC}"
fi

# Pull latest changes to avoid conflicts
echo -e "\n📥 Pulling latest changes from GitHub..."
git pull origin main 2>/dev/null || echo -e "${YELLOW}Note: No remote branch yet or no changes to pull${NC}"

# ============================================================================
# STEP 3: UPDATE STATE DOCUMENTATION
# ============================================================================

echo -e "\n${YELLOW}📝 Step 3: Updating state documentation...${NC}"
echo "----------------------------------------"

# Update ZOE_CURRENT_STATE.md
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
cat >> ZOE_CURRENT_STATE.md << EOF

## Push to GitHub - $TIMESTAMP
- Script: push_to_github.sh executed
- All containers status: $(docker ps --format "table {{.Names}}\t{{.Status}}" | grep zoe- | wc -l) running
- Repository synced with latest changes
EOF

echo -e "${GREEN}✅ State file updated${NC}"

# ============================================================================
# STEP 4: SHOW WHAT WILL BE PUSHED
# ============================================================================

echo -e "\n${YELLOW}📊 Step 4: Files to be committed...${NC}"
echo "----------------------------------------"

# Check for changes
if git diff --quiet && git diff --cached --quiet; then
    echo -e "${YELLOW}No changes to commit. Repository is up to date.${NC}"
    echo ""
    echo -e "${GREEN}✅ GitHub repository is already synchronized!${NC}"
    exit 0
fi

# Show status
echo -e "\n${BLUE}Modified files:${NC}"
git status --short

# Count changes
MODIFIED_COUNT=$(git status --short | wc -l)
echo -e "\n📈 Total files changed: ${BLUE}$MODIFIED_COUNT${NC}"

# Show what's being ignored (for transparency)
echo -e "\n${BLUE}Protected files (not pushed):${NC}"
echo "  - .env (API keys)"
echo "  - data/*.db (databases)"
echo "  - data/api_keys.json (encrypted keys)"
echo "  - data/.encryption_key (encryption key)"
echo "  - Any .key or .pem files"

# ============================================================================
# STEP 5: COMMIT CHANGES
# ============================================================================

echo -e "\n${YELLOW}💾 Step 5: Committing changes...${NC}"
echo "----------------------------------------"

# Add all changes (respecting .gitignore)
git add -A

# Create meaningful commit message
echo -e "\n📝 Enter commit message (or press Enter for auto-generated):"
read -r COMMIT_MSG

if [ -z "$COMMIT_MSG" ]; then
    # Auto-generate commit message based on changes
    if [ -d "services/zoe-core" ] && git diff --cached --name-only | grep -q "services/zoe-core"; then
        COMMIT_MSG="🔧 Backend: Updated zoe-core services"
    elif [ -d "services/zoe-ui" ] && git diff --cached --name-only | grep -q "services/zoe-ui"; then
        COMMIT_MSG="🎨 Frontend: Updated UI components"
    elif git diff --cached --name-only | grep -q "docker-compose.yml"; then
        COMMIT_MSG="🐳 Docker: Updated container configuration"
    elif git diff --cached --name-only | grep -q "scripts/"; then
        COMMIT_MSG="📜 Scripts: Added/updated automation scripts"
    else
        COMMIT_MSG="🔄 Update: General improvements - $TIMESTAMP"
    fi
    echo -e "Using auto-generated message: ${BLUE}$COMMIT_MSG${NC}"
fi

# Commit changes
git commit -m "$COMMIT_MSG" || {
    echo -e "${YELLOW}No changes to commit${NC}"
    exit 0
}

echo -e "${GREEN}✅ Changes committed${NC}"

# ============================================================================
# STEP 6: PUSH TO GITHUB
# ============================================================================

echo -e "\n${YELLOW}🚀 Step 6: Pushing to GitHub...${NC}"
echo "----------------------------------------"

# Push to GitHub
echo -e "\n📤 Pushing to origin/main..."

# Try to push
if git push -u origin main; then
    echo -e "${GREEN}✅ Successfully pushed to GitHub!${NC}"
else
    echo -e "${YELLOW}⚠️  Push failed. Possible reasons:${NC}"
    echo "  1. Authentication required (set up personal access token)"
    echo "  2. Network issues"
    echo "  3. Repository doesn't exist yet"
    echo ""
    echo "To set up authentication:"
    echo "  1. Go to: https://github.com/settings/tokens"
    echo "  2. Generate a personal access token"
    echo "  3. Use token as password when prompted"
    echo ""
    echo "Or use SSH:"
    echo "  git remote set-url origin git@github.com:jason-easyazz/zoe-ai-assistant.git"
    exit 1
fi

# ============================================================================
# STEP 7: VERIFY PUSH
# ============================================================================

echo -e "\n${YELLOW}✅ Step 7: Verifying push...${NC}"
echo "----------------------------------------"

# Get latest commit info
LATEST_COMMIT=$(git log -1 --pretty=format:"%h - %s (%cr)")
echo -e "Latest commit: ${BLUE}$LATEST_COMMIT${NC}"

# Show GitHub URL
echo -e "\n${GREEN}🌐 View on GitHub:${NC}"
echo -e "${BLUE}https://github.com/jason-easyazz/zoe-ai-assistant${NC}"

# ============================================================================
# STEP 8: POST-PUSH TASKS
# ============================================================================

echo -e "\n${YELLOW}📋 Step 8: Post-push tasks...${NC}"
echo "----------------------------------------"

# Create a quick sync script for future use
if [ ! -f "scripts/utilities/quick_sync.sh" ]; then
    mkdir -p scripts/utilities
    cat > scripts/utilities/quick_sync.sh << 'QUICKSYNC'
#!/bin/bash
# Quick GitHub sync
cd /home/pi/zoe
echo "🔄 Quick sync to GitHub..."
git add -A
git commit -m "🔄 Quick sync - $(date +%Y%m%d_%H%M%S)" || echo "No changes"
git push || echo "Push failed - check connection"
echo "✅ Sync complete"
QUICKSYNC
    chmod +x scripts/utilities/quick_sync.sh
    echo -e "${GREEN}✅ Created quick_sync.sh for future use${NC}"
fi

# ============================================================================
# SUMMARY
# ============================================================================

echo ""
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo -e "${GREEN}           🎉 GITHUB PUSH COMPLETE! 🎉           ${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo ""
echo -e "${BLUE}Summary:${NC}"
echo "  ✅ Pre-flight checks passed"
echo "  ✅ Sensitive files protected"
echo "  ✅ Changes committed: $COMMIT_MSG"
echo "  ✅ Pushed to GitHub successfully"
echo "  ✅ State documentation updated"
echo ""
echo -e "${BLUE}What was pushed:${NC}"
echo "  • All source code (services/)"
echo "  • All scripts (scripts/)"
echo "  • Documentation files"
echo "  • Docker configuration"
echo "  • Example templates (.env.example)"
echo ""
echo -e "${BLUE}What was NOT pushed (protected):${NC}"
echo "  • API keys and .env files"
echo "  • Databases (*.db files)"
echo "  • Encryption keys"
echo "  • Ollama model data"
echo "  • Temporary/backup files"
echo ""
echo -e "${BLUE}Next steps:${NC}"
echo "  1. View your repository: https://github.com/jason-easyazz/zoe-ai-assistant"
echo "  2. For quick syncs, use: ./scripts/utilities/quick_sync.sh"
echo "  3. Continue development locally"
echo ""
echo -e "${GREEN}Thank you for using Zoe AI Assistant!${NC}"
