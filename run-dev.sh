#!/bin/bash

# Zoe Development Server Startup Script
echo "🚀 Starting Zoe Development Environment..."

# Function to check if port is available
check_port() {
    if lsof -Pi :$1 -sTCP:LISTEN -t >/dev/null ; then
        echo "❌ Port $1 is already in use"
        return 1
    else
        echo "✅ Port $1 is available"
        return 0
    fi
}

# Check required ports
echo ""
echo "🔍 Checking ports..."
check_port 8002 || exit 1
check_port 8080 || exit 1

echo ""
echo "🔐 Starting Auth Service..."
cd /home/pi/zoe/services/zoe-auth
source venv/bin/activate
python simple_main.py &
AUTH_PID=$!
echo "Auth service started (PID: $AUTH_PID)"

# Wait for auth service to start
sleep 3

echo ""
echo "🌐 Starting UI Server..."
cd /home/pi/zoe/services/zoe-ui/dist
python3 -m http.server 8080 &
UI_PID=$!
echo "UI server started (PID: $UI_PID)"

echo ""
echo "✅ Zoe is ready!"
echo ""
echo "📱 Access your application at: http://localhost:8080"
echo "🔐 Auth service running at: http://localhost:8002"
echo ""
echo "Default credentials:"
echo "  Admin: admin / admin"
echo "  User:  user / user"
echo ""
echo "Press Ctrl+C to stop all services..."

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "🛑 Stopping services..."
    kill $AUTH_PID 2>/dev/null
    kill $UI_PID 2>/dev/null
    echo "👋 Goodbye!"
    exit 0
}

# Set trap to cleanup on script exit
trap cleanup INT TERM

# Wait for user to press Ctrl+C
wait

