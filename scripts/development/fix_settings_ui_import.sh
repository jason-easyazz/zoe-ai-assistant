#!/bin/bash
# FIX_SETTINGS_UI_IMPORT.sh
# Actually import and register settings_ui

echo "📝 FIXING SETTINGS_UI IMPORT"
echo "============================"
echo ""

cd /home/pi/zoe

# First, let's see what's actually being imported
echo "Current imports in main.py:"
docker exec zoe-core grep "from routers import" main.py

# Add settings_ui import properly
echo -e "\n🔧 Adding settings_ui import..."
docker exec zoe-core python3 << 'FIX_IMPORT'
with open('/app/main.py', 'r') as f:
    content = f.read()

# Check current state
print("Current state:")
if 'settings_ui' in content:
    print("  settings_ui mentioned in file")
else:
    print("  settings_ui NOT in file")

# Find the import line and add settings_ui
lines = content.split('\n')
import_found = False
registration_found = False

for i, line in enumerate(lines):
    # Fix the import line
    if 'from routers import' in line and 'settings_ui' not in line:
        if 'settings' in line:
            # Add settings_ui after settings
            lines[i] = line.replace('settings', 'settings, settings_ui')
            print(f"Updated import line: {lines[i]}")
            import_found = True

# Add registration after settings.router
for i, line in enumerate(lines):
    if 'app.include_router(settings.router)' in line:
        # Check if settings_ui.router is already in next line
        if i+1 < len(lines) and 'settings_ui' not in lines[i+1]:
            # Add settings_ui registration
            indent = '    '  # Same indent as other routers
            lines.insert(i+1, f'{indent}app.include_router(settings_ui.router)')
            lines.insert(i+2, f'{indent}logger.info("✅ Settings UI router loaded")')
            print(f"Added settings_ui.router registration after line {i}")
            registration_found = True
            break

if import_found or registration_found:
    # Write back
    with open('/app/main.py', 'w') as f:
        f.write('\n'.join(lines))
    print("✅ Fixed main.py")
else:
    print("⚠️ No changes made")
FIX_IMPORT

# Alternative approach - add it in try/except block
echo -e "\n🔧 Alternative: Adding in try/except block..."
docker exec zoe-core python3 << 'ALT_FIX'
with open('/app/main.py', 'r') as f:
    lines = f.readlines()

# Find last router registration and add after it
for i in range(len(lines)-1, 0, -1):
    if 'logger.info("✅' in lines[i] and 'router loaded' in lines[i]:
        # Add settings_ui block after this
        if 'settings_ui' not in ''.join(lines):
            indent = '    ' if lines[i].startswith('    ') else ''
            new_block = f'''
try:
    from routers import settings_ui
    app.include_router(settings_ui.router)
    logger.info("✅ Settings UI router loaded")
except Exception as e:
    logger.error(f"Failed to load settings_ui router: {{e}}")

'''
            lines.insert(i+2, new_block)
            print("Added settings_ui block")
            
            with open('/app/main.py', 'w') as f:
                f.writelines(lines)
            print("✅ Added settings_ui router block")
            break
ALT_FIX

# Restart
echo -e "\n🔄 Restarting zoe-core..."
docker compose restart zoe-core
sleep 10

# Test
echo -e "\n🧪 Testing endpoints..."
echo "Settings UI: $(curl -s http://localhost:8000/api/settings-ui/personalities -o /dev/null -w '%{http_code}')"

# Check logs for errors
echo -e "\nChecking logs for settings_ui:"
docker logs zoe-core --tail 20 | grep -i "settings" || true

# If still not working, check the actual routes
if [ "$(curl -s http://localhost:8000/api/settings-ui/personalities -o /dev/null -w '%{http_code}')" != "200" ]; then
    echo -e "\n📋 Available routes:"
    curl -s http://localhost:8000/openapi.json | jq '.paths | keys[]' 2>/dev/null | head -20
fi
