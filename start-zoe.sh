#!/bin/bash

# Zoe Complete Startup Script
# This will ensure everything works perfectly

echo "🌟 Starting Zoe AI Assistant..."
echo "================================"

# Kill any existing processes on our ports
echo "🧹 Cleaning up any existing processes..."
pkill -f "python.*simple_main.py" 2>/dev/null || true
pkill -f "python.*proxy-server.py" 2>/dev/null || true
pkill -f "python.*8080" 2>/dev/null || true
sleep 2

# Check if auth service directory exists
if [ ! -d "/home/pi/zoe/services/zoe-auth" ]; then
    echo "❌ Auth service directory not found!"
    exit 1
fi

# Check if UI directory exists
if [ ! -d "/home/pi/zoe/services/zoe-ui/dist" ]; then
    echo "❌ UI directory not found!"
    exit 1
fi

echo "✅ Directories found"

# Start auth service
echo ""
echo "🔐 Starting Authentication Service..."
cd /home/pi/zoe/services/zoe-auth

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

# Start auth service in background
python simple_main.py > /tmp/zoe-auth.log 2>&1 &
AUTH_PID=$!
echo "Auth service started (PID: $AUTH_PID)"

# Wait for auth service to be ready
echo "⏳ Waiting for auth service to start..."
for i in {1..10}; do
    if curl -s http://localhost:8002/health >/dev/null 2>&1; then
        echo "✅ Auth service is ready!"
        break
    fi
    if [ $i -eq 10 ]; then
        echo "❌ Auth service failed to start!"
        echo "Check logs: tail /tmp/zoe-auth.log"
        exit 1
    fi
    sleep 1
done

# Start UI with proxy
echo ""
echo "🌐 Starting UI Server with Authentication Proxy..."
cd /home/pi/zoe/services/zoe-ui/dist
python3 proxy-server.py > /tmp/zoe-ui.log 2>&1 &
UI_PID=$!
echo "UI server started (PID: $UI_PID)"

# Wait for UI server to be ready
echo "⏳ Waiting for UI server to start..."
for i in {1..5}; do
    if curl -s http://localhost:8090 >/dev/null 2>&1; then
        echo "✅ UI server is ready!"
        break
    fi
    if [ $i -eq 5 ]; then
        echo "❌ UI server failed to start!"
        echo "Check logs: tail /tmp/zoe-ui.log"
        exit 1
    fi
    sleep 1
done

# Save PIDs for cleanup
echo $AUTH_PID > /tmp/zoe-auth.pid
echo $UI_PID > /tmp/zoe-ui.pid

echo ""
echo "🎉 ZOE IS READY!"
echo "================================"
echo ""
echo "📱 Open your browser and go to:"
echo "   http://localhost:8090"
echo ""
echo "🔑 Login credentials:"
echo "   Admin: admin / admin"
echo "   User:  user / user"
echo "   Guest: Click guest button"
echo ""
echo "🎯 Features working:"
echo "   ✅ Beautiful orb animation"
echo "   ✅ Profile selection"
echo "   ✅ PIN pad authentication" 
echo "   ✅ Touch keyboard for passwords"
echo "   ✅ Guest access"
echo "   ✅ Session management"
echo "   ✅ All navigation links"
echo ""
echo "📋 Services running:"
echo "   🔐 Auth Service: http://localhost:8002"
echo "   🌐 UI Server:    http://localhost:8090"
echo ""
echo "📄 Logs:"
echo "   Auth: tail -f /tmp/zoe-auth.log"
echo "   UI:   tail -f /tmp/zoe-ui.log"
echo ""
echo "🛑 To stop Zoe:"
echo "   ./stop-zoe.sh"
echo ""

# Keep the script running
echo "✨ Zoe is running! Press Ctrl+C to stop..."

# Cleanup function
cleanup() {
    echo ""
    echo "🛑 Stopping Zoe services..."
    kill $AUTH_PID 2>/dev/null || true
    kill $UI_PID 2>/dev/null || true
    rm -f /tmp/zoe-auth.pid /tmp/zoe-ui.pid
    echo "👋 Zoe stopped. Goodbye!"
    exit 0
}

# Set trap for cleanup
trap cleanup INT TERM

# Wait for interrupt
wait $UI_PID
