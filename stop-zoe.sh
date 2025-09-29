#!/bin/bash

echo "🛑 Stopping Zoe AI Assistant..."

# Kill processes by PID if files exist
if [ -f /tmp/zoe-auth.pid ]; then
    AUTH_PID=$(cat /tmp/zoe-auth.pid)
    kill $AUTH_PID 2>/dev/null && echo "✅ Auth service stopped (PID: $AUTH_PID)"
    rm -f /tmp/zoe-auth.pid
fi

if [ -f /tmp/zoe-ui.pid ]; then
    UI_PID=$(cat /tmp/zoe-ui.pid)
    kill $UI_PID 2>/dev/null && echo "✅ UI server stopped (PID: $UI_PID)"
    rm -f /tmp/zoe-ui.pid
fi

# Kill any remaining processes
pkill -f "python.*simple_main.py" 2>/dev/null
pkill -f "python.*proxy-server.py" 2>/dev/null

echo "👋 Zoe stopped completely!"

