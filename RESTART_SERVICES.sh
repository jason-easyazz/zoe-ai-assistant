#!/bin/bash
# Script to properly restart Zoe services with correct network configuration
# Updated: Dec 2025 - Uses llama.cpp instead of Ollama

set -e

echo "🔄 Restarting Zoe services with fixed network configuration..."
echo ""

# Stop all containers
echo "⏸️  Stopping all containers..."
docker stop $(docker ps -q) 2>/dev/null || true

echo "🗑️  Removing zoe-core and zoe-llamacpp to recreate them..."
docker rm -f zoe-core zoe-llamacpp 2>/dev/null || true

echo "🚀 Starting services with docker-compose..."
cd /home/zoe/assistant

# Use Python docker library to start services (avoids docker-compose issues)
python3 << 'PYTHON_EOF'
import docker
import time

client = docker.from_env()

print("📦 Starting containers from compose file...")

# Start essential services in order
services_to_start = [
    "zoe-llamacpp",
    "zoe-redis",
    "zoe-auth",
    "zoe-litellm",
    "zoe-mcp-server",
    "zoe-mem-agent",
    "zoe-code-execution",
    "zoe-core",
    "zoe-ui"
]

for service in services_to_start:
    try:
        container = client.containers.get(service)
        if container.status != "running":
            print(f"  ▶️  Starting {service}...")
            container.start()
            time.sleep(2)
        else:
            print(f"  ✅ {service} already running")
    except docker.errors.NotFound:
        print(f"  ⚠️  {service} not found - will be created by compose")
    except Exception as e:
        print(f"  ❌ Error with {service}: {e}")

print("\n✅ Services started!")
PYTHON_EOF

echo ""
echo "⏱️  Waiting 30 seconds for services to initialize..."
sleep 30

echo ""
echo "🔍 Verifying network configuration..."
bash /home/zoe/assistant/tools/docker/validate_networks.sh

echo ""
echo "✅ Restart complete!"
echo ""
echo "Next steps:"
echo "  1. Run tests: cd /home/zoe/assistant && python3 scripts/utilities/natural_language_learning.py"
echo "  2. Check logs: docker logs zoe-core -f"
echo "  3. Verify connectivity: docker exec zoe-core ping -c 2 zoe-llamacpp"
