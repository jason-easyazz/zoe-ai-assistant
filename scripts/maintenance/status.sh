#!/bin/bash

cd "$(dirname "$0")/../../config"

echo "🤖 Zoe v3.1 System Status"
echo "========================="
echo ""

echo "📊 Docker Services:"
docker compose ps

echo ""
IP=$(hostname -I | awk '{print $1}')
echo "🌐 Access Points:"
echo "   Web UI: http://$IP:8080"
echo "   API: http://$IP:8000"
