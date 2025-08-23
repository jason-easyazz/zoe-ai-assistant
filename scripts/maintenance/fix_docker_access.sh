#!/bin/bash
# FIX_DOCKER_ACCESS.sh
# Purpose: Install Docker CLI inside zoe-core container so commands work
# Location: scripts/maintenance/fix_docker_access.sh

set -e

echo "🔧 FIXING DOCKER ACCESS FOR DEVELOPER COMMANDS"
echo "=============================================="
echo ""
echo "This will install Docker CLI inside the container"
echo "so /api/developer/execute can run Docker commands"
echo ""

cd /home/pi/zoe

# Step 1: Install Docker CLI in running container
echo "📦 Installing Docker CLI in zoe-core container..."
docker exec zoe-core apt-get update
docker exec zoe-core apt-get install -y docker.io docker-compose

# Step 2: Test Docker access
echo ""
echo "🧪 Testing Docker access from inside container..."
if docker exec zoe-core docker --version; then
    echo "✅ Docker CLI installed successfully"
else
    echo "❌ Docker CLI installation failed"
    exit 1
fi

# Step 3: Mount Docker socket if not already mounted
echo ""
echo "🔌 Checking Docker socket mount..."
if docker exec zoe-core ls -la /var/run/docker.sock 2>/dev/null; then
    echo "✅ Docker socket already accessible"
else
    echo "⚠️  Docker socket not mounted. Updating docker-compose.yml..."
    
    # Backup docker-compose.yml
    cp docker-compose.yml docker-compose.yml.backup_$(date +%Y%m%d_%H%M%S)
    
    # Add Docker socket mount using Python (since yaml is tricky in bash)
    python3 << 'PYTHON_EOF'
import yaml

with open('docker-compose.yml', 'r') as f:
    config = yaml.safe_load(f)

if 'services' in config and 'zoe-core' in config['services']:
    if 'volumes' not in config['services']['zoe-core']:
        config['services']['zoe-core']['volumes'] = []
    
    socket_mount = '/var/run/docker.sock:/var/run/docker.sock'
    if socket_mount not in config['services']['zoe-core']['volumes']:
        config['services']['zoe-core']['volumes'].append(socket_mount)
        print("Added Docker socket mount")

with open('docker-compose.yml', 'w') as f:
    yaml.dump(config, f, default_flow_style=False, sort_keys=False)
PYTHON_EOF
    
    echo "📝 Docker socket mount added to docker-compose.yml"
    echo "🔄 Recreating container with socket mount..."
    docker compose up -d zoe-core
    sleep 10
fi

# Step 4: Test Docker commands via API
echo ""
echo "🧪 Testing Docker commands via API..."

# Test basic command execution
echo "Testing basic command..."
RESULT=$(curl -s -X POST http://localhost:8000/api/developer/execute \
  -H "Content-Type: application/json" \
  -d '{"command": "echo Docker test"}' | jq -r '.stdout')

if [[ "$RESULT" == *"Docker test"* ]]; then
    echo "✅ Basic command execution works"
else
    echo "⚠️  Basic commands may have issues"
fi

# Test Docker command
echo "Testing Docker command..."
DOCKER_RESULT=$(curl -s -X POST http://localhost:8000/api/developer/execute \
  -H "Content-Type: application/json" \
  -d '{"command": "docker ps --format \"table {{.Names}}\""}' 2>/dev/null)

if echo "$DOCKER_RESULT" | grep -q "zoe-"; then
    echo "✅ Docker commands work via API!"
    echo ""
    echo "Containers found:"
    echo "$DOCKER_RESULT" | jq -r '.stdout'
else
    echo "⚠️  Docker commands not working yet"
    echo "Response: $DOCKER_RESULT"
    echo ""
    echo "Try rebuilding the container:"
    echo "  docker compose up -d --build zoe-core"
fi

# Step 5: Final verification
echo ""
echo "📊 FINAL STATUS CHECK"
echo "===================="

# Check developer endpoint
if curl -s http://localhost:8000/api/developer/status | grep -q "operational"; then
    echo "✅ Developer API: WORKING"
else
    echo "❌ Developer API: NOT RESPONDING"
fi

# Check if Docker is accessible
if docker exec zoe-core docker ps > /dev/null 2>&1; then
    echo "✅ Docker CLI: INSTALLED"
else
    echo "❌ Docker CLI: NOT WORKING"
fi

# Check socket mount
if docker inspect zoe-core | grep -q "/var/run/docker.sock"; then
    echo "✅ Docker Socket: MOUNTED"
else
    echo "❌ Docker Socket: NOT MOUNTED"
fi

echo ""
echo "🎯 NEXT STEPS:"
echo "=============="
echo ""
echo "If everything shows ✅ above, Claude can now execute Docker commands!"
echo ""
echo "Test in the developer dashboard by asking Claude:"
echo '  "Show me all Docker containers"'
echo '  "Check the system status"'
echo '  "Fix any issues you find"'
echo ""
echo "If Docker commands still don't work:"
echo "  1. Rebuild the container: docker compose up -d --build zoe-core"
echo "  2. Check logs: docker logs zoe-core --tail 50"
echo "  3. Verify socket permissions: ls -la /var/run/docker.sock"
echo ""
echo "Access developer dashboard at: http://192.168.1.60:8080/developer/"
