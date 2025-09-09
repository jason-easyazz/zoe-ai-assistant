#!/bin/bash
# REGISTER_SETTINGS_UI.sh
# Properly register the settings_ui router

echo "🔧 REGISTERING SETTINGS_UI ROUTER"
echo "=================================="
echo ""

cd /home/pi/zoe

# Add settings_ui to main.py
echo "📝 Adding settings_ui to main.py..."
docker exec zoe-core python3 << 'REGISTER'
# Read main.py
with open('/app/main.py', 'r') as f:
    lines = f.readlines()

# Find where to add the import and registration
modified = False
for i, line in enumerate(lines):
    # After settings import, add settings_ui
    if 'from routers import settings' in line and 'settings_ui' not in line:
        lines[i] = line.strip() + ', settings_ui\n'
        print(f"Added settings_ui to imports at line {i+1}")
        modified = True
        break

# Find where settings.router is registered and add settings_ui after it
for i, line in enumerate(lines):
    if 'app.include_router(settings.router)' in line:
        # Check if settings_ui is already registered in next few lines
        already_registered = False
        for j in range(i+1, min(i+5, len(lines))):
            if 'settings_ui.router' in lines[j]:
                already_registered = True
                break
        
        if not already_registered:
            indent = len(line) - len(line.lstrip())
            lines.insert(i+1, ' ' * indent + 'app.include_router(settings_ui.router)\n')
            lines.insert(i+2, ' ' * indent + 'logger.info("✅ Settings UI router loaded")\n')
            print(f"Added settings_ui.router registration after line {i+1}")
            modified = True
        break

if modified:
    # Write back
    with open('/app/main.py', 'w') as f:
        f.writelines(lines)
    print("✅ Updated main.py successfully")
else:
    print("ℹ️ No changes needed or already registered")

# Show the relevant part of main.py
print("\n📋 Current registration block:")
with open('/app/main.py', 'r') as f:
    lines = f.readlines()
    for i, line in enumerate(lines):
        if 'settings' in line and ('import' in line or 'include_router' in line):
            print(f"  Line {i+1}: {line.strip()}")
REGISTER

# Restart zoe-core
echo -e "\n🔄 Restarting zoe-core..."
docker compose restart zoe-core
sleep 10

# Test all endpoints
echo -e "\n🧪 Testing all endpoints..."
echo "Zoe Chat: $(curl -s -X POST http://localhost:8000/api/chat/ -H 'Content-Type: application/json' -d '{"message":"test"}' -o /dev/null -w '%{http_code}')"
echo "Zack Chat: $(curl -s -X POST http://localhost:8000/api/developer/chat -H 'Content-Type: application/json' -d '{"message":"test"}' -o /dev/null -w '%{http_code}')"
echo "Settings (old): $(curl -s http://localhost:8000/api/settings/personalities -o /dev/null -w '%{http_code}')"
echo "Settings UI: $(curl -s http://localhost:8000/api/settings-ui/personalities -o /dev/null -w '%{http_code}')"

# If still 404, check what's actually available
if [ "$(curl -s http://localhost:8000/api/settings-ui/personalities -o /dev/null -w '%{http_code}')" = "404" ]; then
    echo -e "\n❌ Still not working. Checking available routes..."
    curl -s http://localhost:8000/openapi.json | jq '.paths | keys[]' 2>/dev/null | grep settings || echo "No settings routes found in API"
fi

echo -e "\n✅ Registration complete!"
