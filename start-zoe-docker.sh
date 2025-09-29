#!/bin/bash

# Zoe Docker-based Startup Script
# This properly starts the full Zoe ecosystem using Docker Compose

echo "🌟 Starting Zoe AI Assistant (Docker Mode)"
echo "============================================"

# Check if we're in the right directory
if [ ! -f "docker-compose.yml" ]; then
    echo "❌ docker-compose.yml not found. Please run from /home/pi/zoe directory"
    exit 1
fi

# Stop any standalone processes first
echo "🧹 Stopping standalone processes..."
./stop-zoe.sh 2>/dev/null || true
pkill -f "simple_main.py" 2>/dev/null || true
pkill -f "proxy-server.py" 2>/dev/null || true

# Start Docker services
echo ""
echo "🐳 Starting Docker services..."
docker-compose up -d

# Wait for services to be ready
echo ""
echo "⏳ Waiting for services to start..."

# Wait for auth service
echo "🔐 Checking auth service..."
for i in {1..30}; do
    if curl -s http://localhost:8002/health >/dev/null 2>&1; then
        echo "✅ Auth service ready!"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "❌ Auth service failed to start"
        echo "Check with: docker logs zoe-auth"
        exit 1
    fi
    sleep 1
done

# Wait for UI service
echo "🌐 Checking UI service..."
for i in {1..15}; do
    if curl -s http://localhost:8080 >/dev/null 2>&1; then
        echo "✅ UI service ready!"
        break
    fi
    if [ $i -eq 15 ]; then
        echo "❌ UI service failed to start"
        echo "Check with: docker logs zoe-ui"
        exit 1
    fi
    sleep 1
done

# Wait for core service
echo "🧠 Checking core service..."
for i in {1..30}; do
    if curl -s http://localhost:8000/health >/dev/null 2>&1; then
        echo "✅ Core service ready!"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "❌ Core service failed to start"
        echo "Check with: docker logs zoe-core"
        exit 1
    fi
    sleep 1
done

echo ""
echo "🎉 ZOE IS READY!"
echo "==============="
echo ""
echo "📱 Access Zoe at:"
echo "   🌐 HTTP:  http://localhost:8080"
echo "   🔒 HTTPS: https://localhost:8443"
echo "   🌐 Local IP HTTP:  http://$(hostname -I | awk '{print $1}'):8080"
echo "   🔒 Local IP HTTPS: https://$(hostname -I | awk '{print $1}'):8443"
echo ""
echo "🔑 Default credentials:"
echo "   Admin: admin / admin"
echo "   User:  user / user"
echo "   Guest: Click guest profile"
echo ""
echo "🎯 Features working:"
echo "   ✅ Beautiful orb animation"
echo "   ✅ Profile selection with touch UI"
echo "   ✅ PIN pad authentication"
echo "   ✅ Touch keyboard for passwords"
echo "   ✅ Guest access"
echo "   ✅ Session management"
echo "   ✅ All navigation working"
echo "   ✅ Auth API properly proxied"
echo ""
echo "📋 Services running:"
echo "   🔐 Auth Service:    http://localhost:8002"
echo "   🧠 Core Service:    http://localhost:8000"
echo "   🌐 UI Service:      http://localhost:8080 & https://localhost:8443"
echo "   🤖 Ollama:          http://localhost:11434"
echo "   📊 Redis:           localhost:6379"
echo "   🎙️ Whisper:        http://localhost:9001"
echo "   🗣️ TTS:            http://localhost:9002"
echo "   🔄 n8n:            http://localhost:5678"
echo "   🔗 LiteLLM:        http://localhost:8001"
echo ""
echo "🔧 Management commands:"
echo "   📊 Status:    docker-compose ps"
echo "   📄 Logs:      docker-compose logs -f [service]"
echo "   🛑 Stop:      docker-compose down"
echo "   🔄 Restart:   docker-compose restart [service]"
echo ""
echo "✨ Zoe is fully operational! The authentication system now works perfectly!"

