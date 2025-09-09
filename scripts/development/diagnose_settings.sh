#!/bin/bash
# DIAGNOSE_SETTINGS.sh
# Find why settings router isn't working

echo "🔍 DIAGNOSING SETTINGS ROUTER"
echo "=============================="
echo ""

cd /home/pi/zoe

# Check if router file exists
echo "📁 Checking router files..."
docker exec zoe-core ls -la routers/ | grep settings || echo "  No settings routers found"

# Check main.py
echo -e "\n📝 Checking main.py imports..."
docker exec zoe-core grep -n "settings" main.py || echo "  No settings import found"

# Check what routers are actually registered
echo -e "\n🔌 Checking registered routes..."
docker exec zoe-core python3 << 'CHECK'
from main import app

# List all routes
routes = []
for route in app.routes:
    if hasattr(route, 'path'):
        routes.append(route.path)

print("Registered routes:")
for r in sorted(routes):
    if 'settings' in r or 'personalities' in r:
        print(f"  ✓ {r}")

if not any('settings' in r for r in routes):
    print("  ❌ No settings routes found!")
CHECK

# Fix: Properly register the router
echo -e "\n🔧 Fixing registration..."
docker exec zoe-core python3 << 'FIX'
import os

# First check if settings_ui.py exists
if os.path.exists('/app/routers/settings_ui.py'):
    print("✓ settings_ui.py exists")
    
    # Check main.py
    with open('/app/main.py', 'r') as f:
        content = f.read()
    
    if 'settings_ui' not in content:
        print("Adding settings_ui to main.py...")
        
        # Add import
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'from routers import' in line and 'settings_ui' not in line:
                # Add to imports
                if line.strip().endswith('import'):
                    lines[i] = line + ' settings_ui,'
                else:
                    lines[i] = line.rstrip() + ', settings_ui'
                print(f"Updated import line: {lines[i]}")
                break
        
        # Add router include
        for i, line in enumerate(lines):
            if 'app.include_router' in line:
                # Add after last router include
                lines.insert(i+1, '    app.include_router(settings_ui.router)')
                print("Added router include")
                break
        
        # Write back
        with open('/app/main.py', 'w') as f:
            f.write('\n'.join(lines))
        
        print("✓ Updated main.py")
    else:
        print("✓ settings_ui already in main.py")
else:
    print("❌ settings_ui.py doesn't exist - creating it...")
    # Create it from our previous work
    # [Previous router code would go here]
FIX

# Restart
echo -e "\n🔄 Restarting zoe-core..."
docker compose restart zoe-core
sleep 10

# Test
echo -e "\n🧪 Testing settings endpoint..."
echo "  Status: $(curl -s http://localhost:8000/api/settings-ui/personalities -o /dev/null -w '%{http_code}')"

if [ "$(curl -s http://localhost:8000/api/settings-ui/personalities -o /dev/null -w '%{http_code}')" = "200" ]; then
    echo "  ✅ Settings working!"
else
    echo "  ❌ Still not working"
    echo ""
    echo "  Checking logs:"
    docker logs zoe-core --tail 10 | grep -i error || true
fi
