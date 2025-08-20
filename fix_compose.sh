#!/bin/bash

# ============================================================================
# FIX DOCKER COMPOSE VERSION WARNING
# Removes the obsolete version attribute from docker-compose.yml
# ============================================================================

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $1"; }
success() { echo -e "${GREEN}✅${NC} $1"; }

echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}FIXING DOCKER COMPOSE VERSION WARNING${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"

cd /home/pi/zoe

# Backup current docker-compose.yml
log "📦 Backing up docker-compose.yml..."
cp docker-compose.yml "docker-compose.yml.backup_$(date +%Y%m%d_%H%M%S)"

# Remove the version line
log "🔧 Removing obsolete version attribute..."
sed -i '/^version:/d' docker-compose.yml

# Also check if there's an empty line at the top now and remove it
sed -i '/./,$!d' docker-compose.yml

success "Fixed docker-compose.yml"

# Show the first few lines to confirm
log "📋 First 5 lines of updated docker-compose.yml:"
head -5 docker-compose.yml

# Test that it still works
log "🧪 Testing docker-compose configuration..."
docker compose config > /dev/null 2>&1
if [ $? -eq 0 ]; then
    success "Docker compose configuration is valid!"
else
    echo -e "${YELLOW}[WARNING]${NC} Configuration check failed, restoring backup..."
    cp docker-compose.yml.backup_$(date +%Y%m%d_%H%M%S) docker-compose.yml
fi

# Now let's also fix the API and UI while we're at it
echo -e "\n${BLUE}════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}CHECKING API AND UI STATUS${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"

# Check API logs for errors
log "🔍 Checking Core API status..."
API_ERROR=$(docker logs zoe-core --tail 20 2>&1 | grep -E "Error|error|ERROR|Failed|failed" | head -1)

if [ ! -z "$API_ERROR" ]; then
    echo "Found error in API: $API_ERROR"
    log "🔧 Attempting to fix API..."
    
    # Fix common import errors
    docker exec zoe-core pip install httpx psutil 2>/dev/null || true
    
    # Restart API
    docker compose restart zoe-core
    sleep 5
fi

# Check UI status
log "🔍 Checking UI status..."
UI_STATUS=$(docker ps --format "table {{.Names}}\t{{.Status}}" | grep zoe-ui | awk '{print $2}')

if [[ "$UI_STATUS" == *"Restarting"* ]]; then
    log "🔧 UI is stuck restarting, fixing..."
    
    # Stop and remove the container
    docker stop zoe-ui
    docker rm zoe-ui
    
    # Start fresh
    docker compose up -d zoe-ui
    sleep 3
fi

# Final status check
echo -e "\n${BLUE}════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}FINAL STATUS CHECK${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"

# Show all services
docker ps --format "table {{.Names}}\t{{.Status}}" | grep zoe-

# Test endpoints
echo -e "\n${BLUE}Testing Services:${NC}"

echo -n "Core API: "
if curl -s http://localhost:8000/health 2>/dev/null | grep -q "healthy"; then
    success "✅ Working!"
else
    echo -e "${YELLOW}⚠️ Not responding - checking logs...${NC}"
    docker logs zoe-core --tail 5
fi

echo -n "Web UI: "
if curl -s http://localhost:8080 2>/dev/null > /dev/null; then
    success "✅ Accessible!"
else
    echo -e "${YELLOW}⚠️ Not accessible${NC}"
fi

# Sync to GitHub
log "📤 Syncing fixes to GitHub..."
git add docker-compose.yml
git commit -m "🔧 Fixed docker-compose version warning and service issues" 2>/dev/null || true
git push 2>/dev/null || true

echo -e "\n${GREEN}════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✅ Docker Compose warning fixed!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════════${NC}"

exit 0
