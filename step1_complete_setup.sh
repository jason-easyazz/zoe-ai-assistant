#!/bin/bash
# Complete backup and setup for enhanced calendar deployment

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
echo "🚀 STEP 1: COMPLETE BACKUP & SETUP"
echo "=================================================================="

# Test backend connection
log "🔍 Testing backend connection..."
if curl -s -f http://192.168.1.60:8000/health > /dev/null; then
    log "✅ Backend healthy and running"
    EVENT_COUNT=$(curl -s http://192.168.1.60:8000/api/events | jq -r '.events | length' 2>/dev/null || echo "unknown")
    info "📊 Found $EVENT_COUNT events in database"
else
    error "❌ Backend not responding - please start with: docker compose up -d"
    exit 1
fi

# Create backup
BACKUP_TIME=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="backups/pre-enhanced-$BACKUP_TIME"
log "📁 Creating backup directory: $BACKUP_DIR"
mkdir -p "$BACKUP_DIR"

# Check current UI structure
log "📋 Checking current UI structure..."
if [ -d "services/zoe-ui/static" ]; then
    info "Found static directory"
    cp -r services/zoe-ui/static "$BACKUP_DIR/static-backup"
    ls -la services/zoe-ui/static/*.html 2>/dev/null | head -10 || info "No HTML files in static"
fi

if [ -d "services/zoe-ui/dist" ]; then
    info "Found dist directory"
    cp -r services/zoe-ui/dist "$BACKUP_DIR/dist-backup"
    ls -la services/zoe-ui/dist/*.html 2>/dev/null | head -10 || info "No HTML files in dist"
fi

# Backup configs
[ -f "services/zoe-ui/nginx.conf" ] && cp services/zoe-ui/nginx.conf "$BACKUP_DIR/"
[ -f "services/zoe-ui/Dockerfile" ] && cp services/zoe-ui/Dockerfile "$BACKUP_DIR/"

# Determine which directory to use for deployment
if [ -d "services/zoe-ui/static" ] && [ "$(ls -A services/zoe-ui/static 2>/dev/null)" ]; then
    DEPLOY_DIR="static"
    info "Will deploy to: services/zoe-ui/static"
elif [ -d "services/zoe-ui/dist" ] && [ "$(ls -A services/zoe-ui/dist 2>/dev/null)" ]; then
    DEPLOY_DIR="dist"
    info "Will deploy to: services/zoe-ui/dist"
else
    DEPLOY_DIR="static"
    info "Creating new deployment directory: services/zoe-ui/static"
    mkdir -p "services/zoe-ui/static"
fi

# Test current UI access
log "🌐 Testing current UI access..."
if curl -s -f http://192.168.1.60:8080/ > /dev/null; then
    log "✅ Current UI accessible at http://192.168.1.60:8080"
else
    warn "⚠️ Current UI not accessible (this is normal if not running)"
fi

# Check docker services
log "🐳 Checking Docker services..."
if docker compose ps | grep -q "Up"; then
    info "Running services:"
    docker compose ps | grep "Up" | awk '{print "   " $1 " - " $2}'
else
    warn "No services currently running"
fi

echo ""
log "✅ STEP 1 COMPLETE - System ready for enhancement"
info "📁 Backup saved to: $BACKUP_DIR"
info "🎯 Deployment target: services/zoe-ui/$DEPLOY_DIR"
info "📊 Events available: $EVENT_COUNT"
echo ""
echo "🚀 Ready for Step 2: Enhanced Calendar Deployment"
echo "=================================================================="
