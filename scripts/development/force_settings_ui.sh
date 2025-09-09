#!/bin/bash
# FORCE_SETTINGS_UI.sh
# Directly add settings_ui import and registration

echo "🔨 FORCING SETTINGS_UI REGISTRATION"
echo "===================================="
echo ""

cd /home/pi/zoe

# Show current main.py structure
echo "📋 Current main.py structure:"
docker exec zoe-core bash -c "grep -n 'from routers import' main.py && grep -n 'include_router' main.py | head -6"

# Force add settings_ui
echo -e "\n🔧 Force-adding settings_ui to main.py..."
docker exec zoe-core bash -c 'cat > /tmp/add_settings_ui.py << "EOF"
# Read main.py
with open("/app/main.py", "r") as f:
    lines = f.readlines()

# Find where the last router is imported and add settings_ui
added = False
for i, line in enumerate(lines):
    if "from routers import settings" in line:
        # Check if settings_ui is already there
        if "settings_ui" not in line:
            lines[i] = line.strip() + ", settings_ui\n"
            print(f"Added settings_ui to import at line {i+1}")
            added = True
        break

# Find where settings router is included and add settings_ui after it
for i, line in enumerate(lines):
    if "app.include_router(settings.router)" in line:
        # Check next lines to see if settings_ui already there
        already_there = False
        for j in range(i+1, min(i+5, len(lines))):
            if "settings_ui" in lines[j]:
                already_there = True
                break
        
        if not already_there:
            # Insert settings_ui router after settings router
            lines.insert(i+1, "    app.include_router(settings_ui.router)\n")
            lines.insert(i+2, "    logger.info(\"✅ Settings UI router loaded\")\n")
            print(f"Added settings_ui.router at line {i+2}")
            added = True
        break

if added:
    with open("/app/main.py", "w") as f:
        f.writelines(lines)
    print("✅ Successfully updated main.py")
else:
    print("⚠️ No changes needed or already present")

# Show the result
print("\nUpdated sections:")
with open("/app/main.py", "r") as f:
    for i, line in enumerate(f, 1):
        if "settings" in line.lower():
            print(f"  Line {i}: {line.strip()}")
EOF
python3 /tmp/add_settings_ui.py'

# Restart
echo -e "\n🔄 Restarting zoe-core..."
docker compose restart zoe-core
sleep 10

# Verify it loaded
echo -e "\n📋 Checking if settings_ui loaded..."
docker logs zoe-core --tail 15 | grep -E "Settings|settings" || echo "No settings messages in logs"

# Test the endpoint
echo -e "\n🧪 Testing settings-ui endpoint..."
response=$(curl -s -w "\nHTTP_CODE:%{http_code}" http://localhost:8000/api/settings-ui/personalities)
http_code=$(echo "$response" | grep HTTP_CODE | cut -d: -f2)

if [ "$http_code" = "200" ]; then
    echo "✅ SUCCESS! Settings UI endpoint working!"
    echo "Response preview:"
    echo "$response" | head -n -1 | jq 'keys' 2>/dev/null || echo "$response"
else
    echo "❌ Still not working (HTTP $http_code)"
    echo ""
    echo "Checking available routes:"
    curl -s http://localhost:8000/openapi.json | jq '.paths | keys[]' 2>/dev/null | grep settings
    
    echo -e "\nChecking for import errors:"
    docker logs zoe-core --tail 30 | grep -i "error\|exception\|failed" || echo "No errors found"
fi

# Final status
echo -e "\n📊 Final Status:"
echo "Zoe: $(curl -s -X POST http://localhost:8000/api/chat/ -H 'Content-Type: application/json' -d '{"message":"test"}' -o /dev/null -w '%{http_code}')"
echo "Zack: $(curl -s -X POST http://localhost:8000/api/developer/chat -H 'Content-Type: application/json' -d '{"message":"test"}' -o /dev/null -w '%{http_code}')"
echo "Settings UI: $(curl -s http://localhost:8000/api/settings-ui/personalities -o /dev/null -w '%{http_code}')"
