#!/bin/bash
# Zoe v3.1 Startup Script
set -euo pipefail

PROJECT_DIR="$HOME/zoe-v31"
cd "$PROJECT_DIR"

echo "🚀 Starting Zoe v3.1 Complete Integration Stack..."

# Start services in order
echo "Starting core services..."
docker compose up -d zoe-ollama zoe-redis zoe-core

echo "Starting integration services..."
docker compose up -d zoe-whisper zoe-tts zoe-n8n zoe-homeassistant zoe-matrix

echo "Starting UI..."
docker compose up -d zoe-ui

echo "Waiting for services to stabilize..."
sleep 30

echo "✅ Zoe v3.1 started successfully!"
echo ""
IP=$(hostname -I | awk '{print $1}')
echo "🌐 Access Zoe at: http://$IP:8080"
echo "📊 API Status: http://$IP:8000/health"
echo ""
echo "🎯 All integration services are now running!"
